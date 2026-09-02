"""`meg_bids` stage: adapter between the vendored `bids_writer.write_bids_dataset()`
and NILS jobs/pipeline steps.

Builds a per-recording conversion table directly from `meg_acquisition` rows
(the `meg_acquisition` table *is* the conversion table for phase 1 — see
`meg.conversion_table`'s module docstring), writes BIDS output via
`mne-bids`, and persists the resulting `bids_status`/`bids_path`/`bids_name`
back onto each `meg_acquisition` row.

Cancellation: `bids_writer.write_bids_dataset()` swallows exceptions raised
from its `progress_callback`/`row_persist_callback` arguments (matching the
vendored source's own resilience — a broken UI callback must never abort a
running conversion), so `control.checkpoint_blocking()` cannot be raised
*through* those hooks. Instead, this module calls `write_bids_dataset()`
once per acquisition (a single-row conversion table each time) and checks
`control.checkpoint_blocking(job_id)` between calls, mirroring the
per-file cancellation granularity already used by `meg.scanner.run_meg_scan`
and `meg.ingest.run_meg_ingest`. Calibration/crosstalk files are re-checked
(cheaply — `mne_bids.write_meg_calibration`/`write_meg_crosstalk` skip
writing when already present) on every call as a result; this is accepted
per-row overhead in exchange for real cancellation support.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from sqlalchemy import select, update

from jobs.control import JobControl
from metadata_db.schema import MegAcquisition, Subject, SubjectCohort, Study
from metadata_db.session import SessionLocal

from .bids_writer import create_dataset_description, write_bids_dataset
from .config import MegBidsConfig, MegBidsOverwriteMode
from .conversion_table import CONVERSION_COLUMNS

logger = logging.getLogger(__name__)

# Rows already in one of these `bids_status` values are considered converted
# (or intentionally not convertible) and are skipped unless overwrite mode
# is requested — mirrors `bids_writer.write_bids_dataset`'s own
# `~df["status"].isin(["processed", "skip", "missing"])` process mask.
_ALREADY_CONVERTED_STATUSES = ("processed", "skip", "missing")


@dataclass
class MegBidsResult:
    """Aggregated counters and diagnostics for one `meg_bids` run."""

    total: int = 0
    to_process: int = 0
    processed: int = 0
    errors: int = 0
    error_details: list[dict] = field(default_factory=list)
    message: str = ""

    def as_metrics(self) -> dict:
        return {
            "total": self.total,
            "to_process": self.to_process,
            "processed": self.processed,
            "errors": self.errors,
            "error_details": self.error_details,
            "message": self.message,
        }


def _fetch_acquisitions(cohort_id: int) -> list[tuple[MegAcquisition, str]]:
    """Load every `meg_acquisition` row reachable from `cohort_id` via
    `subject_cohorts`, paired with the owning subject's `subject_code`."""
    with SessionLocal() as session:
        rows = session.execute(
            select(MegAcquisition, Subject.subject_code)
            .join(Subject, MegAcquisition.subject_id == Subject.subject_id)
            .join(SubjectCohort, Subject.subject_id == SubjectCohort.subject_id)
            .where(SubjectCohort.cohort_id == cohort_id)
            .order_by(MegAcquisition.meg_acquisition_id)
        ).all()
        # Detach values we need past session close by materializing a plain list.
        return [(acquisition, subject_code) for acquisition, subject_code in rows]


def _acquisition_to_row(acquisition: MegAcquisition, subject_code: str, *, overwrite: bool) -> dict:
    """Build one `meg.conversion_table.CONVERSION_COLUMNS`-shaped row from a
    `meg_acquisition` ORM row.

    `session_from`/`session_to` are both set to the raw parsed
    `session_label` (see `meg.models.FifHeader.session_from`): phase 1 has
    no session-code-mapping table, only the subject-code mapping already
    applied during `meg_scan` (see the plan's Decisions), so the resolved
    subject code is the only identifier that differs from its "from" value.
    """
    fif_path = Path(acquisition.fif_file_path) if acquisition.fif_file_path else None
    session_label = acquisition.session_label or "01"

    already_converted = (acquisition.bids_status or "") in _ALREADY_CONVERTED_STATUSES
    status = "run" if (overwrite or not already_converted) else acquisition.bids_status

    # Start from the full CONVERSION_COLUMNS schema (all None) so that
    # bids_writer's bookkeeping helpers (`_record_processing_success`,
    # `_build_row_metadata`, ...), which unconditionally index columns like
    # "metadata"/"time_stamp"/"mtime"/"size", never hit a KeyError for
    # columns this stage doesn't otherwise populate.
    row: dict = {column: None for column in CONVERSION_COLUMNS}
    row.update(
        {
            "status": status,
            "participant_from": subject_code,
            "participant_to": subject_code,
            "session_from": session_label,
            "session_to": session_label,
            "task": acquisition.bids_task or "",
            "run": acquisition.bids_run or "",
            "datatype": acquisition.bids_datatype or "meg",
            "acquisition": acquisition.bids_acq_label or "",
            "processing": acquisition.bids_processing_label or "",
            "description": "",
            "raw_path": str(fif_path.parent) if fif_path else "",
            "raw_name": fif_path.name if fif_path else "",
            "bids_path": acquisition.bids_path,
            "bids_name": acquisition.bids_name,
        }
    )
    # Not part of CONVERSION_COLUMNS; carried through solely so
    # `_persist_row` (our `row_persist_callback`) knows which
    # `meg_acquisition` row a finished row corresponds to. `write_bids_dataset`
    # does not filter unknown columns off the DataFrame it operates on.
    row["meg_acquisition_id"] = acquisition.meg_acquisition_id
    return row


def _persist_row(row: dict) -> None:
    """`row_persist_callback` for `write_bids_dataset`: writes the finished
    row's `status`/`bids_path`/`bids_name` back onto its `meg_acquisition`
    row."""
    acquisition_id = row.get("meg_acquisition_id")
    if acquisition_id is None:
        return
    with SessionLocal() as session:
        session.execute(
            update(MegAcquisition)
            .where(MegAcquisition.meg_acquisition_id == acquisition_id)
            .values(
                bids_status=row.get("status"),
                bids_path=row.get("bids_path"),
                bids_name=row.get("bids_name"),
            )
        )
        session.commit()


def _resolve_dataset_name(cohort_id: int, cohort_name: str, configured_name: str) -> str:
    """Resolve the `dataset_description.json` `Name` field.

    Priority: explicit `MegBidsConfig.dataset_description_name` config, then
    the first non-empty `study.study_description` reachable from this
    cohort (populated by `meg_scan`, task 7), then the cohort name itself.
    """
    if configured_name:
        return configured_name

    with SessionLocal() as session:
        description = session.execute(
            select(Study.study_description)
            .join(Subject, Study.subject_id == Subject.subject_id)
            .join(SubjectCohort, Subject.subject_id == SubjectCohort.subject_id)
            .where(SubjectCohort.cohort_id == cohort_id, Study.study_description.is_not(None))
            .limit(1)
        ).scalar_one_or_none()

    return description or cohort_name


def run_meg_bids(
    config: MegBidsConfig,
    cohort_id: int,
    cohort_name: str,
    raw_root: Path,
    bids_root: Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
    control: Optional[JobControl] = None,
    job_id: Optional[int] = None,
) -> MegBidsResult:
    """Convert every eligible `meg_acquisition` row for `cohort_id` into BIDS
    output under `bids_root`, updating each row's `bids_status`/`bids_path`/
    `bids_name` as it completes.

    `raw_root` is the same staged-recordings root `meg_scan` read headers
    from (`meg.ingest.get_meg_raw_root`); calibration/crosstalk files, if
    present, were copied there by `meg_ingest` under their well-known
    filenames (`meg.ingest.CALIBRATION_FILENAMES`/`CROSSTALK_FILENAMES`).
    """
    result = MegBidsResult()

    calibration_path = raw_root / "sss_cal.dat"
    crosstalk_path = raw_root / "ct_sparse.fif"

    bids_root.mkdir(parents=True, exist_ok=True)
    create_dataset_description(
        str(bids_root),
        name=_resolve_dataset_name(cohort_id, cohort_name, config.dataset_description_name),
    )

    def _emit(payload: dict) -> None:
        if callable(progress_callback):
            try:
                progress_callback(payload)
            except Exception:  # noqa: BLE001 - progress reporting must not break the run
                pass

    acquisitions = _fetch_acquisitions(cohort_id)
    overwrite = config.overwrite_mode == MegBidsOverwriteMode.OVERWRITE

    rows = [_acquisition_to_row(acquisition, subject_code, overwrite=overwrite) for acquisition, subject_code in acquisitions]
    result.total = len(rows)
    to_process = [row for row in rows if row["status"] == "run"]
    result.to_process = len(to_process)

    _emit({"stage": "starting", "message": "Starting BIDS conversion", "total": result.to_process, "processed": 0, "errors": 0})

    for index, row in enumerate(to_process):
        if control is not None:
            control.checkpoint_blocking(job_id)

        _emit(
            {
                "stage": "writing",
                "message": f"Writing BIDS file {index + 1}/{result.to_process}",
                "total": result.to_process,
                "processed": result.processed,
                "errors": result.errors,
                "current_file": row.get("raw_name"),
            }
        )

        single_row_df = pd.DataFrame.from_records([row])
        try:
            summary = write_bids_dataset(
                single_row_df,
                str(bids_root),
                calibration_path=str(calibration_path) if calibration_path.exists() else None,
                crosstalk_path=str(crosstalk_path) if crosstalk_path.exists() else None,
                overwrite=True,
                row_persist_callback=_persist_row,
            )
        except Exception as exc:  # noqa: BLE001 - per-row isolation; one bad recording must not abort the run
            logger.error("Unexpected error converting %s: %s", row.get("raw_name"), exc)
            result.errors += 1
            result.error_details.append({"raw_name": row.get("raw_name"), "reason": str(exc)})
            continue

        result.processed += summary.get("processed_now", 0)
        result.errors += summary.get("errors_now", 0)
        result.error_details.extend(summary.get("error_details", []))

    _emit(
        {
            "stage": "done",
            "message": "MEG BIDS conversion completed",
            "total": result.to_process,
            "processed": result.processed,
            "errors": result.errors,
        }
    )
    result.message = "MEG BIDS conversion completed" if result.to_process else "No recordings required conversion"

    return result
