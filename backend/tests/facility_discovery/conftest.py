"""Shared in-memory SQLite fixture for facility_discovery unit tests.

Mirrors the pattern in `tests/meg/test_scan_extractor_integration.py`:
build an isolated `StaticPool` SQLite engine, create all `cohorts.models.Base`
tables (facility_discovery models register on the same shared app-DB Base),
and hand back a plain sessionmaker for tests to use directly (no monkeypatch
needed since scanner/mapping_importer/confirm helpers all take an explicit
`session` argument rather than importing a module-level SessionLocal).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def app_db_session():
    # Import here (not at module scope) so cohorts.models.Base picks up the
    # facility_discovery model registrations before create_all runs.
    # (`cohorts.models` itself already imports `projects.models` at its own
    # module level so `Cohort.project_id`'s FK always resolves -- no need to
    # import it separately here.)
    from cohorts.models import Base
    import facility_discovery.models  # noqa: F401 (registers on Base)
    # `Cohort` has a string-name relationship to `NilsDatasetPipelineStep`, so
    # this import is required for mapper configuration to succeed -- but
    # that table itself FKs to `jobs` (a *different* declarative Base) and is
    # therefore excluded from create_all below, exactly mirroring
    # `cohorts.service.CohortService._ensure_initialized`'s own
    # `tables_to_create` filtering (that table is provisioned separately by a
    # raw-SQL migration in the real app; irrelevant to these unit tests).
    import nils_dataset_pipeline.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables_to_create = [
        t for t in Base.metadata.tables.values() if t.name != "nils_dataset_pipeline_steps"
    ]
    Base.metadata.create_all(engine, tables=tables_to_create)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with Session() as session:
        yield session
