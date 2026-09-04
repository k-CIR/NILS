"""Unit tests for `facility_discovery.subject_code_csv`, including a
round-trip check through the *existing, unmodified* readers
(`extract.subject_mapping.load_subject_code_csv` /
`meg.extractor.load_participant_subject_code_csv`) that the DICOM extract
and MEG scan stages actually use -- this is the mechanism the facility
discovery confirm flow reuses without touching either stage's code.
"""

from __future__ import annotations

from pathlib import Path

from extract.subject_mapping import load_subject_code_csv
from facility_discovery.subject_code_csv import upsert_mrc_override, upsert_natmeg_override
from meg.extractor import load_participant_subject_code_csv


def test_upsert_mrc_override_round_trips_through_extract_loader(tmp_path):
    root = tmp_path / "codecsv"
    path = upsert_mrc_override(root, cohort_id=42, patient_id="REALPATID001", cir_id="CIR001")

    assert path == root / "42.csv"
    mapping = load_subject_code_csv(path, "PatientID", "subject_code")
    assert mapping == {"REALPATID001": "CIR001"}


def test_upsert_mrc_override_is_idempotent_and_updates_existing_key(tmp_path):
    root = tmp_path / "codecsv"
    upsert_mrc_override(root, cohort_id=1, patient_id="PAT1", cir_id="CIR001")
    upsert_mrc_override(root, cohort_id=1, patient_id="PAT2", cir_id="CIR002")
    # Re-confirming the same patient with an updated cir_id should overwrite,
    # not duplicate, the row.
    path = upsert_mrc_override(root, cohort_id=1, patient_id="PAT1", cir_id="CIR001-UPDATED")

    mapping = load_subject_code_csv(path, "PatientID", "subject_code")
    assert mapping == {"PAT1": "CIR001-UPDATED", "PAT2": "CIR002"}


def test_upsert_natmeg_override_round_trips_through_meg_loader(tmp_path):
    root = tmp_path / "codecsv"
    path = upsert_natmeg_override(root, cohort_id=7, natmeg_id="NM001", cir_id="CIR002")

    assert path == root / "7.csv"
    mapping = load_participant_subject_code_csv(path)
    assert mapping == {"NM001": "CIR002"}


def test_upsert_natmeg_override_accumulates_multiple_subjects(tmp_path):
    root = tmp_path / "codecsv"
    upsert_natmeg_override(root, cohort_id=7, natmeg_id="NM001", cir_id="CIR001")
    path = upsert_natmeg_override(root, cohort_id=7, natmeg_id="NM002", cir_id="CIR002")

    mapping = load_participant_subject_code_csv(path)
    assert mapping == {"NM001": "CIR001", "NM002": "CIR002"}


def test_mrc_and_natmeg_overrides_use_separate_per_cohort_files(tmp_path):
    root = tmp_path / "codecsv"
    mrc_path = upsert_mrc_override(root, cohort_id=1, patient_id="PAT1", cir_id="CIR001")
    natmeg_path = upsert_natmeg_override(root, cohort_id=2, natmeg_id="NM001", cir_id="CIR002")

    assert mrc_path != natmeg_path
    assert mrc_path.name == "1.csv"
    assert natmeg_path.name == "2.csv"
