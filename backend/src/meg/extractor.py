"""`meg_scan` stage: scan-to-DB write layer.

`MegExtractor.scan_recording()` is the per-recording sibling of
`extract.writer.Writer` (see that module's `_bulk_ensure_subjects` /
`_bulk_ensure_studies` / `_link_studies_to_events` for the DICOM-side
patterns this mirrors), but deliberately lean, synchronous, and scoped to
one `FifHeader` at a time rather than batched/async — MEG cohorts scan at
most a few hundred recordings per run, so there is no need for the
batching/queueing machinery `extract.writer` uses for multi-million-instance
DICOM extraction.

Each call to `scan_recording()` opens its own DB session/transaction and
commits (or rolls back) independently, so one bad recording never loses
progress already made on others in the same `meg_scan` run (see
`scanner.run_meg_scan`, which calls this per discovered FIF file inside its
own per-file try/except).

Subject resolution follows the MEG parallel track plan's two-tier strategy
(no hash fallback, unlike DICOM extraction):
  1. primary: `subject_id_type_id` lookup against `subject_other_identifiers`
     using the FIF-filename-derived participant label
     (`FifHeader.participant_from`).
  2. fallback: an uploaded CSV mapping participant label -> `subject_code`
     (two-column CSV: `participant`, `subject_code` — see
     `load_participant_subject_code_csv`).
If neither resolves, the recording fails with `SubjectResolutionError` and
is recorded as an `ingest_conflicts` row (scope `meg_subject_resolution`),
matching the plan's "Subject resolution fails for some recordings" failure
mode.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from metadata_db.schema import (
    Event,
    IngestConflict,
    MegAcquisition,
    MegChannel,
    Study,
    Subject,
    SubjectCohort,
    SubjectOtherIdentifier,
)
from metadata_db.session import SessionLocal

from .config import MegScanConfig
from .models import FifHeader, MegScanResult, SubjectResolution, synthesize_study_uid

logger = logging.getLogger(__name__)

# observation_types.observation_type_id for MEG session events. Must stay in
# sync with the two other hardcoded copies of this mapping:
#   - extract.writer._MODALITY_TO_OBSERVATION_TYPE["MEG"]
#   - metadata_db.migrations.backfill_study_events.MODALITY_TO_OBSERVATION_TYPE["MEG"]
# (see the MEG parallel track plan, task 7, for why this is duplicated
# instead of imported: extract.writer's mapping is a private module-level
# constant, and importing across the extract/meg boundary for one integer
# would be a heavier coupling than keeping the three copies documented and
# in sync.)
MEG_OBSERVATION_TYPE_ID = 16


class SubjectResolutionError(Exception):
    """Raised when a recording's subject identity cannot be resolved."""


def load_participant_subject_code_csv(path: Path) -> Dict[str, str]:
    """Load a two-column CSV mapping FIF participant labels to `subject_code`.

    Thin, MEG-specific wrapper around `extract.subject_mapping.load_subject_code_csv`
    with fixed column names (`participant`, `subject_code`) since the MEG
    stage config (`meg.config.MegSubjectResolutionConfig`) exposes only a
    CSV path, not configurable column names like the DICOM extract stage's
    upload flow does.
    """
    from extract.subject_mapping import load_subject_code_csv

    return load_subject_code_csv(path, "participant", "subject_code")


class MegExtractor:
    """Per-recording scan-to-DB writer for the `meg_scan` stage.

    One instance is constructed per `meg_scan` job/run and reused across all
    recordings discovered in that run (so its subject-code-CSV mapping is
    loaded once), but each `scan_recording()` call is independently
    transactional.
    """

    def __init__(
        self,
        cohort_id: int,
        config: MegScanConfig,
        subject_code_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.cohort_id = cohort_id
        self.config = config
        self._subject_code_map = subject_code_map or {}
        self.result = MegScanResult()

    def scan_recording(self, header: FifHeader) -> None:
        """Persist one scanned `FifHeader` into the metadata DB.

        Raises `SubjectResolutionError` if subject identity cannot be
        resolved, or `ValueError` if the header is missing data required to
        build a session key (acquisition date). Both are recoverable at the
        `run_meg_scan` call site: the failing recording is counted and
        skipped, other recordings in the same run are unaffected.
        """
        with SessionLocal() as session:
            try:
                resolution = self._resolve_subject(session, header)
                self._ensure_subject_cohort(session, resolution.subject_id)
                study_id, study_inserted = self._ensure_study(session, resolution, header)
                if study_inserted:
                    self.result.studies_inserted += 1
                else:
                    self.result.studies_updated += 1

                self._link_event(session, resolution.subject_id, study_id, resolution.session_date)

                acquisition_id, acquisition_inserted = self._ensure_acquisition(
                    session, study_id, resolution.subject_id, header
                )
                if acquisition_inserted:
                    self.result.acquisitions_inserted += 1
                else:
                    self.result.acquisitions_updated += 1

                n_channels = self._replace_channels(session, acquisition_id, header)
                self.result.channels_inserted += n_channels

                session.commit()
            except SubjectResolutionError as exc:
                session.rollback()
                self._log_conflict_standalone(
                    "meg_subject_resolution", header.fif_file_path, str(exc), header.fif_file_path
                )
                raise
            except Exception:
                session.rollback()
                raise

    # -- subject resolution -------------------------------------------------

    def _resolve_subject(self, session, header: FifHeader) -> SubjectResolution:
        participant = (header.participant_from or "").strip()
        if not participant:
            raise SubjectResolutionError(
                f"FIF header has no participant identifier to resolve subject identity: {header.fif_file_path}"
            )

        session_label = header.session_from or ""
        session_date = header.acquisition_date
        if session_date is None:
            raise ValueError(
                f"FIF header has no acquisition (measurement) date; cannot resolve MEG session: {header.fif_file_path}"
            )

        # 1. Primary: subject_id_type_id lookup against subject_other_identifiers.
        if self.config.subject_id_type_id is not None:
            stmt = select(SubjectOtherIdentifier.subject_id).where(
                SubjectOtherIdentifier.id_type_id == self.config.subject_id_type_id,
                SubjectOtherIdentifier.other_identifier == participant,
            )
            subject_id = session.execute(stmt).scalar_one_or_none()
            if subject_id is not None:
                subject_code = session.execute(
                    select(Subject.subject_code).where(Subject.subject_id == subject_id)
                ).scalar_one()
                return SubjectResolution(
                    subject_id=subject_id,
                    subject_code=subject_code,
                    source="identifier_lookup",
                    session_label=session_label,
                    session_date=session_date,
                )

        # 2. Fallback: CSV mapping (participant -> subject_code).
        subject_code = self._subject_code_map.get(participant)
        if not subject_code:
            raise SubjectResolutionError(
                f"Could not resolve subject identity for participant '{participant}' "
                f"(no subject_id_type_id match and no CSV mapping entry): {header.fif_file_path}"
            )

        subject_id = self._ensure_subject_row(session, subject_code)
        if self.config.subject_id_type_id is not None:
            self._ensure_subject_identifier(session, subject_id, participant, header)

        return SubjectResolution(
            subject_id=subject_id,
            subject_code=subject_code,
            source="csv",
            session_label=session_label,
            session_date=session_date,
        )

    def _ensure_subject_row(self, session, subject_code: str) -> int:
        existing = session.execute(
            select(Subject.subject_id).where(Subject.subject_code == subject_code)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        result = session.execute(
            insert(Subject)
            .values(subject_code=subject_code)
            .on_conflict_do_nothing()
            .returning(Subject.subject_id)
        )
        subject_id = result.scalar_one_or_none()
        if subject_id is not None:
            self.result.subjects_inserted += 1
            return subject_id

        # Raced with a concurrent insert between the SELECT and INSERT above.
        return session.execute(
            select(Subject.subject_id).where(Subject.subject_code == subject_code)
        ).scalar_one()

    def _ensure_subject_identifier(self, session, subject_id: int, participant: str, header: FifHeader) -> None:
        stmt = select(SubjectOtherIdentifier).where(
            SubjectOtherIdentifier.subject_id == subject_id,
            SubjectOtherIdentifier.id_type_id == self.config.subject_id_type_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing is not None:
            if existing.other_identifier != participant:
                self._log_conflict(
                    session,
                    "meg_subject_identifier",
                    participant,
                    "Conflicting participant identifier for subject",
                    header.fif_file_path,
                )
                session.execute(
                    update(SubjectOtherIdentifier)
                    .where(SubjectOtherIdentifier.subject_other_identifier_id == existing.subject_other_identifier_id)
                    .values(other_identifier=participant)
                )
            return

        session.execute(
            insert(SubjectOtherIdentifier)
            .values(subject_id=subject_id, id_type_id=self.config.subject_id_type_id, other_identifier=participant)
            .on_conflict_do_nothing()
        )

    def _ensure_subject_cohort(self, session, subject_id: int) -> None:
        existing = session.execute(
            select(SubjectCohort.subject_cohort_id).where(
                SubjectCohort.subject_id == subject_id, SubjectCohort.cohort_id == self.cohort_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.execute(
            insert(SubjectCohort)
            .values(subject_id=subject_id, cohort_id=self.cohort_id)
            .on_conflict_do_nothing()
        )

    # -- study / event -------------------------------------------------------

    def _ensure_study(self, session, resolution: SubjectResolution, header: FifHeader) -> tuple[int, bool]:
        study_uid = synthesize_study_uid(resolution.subject_code, resolution.session_label, resolution.session_date)

        fields = {
            "study_date": resolution.session_date,
            "study_description": header.study_description,
            "modality": "MEG",
            "manufacturer": header.manufacturer,
            "manufacturer_model_name": header.manufacturer_model_name,
            "station_name": header.station_name,
            "institution_name": header.institution_name,
            "subject_id": resolution.subject_id,
        }

        existing = session.execute(select(Study).where(Study.study_instance_uid == study_uid)).scalar_one_or_none()
        if existing is not None:
            if existing.subject_id != resolution.subject_id:
                self._log_conflict(
                    session,
                    "meg_study",
                    study_uid,
                    "Synthesized MEG study UID resolved to a different subject; re-linking",
                    header.fif_file_path,
                )
            changed = {
                key: value
                for key, value in fields.items()
                if value is not None and getattr(existing, key) != value
            }
            if changed:
                session.execute(update(Study).where(Study.study_id == existing.study_id).values(**changed))
            return existing.study_id, False

        insert_values = {"study_instance_uid": study_uid, **fields}
        result = session.execute(
            insert(Study).values(**insert_values).on_conflict_do_nothing().returning(Study.study_id)
        )
        study_id = result.scalar_one_or_none()
        if study_id is not None:
            return study_id, True

        # Raced with a concurrent insert for the same synthesized UID.
        study_id = session.execute(
            select(Study.study_id).where(Study.study_instance_uid == study_uid)
        ).scalar_one()
        return study_id, False

    def _link_event(self, session, subject_id: int, study_id: int, event_date: date) -> None:
        stmt = select(Event.event_id).where(
            Event.subject_id == subject_id,
            Event.observation_type_id == MEG_OBSERVATION_TYPE_ID,
            Event.event_date == event_date,
        )
        event_id = session.execute(stmt).scalar_one_or_none()

        if event_id is None:
            result = session.execute(
                insert(Event)
                .values(subject_id=subject_id, observation_type_id=MEG_OBSERVATION_TYPE_ID, event_date=event_date)
                .on_conflict_do_nothing()
                .returning(Event.event_id)
            )
            event_id = result.scalar_one_or_none()
            if event_id is None:
                event_id = session.execute(stmt).scalar_one()

        session.execute(
            update(Study)
            .where(Study.study_id == study_id)
            .where(Study.event_id.is_(None))
            .values(event_id=event_id)
        )

    # -- acquisition / channels ----------------------------------------------

    def _ensure_acquisition(self, session, study_id: int, subject_id: int, header: FifHeader) -> tuple[int, bool]:
        fields = {
            "subject_id": subject_id,
            "session_label": header.session_from or None,
            "split_count": header.split_count,
            "bids_task": header.task or None,
            "bids_run": header.run or None,
            "bids_acq_label": header.acquisition or None,
            "bids_processing_label": header.processing or None,
            "bids_datatype": header.datatype,
            "acquisition_date": header.acquisition_date,
            "device": header.device,
            "sampling_frequency": header.sampling_frequency,
            "n_channels": header.n_channels,
            "duration_seconds": header.duration_seconds,
            "highpass_hz": header.highpass_hz,
            "lowpass_hz": header.lowpass_hz,
            "notch_filter_hz": None if header.line_freq is None else str(header.line_freq),
        }

        existing = session.execute(
            select(MegAcquisition).where(
                MegAcquisition.study_id == study_id, MegAcquisition.fif_file_path == header.fif_file_path
            )
        ).scalar_one_or_none()
        if existing is not None:
            session.execute(
                update(MegAcquisition)
                .where(MegAcquisition.meg_acquisition_id == existing.meg_acquisition_id)
                .values(**fields)
            )
            return existing.meg_acquisition_id, False

        insert_values = {"study_id": study_id, "fif_file_path": header.fif_file_path, **fields}
        result = session.execute(
            insert(MegAcquisition)
            .values(**insert_values)
            .on_conflict_do_nothing()
            .returning(MegAcquisition.meg_acquisition_id)
        )
        acquisition_id = result.scalar_one_or_none()
        if acquisition_id is not None:
            return acquisition_id, True

        acquisition_id = session.execute(
            select(MegAcquisition.meg_acquisition_id).where(
                MegAcquisition.study_id == study_id, MegAcquisition.fif_file_path == header.fif_file_path
            )
        ).scalar_one()
        return acquisition_id, False

    def _replace_channels(self, session, acquisition_id: int, header: FifHeader) -> int:
        """Rebuild `meg_channel` rows for one acquisition from the current header scan.

        Full delete + re-insert (rather than per-channel diffing) since a
        channel list is a point-in-time snapshot of the FIF header, not an
        independently editable resource — this keeps the per-recording write
        path simple, matching the stage's lean-and-synchronous design.
        """
        session.execute(delete(MegChannel).where(MegChannel.meg_acquisition_id == acquisition_id))
        if not header.channels:
            return 0

        rows = [
            {
                "meg_acquisition_id": acquisition_id,
                "channel_name": ch.channel_name,
                "channel_type": ch.channel_type,
                "unit": ch.unit,
                "is_bad": 1 if ch.is_bad else 0,
                "location_x": ch.location_x,
                "location_y": ch.location_y,
                "location_z": ch.location_z,
            }
            for ch in header.channels
        ]
        session.execute(insert(MegChannel).values(rows))
        return len(rows)

    # -- conflict logging -----------------------------------------------------

    def _log_conflict(self, session, scope: str, uid: str, message: str, file_path: Optional[str]) -> None:
        if self.cohort_id is None:
            return
        stmt = (
            insert(IngestConflict)
            .values(cohort_id=self.cohort_id, scope=scope, uid=uid, message=message, file_path=file_path)
            .on_conflict_do_nothing()
        )
        session.execute(stmt)

    def _log_conflict_standalone(self, scope: str, uid: str, message: str, file_path: Optional[str]) -> None:
        """Log a conflict in its own transaction (used when the caller's
        session was already rolled back, e.g. after a `SubjectResolutionError`)."""
        if self.cohort_id is None:
            return
        try:
            with SessionLocal() as session:
                stmt = (
                    insert(IngestConflict)
                    .values(cohort_id=self.cohort_id, scope=scope, uid=uid, message=message, file_path=file_path)
                    .on_conflict_do_nothing()
                )
                session.execute(stmt)
                session.commit()
        except Exception:
            logger.exception("Failed to record meg_scan conflict for cohort %s (%s): %s", self.cohort_id, scope, message)
