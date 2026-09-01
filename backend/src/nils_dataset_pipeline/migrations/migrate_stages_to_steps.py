"""Migration to convert cohorts.stages JSON to nils_dataset_pipeline_steps table.

This migration:
1. Creates the new nils_dataset_pipeline_steps table
2. Migrates existing cohorts.stages JSON data to the new table
3. Migrates sorting_step_handover and sorting_step_metrics data
4. Drops the old tables and columns (after verification)

Run with:
    python -m nils_dataset_pipeline.migrations.migrate_stages_to_steps

Or import and call:
    from nils_dataset_pipeline.migrations.migrate_stages_to_steps import run_migration
    run_migration()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


# =============================================================================
# STEP ORDERING - Import from ordering.py (single source of truth)
# =============================================================================

from ..ordering import PIPELINE_STAGES, get_stage_config

# Build lookup tables from the authoritative source (ordering.py)
STAGE_TITLES = {stage["id"]: stage["title"] for stage in PIPELINE_STAGES}
STAGE_DESCRIPTIONS = {stage["id"]: stage["description"] for stage in PIPELINE_STAGES}

# Get sorting steps from ordering.py
_sort_stage = get_stage_config("sort")
SORTING_STEPS = _sort_stage["steps"] if _sort_stage and _sort_stage["steps"] else []

SORTING_STEP_TITLES = {s["id"]: s["title"] for s in SORTING_STEPS}
SORTING_STEP_DESCRIPTIONS = {s["id"]: s["description"] for s in SORTING_STEPS}


# =============================================================================
# MIGRATION CHECKS
# =============================================================================


def needs_migration(conn: Connection) -> bool:
    """Check if migration is needed.
    
    Returns True if:
    - nils_dataset_pipeline_steps table doesn't exist, OR
    - cohorts.stages column still exists
    """
    # Check if new table exists
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'nils_dataset_pipeline_steps'
        )
    """))
    table_exists = result.scalar()
    
    if not table_exists:
        return True
    
    # Check if old stages column exists
    result = conn.execute(text("""
        SELECT column_name 
        FROM information_schema.columns
        WHERE table_name = 'cohorts' AND column_name = 'stages'
    """))
    stages_column_exists = result.fetchone() is not None
    
    return stages_column_exists


def is_table_empty(conn: Connection, table_name: str) -> bool:
    """Check if a table is empty."""
    result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
    return result.scalar() == 0


# =============================================================================
# CREATE TABLE
# =============================================================================


def create_pipeline_steps_table(conn: Connection) -> None:
    """Create the nils_dataset_pipeline_steps table."""
    logger.info("Creating nils_dataset_pipeline_steps table...")
    
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS nils_dataset_pipeline_steps (
            id SERIAL PRIMARY KEY,
            cohort_id INTEGER NOT NULL REFERENCES cohorts(id) ON DELETE CASCADE,
            stage_id VARCHAR(20) NOT NULL,
            step_id VARCHAR(50),
            title VARCHAR(100) NOT NULL,
            description TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'blocked',
            progress INTEGER NOT NULL DEFAULT 0,
            config JSONB,
            current_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            handover_data JSONB,
            metrics JSONB,
            sort_order INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_nils_pipeline_step UNIQUE(cohort_id, stage_id, step_id)
        )
    """))
    
    # Create indexes
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_nils_pipeline_steps_cohort 
        ON nils_dataset_pipeline_steps(cohort_id)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_nils_pipeline_steps_running 
        ON nils_dataset_pipeline_steps(status) 
        WHERE status = 'running'
    """))
    
    logger.info("Table and indexes created successfully")


# =============================================================================
# DATA MIGRATION
# =============================================================================


def migrate_cohort_stages(conn: Connection) -> int:
    """Migrate cohorts.stages JSON to pipeline_steps rows.
    
    Returns the number of cohorts migrated.
    """
    logger.info("Migrating cohorts.stages to nils_dataset_pipeline_steps...")
    
    # Check if stages column exists
    result = conn.execute(text("""
        SELECT column_name 
        FROM information_schema.columns
        WHERE table_name = 'cohorts' AND column_name = 'stages'
    """))
    if not result.fetchone():
        logger.info("No stages column found - skipping migration")
        return 0
    
    # Get all cohorts with stages
    cohorts = conn.execute(text("""
        SELECT id, name, source_path, anonymization_enabled, stages
        FROM cohorts
        WHERE stages IS NOT NULL
    """)).fetchall()
    
    migrated_count = 0
    
    for cohort_row in cohorts:
        cohort_id = cohort_row[0]
        cohort_name = cohort_row[1]
        source_path = cohort_row[2]
        anonymization_enabled = cohort_row[3]
        stages_json = cohort_row[4]
        
        if not stages_json:
            continue
        
        # Parse stages JSON
        if isinstance(stages_json, str):
            stages = json.loads(stages_json)
        else:
            stages = stages_json
        
        if not stages:
            continue
        
        # Check if this cohort is already migrated
        existing = conn.execute(text("""
            SELECT COUNT(*) FROM nils_dataset_pipeline_steps WHERE cohort_id = :cohort_id
        """), {"cohort_id": cohort_id}).scalar()
        
        if existing > 0:
            logger.info(f"Cohort {cohort_id} already migrated, skipping")
            continue
        
        # Migrate each stage
        sort_order = 0
        
        for stage in stages:
            stage_id = stage.get("id")
            if not stage_id:
                continue
            
            # Check if this is a multi-step stage (sorting)
            stage_steps = stage.get("steps")
            
            if stage_id == "sort" and stage_steps:
                # Multi-step stage - create a row for each step
                for step_id in SORTING_STEP_TITLES.keys():
                    step_status = "blocked"
                    if isinstance(stage_steps, dict):
                        step_status = stage_steps.get(step_id, "blocked")
                    
                    # Get handover and metrics from old tables
                    handover = _get_old_handover(conn, cohort_id, step_id)
                    metrics = _get_old_metrics(conn, cohort_id, step_id)
                    
                    _insert_step(
                        conn,
                        cohort_id=cohort_id,
                        stage_id=stage_id,
                        step_id=step_id,
                        title=SORTING_STEP_TITLES.get(step_id, step_id),
                        description=SORTING_STEP_DESCRIPTIONS.get(step_id, ""),
                        status=step_status,
                        progress=100 if step_status == "completed" else 0,
                        config=stage.get("config") if sort_order == 0 else None,
                        handover_data=handover,
                        metrics=metrics,
                        sort_order=sort_order,
                    )
                    sort_order += 1
            else:
                # Simple stage - single row
                _insert_step(
                    conn,
                    cohort_id=cohort_id,
                    stage_id=stage_id,
                    step_id=None,
                    title=STAGE_TITLES.get(stage_id, stage_id),
                    description=STAGE_DESCRIPTIONS.get(stage_id, ""),
                    status=stage.get("status", "blocked"),
                    progress=stage.get("progress", 0),
                    config=stage.get("config"),
                    handover_data=None,
                    metrics=None,
                    sort_order=sort_order,
                )
                sort_order += 1
        
        migrated_count += 1
        logger.info(f"Migrated cohort {cohort_id} ({cohort_name}): {sort_order} steps")
    
    return migrated_count


def _get_old_handover(conn: Connection, cohort_id: int, step_id: str) -> dict | None:
    """Get handover data from old sorting_step_handover table."""
    result = conn.execute(text("""
        SELECT handover_data FROM sorting_step_handover
        WHERE cohort_id = :cohort_id AND step_id = :step_id
    """), {"cohort_id": cohort_id, "step_id": step_id}).fetchone()
    
    if result:
        data = result[0]
        if isinstance(data, str):
            return json.loads(data)
        return data
    return None


def _get_old_metrics(conn: Connection, cohort_id: int, step_id: str) -> dict | None:
    """Get metrics from old sorting_step_metrics table."""
    result = conn.execute(text("""
        SELECT metrics FROM sorting_step_metrics
        WHERE cohort_id = :cohort_id AND step_id = :step_id
    """), {"cohort_id": cohort_id, "step_id": step_id}).fetchone()
    
    if result:
        data = result[0]
        if isinstance(data, str):
            return json.loads(data)
        return data
    return None


def _insert_step(
    conn: Connection,
    cohort_id: int,
    stage_id: str,
    step_id: str | None,
    title: str,
    description: str,
    status: str,
    progress: int,
    config: dict | None,
    handover_data: dict | None,
    metrics: dict | None,
    sort_order: int,
) -> None:
    """Insert a pipeline step row."""
    conn.execute(text("""
        INSERT INTO nils_dataset_pipeline_steps (
            cohort_id, stage_id, step_id, title, description,
            status, progress, config, handover_data, metrics, sort_order
        ) VALUES (
            :cohort_id, :stage_id, :step_id, :title, :description,
            :status, :progress, :config, :handover_data, :metrics, :sort_order
        )
        ON CONFLICT (cohort_id, stage_id, step_id) DO UPDATE SET
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            status = EXCLUDED.status,
            progress = EXCLUDED.progress,
            config = COALESCE(EXCLUDED.config, nils_dataset_pipeline_steps.config),
            handover_data = COALESCE(EXCLUDED.handover_data, nils_dataset_pipeline_steps.handover_data),
            metrics = COALESCE(EXCLUDED.metrics, nils_dataset_pipeline_steps.metrics),
            sort_order = EXCLUDED.sort_order,
            updated_at = NOW()
    """), {
        "cohort_id": cohort_id,
        "stage_id": stage_id,
        "step_id": step_id,
        "title": title,
        "description": description,
        "status": status,
        "progress": progress,
        "config": json.dumps(config) if config else None,
        "handover_data": json.dumps(handover_data) if handover_data else None,
        "metrics": json.dumps(metrics) if metrics else None,
        "sort_order": sort_order,
    })


# =============================================================================
# CLEANUP (optional - run after verification)
# =============================================================================


def drop_old_tables(conn: Connection) -> None:
    """Drop old tables after successful migration.
    
    WARNING: Only run this after verifying the migration was successful!
    """
    logger.warning("Dropping old tables...")
    
    conn.execute(text("DROP TABLE IF EXISTS sorting_step_handover CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS sorting_step_metrics CASCADE"))
    
    logger.info("Old tables dropped")


def drop_stages_column(conn: Connection) -> None:
    """Drop the stages column from cohorts table.
    
    WARNING: Only run this after verifying the migration was successful!
    """
    logger.warning("Dropping cohorts.stages column...")
    
    conn.execute(text("ALTER TABLE cohorts DROP COLUMN IF EXISTS stages"))
    
    logger.info("Stages column dropped")


# =============================================================================
# SYNC MISSING STEPS (for backward compatibility when new steps are added)
# =============================================================================


def sync_missing_steps(conn: Connection) -> int:
    """Add any missing steps and fix sort_order for existing cohorts.
    
    This handles the case where new steps are added to ordering.py after
    cohorts were already created. It ensures existing cohorts get the new
    steps added to their pipeline AND updates sort_order to match the
    current ordering.py (which is the source of truth).
    
    Returns:
        Number of steps added.
    """
    from ..ordering import get_pipeline_items
    
    logger.info("Checking for missing pipeline steps...")
    
    # Get all cohorts. `modality` may not exist yet on older schemas that
    # haven't run the cohorts.add_cohort_modality_column migration; fall
    # back to "imaging" in that case so this stays safe to run in either
    # order relative to that migration.
    cohort_cols = {c["name"] for c in inspect(conn).get_columns("cohorts")}
    modality_select = "modality" if "modality" in cohort_cols else "NULL AS modality"
    cohorts = conn.execute(text(f"""
        SELECT id, name, anonymization_enabled, {modality_select} FROM cohorts
    """)).fetchall()
    
    if not cohorts:
        return 0
    
    steps_added = 0
    
    for cohort_row in cohorts:
        cohort_id = cohort_row[0]
        cohort_name = cohort_row[1]
        anonymization_enabled = cohort_row[2] if cohort_row[2] is not None else True
        modality = cohort_row[3] if cohort_row[3] else "imaging"
        
        # Get expected steps from ordering.py (source of truth)
        expected_items = get_pipeline_items(anonymization_enabled, modality)
        
        # Build lookup: (stage_id, step_id) -> expected sort_order
        expected_order = {
            (item["stage_id"], item["step_id"]): item["sort_order"]
            for item in expected_items
        }
        
        # Legacy bidsify/convert rows (to be merged into new 'bids' stage)
        legacy_rows = conn.execute(text("""
            SELECT stage_id, status, progress, config, handover_data, metrics
            FROM nils_dataset_pipeline_steps
            WHERE cohort_id = :cohort_id
              AND stage_id IN ('bidsify', 'convert')
        """), {"cohort_id": cohort_id}).fetchall()

        def _pick_legacy(rows: list) -> tuple | None:
            preferred = ["convert", "bidsify"]
            for stage in preferred:
                for row in rows:
                    if row[0] == stage:
                        return row
            return rows[0] if rows else None

        legacy_row = _pick_legacy(legacy_rows)
        legacy_status = legacy_progress = None
        legacy_metrics = legacy_config = None
        if legacy_row:
            legacy_status = legacy_row[1]
            legacy_progress = legacy_row[2]
            raw_cfg = legacy_row[3]
            if isinstance(raw_cfg, str):
                try:
                    legacy_config = json.loads(raw_cfg)
                except Exception:
                    legacy_config = None
            else:
                legacy_config = raw_cfg
            legacy_metrics = legacy_row[5] if len(legacy_row) > 5 else None

        # Get existing steps for this cohort
        existing = conn.execute(text("""
            SELECT stage_id, step_id, sort_order, handover_data
            FROM nils_dataset_pipeline_steps
            WHERE cohort_id = :cohort_id
        """), {"cohort_id": cohort_id}).fetchall()
        
        existing_keys = {(row[0], row[1]) for row in existing}
        
        # Find the last completed step's NEW sort_order (based on ordering.py)
        last_completed_order = -1
        for row in existing:
            if row[3] is not None:  # has handover_data = completed
                key = (row[0], row[1])
                if key in expected_order:
                    new_order = expected_order[key]
                    if new_order > last_completed_order:
                        last_completed_order = new_order
        
        # Update sort_order for existing steps to match ordering.py
        for row in existing:
            key = (row[0], row[1])
            old_order = row[2]
            if key in expected_order:
                new_order = expected_order[key]
                if old_order != new_order:
                    conn.execute(text("""
                        UPDATE nils_dataset_pipeline_steps
                        SET sort_order = :new_order, updated_at = NOW()
                        WHERE cohort_id = :cohort_id 
                          AND stage_id = :stage_id 
                          AND (step_id = :step_id OR (step_id IS NULL AND :step_id IS NULL))
                    """), {
                        "cohort_id": cohort_id,
                        "stage_id": key[0],
                        "step_id": key[1],
                        "new_order": new_order,
                    })
                    logger.info(
                        f"Updated sort_order for '{key[1] or key[0]}' in cohort {cohort_id}: {old_order} -> {new_order}"
                    )
        
        # Add missing steps
        for item in expected_items:
            key = (item["stage_id"], item["step_id"])
            if key not in existing_keys:
                metrics = None
                # If this is the new combined bids stage, carry legacy state forward
                if item["stage_id"] == "bids" and legacy_status is not None:
                    status = legacy_status
                    progress = legacy_progress or (100 if legacy_status == "completed" else 0)
                    metrics = legacy_metrics
                else:
                    # Determine status: pending if it's the next step after completed, else blocked
                    if item["sort_order"] == last_completed_order + 1:
                        status = "pending"
                        progress = 5
                    else:
                        status = "blocked"
                        progress = 0

                _insert_step(
                    conn,
                    cohort_id=cohort_id,
                    stage_id=item["stage_id"],
                    step_id=item["step_id"],
                    title=item["title"],
                    description=item["description"],
                    status=status,
                    progress=progress,
                    config=legacy_config if item["stage_id"] == "bids" else None,
                    handover_data=None,
                    metrics=metrics,
                    sort_order=item["sort_order"],
                )
                steps_added += 1
                logger.info(
                    f"Added missing step '{item['step_id'] or item['stage_id']}' to cohort {cohort_id} ({cohort_name})"
                )

        # Clean up legacy rows to avoid duplicate stages
        if legacy_rows:
            conn.execute(text("""
                DELETE FROM nils_dataset_pipeline_steps
                WHERE cohort_id = :cohort_id
                  AND stage_id IN ('bidsify', 'convert')
            """), {"cohort_id": cohort_id})
    
    if steps_added > 0:
        logger.info(f"Added {steps_added} missing steps across all cohorts")
    else:
        logger.info("All cohorts have complete pipeline steps")
    
    return steps_added


# =============================================================================
# MAIN MIGRATION FUNCTION
# =============================================================================


def run_migration(drop_old: bool = False) -> None:
    """Run the full migration.
    
    Args:
        drop_old: If True, drop old tables/columns after migration.
                  Default False for safety.
    """
    from db.session import engine
    
    with engine.begin() as conn:
        if not needs_migration(conn):
            logger.info("Migration already applied, skipping")
            return
        
        # Create new table
        create_pipeline_steps_table(conn)
        
        # Migrate data
        migrated = migrate_cohort_stages(conn)
        logger.info(f"Migrated {migrated} cohorts")
        
        # Optionally drop old structures
        if drop_old:
            drop_old_tables(conn)
            drop_stages_column(conn)
        else:
            logger.info(
                "Old tables/columns retained. Run with drop_old=True to remove them, "
                "or manually drop after verification."
            )
    
    logger.info("Migration complete!")


def ensure_migrated() -> None:
    """Ensure migration has been applied (safe to call multiple times).
    
    This is called during application startup. It:
    1. Creates the pipeline_steps table if it doesn't exist
    2. Migrates old cohorts.stages JSON data if present
    3. Syncs any missing steps to existing cohorts (for backward compatibility
       when new steps are added to ordering.py)
    """
    from db.session import engine
    
    with engine.begin() as conn:
        if needs_migration(conn):
            logger.info("Running pipeline migration...")
            create_pipeline_steps_table(conn)
            migrate_cohort_stages(conn)
            logger.info("Pipeline migration complete")
        
        # Always sync missing steps (handles new steps added to ordering.py)
        sync_missing_steps(conn)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration(drop_old=False)
