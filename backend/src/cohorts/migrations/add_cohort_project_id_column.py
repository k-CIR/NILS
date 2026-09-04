"""Idempotent migration: add ``project_id`` FK column to ``cohorts``.

Adds the nullable link from a cohort to a cross-facility ``project`` row
(``projects.models.Project``). Existing cohorts stay ``NULL`` -- no
backfill -- preserving current behaviour for every cohort created before
this migration and for every non-facility cohort going forward.

Safe to call repeatedly; checks ``information_schema`` for column presence
before issuing any DDL. Requires the ``project`` table to already exist
(created via ``Base.metadata.create_all`` alongside ``cohorts`` on
first-time deploys; ``ensure_migrated()`` here only adds the FK column to
an already-existing ``cohorts`` table on upgrade).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


_TABLE = "cohorts"


def _has_column(conn: Connection, column: str) -> bool:
    sql = text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        LIMIT 1
        """
    )
    return conn.execute(sql, {"t": _TABLE, "c": column}).first() is not None


def _table_exists(conn: Connection, table: str) -> bool:
    sql = text(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = :t
        LIMIT 1
        """
    )
    return conn.execute(sql, {"t": table}).first() is not None


def ensure_migrated() -> None:
    """Add the ``project_id`` column + FK if absent."""
    from db.session import engine

    with engine.begin() as conn:
        if not _table_exists(conn, _TABLE):
            # First-time deploys create the table via Base.metadata.create_all
            # which already includes the new column. Nothing to do.
            return

        if _has_column(conn, "project_id"):
            return

        if not _table_exists(conn, "project"):
            # `project` table not created yet (e.g. Base.metadata.create_all
            # hasn't run yet in this process) -- defer; the caller re-runs
            # this migration on every app start via cohorts.service.
            logger.warning(
                "cohorts: skipping project_id column migration -- 'project' "
                "table does not exist yet"
            )
            return

        conn.execute(text(
            f"ALTER TABLE {_TABLE} "
            "ADD COLUMN project_id INTEGER NULL "
            "REFERENCES project(project_id) ON DELETE SET NULL"
        ))
        logger.info("cohorts: added project_id column (nullable FK -> project.project_id)")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    ensure_migrated()
