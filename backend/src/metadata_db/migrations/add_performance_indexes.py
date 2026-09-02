"""
Migration to add performance indexes for DICOM viewer queries.

This migration adds indexes on columns frequently used in WHERE/JOIN clauses
for DICOM loading, QC workflows, and classification queries.

All indexes use CREATE INDEX IF NOT EXISTS, making this migration idempotent
and safe for repeated execution after backup restore.

Usage:
    Runs automatically on server startup via lifecycle.py
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

# List of indexes to create: (index_name, table_name, column_expression)
INDEXES = [
    # High Priority - DICOM Viewer Performance
    # Supports get_series_instance_ids() queries with stack filtering and ordering
    (
        "idx_instance_series_uid_stack",
        "instance",
        "series_instance_uid, series_stack_id, slice_location, instance_number",
    ),
    # Supports get_image_comments_for_stack() filtering by stack
    ("idx_instance_stack_id", "instance", "series_stack_id"),

    # High Priority - Classification Cache Lookups
    # Supports axes_service queries that lookup by stack_id
    ("idx_scc_series_stack_id", "series_classification_cache", "series_stack_id"),
    # Supports get_axes_qc_items() filtering for items needing review
    (
        "idx_scc_manual_review",
        "series_classification_cache",
        "manual_review_required, dicom_origin_cohort",
    ),

    # Medium Priority - Subject/Session Queries
    # Supports get_stacks_for_session() joining series with subjects
    ("idx_series_subject_study", "series", "subject_id, study_id"),
    # Supports get_sister_series() filtering by study and modality
    ("idx_series_study_modality", "series", "study_id, modality"),
    # Supports get_sessions_for_subject() grouping by date
    ("idx_study_subject_date", "study", "subject_id, study_date"),

    # Medium Priority - Cohort Queries
    # Supports get_subjects_for_cohort() joining by cohort
    ("idx_subject_cohorts_cohort_subject", "subject_cohorts", "cohort_id, subject_id"),
    # Supports joining stack_fingerprint in classification queries
    ("idx_stack_fingerprint_stack_id", "stack_fingerprint", "series_stack_id"),

    # =========================================================================
    # Cohort Page & Database Management Performance (added for cohort/db pages)
    # =========================================================================
    # Supports cohort stats queries - single column index for WHERE cohort_id = ?
    ("idx_subject_cohorts_cohort_id", "subject_cohorts", "cohort_id"),
    # Supports study lookups by subject for cohort stats
    ("idx_study_subject_id", "study", "subject_id"),
    # Supports series lookups by study for cohort stats
    ("idx_series_study_id", "series", "study_id"),
    # Supports series lookups by subject
    ("idx_series_subject_id", "series", "subject_id"),
    # Supports subject identifier queries in database explorer
    ("idx_subject_other_id_subject", "subject_other_identifiers", "subject_id"),
    ("idx_subject_other_id_type", "subject_other_identifiers", "id_type_id"),

    # =========================================================================
    # BIDS Export Performance (added to fix timeout on large cohorts)
    # =========================================================================
    # Critical for BIDS export WHERE clause filtering by cohort
    # Without this, export query scans ALL stacks across ALL cohorts
    ("idx_scc_dicom_origin_cohort", "series_classification_cache", "dicom_origin_cohort"),
    # Covering index for ARRAY_AGG(dicom_file_path ORDER BY instance_number)
    # Enables index-only scans, avoiding heap access for millions of instance rows
    (
        "idx_instance_stack_path_order",
        "instance",
        "series_stack_id, instance_number, dicom_file_path",
    ),

    # =========================================================================
    # MEG Parallel Track Performance
    # =========================================================================
    # Supports meg_scan upserts and study->meg_acquisition joins (cohort stats)
    ("idx_meg_acquisition_study_id", "meg_acquisition", "study_id"),
    # Supports subject-scoped MEG acquisition lookups
    ("idx_meg_acquisition_subject_id", "meg_acquisition", "subject_id"),
    # Supports meg_bids stage queries filtering by conversion status
    ("idx_meg_acquisition_bids_status", "meg_acquisition", "bids_status"),
    # Supports channels.tsv lookups for a given acquisition
    ("idx_meg_channel_acquisition_id", "meg_channel", "meg_acquisition_id"),
]


def _get_existing_indexes(conn: Connection) -> set[str]:
    """Get names of all existing indexes in public schema."""
    result = conn.execute(
        text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    )
    return {row[0] for row in result.fetchall()}


def _needs_migration(conn: Connection) -> bool:
    """Check if any indexes need to be created."""
    existing = _get_existing_indexes(conn)
    return not all(idx[0] in existing for idx in INDEXES)


def migrate(engine: Engine, dry_run: bool = False) -> dict:
    """
    Add performance indexes to metadata database.

    Args:
        engine: SQLAlchemy engine for metadata database
        dry_run: If True, only check if migration is needed without applying

    Returns:
        Dict with migration results:
        {
            "success": bool,
            "already_migrated": bool,
            "indexes_created": list[str],
            "indexes_skipped": list[str],
            "elapsed_seconds": float
        }
    """
    results = {
        "success": False,
        "already_migrated": False,
        "indexes_created": [],
        "indexes_skipped": [],
        "elapsed_seconds": 0.0,
    }

    start_time = time.time()

    with engine.begin() as conn:
        existing = _get_existing_indexes(conn)

        # Check if all indexes already exist
        if all(idx[0] in existing for idx in INDEXES):
            logger.info("Performance indexes migration not needed (all indexes exist)")
            results["already_migrated"] = True
            results["success"] = True
            results["elapsed_seconds"] = time.time() - start_time
            return results

        if dry_run:
            missing = [idx[0] for idx in INDEXES if idx[0] not in existing]
            logger.info("DRY RUN: Would create %d indexes: %s", len(missing), missing)
            results["success"] = True
            results["elapsed_seconds"] = time.time() - start_time
            return results

        logger.info("Starting performance indexes migration...")

    # Create missing indexes - use separate transactions for each to avoid
    # one failure aborting all subsequent index creations
    for idx_name, table, columns in INDEXES:
        if idx_name in existing:
            results["indexes_skipped"].append(idx_name)
            logger.debug("Skipping existing index: %s", idx_name)
            continue

        try:
            with engine.begin() as idx_conn:
                # Check if table exists before creating index
                inspector = inspect(idx_conn)
                if table not in inspector.get_table_names():
                    logger.warning(
                        "Skipping index %s: table %s does not exist", idx_name, table
                    )
                    results["indexes_skipped"].append(f"{idx_name} (table missing)")
                    continue

                # Check if all columns exist in the table
                table_columns = {c["name"] for c in inspector.get_columns(table)}
                index_columns = [c.strip() for c in columns.split(",")]
                missing_cols = [c for c in index_columns if c not in table_columns]
                if missing_cols:
                    logger.warning(
                        "Skipping index %s: columns %s do not exist in table %s",
                        idx_name, missing_cols, table
                    )
                    results["indexes_skipped"].append(f"{idx_name} (columns missing: {missing_cols})")
                    continue

                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({columns})"
                idx_conn.execute(text(sql))
                results["indexes_created"].append(idx_name)
                logger.info("Created index: %s on %s(%s)", idx_name, table, columns)
        except Exception as e:
            logger.warning("Failed to create index %s: %s", idx_name, e)
            results["indexes_skipped"].append(f"{idx_name} (error: {e})")

    results["success"] = True
    results["elapsed_seconds"] = time.time() - start_time

    logger.info(
        "Performance indexes migration completed: %d created, %d skipped (%.1fs)",
        len(results["indexes_created"]),
        len(results["indexes_skipped"]),
        results["elapsed_seconds"],
    )

    return results


def get_status(engine: Engine) -> dict:
    """
    Get the current migration status.

    Returns:
        Dict with status info:
        {
            "migrated": bool,
            "indexes_present": list[str],
            "indexes_missing": list[str],
        }
    """
    status = {
        "migrated": False,
        "indexes_present": [],
        "indexes_missing": [],
    }

    with engine.connect() as conn:
        existing = _get_existing_indexes(conn)

        for idx_name, _, _ in INDEXES:
            if idx_name in existing:
                status["indexes_present"].append(idx_name)
            else:
                status["indexes_missing"].append(idx_name)

        status["migrated"] = len(status["indexes_missing"]) == 0

    return status
