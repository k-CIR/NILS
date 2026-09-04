"""Discovery confirm flow: creates/links `project`/`cohort`/`subject` records
and wires the per-cohort subject-code override CSV.

Confirming a discovery only creates/links records -- it does **not**
auto-queue extraction. Extraction remains a fully separate, explicit manual
step exactly as today (see the plan's Decisions #6).

Idempotent: confirming an already-`confirmed` discovery is a no-op that
returns the existing row (never creates a duplicate `project`/`cohort`/
`subject_cohorts` row or duplicate override-CSV entry).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from cohorts.models import Cohort, CreateCohortPayload
from cohorts.service import cohort_service
from db.session import session_scope
from metadata_db.schema import Subject, SubjectCohort
from metadata_db.session import SessionLocal as MetadataSessionLocal
from nils_dataset_pipeline.repository import save_config

from . import subject_code_csv
from .dicom_peek import NoDicomFileFoundError, PatientIdNotFoundError, peek_patient_id
from .models import FacilityDiscovery
from .config import get_settings
from projects.repository import find_or_create_project

logger = logging.getLogger(__name__)

# Generic observation_type_id used for facility-discovered subject linkage.
# Only used as a fallback when no cohort/study event already exists; the
# regular cohort pipeline stages (extract / meg_scan) are the ones that
# actually populate studies/events once the operator runs them -- confirm
# itself never creates a study or event, only the subject + subject_cohorts
# link (see plan Decisions #6: confirm never queues extraction).


class ConfirmError(Exception):
    """Raised for any confirm-flow failure that should surface clearly to
    the operator rather than silently linking partial state."""


@dataclass
class ConfirmResult:
    discovery: FacilityDiscovery
    already_confirmed: bool = False


def _cohort_name_for(cir_project: str, facility: str) -> str:
    return f"{cir_project}-{facility}"


def _resolve_or_create_cohort(project_id: int, project_code: str, facility: str) -> Cohort:
    settings = get_settings()

    if facility == "mrc":
        if settings.mrc_staging_root is None:
            raise ConfirmError("MRC_STAGING_ROOT is not configured")
        source_path = settings.mrc_staging_root / project_code / "mrc"
        source_path.mkdir(parents=True, exist_ok=True)
        modality = "imaging"
    elif facility == "natmeg":
        if settings.vault_root is None:
            raise ConfirmError("FACILITY_VAULT_ROOT is not configured")
        source_path = settings.vault_root / "natmeg" / project_code
        modality = "meg"
    else:
        raise ConfirmError(f"Unknown facility: {facility}")

    payload = CreateCohortPayload(
        name=_cohort_name_for(project_code, facility),
        source_path=str(source_path),
        description=f"Facility-discovery cohort for project {project_code} ({facility})",
        modality=modality,
        project_id=project_id,
    )
    dto = cohort_service.create_cohort(payload)

    with session_scope() as session:
        cohort = session.get(Cohort, dto.id)
        if cohort is not None and cohort.project_id != project_id:
            cohort.project_id = project_id
            session.flush()
        return session.get(Cohort, dto.id)


def _stage_mrc_symlink(mrc_staging_root: Path, project_code: str, discovery: FacilityDiscovery) -> Path:
    staging_dir = mrc_staging_root / project_code / "mrc"
    staging_dir.mkdir(parents=True, exist_ok=True)
    link_path = staging_dir / f"sub-{discovery.facility_id_value}"
    target = Path(discovery.folder_path)

    if link_path.is_symlink():
        if link_path.resolve() == target.resolve():
            return link_path
        link_path.unlink()
    elif link_path.exists():
        raise ConfirmError(f"Staging path already exists and is not a symlink: {link_path}")

    os.symlink(target, link_path, target_is_directory=True)
    return link_path


def _ensure_subject_row(session, subject_code: str) -> int:
    """Find-or-create a metadata-DB `subject` row by `subject_code`.

    Duplicates `meg.extractor.MegExtractor._ensure_subject_row`'s small
    find-or-create query rather than importing across the
    facility_discovery/meg boundary for one query (mirrors the precedent
    already established for `extract.writer`/`meg.extractor`'s duplicated
    observation-type mapping).
    """
    existing = session.execute(
        select(Subject.subject_id).where(Subject.subject_code == subject_code)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    result = session.execute(
        insert(Subject).values(subject_code=subject_code).on_conflict_do_nothing().returning(Subject.subject_id)
    )
    subject_id = result.scalar_one_or_none()
    if subject_id is not None:
        return subject_id

    # Raced with a concurrent insert.
    return session.execute(
        select(Subject.subject_id).where(Subject.subject_code == subject_code)
    ).scalar_one()


def _ensure_subject_cohort(session, subject_id: int, cohort_id: int) -> None:
    existing = session.execute(
        select(SubjectCohort.subject_cohort_id).where(
            SubjectCohort.subject_id == subject_id, SubjectCohort.cohort_id == cohort_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.execute(
        insert(SubjectCohort).values(subject_id=subject_id, cohort_id=cohort_id).on_conflict_do_nothing()
    )


def _ensure_subject_and_link(cir_id: str, cohort_id: int) -> int:
    with MetadataSessionLocal() as session:
        subject_id = _ensure_subject_row(session, cir_id)
        _ensure_subject_cohort(session, subject_id, cohort_id)
        session.commit()
        return subject_id


def confirm_discovery(discovery_id: int) -> ConfirmResult:
    """Run the full confirm flow for one `facility_discoveries` row.

    Steps (per the plan's task 5):
    1. Resolve/create `project` for `cir_project`.
    2. Resolve/create the facility+project's `cohort`.
    3. `mrc`: symlink staging + DICOM PatientID peek + override CSV +
       `extract` stage `subjectCodeCsv` config.
       `natmeg`: override CSV + `meg_scan` stage `subjectCsvMappingPath`
       config.
    4. Ensure a metadata-DB `subject` row + `subject_cohorts` link.
    5. Mark the discovery `confirmed`.
    """
    settings = get_settings()

    with session_scope() as session:
        discovery = session.get(FacilityDiscovery, discovery_id)
        if discovery is None:
            raise ConfirmError(f"Discovery {discovery_id} not found")
        if discovery.status == "confirmed":
            return ConfirmResult(discovery=discovery, already_confirmed=True)
        if discovery.status == "rejected":
            raise ConfirmError(f"Discovery {discovery_id} was rejected; cannot confirm")

        facility = discovery.facility
        cir_id = discovery.cir_id
        cir_project = discovery.cir_project
        folder_path = discovery.folder_path
        facility_id_value = discovery.facility_id_value

        project = find_or_create_project(session, cir_project)
        project_id = project.project_id
        session.flush()

    # Cohort creation runs its own transaction (cohort_service.create_cohort).
    cohort = _resolve_or_create_cohort(project_id, cir_project, facility)
    cohort_id = cohort.id

    if facility == "mrc":
        if settings.subject_code_csv_root is None:
            raise ConfirmError("FACILITY_SUBJECT_CODE_CSV_ROOT is not configured")
        if settings.mrc_staging_root is None:
            raise ConfirmError("MRC_STAGING_ROOT is not configured")

        with session_scope() as session:
            discovery = session.get(FacilityDiscovery, discovery_id)
            _stage_mrc_symlink(settings.mrc_staging_root, cir_project, discovery)

        try:
            patient_id = peek_patient_id(Path(folder_path))
        except (NoDicomFileFoundError, PatientIdNotFoundError) as exc:
            raise ConfirmError(
                f"Could not determine PatientID for {folder_path}: {exc}. "
                "Discovery left pending for retry/manual investigation."
            ) from exc

        csv_path = subject_code_csv.upsert_mrc_override(
            settings.subject_code_csv_root, cohort_id, patient_id, cir_id
        )
        with session_scope() as session:
            save_config(
                session,
                cohort_id,
                "extract",
                None,
                {
                    "subjectCodeCsv": {
                        "filePath": str(csv_path),
                        "patientColumn": "PatientID",
                        "subjectCodeColumn": "subject_code",
                    }
                },
            )

    elif facility == "natmeg":
        if settings.subject_code_csv_root is None:
            raise ConfirmError("FACILITY_SUBJECT_CODE_CSV_ROOT is not configured")

        csv_path = subject_code_csv.upsert_natmeg_override(
            settings.subject_code_csv_root, cohort_id, facility_id_value, cir_id
        )
        with session_scope() as session:
            save_config(
                session,
                cohort_id,
                "meg_scan",
                None,
                {"subjectCsvMappingPath": str(csv_path)},
            )
    else:
        raise ConfirmError(f"Unknown facility: {facility}")

    subject_id = _ensure_subject_and_link(cir_id, cohort_id)

    with session_scope() as session:
        discovery = session.get(FacilityDiscovery, discovery_id)
        discovery.cohort_id = cohort_id
        discovery.subject_id = subject_id
        discovery.status = "confirmed"
        discovery.reviewed_at = datetime.now(timezone.utc)
        session.flush()
        session.refresh(discovery)
        return ConfirmResult(discovery=discovery)


def reject_discovery(discovery_id: int) -> FacilityDiscovery:
    """Mark a discovery `rejected`. No side effects (no project/cohort/subject
    linkage is touched)."""
    with session_scope() as session:
        discovery = session.get(FacilityDiscovery, discovery_id)
        if discovery is None:
            raise ConfirmError(f"Discovery {discovery_id} not found")
        if discovery.status == "confirmed":
            raise ConfirmError(f"Discovery {discovery_id} is already confirmed; cannot reject")
        discovery.status = "rejected"
        discovery.reviewed_at = datetime.now(timezone.utc)
        session.flush()
        session.refresh(discovery)
        return discovery
