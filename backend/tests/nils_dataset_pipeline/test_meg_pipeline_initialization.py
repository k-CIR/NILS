"""Integration tests for MEG cohort pipeline initialization.

Covers the MEG parallel track's Validation Plan item: "Integration test
creating an MEG cohort and confirming pipeline steps are meg_ingest,
meg_scan, meg_bids", plus a regression guard that imaging cohorts keep
initializing the original DICOM stage set unchanged.

Exercises `nils_pipeline_service.initialize_for_cohort` /
`build_stages_response` directly (rather than the full
`cohort_service.create_cohort` HTTP-adjacent path), against a real
SQLite-backed session, so the test doesn't depend on a live Postgres
connection for the pipeline-steps migration.
"""

from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.orm import sessionmaker

from cohorts import repository as cohort_repository
from cohorts.models import Cohort
from nils_dataset_pipeline import service as pipeline_service_module
from nils_dataset_pipeline.models import NilsDatasetPipelineStep

# `NilsDatasetPipelineStep.current_job_id` has a real FK to `jobs.id`, but
# `Job` is declared on its own separate `DeclarativeBase` (`jobs.models.Base`)
# rather than on `cohorts.models.Base` (which `NilsDatasetPipelineStep` and
# `Cohort` share), so `jobs` never appears in `cohorts.models.Base.metadata`.
# In production the `jobs` table is created by a raw-SQL migration, not via
# this ORM metadata, so nothing ties the two together at the metadata level.
#
# Rather than mutating the shared, process-global `cohorts.models.Base.metadata`
# (which would leak a stub `jobs` table into every other test module that
# also happens to call `Base.metadata.create_all()`), copy just the two
# tables this test needs into a private `MetaData` instance alongside a
# minimal stub `jobs(id)` table, so SQLAlchemy's DDL compiler can resolve the
# FK target without touching global state.
def _make_test_engine():
    local_metadata = MetaData()
    Table("jobs", local_metadata, Column("id", Integer, primary_key=True))
    Cohort.__table__.to_metadata(local_metadata)
    NilsDatasetPipelineStep.__table__.to_metadata(local_metadata)

    engine = create_engine("sqlite:///:memory:", future=True)
    local_metadata.create_all(engine)
    return engine


def _make_service(monkeypatch, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(pipeline_service_module, "SessionLocal", SessionLocal)
    return SessionLocal, pipeline_service_module.NilsDatasetPipelineService()


def _make_cohort(session, *, name: str, modality: str, source_path: str = "/data/cohort") -> int:
    cohort = cohort_repository.create_cohort(
        session,
        name=name,
        source_path=source_path,
        anonymization_enabled=False,
        modality=modality,
    )
    session.commit()
    session.refresh(cohort)
    return cohort.id


def _dedupe_consecutive(values):
    """Collapse consecutive duplicates (multi-step stages emit one row per step_id)."""
    return list(dict.fromkeys(values))


def test_meg_cohort_initializes_meg_pipeline_stages(monkeypatch):
    engine = _make_test_engine()
    SessionLocal, nils_pipeline_service = _make_service(monkeypatch, engine)

    with SessionLocal() as session:
        cohort_id = _make_cohort(session, name="megstudy", modality="meg", source_path="/data/megstudy")

    steps = nils_pipeline_service.initialize_for_cohort(
        cohort_id=cohort_id,
        anonymization_enabled=False,
        cohort_name="megstudy",
        source_path="/data/megstudy",
        modality="meg",
    )

    assert [step.stage_id for step in steps] == ["meg_ingest", "meg_scan", "meg_bids"]
    assert all(step.step_id is None for step in steps)

    # meg_ingest's default config should be seeded with the cohort source path.
    ingest_step = next(step for step in steps if step.stage_id == "meg_ingest")
    assert ingest_step.config["sourcePath"] == "/data/megstudy"

    # meg_bids's default config should be seeded with the cohort name.
    bids_step = next(step for step in steps if step.stage_id == "meg_bids")
    assert bids_step.config["datasetDescriptionName"] == "megstudy"

    stages = nils_pipeline_service.build_stages_response(cohort_id, modality="meg")
    assert [stage["id"] for stage in stages] == ["meg_ingest", "meg_scan", "meg_bids"]


def test_meg_cohort_stages_never_include_dicom_stage_ids(monkeypatch):
    engine = _make_test_engine()
    SessionLocal, nils_pipeline_service = _make_service(monkeypatch, engine)

    with SessionLocal() as session:
        cohort_id = _make_cohort(session, name="megstudy2", modality="meg")

    steps = nils_pipeline_service.initialize_for_cohort(
        cohort_id=cohort_id,
        anonymization_enabled=True,  # must be ignored entirely for MEG
        cohort_name="megstudy2",
        source_path="/data/megstudy2",
        modality="meg",
    )

    dicom_stage_ids = {"anonymize", "extract", "sort", "bids"}
    assert dicom_stage_ids.isdisjoint({step.stage_id for step in steps})


def test_imaging_cohort_still_initializes_dicom_pipeline_stages(monkeypatch):
    # Regression guard: adding the MEG track must not change existing
    # imaging cohort initialization behavior.
    engine = _make_test_engine()
    SessionLocal, nils_pipeline_service = _make_service(monkeypatch, engine)

    with SessionLocal() as session:
        cohort_id = _make_cohort(session, name="imagingstudy", modality="imaging")

    steps = nils_pipeline_service.initialize_for_cohort(
        cohort_id=cohort_id,
        anonymization_enabled=True,
        cohort_name="imagingstudy",
        source_path="/data/imagingstudy",
        modality="imaging",
    )

    # `sort` is a multi-step stage and emits one row per step_id, so dedupe
    # consecutive stage_ids before comparing the overall stage ordering.
    assert _dedupe_consecutive(step.stage_id for step in steps) == ["anonymize", "extract", "sort", "bids"]

    stages = nils_pipeline_service.build_stages_response(cohort_id, modality="imaging")
    assert [stage["id"] for stage in stages] == ["anonymize", "extract", "sort", "bids"]


def test_imaging_cohort_without_modality_argument_defaults_unchanged(monkeypatch):
    # Callers that omit `modality` entirely (pre-MEG code paths) must keep
    # getting the imaging pipeline.
    engine = _make_test_engine()
    SessionLocal, nils_pipeline_service = _make_service(monkeypatch, engine)

    with SessionLocal() as session:
        cohort_id = _make_cohort(session, name="legacystudy", modality="imaging")

    steps = nils_pipeline_service.initialize_for_cohort(
        cohort_id=cohort_id,
        anonymization_enabled=False,
        cohort_name="legacystudy",
        source_path="/data/legacystudy",
        # modality intentionally omitted
    )

    assert _dedupe_consecutive(step.stage_id for step in steps) == ["extract", "sort", "bids"]
