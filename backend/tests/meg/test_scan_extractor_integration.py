"""Integration test for the `meg_scan` stage: scanner.scan_fif_header() ->
MegExtractor.scan_recording() against a synthetic FIF file and an in-memory
SQLite metadata DB.

Mirrors the DB-setup pattern used by `tests/extract/test_writer_events.py`
for the DICOM extraction writer, adapted for the MEG track's
`meg.extractor.MegExtractor`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import mne

from meg.config import MegScanConfig
from meg.extractor import MegExtractor, SubjectResolutionError
from meg.scanner import scan_fif_header
from metadata_db import schema


def _setup_metadata_db(monkeypatch) -> sessionmaker:
    """Set up an in-memory SQLite database and point meg.extractor at it."""
    import metadata_db.session as session_module
    import meg.extractor as extractor_module

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    schema.Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    monkeypatch.setattr(session_module, "SessionLocal", Sess, raising=False)
    monkeypatch.setattr(extractor_module, "SessionLocal", Sess, raising=False)

    # Seed the MEG observation type (id 16), matching migrate_observation_types.py.
    with Sess() as session:
        session.execute(
            text(
                "INSERT OR IGNORE INTO observation_types "
                "(observation_type_id, category, name, is_active, is_primary) "
                "VALUES (16, 'Imaging', 'MEG Scan', 1, 0)"
            )
        )
        session.execute(
            text("INSERT OR IGNORE INTO id_types (id_type_id, id_type_name) VALUES (1, 'NatMEG Participant ID')")
        )
        session.commit()

    return Sess


def _write_synthetic_fif(tmp_path) -> str:
    """Write a small synthetic raw FIF file under a NatMEG-style BIDS layout.

    Layout: <tmp>/sub-0001/ses-01/meg/NatMEG_0001_RestingState_raw.fif
    so that `scanner._resolve_session_label` derives session "01" from the
    parent-of-parent ("meg" datatype dir -> "ses-01" -> "01") and
    `parsing.extract_info_from_filename` derives participant "0001" and
    task "RestingState" from the filename.
    """
    sfreq = 100.0
    ch_names = ["MEG0111", "MEG0112", "MEG0113", "EEG001"]
    ch_types = ["mag", "grad", "grad", "eeg"]
    info = mne.create_info(ch_names=ch_names, sfreq=sfreq, ch_types=ch_types)
    info["line_freq"] = 50.0

    n_samples = int(sfreq * 5)  # 5 seconds
    rng = np.random.default_rng(42)
    data = rng.standard_normal((len(ch_names), n_samples)) * 1e-12

    raw = mne.io.RawArray(data, info, verbose="error")
    raw.set_meas_date(datetime(2025, 6, 1, 9, 30, 0, tzinfo=timezone.utc))
    raw.info["bads"] = ["EEG001"]

    meg_dir = tmp_path / "sub-0001" / "ses-01" / "meg"
    meg_dir.mkdir(parents=True, exist_ok=True)
    fif_path = meg_dir / "NatMEG_0001_RestingState_raw.fif"
    raw.save(str(fif_path), overwrite=True, verbose="error")
    return str(fif_path)


@pytest.fixture
def synthetic_fif(tmp_path):
    return _write_synthetic_fif(tmp_path)


class TestScanFifHeader:
    def test_header_fields_populated(self, synthetic_fif):
        header = scan_fif_header(synthetic_fif)

        assert header.participant_from == "0001"
        assert header.task == "RestingState"
        assert header.session_from == "01"
        assert header.acquisition_date == date(2025, 6, 1)
        assert header.sampling_frequency == pytest.approx(100.0)
        assert header.n_channels == 4
        assert header.duration_seconds == pytest.approx(5.0, abs=0.05)
        assert header.line_freq == pytest.approx(50.0)
        assert header.bads == ["EEG001"]
        assert len(header.channels) == 4
        bad_channel = next(ch for ch in header.channels if ch.channel_name == "EEG001")
        assert bad_channel.is_bad is True


class TestMegExtractorScanRecording:
    def test_identifier_lookup_resolution_persists_full_recording(self, tmp_path, monkeypatch, synthetic_fif):
        Sess = _setup_metadata_db(monkeypatch)

        # Pre-seed a subject + identifier so the identifier-lookup path resolves.
        with Sess() as session:
            session.execute(text("INSERT INTO subject (subject_code, is_active) VALUES ('SUBJ001', 1)"))
            session.commit()
            subject_id = session.execute(
                select(schema.Subject.subject_id).where(schema.Subject.subject_code == "SUBJ001")
            ).scalar_one()
            session.execute(
                text(
                    "INSERT INTO subject_other_identifiers (subject_id, id_type_id, other_identifier) "
                    "VALUES (:sid, 1, '0001')"
                ),
                {"sid": subject_id},
            )
            session.commit()

        header = scan_fif_header(synthetic_fif)
        config = MegScanConfig(subject_id_type_id=1)
        extractor = MegExtractor(cohort_id=1, config=config)

        extractor.scan_recording(header)

        with Sess() as session:
            study = session.execute(select(schema.Study).where(schema.Study.subject_id == subject_id)).scalar_one()
            assert study.modality == "MEG"
            assert study.study_date == date(2025, 6, 1)
            assert study.event_id is not None

            event = session.execute(
                select(schema.Event).where(schema.Event.event_id == study.event_id)
            ).scalar_one()
            assert event.observation_type_id == 16
            assert event.subject_id == subject_id
            assert event.event_date == date(2025, 6, 1)

            acquisition = session.execute(
                select(schema.MegAcquisition).where(schema.MegAcquisition.study_id == study.study_id)
            ).scalar_one()
            assert acquisition.fif_file_path == header.fif_file_path
            assert acquisition.bids_task == "RestingState"
            assert acquisition.n_channels == 4
            assert acquisition.sampling_frequency == pytest.approx(100.0)
            assert acquisition.notch_filter_hz == "50.0"

            channels = session.execute(
                select(schema.MegChannel).where(
                    schema.MegChannel.meg_acquisition_id == acquisition.meg_acquisition_id
                )
            ).scalars().all()
            assert len(channels) == 4
            bad_row = next(c for c in channels if c.channel_name == "EEG001")
            assert bad_row.is_bad == 1

            subject_cohort = session.execute(
                select(schema.SubjectCohort).where(
                    schema.SubjectCohort.subject_id == subject_id, schema.SubjectCohort.cohort_id == 1
                )
            ).scalar_one_or_none()
            assert subject_cohort is not None

    def test_rerun_is_idempotent_no_duplicate_rows(self, tmp_path, monkeypatch, synthetic_fif):
        """Re-running scan_recording() for the same FIF file must upsert, not duplicate."""
        Sess = _setup_metadata_db(monkeypatch)
        header = scan_fif_header(synthetic_fif)
        config = MegScanConfig(subject_csv_mapping_path=None)
        extractor = MegExtractor(cohort_id=1, config=config, subject_code_map={"0001": "SUBJ_CSV"})

        extractor.scan_recording(header)
        extractor.scan_recording(header)

        with Sess() as session:
            studies = session.execute(select(schema.Study)).scalars().all()
            assert len(studies) == 1

            acquisitions = session.execute(select(schema.MegAcquisition)).scalars().all()
            assert len(acquisitions) == 1

            channels = session.execute(select(schema.MegChannel)).scalars().all()
            assert len(channels) == 4

            events = session.execute(select(schema.Event)).scalars().all()
            assert len(events) == 1

        assert extractor.result.studies_inserted == 1
        assert extractor.result.studies_updated == 1
        assert extractor.result.acquisitions_inserted == 1
        assert extractor.result.acquisitions_updated == 1

    def test_csv_fallback_resolution(self, tmp_path, monkeypatch, synthetic_fif):
        _setup_metadata_db(monkeypatch)
        header = scan_fif_header(synthetic_fif)
        config = MegScanConfig()  # no subject_id_type_id configured
        extractor = MegExtractor(cohort_id=1, config=config, subject_code_map={"0001": "SUBJ_CSV_ONLY"})

        extractor.scan_recording(header)

        assert extractor.result.subjects_inserted == 1
        assert extractor.result.studies_inserted == 1

    def test_unresolved_subject_raises_and_logs_conflict(self, tmp_path, monkeypatch, synthetic_fif):
        Sess = _setup_metadata_db(monkeypatch)
        header = scan_fif_header(synthetic_fif)
        config = MegScanConfig()  # no subject_id_type_id, no CSV mapping
        extractor = MegExtractor(cohort_id=1, config=config)

        with pytest.raises(SubjectResolutionError):
            extractor.scan_recording(header)

        with Sess() as session:
            conflicts = session.execute(select(schema.IngestConflict)).scalars().all()
            assert len(conflicts) == 1
            assert conflicts[0].scope == "meg_subject_resolution"
            assert conflicts[0].cohort_id == 1

            # Nothing else should have been persisted.
            assert session.execute(select(schema.Study)).scalars().all() == []
