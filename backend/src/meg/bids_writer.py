"""Core BIDS-writing loop and dataset_description.json creation for MEG.

Vendored and adapted from `cir-utils/tabs/meg-bids/bidsify/pipeline.py`
(`bidsify()`) and `cir-utils/tabs/meg-bids/bidsify/templates.py`
(`create_dataset_description()`) (local checkout:
`/Users/andreas.gerhardsson/Sites/cir-utils`). No LICENSE file was found in
the source repo; it is an internal sibling project by the same author/
organization as NILS.

Adaptations vs. the source `bidsify()`:
  - Takes an explicit `bids_root` plus optional `calibration_path` /
    `crosstalk_path` instead of a CIR-schema config dict.
  - Does not auto-load or auto-generate a conversion table; the caller
    (the `meg_scan`/`meg_bids` stages, later tasks) is responsible for
    building the `conversion_table` DataFrame (see `meg.conversion_table`)
    from NILS's `meg_acquisition` DB rows.
  - No participant/session-mapping (`pmap`) support: `bids_path_from_rawname()`
    already returns filename-derived subject/session, and the
    `participant_to`/`session_to` columns on the conversion table (already
    resolved upstream by `meg_scan`) are applied via `bids_path.update()`
    exactly as in the source.
  - No TSV round-tripping: instead of `df.to_csv(conversion_file, ...)`
    after every row, an optional `row_persist_callback(row: dict)` is
    invoked so the caller can persist the updated row to the database.
  - No tqdm progress bar; only the `progress_callback` hook (which the
    source already supported) is used.
  - Dropped entirely (out of phase-1 scope, deferred to later
    tasks/`meg_qc`): event-id/events.tsv handling, the hedscan/OPM
    channel-parameter merge (`sidecars.py::add_channel_parameters`), the
    EEG-to-MEG sidecar copy (`sidecars.py::copy_eeg_to_meg`), and the whole
    `update_bids_report()` BIDS-validator/compliance-report system.
  - Kept: per-subject/session calibration+crosstalk writing, the
    `trans`/`headshape` special-suffix write branches, and the
    missing-JSON-sidecar-for-processed-data fallback.
  - `print()` calls replaced with `logger` calls.
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime
from os.path import basename, dirname, exists, join
from typing import Callable, Optional

import mne
import pandas as pd
from mne_bids import BIDSPath, make_dataset_description, write_meg_calibration, write_meg_crosstalk, write_raw_bids

from .conversion_table import _record_processing_success
from .parsing import bids_path_from_rawname

logger = logging.getLogger(__name__)

mne.set_log_level("WARNING")


def _zero_pad_subject(participant_str: str) -> str:
    participant_str = str(participant_str)
    if len(participant_str) >= 4:
        return participant_str
    num = int(participant_str.lstrip("0") or "0")
    return f"{num:03d}" if num < 100 else str(num)


def _write_calibration_crosstalk(
    bids_root: str,
    conversion_table: pd.DataFrame,
    calibration_path: Optional[str],
    crosstalk_path: Optional[str],
) -> None:
    unique = conversion_table[["participant_to", "session_to", "datatype"]].drop_duplicates()
    for _, row in unique.iterrows():
        subject_padded = _zero_pad_subject(row["participant_to"])
        session_padded = str(row["session_to"]).zfill(2)
        bids_path = BIDSPath(
            subject=subject_padded,
            session=session_padded,
            datatype=row["datatype"],
            root=bids_root,
        ).mkdir()
        try:
            if row["datatype"] == "meg":
                if calibration_path and not bids_path.meg_calibration_fpath:
                    write_meg_calibration(calibration_path, bids_path)
                if crosstalk_path and not bids_path.meg_crosstalk_fpath:
                    write_meg_crosstalk(crosstalk_path, bids_path)
        except Exception as exc:  # noqa: BLE001 - mirrors vendored behavior
            logger.warning("Error writing calibration/crosstalk files: %s", exc)


def write_bids_dataset(
    conversion_table: pd.DataFrame,
    bids_root: str,
    calibration_path: Optional[str] = None,
    crosstalk_path: Optional[str] = None,
    overwrite: bool = False,
    verbose: bool = False,
    progress_callback: Optional[Callable[[dict], None]] = None,
    row_persist_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Write BIDS files for every eligible row of `conversion_table`.

    `conversion_table` must follow the `meg.conversion_table.CONVERSION_COLUMNS`
    schema (already resolved: `participant_to`/`session_to` set, `status`
    set to `run`/`check`/`processed`/`skip`/`missing`/`error`).

    Rows with `status == "check"` block the whole run (mirrors the source
    behavior of requiring manual review before conversion); no rows are
    written if any are present. Returns a summary dict without persisting
    anything itself -- call `row_persist_callback` per finished row to
    persist to the database.
    """
    ts = datetime.now().strftime("%Y%m%d")

    def _emit_progress(payload: dict) -> None:
        if callable(progress_callback):
            try:
                progress_callback(payload)
            except Exception:  # noqa: BLE001 - progress reporting must not break the run
                pass

    summary = {
        "total": 0,
        "to_process": 0,
        "initial_status_counts": {},
        "processed_now": 0,
        "errors_now": 0,
        "error_details": [],
        "final_status_counts": {},
        "message": "",
    }

    if conversion_table is None or conversion_table.empty:
        summary["message"] = "Conversion table empty or not defined"
        _emit_progress({"stage": "done", "message": summary["message"], "summary": summary})
        return summary

    df = conversion_table.where(pd.notnull(conversion_table) & (conversion_table != ""), None)

    _write_calibration_crosstalk(bids_root, df, calibration_path, crosstalk_path)

    deviants = df[df["status"] == "check"]
    if len(deviants) > 0:
        summary["total"] = len(df)
        summary["to_process"] = 0
        summary["initial_status_counts"] = df["status"].fillna("error").value_counts().to_dict()
        summary["final_status_counts"] = summary["initial_status_counts"]
        summary["message"] = "Conversion blocked: files marked as check require manual review"
        _emit_progress({"stage": "done", "message": summary["message"], "summary": summary})
        return summary

    if overwrite:
        process_mask = pd.Series([True] * len(df), index=df.index)
    else:
        process_mask = ~df["status"].isin(["processed", "skip", "missing"])

    df["status"] = df["status"].fillna("error")
    status_counts = df["status"].value_counts().to_dict()
    n_files_to_process = int(process_mask.sum())
    summary["total"] = len(df)
    summary["to_process"] = n_files_to_process
    summary["initial_status_counts"] = status_counts
    _emit_progress(
        {
            "stage": "starting",
            "message": "Starting BIDS conversion",
            "total": n_files_to_process,
            "processed": 0,
            "errors": 0,
        }
    )

    if not overwrite and n_files_to_process == 0:
        summary["final_status_counts"] = status_counts
        summary["message"] = "No files marked 'run' to convert"
        _emit_progress({"stage": "done", "message": summary["message"], "summary": summary})
        return summary

    pcount = 0
    processed_now = 0
    errors_now = 0
    recent_errors: list[dict] = []
    max_recent_errors = 25

    for i, d in df[process_mask].iterrows():
        bids_path = None
        raw_file = f"{d['raw_path']}/{d['raw_name']}"
        try:
            pcount += 1
            _emit_progress(
                {
                    "stage": "writing",
                    "message": f"Writing BIDS file {pcount}/{n_files_to_process}",
                    "total": n_files_to_process,
                    "processed": processed_now,
                    "errors": errors_now,
                    "current_file": d.get("raw_name"),
                }
            )
            if verbose:
                logger.debug("Processing file %s/%s [%s]: %s", pcount, n_files_to_process, d["status"], d["raw_name"])

            bids_path, _raw_info = bids_path_from_rawname(raw_file, d["session_from"], bids_root)
            if bids_path is None:
                raise ValueError(f"Could not derive BIDS path for {raw_file}")

            run = None
            if pd.notna(d["run"]) and d["run"] != "":
                run = str(d["run"]).zfill(2)

            current_subject_padded = _zero_pad_subject(d["participant_to"])

            bids_path.update(
                subject=current_subject_padded,
                session=str(d["session_to"]).zfill(2),
                task=d["task"],
                acquisition=None if pd.isna(d["acquisition"]) or d["acquisition"] == "" else d["acquisition"],
                processing=None if pd.isna(d["processing"]) or d["processing"] == "" else d["processing"],
                description=None if pd.isna(d["description"]) or d["description"] == "" else d["description"],
                run=run,
            )

            if bids_path.description and "trans" in bids_path.description:
                trans = mne.read_trans(raw_file, verbose="error")
                mne.write_trans(bids_path, trans, overwrite=True)

            elif bids_path.suffix and "headshape" in bids_path.suffix:
                headpos = mne.chpi.read_head_pos(raw_file)
                mne.chpi.write_head_pos(bids_path, headpos)

            elif bids_path.datatype in ["meg", "eeg"]:
                raw = mne.io.read_raw_fif(raw_file, allow_maxshield=True, verbose="error")
                try:
                    write_raw_bids(
                        raw=raw,
                        bids_path=bids_path,
                        empty_room=None,
                        overwrite=True,
                        verbose="error",
                    )

                    if bids_path.processing:
                        json_path = bids_path.copy().update(extension=".json", split=None)
                        if not exists(json_path.fpath):
                            logger.debug("Creating missing JSON sidecar: %s", json_path.basename)
                            sidecar_data = {
                                "TaskName": bids_path.task,
                                "SamplingFrequency": raw.info["sfreq"],
                                "PowerLineFrequency": raw.info["line_freq"],
                                "Manufacturer": "Elekta",
                            }
                            with open(json_path.fpath, "w") as f:
                                json.dump(sidecar_data, f, indent=4)

                except Exception as exc:  # noqa: BLE001 - mirrors vendored fallback
                    logger.error("Error writing BIDS file: %s", exc)
                    fallback_fname = bids_path.copy().update(suffix="meg", extension=".fif").fpath
                    raw.save(fallback_fname, overwrite=True, verbose="error")

            df.at[i, "status"] = "processed"
            processed_now += 1
            df = _record_processing_success(df, i)
            _emit_progress(
                {
                    "stage": "file-done",
                    "message": f"Completed file {pcount}/{n_files_to_process}",
                    "total": n_files_to_process,
                    "processed": processed_now,
                    "errors": errors_now,
                    "current_file": d.get("raw_name"),
                }
            )

        except Exception as exc:  # noqa: BLE001 - per-row isolation, mirrors vendored behavior
            logger.error("Error processing file %s: %s", d.get("raw_name"), exc)
            error_detail = {
                "raw_name": str(d.get("raw_name", "")),
                "reason": str(exc),
                "exception_type": exc.__class__.__name__,
                "status": str(d.get("status", "")),
                "task": str(d.get("task", "")),
                "run": str(d.get("run", "")),
                "acquisition": str(d.get("acquisition", "")),
                "processing": str(d.get("processing", "")),
                "description": str(d.get("description", "")),
            }
            if verbose:
                error_detail["traceback"] = traceback.format_exc()

            recent_errors.append(error_detail)
            if len(recent_errors) > max_recent_errors:
                recent_errors = recent_errors[-max_recent_errors:]

            df.at[i, "status"] = "error"
            errors_now += 1
            _emit_progress(
                {
                    "stage": "file-error",
                    "message": f"Error in file {pcount}/{n_files_to_process}",
                    "total": n_files_to_process,
                    "processed": processed_now,
                    "errors": errors_now,
                    "current_file": d.get("raw_name"),
                    "last_error": error_detail,
                    "recent_errors": recent_errors,
                }
            )

        df.at[i, "time_stamp"] = ts
        if bids_path is not None:
            df.at[i, "bids_path"] = dirname(str(bids_path))
            df.at[i, "bids_name"] = basename(str(bids_path))

        if callable(row_persist_callback):
            try:
                row_persist_callback(df.loc[i].to_dict())
            except Exception:  # noqa: BLE001 - persistence errors must not break the run
                logger.exception("row_persist_callback failed for row %s", i)

    final_status_counts = df["status"].fillna("error").value_counts().to_dict()
    summary["processed_now"] = processed_now
    summary["errors_now"] = errors_now
    summary["error_details"] = recent_errors
    summary["final_status_counts"] = final_status_counts
    summary["message"] = "BIDS conversion completed"
    _emit_progress({"stage": "done", "message": summary["message"], "summary": summary})
    return summary


def create_dataset_description(
    bids_root: str,
    name: str = "MEG Dataset",
    dataset_type: str = "raw",
    data_license: str = "",
    authors: Optional[list[str]] = None,
    acknowledgements: str = "",
    how_to_acknowledge: str = "",
    funding: Optional[list[str]] = None,
    ethics_approvals: Optional[list[str]] = None,
    references_and_links: Optional[list[str]] = None,
    doi: str = "",
    generated_by: Optional[list[dict]] = None,
    overwrite: bool = False,
) -> None:
    """Create or update `dataset_description.json` for a MEG BIDS dataset.

    Explicit keyword arguments replace the CIR-schema config dict lookups
    (`config.get('Dataset_description', ...)` etc.) used by the source.
    """
    os.makedirs(bids_root, exist_ok=True)
    file_bids = join(bids_root, "dataset_description.json")

    if exists(file_bids) and not overwrite:
        return

    make_dataset_description(
        path=bids_root,
        name=name,
        dataset_type=dataset_type,
        data_license=data_license,
        authors=authors or [],
        acknowledgements=acknowledgements,
        how_to_acknowledge=how_to_acknowledge,
        funding=funding or [],
        ethics_approvals=ethics_approvals or [],
        references_and_links=references_and_links or [],
        doi=doi,
        overwrite=overwrite,
    )

    if generated_by:
        with open(file_bids, "r") as f:
            desc_data = json.load(f)
        desc_data["GeneratedBy"] = generated_by
        with open(file_bids, "w") as f:
            json.dump(desc_data, f, indent=4)
