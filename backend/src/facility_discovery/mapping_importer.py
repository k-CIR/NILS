"""Tolerant CSV importer for the facility-maintained mapping sheet.

Reads `FACILITY_MAPPING_CSV_PATH` and performs a full delete+reinsert of
`facility_subject_mappings` inside one transaction every time a scan runs
(the "fully reloaded on each discovery scan" decision). No incremental
diffing. Tolerates extra/unknown columns (e.g. `bmic_*`) and missing
optional columns without erroring, per the "importer must tolerate
extra/unknown columns without breaking" requirement.

Does not create/resolve `project` rows -- that happens later during the
scan/match step (`facility_discovery.scanner`), so import stays a pure,
side-effect-free reload of the raw sheet.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import FacilitySubjectMapping

logger = logging.getLogger(__name__)

# 1:1 with the CSV columns this importer persists. Any other column present
# in the sheet (including the out-of-scope `bmic_*` columns) is read but
# ignored -- this is what makes the importer "tolerant" of extra columns.
_MAPPING_COLUMNS = (
    "cir_id",
    "cir_project",
    "session_number",
    "scan_date",
    "cir_facility",
    "natmeg_id",
    "mrc_id",
    "bmic_id",
    "bmic_radioligande_factor",
    "tester_name",
    "tester_kiid",
    "persnr_check",
    "sub_id",
    "export_time",
)

# Columns that must be present for a row to be usable by discovery matching.
# `bmic_*`/`sub_id`/etc. are optional -- their absence must not break import.
_REQUIRED_COLUMNS = ("cir_id", "cir_project", "cir_facility")


class MappingCsvError(Exception):
    """Raised when the mapping CSV is missing, unreadable, or malformed."""


def _normalize(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read_rows(path: Path) -> list[dict[str, Optional[str]]]:
    if not path.exists():
        raise MappingCsvError(f"Mapping CSV not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise MappingCsvError("Mapping CSV is missing a header row")

        # Tolerate extra/unknown columns (bmic_* or anything else): only look
        # at the columns we know about, don't error on unrecognized ones.
        available = {name.strip() for name in reader.fieldnames if name}
        missing_required = [c for c in _REQUIRED_COLUMNS if c not in available]
        if missing_required:
            raise MappingCsvError(
                f"Mapping CSV missing required column(s): {', '.join(missing_required)}"
            )

        rows: list[dict[str, Optional[str]]] = []
        for raw_row in reader:
            row = {col: _normalize(raw_row.get(col)) for col in _MAPPING_COLUMNS}
            # Skip fully-blank rows (trailing blank lines etc.)
            if not any(row.values()):
                continue
            rows.append(row)
        return rows


def reload_mappings(session: Session, csv_path: Path) -> int:
    """Full delete+reinsert of `facility_subject_mappings` from `csv_path`.

    Runs inside the caller's transaction: on any parsing error, nothing is
    deleted/inserted (raises before touching the table). Returns the number
    of rows imported.
    """
    rows = _read_rows(csv_path)  # parse before mutating anything

    session.execute(delete(FacilitySubjectMapping))
    session.flush()

    if rows:
        session.bulk_insert_mappings(FacilitySubjectMapping, rows)
        session.flush()

    logger.info("Reloaded %d facility_subject_mappings row(s) from %s", len(rows), csv_path)
    return len(rows)
