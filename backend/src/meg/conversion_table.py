"""Conversion-table schema and metadata-tracking helpers for MEG BIDS conversion.

Vendored and adapted from `cir-utils/tabs/meg-bids/bidsify/conversion_table.py`
(local checkout: `/Users/andreas.gerhardsson/Sites/cir-utils`). No LICENSE
file was found in the source repo; it is an internal sibling project by the
same author/organization as NILS.

Only the pure schema/metadata-bookkeeping helpers are vendored here. The
file-discovery and persistence functions from the source module
(`generate_new_conversion_table`, `load_conversion_table`,
`update_conversion_table`, `_build_event_index`, `_load_index`,
`_write_index`) were NOT ported: they walk a hardcoded NatMEG directory
layout (`sub-*/<session>/<triux|hedscan>/*.fif`) and round-trip through a
TSV file plus a CIR-schema config dict. The `meg_scan` stage (a later task)
reimplements equivalent discovery against NILS's own cohort workspace and
persists rows to the `meg_acquisition` database table instead of a TSV
file, reusing the `CONVERSION_COLUMNS` schema and the helpers below to
build/update each row's `metadata` JSON blob.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from glob import glob
from os.path import exists, getmtime, getsize, join, splitext
from typing import Optional

import pandas as pd

CONVERSION_COLUMNS = [
    "time_stamp",
    "status",
    "participant_from",
    "participant_to",
    "session_from",
    "session_to",
    "task",
    "split",
    "run",
    "datatype",
    "acquisition",
    "processing",
    "description",
    "suffix",
    "extension",
    "recording",
    "space",
    "tracking_system",
    "mtime",
    "size",
    "raw_path",
    "raw_name",
    "bids_path",
    "bids_name",
    "event_id",
    "metadata",
]


def _is_missing_scalar(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set)):
        return False
    try:
        if bool(pd.isna(value)):
            return True
    except Exception:
        return False
    return str(value).strip() == ""


def _parse_int(value, default: int = 0) -> int:
    if _is_missing_scalar(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value):
    if _is_missing_scalar(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_iso_utc(epoch_value):
    if epoch_value is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_value, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _parse_status_history(history_value):
    if _is_missing_scalar(history_value):
        return []
    if isinstance(history_value, list):
        return history_value
    if isinstance(history_value, str):
        try:
            parsed = json.loads(history_value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_metadata_object(value):
    if _is_missing_scalar(value):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_table(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Ensure `df` has exactly `CONVERSION_COLUMNS`, migrating legacy tracking columns."""
    if df is None or df.empty:
        return pd.DataFrame(columns=CONVERSION_COLUMNS)

    if "metadata" not in df.columns:
        df["metadata"] = None
    has_legacy_tracking = any(
        col in df.columns for col in ["attempt_count", "status_history", "notes", "last_processed"]
    )
    if has_legacy_tracking:
        for idx, row in df.iterrows():
            metadata = _parse_metadata_object(row.get("metadata"))
            tracking = metadata.get("tracking", {}) if isinstance(metadata.get("tracking"), dict) else {}

            if _is_missing_scalar(tracking.get("attempt_count")) and "attempt_count" in df.columns:
                tracking["attempt_count"] = _parse_int(row.get("attempt_count"), default=0)

            history = tracking.get("status_history")
            if not isinstance(history, list) or not history:
                if "status_history" in df.columns:
                    tracking["status_history"] = _parse_status_history(row.get("status_history"))
                else:
                    tracking["status_history"] = _parse_status_history(history)

            if (
                _is_missing_scalar(tracking.get("notes"))
                and "notes" in df.columns
                and not _is_missing_scalar(row.get("notes"))
            ):
                tracking["notes"] = str(row.get("notes"))

            if (
                _is_missing_scalar(tracking.get("last_processed"))
                and "last_processed" in df.columns
                and not _is_missing_scalar(row.get("last_processed"))
            ):
                tracking["last_processed"] = str(row.get("last_processed"))

            metadata["tracking"] = tracking
            df.at[idx, "metadata"] = json.dumps(metadata, default=str)

    for col in CONVERSION_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[CONVERSION_COLUMNS].where(pd.notna(df[CONVERSION_COLUMNS]), None)

    if "status" in df.columns:
        df["status"] = df["status"].fillna("error")

    return df[CONVERSION_COLUMNS]


def _file_signature(full_path: str) -> tuple:
    try:
        return str(getmtime(full_path)), str(getsize(full_path))
    except Exception:
        return "", ""


def _backfill_signature_columns(table: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fill missing mtime/size columns from source files for backward compatibility."""
    if table is None or table.empty:
        return table, 0

    updated_rows = 0
    for idx, row in table.iterrows():
        has_missing_mtime = _is_missing_scalar(row.get("mtime"))
        has_missing_size = _is_missing_scalar(row.get("size"))
        if not (has_missing_mtime or has_missing_size):
            continue

        raw_path = row.get("raw_path")
        raw_name = row.get("raw_name")
        if _is_missing_scalar(raw_path) or _is_missing_scalar(raw_name):
            continue

        full_path = join(str(raw_path), str(raw_name))
        mtime, size = _file_signature(full_path)
        row_updated = False

        if has_missing_mtime and mtime:
            table.at[idx, "mtime"] = mtime
            row_updated = True
        if has_missing_size and size:
            table.at[idx, "size"] = size
            row_updated = True

        if row_updated:
            updated_rows += 1

    return table, updated_rows


def _build_file_metadata(full_path, fallback_mtime=None, fallback_size=None):
    metadata = {
        "path": full_path,
        "exists": False,
        "size_bytes": None,
        "mtime_epoch": None,
        "mtime_iso": None,
    }

    if full_path and exists(full_path):
        try:
            mtime = getmtime(full_path)
            size = getsize(full_path)
            metadata.update(
                {
                    "exists": True,
                    "size_bytes": int(size),
                    "mtime_epoch": float(mtime),
                    "mtime_iso": _to_iso_utc(float(mtime)),
                }
            )
            return metadata
        except Exception:
            pass

    fallback_mtime_num = _parse_float(fallback_mtime)
    fallback_size_num = None
    if not _is_missing_scalar(fallback_size):
        try:
            fallback_size_num = int(str(fallback_size))
        except (TypeError, ValueError):
            fallback_size_num = None
    metadata["size_bytes"] = fallback_size_num
    metadata["mtime_epoch"] = fallback_mtime_num
    metadata["mtime_iso"] = _to_iso_utc(fallback_mtime_num)
    return metadata


def _build_row_metadata(row, refreshed_at):
    raw_path = row.get("raw_path")
    raw_name = row.get("raw_name")
    source_path = None
    if not _is_missing_scalar(raw_path) and not _is_missing_scalar(raw_name):
        source_path = join(str(raw_path), str(raw_name))

    bids_path = row.get("bids_path")
    bids_name = row.get("bids_name")
    converted_path = None
    if not _is_missing_scalar(bids_path) and not _is_missing_scalar(bids_name):
        converted_path = join(str(bids_path), str(bids_name))

    metadata = _parse_metadata_object(row.get("metadata"))
    tracking_existing = metadata.get("tracking", {}) if isinstance(metadata.get("tracking"), dict) else {}
    attempt_count_source = tracking_existing.get("attempt_count")
    if _is_missing_scalar(attempt_count_source):
        attempt_count_source = row.get("attempt_count")

    history_source = tracking_existing.get("status_history")
    if not isinstance(history_source, list) or not history_source:
        history_source = row.get("status_history")

    notes_value = tracking_existing.get("notes")
    if _is_missing_scalar(notes_value) and not _is_missing_scalar(row.get("notes")):
        notes_value = str(row.get("notes"))

    last_processed_source = tracking_existing.get("last_processed")
    if _is_missing_scalar(last_processed_source):
        last_processed_source = row.get("last_processed")

    metadata.update(
        {
            "schema_version": 1,
            "refreshed_at": refreshed_at,
            "source": _build_file_metadata(source_path, row.get("mtime"), row.get("size")),
            "converted": _build_file_metadata(converted_path),
            "tracking": {
                "status": None if _is_missing_scalar(row.get("status")) else str(row.get("status")),
                "last_processed": None if _is_missing_scalar(last_processed_source) else str(last_processed_source),
                "attempt_count": _parse_int(attempt_count_source, default=0),
                "status_history": _parse_status_history(history_source),
                "notes": None if _is_missing_scalar(notes_value) else str(notes_value),
            },
        }
    )
    return metadata


def _refresh_metadata_column(table: pd.DataFrame) -> pd.DataFrame:
    if table is None or table.empty:
        return table

    refreshed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for idx, row in table.iterrows():
        metadata = _build_row_metadata(row, refreshed_at)
        table.at[idx, "metadata"] = json.dumps(metadata, default=str)
    return table


def _bids_output_exists(bids_path: Optional[str], bids_name: Optional[str]) -> bool:
    if pd.isna(bids_path) or pd.isna(bids_name):
        return False
    if not bids_path or not bids_name:
        return False
    bids_path = str(bids_path)
    bids_name = str(bids_name)
    exact = join(bids_path, bids_name)
    if exists(exact):
        return True
    base, _ext = splitext(bids_name)
    if base:
        # Fallback: allow any extension or sidecar for the same base name.
        pattern = join(bids_path, f"{base}.*")
        if glob(pattern):
            return True
    return False


def _update_status_with_history(table: pd.DataFrame, row_idx, new_status: str) -> pd.DataFrame:
    """Update status and record transition in metadata.tracking.status_history."""
    old_status = table.at[row_idx, "status"]
    table.at[row_idx, "status"] = new_status

    metadata = _parse_metadata_object(table.at[row_idx, "metadata"])
    tracking = metadata.get("tracking", {}) if isinstance(metadata.get("tracking"), dict) else {}
    history = _parse_status_history(tracking.get("status_history"))

    if old_status != new_status:
        history.append(
            {
                "from": str(old_status) if pd.notna(old_status) else None,
                "to": new_status,
                "timestamp": datetime.now().isoformat(),
            }
        )

    tracking["status"] = new_status
    tracking["status_history"] = history
    tracking["attempt_count"] = _parse_int(tracking.get("attempt_count"), default=0)
    if _is_missing_scalar(tracking.get("notes")):
        tracking["notes"] = None
    metadata["tracking"] = tracking
    table.at[row_idx, "metadata"] = json.dumps(metadata, default=str)

    return table


def _record_processing_success(table: pd.DataFrame, row_idx) -> pd.DataFrame:
    """Update metadata.tracking.last_processed and increment attempt_count after success."""
    last_processed = datetime.now().isoformat()

    metadata = _parse_metadata_object(table.at[row_idx, "metadata"])
    tracking = metadata.get("tracking", {}) if isinstance(metadata.get("tracking"), dict) else {}
    current_count = _parse_int(tracking.get("attempt_count"), default=0)
    tracking["attempt_count"] = current_count + 1
    tracking["last_processed"] = last_processed
    tracking["status"] = table.at[row_idx, "status"]
    tracking["status_history"] = _parse_status_history(tracking.get("status_history"))
    if _is_missing_scalar(tracking.get("notes")):
        tracking["notes"] = None
    metadata["tracking"] = tracking
    table.at[row_idx, "metadata"] = json.dumps(metadata, default=str)
    return table
