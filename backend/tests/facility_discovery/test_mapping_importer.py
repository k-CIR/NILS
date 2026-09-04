"""Unit tests for `facility_discovery.mapping_importer.reload_mappings`.

Covers: full delete+reinsert semantics, tolerance of extra/missing optional
columns, and refusal to mutate anything when the CSV is malformed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from facility_discovery.mapping_importer import MappingCsvError, reload_mappings
from facility_discovery.models import FacilitySubjectMapping

HEADER = (
    "cir_id,cir_project,session_number,scan_date,cir_facility,natmeg_id,"
    "mrc_id,bmic_id,bmic_radioligande_factor,tester_name,tester_kiid,"
    "persnr_check,sub_id,export_time\n"
)


def _write_csv(path: Path, body: str) -> Path:
    csv_path = path / "mapping.csv"
    csv_path.write_text(HEADER + body, encoding="utf-8")
    return csv_path


def test_reload_mappings_imports_all_rows(app_db_session, tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n"
        "CIR002,PROJ1,1,20260101,natmeg,NM001,,,,,,,,\n",
    )

    count = reload_mappings(app_db_session, csv_path)
    assert count == 2

    rows = app_db_session.scalars(select(FacilitySubjectMapping)).all()
    assert {r.cir_id for r in rows} == {"CIR001", "CIR002"}
    mrc_row = next(r for r in rows if r.cir_id == "CIR001")
    assert mrc_row.mrc_id == "MRC001"
    assert mrc_row.cir_project == "PROJ1"
    natmeg_row = next(r for r in rows if r.cir_id == "CIR002")
    assert natmeg_row.natmeg_id == "NM001"


def test_reload_mappings_is_full_delete_reinsert(app_db_session, tmp_path):
    csv_path = _write_csv(tmp_path, "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n")
    reload_mappings(app_db_session, csv_path)
    assert len(app_db_session.scalars(select(FacilitySubjectMapping)).all()) == 1

    # Second reload with a totally different row set: the old row must be gone.
    csv_path2 = _write_csv(tmp_path, "CIR999,PROJ9,1,2026-02-02,natmeg,NM999,,,,,,,,\n")
    count = reload_mappings(app_db_session, csv_path2)
    assert count == 1

    rows = app_db_session.scalars(select(FacilitySubjectMapping)).all()
    assert len(rows) == 1
    assert rows[0].cir_id == "CIR999"


def test_reload_mappings_tolerates_extra_and_missing_optional_columns(app_db_session, tmp_path):
    # Only the required columns + a couple of the known optional ones; the
    # importer must not error on the missing optional columns, and must
    # ignore any unrecognized extra column.
    csv_path = tmp_path / "mapping.csv"
    csv_path.write_text(
        "cir_id,cir_project,cir_facility,mrc_id,some_unknown_column\n"
        "CIR001,PROJ1,mrc,MRC001,ignored-value\n",
        encoding="utf-8",
    )

    count = reload_mappings(app_db_session, csv_path)
    assert count == 1
    row = app_db_session.scalars(select(FacilitySubjectMapping)).one()
    assert row.cir_id == "CIR001"
    assert row.mrc_id == "MRC001"
    assert row.session_number is None
    assert row.scan_date is None


def test_reload_mappings_missing_required_column_raises_without_mutating(app_db_session, tmp_path):
    # Seed one existing row first.
    csv_path = _write_csv(tmp_path, "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n")
    reload_mappings(app_db_session, csv_path)
    assert len(app_db_session.scalars(select(FacilitySubjectMapping)).all()) == 1

    # Malformed CSV: missing the required `cir_facility` column entirely.
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("cir_id,cir_project\nCIR002,PROJ2\n", encoding="utf-8")

    with pytest.raises(MappingCsvError):
        reload_mappings(app_db_session, bad_csv)

    # Nothing was mutated: the original row is still there.
    rows = app_db_session.scalars(select(FacilitySubjectMapping)).all()
    assert len(rows) == 1
    assert rows[0].cir_id == "CIR001"


def test_reload_mappings_missing_file_raises(app_db_session, tmp_path):
    with pytest.raises(MappingCsvError):
        reload_mappings(app_db_session, tmp_path / "does-not-exist.csv")


def test_reload_mappings_skips_blank_rows(app_db_session, tmp_path):
    csv_path = _write_csv(
        tmp_path,
        "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n"
        ",,,,,,,,,,,,,\n",
    )
    count = reload_mappings(app_db_session, csv_path)
    assert count == 1
