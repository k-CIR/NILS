"""Idempotent migration: add ``modality`` column to ``cohorts``.

Adds the cohort-level modality/type column that lets pipeline
initialization choose between the imaging (DICOM MR/CT/PET) stage list
and the MEG stage list. See ``nils_dataset_pipeline/ordering.py``.

* ``modality``  VARCHAR(20) NOT NULL DEFAULT 'imaging'

All existing cohorts backfill to ``'imaging'``, preserving current
behaviour for every cohort created before this migration.

Safe to call repeatedly; checks ``information_schema`` for column
presence before issuing any DDL.
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


def _table_exists(conn: Connection) -> bool:
    sql = text(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = :t
        LIMIT 1
        """
    )
    return conn.execute(sql, {"t": _TABLE}).first() is not None


def ensure_migrated() -> None:
    """Add the ``modality`` column if absent and backfill existing rows."""
    from db.session import engine

    with engine.begin() as conn:
        if not _table_exists(conn):
            # First-time deploys create the table via Base.metadata.create_all
            # which already includes the new column. Nothing to do.
            return

        if _has_column(conn, "modality"):
            return

        conn.execute(text(
            f"ALTER TABLE {_TABLE} "
            "ADD COLUMN modality VARCHAR(20) NOT NULL DEFAULT 'imaging'"
        ))
        logger.info("cohorts: added modality column (default 'imaging')")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    ensure_migrated()
