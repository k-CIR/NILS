"""Database summary API routes.

Performance optimized: Uses batched COUNT queries via UNION ALL
to reduce 31 individual queries to 2 queries (one per database).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from backup.manager import DatabaseKey
from db.session import SessionLocal as AppSessionLocal
from metadata_db.session import SessionLocal as MetadataSessionLocal

from api.models.database import DatabaseSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/database", tags=["database"])

# Database labels for display
DATABASE_LABELS = {
    DatabaseKey.METADATA: "Metadata",
    DatabaseKey.APPLICATION: "Application",
}

# Metadata tables to count (table_key, actual_table_name)
# Table names must match actual PostgreSQL table names exactly
_METADATA_COUNT_TABLES: tuple[tuple[str, str], ...] = (
    ("subjects", "subject"),
    ("cohorts", "cohort"),
    ("subject_cohorts", "subject_cohorts"),
    ("id_types", "id_types"),
    ("subject_other_identifiers", "subject_other_identifiers"),
    ("observation_types", "observation_types"),
    ("events", "event"),
    ("diseases", "diseases"),  # plural
    ("disease_types", "disease_types"),
    ("subject_diseases", "subject_diseases"),  # plural
    ("subject_disease_types", "subject_disease_types"),  # plural
    ("numeric_measures", "numeric_measures"),  # plural
    ("text_measures", "text_measures"),  # plural
    ("boolean_measures", "boolean_measures"),  # plural
    ("json_measures", "json_measures"),  # plural
    ("studies", "study"),
    ("series", "series"),
    ("series_stacks", "series_stack"),
    ("mri_series_details", "mri_series_details"),
    ("ct_series_details", "ct_series_details"),
    ("pet_series_details", "pet_series_details"),
    ("series_classification_cache", "series_classification_cache"),
    ("instances", "instance"),
    ("meg_acquisitions", "meg_acquisition"),
    ("meg_channels", "meg_channel"),
    ("ingest_conflicts", "ingest_conflicts"),  # plural
    ("schema_versions", "schema_version"),
)

# Application tables to count
# Table names must match actual PostgreSQL table names exactly
_APPLICATION_COUNT_TABLES: tuple[tuple[str, str], ...] = (
    ("cohorts", "cohorts"),  # plural
    ("jobs", "jobs"),  # plural
    ("job_runs", "job_runs"),  # plural
    ("anonymize_study_audits", "anonymize_study_audit"),
    ("anonymize_leaf_summaries", "anonymize_leaf_summary"),
)


@lru_cache(maxsize=1)
def _build_metadata_count_query() -> str:
    """Build a single UNION ALL query to count all metadata tables."""
    unions = []
    for key, table_name in _METADATA_COUNT_TABLES:
        unions.append(f"SELECT '{key}' as table_key, COUNT(*) as cnt FROM {table_name}")
    return " UNION ALL ".join(unions)


@lru_cache(maxsize=1)
def _build_application_count_query() -> str:
    """Build a single UNION ALL query to count all application tables."""
    unions = []
    for key, table_name in _APPLICATION_COUNT_TABLES:
        unions.append(f"SELECT '{key}' as table_key, COUNT(*) as cnt FROM {table_name}")
    return " UNION ALL ".join(unions)


def _metadata_table_counts() -> dict[str, int]:
    """Get row counts for metadata database tables in a single batched query."""
    default_counts = {key: 0 for key, _ in _METADATA_COUNT_TABLES}
    try:
        with MetadataSessionLocal() as session:
            query = _build_metadata_count_query()
            result = session.execute(text(query))
            counts = {row[0]: int(row[1]) for row in result.fetchall()}
            # Merge with defaults to ensure all keys present
            return {**default_counts, **counts}
    except Exception as e:
        logger.warning("Failed to get metadata table counts: %s", e)
        return default_counts


def _application_table_counts() -> dict[str, int]:
    """Get row counts for application database tables in a single batched query."""
    default_counts = {key: 0 for key, _ in _APPLICATION_COUNT_TABLES}
    try:
        with AppSessionLocal() as session:
            query = _build_application_count_query()
            result = session.execute(text(query))
            counts = {row[0]: int(row[1]) for row in result.fetchall()}
            # Merge with defaults to ensure all keys present
            return {**default_counts, **counts}
    except Exception as e:
        logger.warning("Failed to get application table counts: %s", e)
        return default_counts


def _build_database_summary(database: DatabaseKey) -> DatabaseSummary:
    """Build summary for a database."""
    counts = _metadata_table_counts() if database is DatabaseKey.METADATA else _application_table_counts()
    return DatabaseSummary(
        database=database.value,
        database_label=DATABASE_LABELS[database],
        tables=counts,
    )


@router.get("/summary", response_model=list[DatabaseSummary])
def database_summary():
    """Get summary of both databases with table row counts."""
    return [
        _build_database_summary(DatabaseKey.METADATA),
        _build_database_summary(DatabaseKey.APPLICATION),
    ]
