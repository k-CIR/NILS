"""Unit tests for `facility_discovery.scanner.run_discovery_scan`.

Covers: mrc immediate-subdir matching, natmeg glob + exact/fallback
scan_date matching, the natural-key upsert rule (never resurrect
confirmed/rejected rows, refresh pending ones), and unmatched-folder
counting.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from facility_discovery.mapping_importer import reload_mappings
from facility_discovery.models import FacilityDiscovery
from facility_discovery.scanner import run_discovery_scan

HEADER = (
    "cir_id,cir_project,session_number,scan_date,cir_facility,natmeg_id,"
    "mrc_id,bmic_id,bmic_radioligande_factor,tester_name,tester_kiid,"
    "persnr_check,sub_id,export_time\n"
)


def _write_csv(path: Path, body: str) -> Path:
    csv_path = path / "mapping.csv"
    csv_path.write_text(HEADER + body, encoding="utf-8")
    return csv_path


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "mrc" / "sub-MRC001").mkdir(parents=True)
    (vault / "natmeg" / "PROJ1" / "raw" / "ACQ001" / "sub-NM001" / "20260101").mkdir(parents=True)
    return vault


def test_scan_mrc_and_natmeg_match(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    csv_path = _write_csv(
        tmp_path,
        "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n"
        "CIR002,PROJ1,1,20260101,natmeg,NM001,,,,,,,,\n",
    )

    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)

    assert summary.mappings_loaded == 2
    assert summary.matched_new == 2
    assert summary.unmatched_folders == 0

    rows = {r.facility: r for r in app_db_session.scalars(select(FacilityDiscovery)).all()}
    assert rows["mrc"].facility_id_value == "MRC001"
    assert rows["mrc"].cir_id == "CIR001"
    assert rows["mrc"].status == "pending"
    assert rows["natmeg"].facility_id_value == "NM001"
    assert rows["natmeg"].cir_id == "CIR002"
    assert rows["natmeg"].scan_date == "20260101"


def test_scan_mrc_unmatched_folder_counted(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    # No mapping row references MRC001/NM001 at all.
    csv_path = _write_csv(tmp_path, "CIR999,PROJ9,1,2026-09-09,mrc,,MRC999,,,,,,,\n")

    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)
    assert summary.matched_new == 0
    assert summary.unmatched_folders == 2  # neither MRC001 nor NM001 matched


def test_scan_rerun_refreshes_pending_but_not_confirmed_or_rejected(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    csv_path = _write_csv(
        tmp_path,
        "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n"
        "CIR002,PROJ1,1,20260101,natmeg,NM001,,,,,,,,\n",
    )
    run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)

    rows = {r.facility: r for r in app_db_session.scalars(select(FacilityDiscovery)).all()}
    mrc_row = rows["mrc"]
    natmeg_row = rows["natmeg"]

    # Manually mark mrc as confirmed and natmeg as rejected (simulating the
    # confirm/reject endpoints, without exercising the full confirm flow).
    mrc_row.status = "confirmed"
    natmeg_row.status = "rejected"
    app_db_session.flush()

    # Rescan with the same CSV/vault: nothing should move off confirmed/rejected.
    summary2 = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)
    assert summary2.matched_new == 0
    assert summary2.matched_already_confirmed == 1
    assert summary2.matched_already_rejected == 1

    rows2 = {r.facility: r for r in app_db_session.scalars(select(FacilityDiscovery)).all()}
    assert rows2["mrc"].status == "confirmed"
    assert rows2["natmeg"].status == "rejected"


def test_scan_rerun_refreshes_pending_row_fields(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    csv_path = _write_csv(tmp_path, "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n")
    run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)

    # Change cir_project for the same mrc_id and rescan: the pending row's
    # cir_project should be refreshed in place (not duplicated).
    csv_path2 = _write_csv(tmp_path, "CIR001,PROJ2,1,2026-01-01,mrc,,MRC001,,,,,,,\n")
    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path2, vault_root=vault)
    assert summary.matched_already_pending == 1
    assert summary.matched_new == 0

    rows = app_db_session.scalars(select(FacilityDiscovery)).all()
    assert len(rows) == 1
    assert rows[0].cir_project == "PROJ2"


def test_scan_natmeg_falls_back_to_id_only_match_when_unambiguous(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    # scan_date in the CSV does not match the folder date (20260101), but
    # there's exactly one natmeg_id=NM001 row, so it should still match via
    # the unambiguous id-only fallback.
    csv_path = _write_csv(tmp_path, "CIR002,PROJ1,1,2025-12-31,natmeg,NM001,,,,,,,,\n")

    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)
    assert summary.matched_new == 1
    row = app_db_session.scalars(select(FacilityDiscovery)).one()
    assert row.cir_id == "CIR002"
    # scan_date recorded is the folder-derived date, not the CSV's mismatched one.
    assert row.scan_date == "20260101"


def test_scan_natmeg_ambiguous_id_only_match_is_unmatched(app_db_session, tmp_path):
    vault = _make_vault(tmp_path)
    # Two mapping rows share natmeg_id=NM001 with mismatching scan_dates and
    # no exact match for the folder's date -- ambiguous, so no fallback match.
    csv_path = _write_csv(
        tmp_path,
        "CIR002,PROJ1,1,2025-11-11,natmeg,NM001,,,,,,,,\n"
        "CIR003,PROJ1,2,2025-12-12,natmeg,NM001,,,,,,,,\n",
    )
    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)
    assert summary.matched_new == 0
    assert summary.unmatched_folders >= 1


def test_scan_missing_vault_root_does_not_raise(app_db_session, tmp_path):
    csv_path = _write_csv(tmp_path, "CIR001,PROJ1,1,2026-01-01,mrc,,MRC001,,,,,,,\n")
    missing_vault = tmp_path / "does-not-exist"
    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=missing_vault)
    assert summary.matched_new == 0
    assert summary.unmatched_folders == 0


def test_scan_natmeg_matches_layout_without_acquisition_dir(app_db_session, tmp_path):
    # Confirmed against real on-disk vault data: `<project>/raw/sub-<id>/<date>`
    # with NO intermediate <acquisition> directory between `raw` and `sub-<id>`
    # (unlike `_make_vault`'s layout above, which does include one). Both must
    # be supported since the real vault turned out not to have that level.
    vault = tmp_path / "vault"
    (vault / "natmeg" / "CAPSI" / "raw" / "sub-0916" / "240320").mkdir(parents=True)
    csv_path = _write_csv(tmp_path, "CIR001,CAPSI,1,240320,natmeg,0916,,,,,,,,\n")

    summary = run_discovery_scan(app_db_session, mapping_csv_path=csv_path, vault_root=vault)
    assert summary.matched_new == 1
    assert summary.unmatched_folders == 0

    row = app_db_session.scalars(select(FacilityDiscovery)).one()
    assert row.facility == "natmeg"
    assert row.facility_id_value == "0916"
    assert row.scan_date == "240320"
    assert row.cir_id == "CIR001"
