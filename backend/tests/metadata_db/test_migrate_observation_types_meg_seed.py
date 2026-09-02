"""Regression test: `migrate_observation_types` must backfill newly-added
seed rows (e.g. observation_type_id 16 / "MEG Scan") into a database that
already ran this migration before that row existed in
`OBSERVATION_TYPES_SEED`, not just into a fresh/empty table.

Before this fix, `_needs_migration()` returned False as soon as
`observation_types` had *any* rows, so `_seed_observation_types()` (itself
correctly per-row idempotent) was never even invoked on an
already-migrated deployment.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from metadata_db import schema
from metadata_db.migrations.migrate_observation_types import _needs_migration, run_migration


def _make_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    schema.Base.metadata.create_all(engine)
    return engine


def _seed_pre_meg_observation_types(engine) -> None:
    """Simulate a deployment migrated before MEG (id 16) existed: table is
    fully created/renamed already, populated only with ids 1-15."""
    with engine.begin() as conn:
        for obs_id in range(1, 16):
            conn.execute(
                text(
                    "INSERT INTO observation_types (observation_type_id, category, name, is_active, is_primary) "
                    "VALUES (:id, 'Imaging', 'placeholder', 1, 0)"
                ),
                {"id": obs_id},
            )


class TestMegObservationTypeBackfill:
    def test_needs_migration_true_when_meg_row_missing(self):
        engine = _make_engine()
        _seed_pre_meg_observation_types(engine)

        with engine.connect() as conn:
            assert _needs_migration(conn) is True

    def test_run_migration_backfills_meg_row_without_touching_existing(self):
        engine = _make_engine()
        _seed_pre_meg_observation_types(engine)

        results = run_migration(engine)

        assert results["success"] is True
        assert results["already_migrated"] is False

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT category, name FROM observation_types WHERE observation_type_id = 16")
            ).fetchone()
            assert row is not None
            assert row[0] == "Imaging"
            assert row[1] == "MEG Scan"

            # Pre-existing rows must be untouched (still the placeholder name).
            existing = conn.execute(
                text("SELECT name FROM observation_types WHERE observation_type_id = 1")
            ).scalar_one()
            assert existing == "placeholder"

    def test_needs_migration_false_and_idempotent_when_fully_seeded(self):
        engine = _make_engine()
        first = run_migration(engine)
        assert first["success"] is True

        with engine.connect() as conn:
            assert _needs_migration(conn) is False

        second = run_migration(engine)
        assert second["success"] is True
        assert second["already_migrated"] is True
