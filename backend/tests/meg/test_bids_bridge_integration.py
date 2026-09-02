"""Integration test for the `meg_bids` stage: `bids_bridge.run_meg_bids()`
against a synthetic FIF file already scanned into `meg_acquisition` via
`MegExtractor`, writing real BIDS output via `mne-bids`.

Reuses the DB-setup and synthetic-FIF-file helpers from
`test_scan_extractor_integration.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text

from meg.bids_bridge import run_meg_bids
from meg.config import MegBidsConfig, MegBidsOverwriteMode, MegScanConfig
from meg.extractor import MegExtractor
from meg.scanner import scan_fif_header
from metadata_db import schema

from test_scan_extractor_integration import _setup_metadata_db, _write_synthetic_fif


def _seed_scanned_acquisition(monkeypatch, tmp_path):
    """Set up the DB and persist one `meg_acquisition` row via a real
    `meg_scan` pass, mirroring what the `meg_bids` stage expects to find."""
    import meg.bids_bridge as bids_bridge_module

    Sess = _setup_metadata_db(monkeypatch)
    monkeypatch.setattr(bids_bridge_module, "SessionLocal", Sess, raising=False)

    fif_path = _write_synthetic_fif(tmp_path)
    header = scan_fif_header(fif_path)
    config = MegScanConfig(subject_csv_mapping_path=None)
    extractor = MegExtractor(cohort_id=1, config=config, subject_code_map={"0001": "SUBJ001"})
    extractor.scan_recording(header)

    return Sess


class TestRunMegBids:
    def test_converts_scanned_acquisition_to_bids(self, tmp_path, monkeypatch):
        Sess = _seed_scanned_acquisition(monkeypatch, tmp_path)

        raw_root = tmp_path  # calibration/crosstalk lookup root; none present here
        bids_root = tmp_path / "bids-meg"
        bids_config = MegBidsConfig()

        result = run_meg_bids(bids_config, cohort_id=1, cohort_name="TestCohort", raw_root=raw_root, bids_root=bids_root)

        assert result.total == 1
        assert result.to_process == 1
        assert result.processed == 1
        assert result.errors == 0
        assert (bids_root / "dataset_description.json").exists()

        with Sess() as session:
            acquisition = session.execute(select(schema.MegAcquisition)).scalar_one()
            assert acquisition.bids_status == "processed"
            assert acquisition.bids_path is not None
            assert acquisition.bids_name is not None
            written_file = Path(acquisition.bids_path) / acquisition.bids_name
            assert written_file.exists()

    def test_rerun_with_skip_overwrite_mode_does_not_reconvert(self, tmp_path, monkeypatch):
        Sess = _seed_scanned_acquisition(monkeypatch, tmp_path)

        raw_root = tmp_path
        bids_root = tmp_path / "bids-meg"
        bids_config = MegBidsConfig(overwrite_mode=MegBidsOverwriteMode.SKIP)

        first = run_meg_bids(bids_config, cohort_id=1, cohort_name="TestCohort", raw_root=raw_root, bids_root=bids_root)
        assert first.processed == 1

        second = run_meg_bids(bids_config, cohort_id=1, cohort_name="TestCohort", raw_root=raw_root, bids_root=bids_root)
        assert second.total == 1
        assert second.to_process == 0
        assert second.processed == 0

    def test_dataset_description_name_defaults_to_study_description(self, tmp_path, monkeypatch):
        Sess = _seed_scanned_acquisition(monkeypatch, tmp_path)

        with Sess() as session:
            session.execute(text("UPDATE study SET study_description = 'My MEG Study'"))
            session.commit()

        bids_root = tmp_path / "bids-meg"
        run_meg_bids(MegBidsConfig(), cohort_id=1, cohort_name="TestCohort", raw_root=tmp_path, bids_root=bids_root)

        description_path = bids_root / "dataset_description.json"
        contents = description_path.read_text()
        assert "My MEG Study" in contents
