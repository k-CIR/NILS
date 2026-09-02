"""
Migration: Unified Observation Types

Merges `event_types` and `clinical_measure_types` into a single
`observation_types` table. See docs/concepts/observation-types-refactor.md.

Changes:
  - Rename `clinical_measure_types` → `observation_types` with column renames
  - Rename `event.event_type_id` → `event.observation_type_id`
  - Rename `measure_type_id` → `observation_type_id` on all four measure tables
  - Add `event_id` column to `study` (nullable)
  - Drop `event_types` table
  - Insert seed data into `observation_types`

Handles two scenarios:
  1. Fresh/current database — old tables exist but are empty
  2. Restoring old backups — old tables may contain data

Runs automatically on server startup via lifecycle.py.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

OBSERVATION_TYPES_SEED = [
    {"observation_type_id": 1,  "category": "Clinical Measurement", "name": "EDSS", "value_type": "Numeric", "unit": "points",  "min_value": 0,  "max_value": 9.5,  "is_primary": 1, "description": "Expanded Disability Status Scale"},
    {"observation_type_id": 2,  "category": "Clinical Measurement", "name": "SDMT", "value_type": "Numeric", "unit": "correct", "min_value": 0,  "max_value": 110,  "is_primary": 0, "description": "Symbol Digit Modalities Test"},
    {"observation_type_id": 3,  "category": "Clinical Measurement", "name": "FSMC", "value_type": "Numeric", "unit": "points",  "min_value": 20, "max_value": 100,  "is_primary": 0, "description": "Fatigue Scale for Motor and Cognitive Functions"},
    {"observation_type_id": 4,  "category": "Clinical Measurement", "name": "FSS",  "value_type": "Numeric", "unit": "score",   "min_value": 1,  "max_value": 7,    "is_primary": 0, "description": "Fatigue Severity Scale"},
    {"observation_type_id": 5,  "category": "Clinical Measurement", "name": "HADS", "value_type": "Numeric", "unit": "score",   "min_value": 0,  "max_value": 21,   "is_primary": 0, "description": "Hospital Anxiety and Depression Scale"},
    {"observation_type_id": 6,  "category": "Clinical Measurement", "name": "MSI",  "value_type": "Numeric", "unit": "score",   "min_value": 1,  "max_value": 5,    "is_primary": 0, "description": "Multiple Sclerosis Impact Scale"},
    {"observation_type_id": 7,  "category": "Intervention",         "name": "Treatment",      "value_type": "Text", "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "Disease Modifying Treatment (drug name)"},
    {"observation_type_id": 8,  "category": "Assessment",           "name": "Diagnosis",      "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "Official disease diagnosis date"},
    {"observation_type_id": 9,  "category": "Assessment",           "name": "Disease Onset",  "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "Date symptoms first appeared"},
    {"observation_type_id": 10, "category": "Assessment",           "name": "SP Transition",  "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "Transition to Secondary Progressive MS"},
    {"observation_type_id": 11, "category": "Imaging",              "name": "MRI Scan",       "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "MRI imaging study performed"},
    {"observation_type_id": 12, "category": "Imaging",              "name": "CT Scan",        "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "CT imaging study performed"},
    {"observation_type_id": 13, "category": "Imaging",              "name": "PET Scan",       "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "PET imaging study performed"},

    # Anthropometric — body measurements recorded over time
    {"observation_type_id": 14, "category": "Anthropometric",       "name": "Weight",         "value_type": "Numeric", "unit": "kg",  "min_value": 0, "max_value": 500, "is_primary": 0, "description": "Body weight"},
    {"observation_type_id": 15, "category": "Anthropometric",       "name": "Height",         "value_type": "Numeric", "unit": "m",   "min_value": 0, "max_value": 3,   "is_primary": 0, "description": "Body height"},

    # Imaging — MEG (parallel track alongside MR/CT/PET, see id 11-13)
    {"observation_type_id": 16, "category": "Imaging",              "name": "MEG Scan",       "value_type": None, "unit": None, "min_value": None, "max_value": None, "is_primary": 0, "description": "MEG recording session performed"},
]


def _needs_migration(conn: Connection) -> bool:
    """Migration is needed when old tables exist OR new table is missing."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    has_old_event_types = "event_types" in tables
    has_old_clinical = "clinical_measure_types" in tables
    has_new = "observation_types" in tables

    if has_old_event_types or has_old_clinical:
        return True

    if has_new:
        columns = {c["name"] for c in inspector.get_columns("event")} if "event" in tables else set()
        if "event_type_id" in columns:
            return True

    if "study" in tables:
        study_cols = {c["name"] for c in inspector.get_columns("study")}
        if "event_id" not in study_cols:
            return True

    if not has_new:
        return True

    # Check if seed data is missing (fresh DB created by Base.metadata.create_all)
    if has_new:
        count = conn.execute(text("SELECT COUNT(*) FROM observation_types")).scalar() or 0
        if count == 0:
            return True

        # A non-empty table only proves *some* prior run of this migration
        # happened -- it does not prove every row in OBSERVATION_TYPES_SEED
        # is present. A deployment migrated before a new seed row was added
        # to this list (e.g. id 16 / "MEG Scan", added alongside the MEG
        # parallel track) would otherwise never get that row, since
        # `_seed_observation_types` is itself per-row idempotent but is
        # never called once this early-return sees count > 0. Check for any
        # missing seed id explicitly so newly-added seed rows always land.
        seed_ids = tuple(row["observation_type_id"] for row in OBSERVATION_TYPES_SEED)
        present_count = conn.execute(
            text("SELECT COUNT(*) FROM observation_types WHERE observation_type_id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": seed_ids},
        ).scalar() or 0
        if present_count < len(seed_ids):
            return True

    return False


def _migrate_clinical_measure_data(conn: Connection) -> int:
    """Copy data from clinical_measure_types into observation_types. Returns rows copied."""
    result = conn.execute(text("SELECT COUNT(*) FROM clinical_measure_types"))
    count = result.scalar() or 0
    if count == 0:
        return 0

    conn.execute(text("""
        INSERT INTO observation_types (
            observation_type_id, category, name, description, unit,
            value_type, min_value, max_value, is_active, is_primary,
            created_at, updated_at
        )
        SELECT
            measure_type_id, category_name, measure_name, description, unit,
            value_type, min_value, max_value, is_active, is_primary,
            created_at, updated_at
        FROM clinical_measure_types
        ON CONFLICT (observation_type_id) DO NOTHING
    """))
    logger.info("Copied %d rows from clinical_measure_types into observation_types", count)
    return count


def _seed_observation_types(conn: Connection) -> int:
    """Insert seed rows that don't already exist. Returns rows inserted."""
    inserted = 0
    for row in OBSERVATION_TYPES_SEED:
        exists = conn.execute(
            text("SELECT 1 FROM observation_types WHERE observation_type_id = :id"),
            {"id": row["observation_type_id"]},
        ).scalar()
        if exists:
            continue
        conn.execute(text("""
            INSERT INTO observation_types (
                observation_type_id, category, name, description, unit,
                value_type, min_value, max_value, is_active, is_primary
            ) VALUES (
                :observation_type_id, :category, :name, :description, :unit,
                :value_type, :min_value, :max_value, :is_active, 1
            )
        """), {
            "observation_type_id": row["observation_type_id"],
            "category": row["category"],
            "name": row["name"],
            "description": row["description"],
            "unit": row["unit"],
            "value_type": row["value_type"],
            "min_value": row["min_value"],
            "max_value": row["max_value"],
            "is_active": 1,
        })
        inserted += 1
    if inserted:
        logger.info("Seeded %d observation type rows", inserted)
    return inserted


def run_migration(engine: Engine, dry_run: bool = False) -> dict:
    """Execute the observation_types migration.

    Args:
        engine: SQLAlchemy engine for the metadata database.
        dry_run: If True, only report what would happen.

    Returns:
        Dict with migration results.
    """
    results: dict = {
        "success": False,
        "already_migrated": False,
        "changes_made": [],
        "elapsed_seconds": 0.0,
    }
    start = time.time()

    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())

        if not _needs_migration(conn):
            results["success"] = True
            results["already_migrated"] = True
            results["elapsed_seconds"] = time.time() - start
            return results

        if dry_run:
            results["success"] = True
            results["changes_made"].append("DRY RUN — no changes applied")
            results["elapsed_seconds"] = time.time() - start
            return results

        # 1. Create observation_types table if it doesn't exist
        if "observation_types" not in tables:
            conn.execute(text("""
                CREATE TABLE observation_types (
                    observation_type_id SERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    unit TEXT,
                    value_type TEXT,
                    min_value DOUBLE PRECISION,
                    max_value DOUBLE PRECISION,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            results["changes_made"].append("Created observation_types table")

        # 2. Migrate data from clinical_measure_types if it exists
        if "clinical_measure_types" in tables:
            copied = _migrate_clinical_measure_data(conn)
            if copied:
                results["changes_made"].append(f"Copied {copied} rows from clinical_measure_types")

        # 3. Seed observation types
        seeded = _seed_observation_types(conn)
        if seeded:
            results["changes_made"].append(f"Seeded {seeded} observation type rows")

        # 4. Rename event.event_type_id → event.observation_type_id
        if "event" in tables:
            event_cols = {c["name"] for c in inspector.get_columns("event")}
            if "event_type_id" in event_cols and "observation_type_id" not in event_cols:
                conn.execute(text(
                    "ALTER TABLE event RENAME COLUMN event_type_id TO observation_type_id"
                ))
                results["changes_made"].append("Renamed event.event_type_id → observation_type_id")

        # 5. Rename measure_type_id → observation_type_id on measure tables
        measure_tables = ["numeric_measures", "text_measures", "boolean_measures", "json_measures"]
        for tbl in measure_tables:
            if tbl not in tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(tbl)}
            if "measure_type_id" in cols and "observation_type_id" not in cols:
                conn.execute(text(
                    f"ALTER TABLE {tbl} RENAME COLUMN measure_type_id TO observation_type_id"
                ))
                results["changes_made"].append(f"Renamed {tbl}.measure_type_id → observation_type_id")

        # 6. Add event_id column to study if missing
        if "study" in tables:
            study_cols = {c["name"] for c in inspector.get_columns("study")}
            if "event_id" not in study_cols:
                conn.execute(text(
                    "ALTER TABLE study ADD COLUMN event_id INTEGER"
                ))
                results["changes_made"].append("Added study.event_id column")

        # 7. Drop old tables
        if "event_types" in tables:
            conn.execute(text("DROP TABLE IF EXISTS event_types CASCADE"))
            results["changes_made"].append("Dropped event_types table")

        if "clinical_measure_types" in tables:
            conn.execute(text("DROP TABLE IF EXISTS clinical_measure_types CASCADE"))
            results["changes_made"].append("Dropped clinical_measure_types table")

    results["success"] = True
    results["elapsed_seconds"] = time.time() - start
    return results
