"""Per-cohort subject-code override CSV generation.

Reuses the **existing, unmodified** CSV-override mechanisms in
`extract.subject_mapping` (DICOM) and `meg.extractor` (MEG) by generating
per-cohort override CSVs that those mechanisms already know how to read --
no `SubjectResolver`/extraction/MEG-scan code changes.

One CSV per cohort: `<FACILITY_SUBJECT_CODE_CSV_ROOT>/<cohort_id>.csv`.
Regenerated/appended incrementally as new discoveries are confirmed for a
cohort (read-modify-write, keyed by the natural key so repeat confirmations
are idempotent).

- `mrc` (DICOM extract stage): columns `PatientID,subject_code` -- matches
  the extract stage's configurable `patientColumn`/`subjectCodeColumn`,
  defaulted to those names (see `_load_subject_code_mapping` /
  `subjectCodeCsv` in `api/routes/cohorts.py`).
- `natmeg` (MEG scan stage): columns `participant,subject_code` -- matches
  `meg.extractor.load_participant_subject_code_csv`'s fixed column names.
"""

from __future__ import annotations

import csv
from pathlib import Path


def _csv_path(csv_root: Path, cohort_id: int) -> Path:
    return csv_root / f"{cohort_id}.csv"


def _read_existing(path: Path, key_column: str, value_column: str) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return {}
        return {
            (row.get(key_column) or "").strip(): (row.get(value_column) or "").strip()
            for row in reader
            if (row.get(key_column) or "").strip()
        }


def _write_mapping(path: Path, key_column: str, value_column: str, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([key_column, value_column])
        for key in sorted(mapping):
            writer.writerow([key, mapping[key]])


def upsert_mrc_override(csv_root: Path, cohort_id: int, patient_id: str, cir_id: str) -> Path:
    """Append/update the `PatientID -> cir_id` row for one cohort's `mrc`
    subject-code override CSV. Idempotent: re-writing the same
    `(patient_id, cir_id)` pair is a no-op on the file contents."""
    path = _csv_path(csv_root, cohort_id)
    mapping = _read_existing(path, "PatientID", "subject_code")
    mapping[patient_id.strip()] = cir_id.strip()
    _write_mapping(path, "PatientID", "subject_code", mapping)
    return path


def upsert_natmeg_override(csv_root: Path, cohort_id: int, natmeg_id: str, cir_id: str) -> Path:
    """Append/update the `natmeg_id -> cir_id` row for one cohort's `natmeg`
    subject-code override CSV."""
    path = _csv_path(csv_root, cohort_id)
    mapping = _read_existing(path, "participant", "subject_code")
    mapping[natmeg_id.strip()] = cir_id.strip()
    _write_mapping(path, "participant", "subject_code", mapping)
    return path
