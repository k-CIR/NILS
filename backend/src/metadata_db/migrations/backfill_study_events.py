"""
Migration: Backfill imaging session events for studies.

Creates event rows for existing studies and links them via study.event_id.
A "session" is one event per (subject_id, observation_type_id, event_date),
meaning all studies for the same subject on the same date with the same
imaging modality share a single event.

Also adds a unique index on event(subject_id, observation_type_id, event_date)
to enforce the one-event-per-session invariant and speed up lookups.

Handles three scenarios:
  1. Fresh database -- no studies exist, nothing to do
  2. Restored old dump -- studies exist with event_id NULL
  3. Running system -- studies added by extraction without event linkage

Runs automatically on server startup via lifecycle.py, after the
observation_types migration has completed.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

MODALITY_TO_OBSERVATION_TYPE: dict[str, int] = {
    "MR": 11,
    "CT": 12,
    "PT": 13,
    "PET": 13,
    "MEG": 16,
}


def _needs_migration(conn: Connection) -> bool:
    """Migration needed when observation_types exists and any study has event_id IS NULL."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    if "observation_types" not in tables or "study" not in tables or "event" not in tables:
        return False

    study_cols = {c["name"] for c in inspector.get_columns("study")}
    if "event_id" not in study_cols:
        return False

    # Check for the unique constraint/index -- if missing, we need to add it
    if not _has_session_unique_constraint(inspector):
        return True

    # Check for studies without event linkage
    result = conn.execute(text(
        "SELECT EXISTS(SELECT 1 FROM study WHERE event_id IS NULL LIMIT 1)"
    ))
    return bool(result.scalar())


def _has_session_unique_constraint(inspector) -> bool:
    """Check if the event table has the session uniqueness constraint (index or constraint)."""
    target_cols = {"subject_id", "observation_type_id", "event_date"}

    # Check indexes (PostgreSQL stores CREATE UNIQUE INDEX here)
    for idx in inspector.get_indexes("event"):
        if set(idx.get("column_names", [])) == target_cols and idx.get("unique", False):
            return True

    # Check unique constraints (SQLite with UniqueConstraint in model)
    try:
        for uc in inspector.get_unique_constraints("event"):
            if set(uc.get("column_names", [])) == target_cols:
                return True
    except (NotImplementedError, AttributeError):
        pass

    return False


def _add_unique_index(conn: Connection, dialect: str) -> bool:
    """Add unique index on event(subject_id, observation_type_id, event_date) if missing."""
    inspector = inspect(conn)
    if _has_session_unique_constraint(inspector):
        return False

    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_event_subject_type_date "
        "ON event (subject_id, observation_type_id, event_date)"
    ))
    return True


def _backfill_postgresql(conn: Connection) -> dict:
    """Backfill using pure SQL CTEs for PostgreSQL."""
    changes: list[str] = []

    # Step 1: Determine dominant modality per study from series table
    # Step 2: Map to observation_type_id
    # Step 3: INSERT events for unique sessions (ON CONFLICT skip)
    # Step 4: UPDATE study.event_id

    # Build the modality CASE expression
    case_parts = []
    for modality, obs_id in MODALITY_TO_OBSERVATION_TYPE.items():
        case_parts.append(f"WHEN dominant_modality = '{modality}' THEN {obs_id}")
    case_expr = "CASE " + " ".join(case_parts) + " ELSE NULL END"

    # CTE: get dominant modality per study (most common series modality)
    result = conn.execute(text(f"""
        WITH study_modality AS (
            SELECT
                st.study_id,
                st.subject_id,
                st.study_date,
                (
                    SELECT s.modality
                    FROM series s
                    WHERE s.study_id = st.study_id
                    GROUP BY s.modality
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                ) AS dominant_modality
            FROM study st
            WHERE st.event_id IS NULL
              AND st.study_date IS NOT NULL
        ),
        study_obs AS (
            SELECT
                study_id,
                subject_id,
                study_date,
                dominant_modality,
                {case_expr} AS observation_type_id
            FROM study_modality
            WHERE dominant_modality IS NOT NULL
        ),
        sessions AS (
            SELECT DISTINCT subject_id, observation_type_id, study_date
            FROM study_obs
            WHERE observation_type_id IS NOT NULL
        ),
        inserted_events AS (
            INSERT INTO event (subject_id, observation_type_id, event_date)
            SELECT subject_id, observation_type_id, study_date
            FROM sessions
            ON CONFLICT (subject_id, observation_type_id, event_date) DO NOTHING
            RETURNING event_id, subject_id, observation_type_id, event_date
        )
        SELECT COUNT(*) FROM inserted_events
    """))
    events_inserted = result.scalar() or 0
    if events_inserted:
        changes.append(f"Inserted {events_inserted} imaging session events")

    # Now update study.event_id for all unlinked studies
    result = conn.execute(text(f"""
        WITH study_modality AS (
            SELECT
                st.study_id,
                st.subject_id,
                st.study_date,
                (
                    SELECT s.modality
                    FROM series s
                    WHERE s.study_id = st.study_id
                    GROUP BY s.modality
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                ) AS dominant_modality
            FROM study st
            WHERE st.event_id IS NULL
              AND st.study_date IS NOT NULL
        ),
        study_obs AS (
            SELECT
                study_id,
                subject_id,
                study_date,
                {case_expr} AS observation_type_id
            FROM study_modality
            WHERE dominant_modality IS NOT NULL
        )
        UPDATE study
        SET event_id = e.event_id
        FROM study_obs so
        JOIN event e ON e.subject_id = so.subject_id
                    AND e.observation_type_id = so.observation_type_id
                    AND e.event_date = so.study_date
        WHERE study.study_id = so.study_id
          AND so.observation_type_id IS NOT NULL
    """))
    studies_linked = result.rowcount or 0
    if studies_linked:
        changes.append(f"Linked {studies_linked} studies to events")

    # Check for studies that couldn't be linked (no series, unknown modality, null date)
    result = conn.execute(text(
        "SELECT COUNT(*) FROM study WHERE event_id IS NULL"
    ))
    unlinked = result.scalar() or 0
    if unlinked:
        changes.append(f"{unlinked} studies remain unlinked (no series or unknown modality)")
        logger.warning("%d studies could not be linked to events", unlinked)

    return {"events_inserted": events_inserted, "studies_linked": studies_linked, "changes": changes}


def _backfill_sqlite(conn: Connection) -> dict:
    """Backfill using individual statements for SQLite (no writable CTEs)."""
    changes: list[str] = []

    # Step 1: Find unlinked studies with their dominant modality
    rows = conn.execute(text("""
        SELECT
            st.study_id,
            st.subject_id,
            st.study_date,
            (
                SELECT s.modality
                FROM series s
                WHERE s.study_id = st.study_id
                GROUP BY s.modality
                ORDER BY COUNT(*) DESC
                LIMIT 1
            ) AS dominant_modality
        FROM study st
        WHERE st.event_id IS NULL
          AND st.study_date IS NOT NULL
    """)).mappings().all()

    if not rows:
        return {"events_inserted": 0, "studies_linked": 0, "changes": changes}

    # Step 2: Compute sessions and insert events
    sessions: dict[tuple[int, int, str], list[int]] = {}  # (subject_id, obs_type_id, date) -> [study_ids]
    skipped = 0
    for row in rows:
        modality = row["dominant_modality"]
        if not modality:
            skipped += 1
            continue
        obs_type_id = MODALITY_TO_OBSERVATION_TYPE.get(str(modality).upper())
        if obs_type_id is None:
            skipped += 1
            continue
        study_date = row["study_date"]
        if study_date is None:
            skipped += 1
            continue
        key = (row["subject_id"], obs_type_id, str(study_date))
        sessions.setdefault(key, []).append(row["study_id"])

    events_inserted = 0
    for (subject_id, obs_type_id, event_date), study_ids in sessions.items():
        # Check if event already exists
        existing = conn.execute(text(
            "SELECT event_id FROM event "
            "WHERE subject_id = :sid AND observation_type_id = :oid AND event_date = :ed"
        ), {"sid": subject_id, "oid": obs_type_id, "ed": event_date}).scalar()

        if existing is None:
            conn.execute(text(
                "INSERT INTO event (subject_id, observation_type_id, event_date) "
                "VALUES (:sid, :oid, :ed)"
            ), {"sid": subject_id, "oid": obs_type_id, "ed": event_date})
            event_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
            events_inserted += 1
        else:
            event_id = existing

        # Link studies
        for study_id in study_ids:
            conn.execute(text(
                "UPDATE study SET event_id = :eid WHERE study_id = :sid"
            ), {"eid": event_id, "sid": study_id})

    studies_linked = sum(len(ids) for ids in sessions.values())
    if events_inserted:
        changes.append(f"Inserted {events_inserted} imaging session events")
    if studies_linked:
        changes.append(f"Linked {studies_linked} studies to events")
    if skipped:
        changes.append(f"{skipped} studies skipped (no series or unknown modality)")
        logger.warning("%d studies could not be linked to events", skipped)

    return {"events_inserted": events_inserted, "studies_linked": studies_linked, "changes": changes}


def run_migration(engine: Engine, dry_run: bool = False) -> dict:
    results: dict = {
        "success": False,
        "already_migrated": False,
        "changes_made": [],
        "events_inserted": 0,
        "studies_linked": 0,
        "elapsed_seconds": 0.0,
    }
    start = time.time()

    with engine.begin() as conn:
        if not _needs_migration(conn):
            results["success"] = True
            results["already_migrated"] = True
            results["elapsed_seconds"] = time.time() - start
            return results

        if dry_run:
            results["success"] = True
            results["changes_made"].append("DRY RUN -- no changes applied")
            results["elapsed_seconds"] = time.time() - start
            return results

        # Add unique index
        if _add_unique_index(conn, conn.dialect.name):
            results["changes_made"].append("Added unique index uq_event_subject_type_date on event")

        # Backfill events
        dialect = conn.dialect.name
        if dialect == "postgresql":
            backfill = _backfill_postgresql(conn)
        else:
            backfill = _backfill_sqlite(conn)

        results["events_inserted"] = backfill["events_inserted"]
        results["studies_linked"] = backfill["studies_linked"]
        results["changes_made"].extend(backfill["changes"])

    results["success"] = True
    results["elapsed_seconds"] = time.time() - start
    return results
