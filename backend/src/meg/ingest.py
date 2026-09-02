"""`meg_ingest` stage: discover raw MEG (FIF) recordings under a cohort's
source path and stage them into the cohort's raw workspace.

Uses the vendored (`copy_utils.py`) SESHAT-derived copy/integrity-check
helpers to preserve split-file sets and copy calibration/crosstalk
reference files when configured. Failures (missing source path, missing
calibration/crosstalk, copy failures) are logged to the shared
`ingest_conflicts` table -- see `extract.writer.MetadataWriter._log_conflict`
for the DICOM-side sibling of this pattern -- per the MEG parallel track
plan's decision to reuse that table instead of adding MEG-only failure
columns.

This module only implements the core discovery/copy logic; wiring it into
a pipeline-step API route (job creation, progress streaming, pipeline-step
sync) is a separate, later task (see the MEG parallel track plan, task 10).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.dialects.postgresql import insert

from jobs.control import JobControl
from metadata_db.schema import IngestConflict
from metadata_db.session import SessionLocal

from .config import MegIngestConfig
from .copy_utils import check_fif, copy_data, copy_squid_databases

logger = logging.getLogger(__name__)

# Standard MaxFilter reference filenames looked for anywhere under a
# cohort's MEG source tree (mirrors the well-known Elekta/NatMEG defaults
# that SESHAT's copy_squid_databases previously hardcoded as absolute
# paths -- see meg.copy_utils module docstring).
CALIBRATION_FILENAMES = ("sss_cal.dat",)
CROSSTALK_FILENAMES = ("ct_sparse.fif",)


@dataclass
class MegIngestResult:
    """Summary counters for one `meg_ingest` run."""

    discovered: int = 0
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    calibration_copied: bool = False
    crosstalk_copied: bool = False
    failures: list[dict] = field(default_factory=list)

    def as_metrics(self) -> dict:
        return {
            "discovered": self.discovered,
            "copied": self.copied,
            "skipped": self.skipped,
            "failed": self.failed,
            "calibration_copied": self.calibration_copied,
            "crosstalk_copied": self.crosstalk_copied,
            "failure_count": len(self.failures),
        }


def get_meg_raw_root(cohort_source_path: str) -> Path:
    """Cohort raw-workspace root for staged FIF files.

    This is where ``meg_ingest`` copies discovered recordings and where
    ``meg_scan`` reads them back from. MEG cohorts skip the DICOM anonymize
    stage entirely (see ``nils_dataset_pipeline.ordering.MEG_PIPELINE_STAGES``:
    ``meg_ingest -> meg_scan -> meg_bids``), so there is no DICOM-style
    ``dcm-original``/``dcm-raw`` split (``anonymize.config.setup_derivatives_folders``)
    to reuse. Staged FIF files instead live under a single
    ``derivatives/meg-raw`` directory, keeping the same "derivatives" root
    convention DICOM cohorts use without the DICOM-only original/raw split.
    """
    return Path(cohort_source_path).resolve() / "derivatives" / "meg-raw"


def discover_fif_files(source_root: Path, preserve_split_files: bool = True) -> list[Path]:
    """Recursively discover raw FIF files under `source_root`.

    When `preserve_split_files` is False, continuation parts of a split
    set (`*-1.fif`, `*-2.fif`, ...) are excluded, keeping only the primary
    file of each recording -- callers that want a complete, resumable copy
    of a split recording should leave this at its default of True.
    """
    if not source_root.exists():
        return []

    discovered: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(source_root):
        for filename in filenames:
            if not filename.lower().endswith(".fif"):
                continue
            full_path = Path(dirpath) / filename
            if not preserve_split_files and check_fif(str(full_path))["is_split"]:
                continue
            discovered.append(full_path)
    return sorted(discovered)


def _find_reference_file(source_root: Path, candidate_names: tuple[str, ...]) -> Optional[Path]:
    """Find the first file under `source_root` whose name matches a candidate."""
    for dirpath, _dirnames, filenames in os.walk(source_root):
        for filename in filenames:
            if filename in candidate_names:
                return Path(dirpath) / filename
    return None


def _log_ingest_conflict(cohort_id: int, scope: str, uid: str, message: str, file_path: Optional[str]) -> None:
    if cohort_id is None:
        return
    try:
        with SessionLocal() as session:
            stmt = (
                insert(IngestConflict)
                .values(cohort_id=cohort_id, scope=scope, uid=uid, message=message, file_path=file_path)
                .on_conflict_do_nothing()
            )
            session.execute(stmt)
            session.commit()
    except Exception:
        logger.exception("Failed to record ingest conflict for cohort %s (%s): %s", cohort_id, scope, message)


def run_meg_ingest(
    config: MegIngestConfig,
    cohort_id: int,
    raw_root: Path,
    progress_callback: Optional[Callable[[dict], None]] = None,
    control: Optional[JobControl] = None,
    job_id: Optional[int] = None,
) -> MegIngestResult:
    """Discover FIF recordings under `config.source_path` and copy them
    (preserving relative layout and split-file sets) into `raw_root`.

    Raises `jobs.errors.JobCancelledError` (via `control.checkpoint_blocking`)
    if `control` is provided and cancellation is requested mid-run, mirroring
    `extract.core.run_extraction`'s cancellation behavior.
    """
    result = MegIngestResult()

    if not config.source_path:
        logger.warning("MEG ingest has no source_path configured for cohort %s", cohort_id)
        _log_ingest_conflict(cohort_id, "meg_ingest_source", "", "No source_path configured", None)
        return result

    source_root = Path(config.source_path)
    if not source_root.exists():
        logger.warning("MEG ingest source path does not exist: %s", source_root)
        _log_ingest_conflict(
            cohort_id, "meg_ingest_source", str(source_root), "Source path does not exist", str(source_root)
        )
        return result

    raw_root.mkdir(parents=True, exist_ok=True)

    def _emit(payload: dict) -> None:
        if callable(progress_callback):
            try:
                progress_callback(payload)
            except Exception:  # noqa: BLE001 - progress reporting must not break the run
                pass

    discovered_files = discover_fif_files(source_root, preserve_split_files=config.preserve_split_files)

    calibration_source = _find_reference_file(source_root, CALIBRATION_FILENAMES) if config.copy_calibration_files else None
    crosstalk_source = _find_reference_file(source_root, CROSSTALK_FILENAMES) if config.copy_crosstalk_files else None

    # Calibration/crosstalk files are copied separately below (to a stable,
    # well-known destination name); exclude them from the generic per-file
    # copy loop so they aren't copied twice. Only crosstalk (`.fif`) can
    # appear in `discovered_files`; calibration (`.dat`) never matches the
    # `.fif` filter in discover_fif_files().
    reference_sources = {p for p in (calibration_source, crosstalk_source) if p is not None}
    if reference_sources:
        discovered_files = [f for f in discovered_files if f not in reference_sources]

    result.discovered = len(discovered_files)
    _emit(
        {
            "stage": "discovered",
            "message": f"Discovered {result.discovered} FIF file(s)",
            "total": result.discovered,
        }
    )

    if config.copy_calibration_files and calibration_source is None:
        logger.info("No calibration file found under %s", source_root)
        _log_ingest_conflict(
            cohort_id, "meg_calibration", str(source_root), "No calibration file found alongside source recordings", None
        )
    if config.copy_crosstalk_files and crosstalk_source is None:
        logger.info("No crosstalk file found under %s", source_root)
        _log_ingest_conflict(
            cohort_id, "meg_crosstalk", str(source_root), "No crosstalk file found alongside source recordings", None
        )

    if calibration_source or crosstalk_source:
        calibration_dest = raw_root / CALIBRATION_FILENAMES[0] if calibration_source else None
        crosstalk_dest = raw_root / CROSSTALK_FILENAMES[0] if crosstalk_source else None
        try:
            copy_squid_databases(
                str(calibration_source) if calibration_source else None,
                str(calibration_dest) if calibration_dest else None,
                str(crosstalk_source) if crosstalk_source else None,
                str(crosstalk_dest) if crosstalk_dest else None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to copy calibration/crosstalk files: %s", exc)
            _log_ingest_conflict(
                cohort_id,
                "meg_calibration",
                str(source_root),
                f"Failed to copy calibration/crosstalk files: {exc}",
                str(source_root),
            )
        result.calibration_copied = bool(calibration_dest and calibration_dest.exists())
        result.crosstalk_copied = bool(crosstalk_dest and crosstalk_dest.exists())

    for index, source_file in enumerate(discovered_files):
        if control is not None:
            control.checkpoint_blocking(job_id)

        rel_path = source_file.relative_to(source_root)
        dest_file = raw_root / rel_path

        _emit(
            {
                "stage": "copying",
                "message": f"Copying {index + 1}/{result.discovered}",
                "total": result.discovered,
                "processed": index,
                "errors": result.failed,
                "current_file": str(rel_path),
            }
        )

        try:
            _match, _src, _dst, message, existing_file, new_file, failed_file = copy_data(
                str(source_file), str(dest_file)
            )
            if failed_file:
                result.failed += 1
                result.failures.append({"source": str(source_file), "reason": message})
                _log_ingest_conflict(cohort_id, "meg_file", str(rel_path), message, str(source_file))
            elif existing_file:
                result.skipped += 1
            elif new_file:
                result.copied += 1
        except Exception as exc:  # noqa: BLE001 - per-file isolation, matches copy_utils.copy_data's own style
            logger.error("Failed to copy %s: %s", source_file, exc)
            result.failed += 1
            result.failures.append({"source": str(source_file), "reason": str(exc)})
            _log_ingest_conflict(cohort_id, "meg_file", str(rel_path), str(exc), str(source_file))

    _emit(
        {
            "stage": "done",
            "message": "MEG ingest completed",
            "total": result.discovered,
            "processed": result.copied + result.skipped,
            "errors": result.failed,
            "metrics": result.as_metrics(),
        }
    )

    return result
