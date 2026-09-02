"""Compatibility wrappers around the shared backup management utilities."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from metadata_db.config import get_backup_settings

from backup.manager import BackupError, DatabaseKey, PostgresBackupManager, get_backup_config

logger = logging.getLogger(__name__)

# Tables that must be dropped BEFORE pg_restore to avoid FK dependency errors.
# pg_restore --clean tries to DROP TABLE without CASCADE, which fails when
# other tables have foreign keys referencing the table being dropped.
#
# Order matters: drop tables with FKs first (children), then tables they reference (parents).
# This is the reverse of creation order.
TABLES_TO_DROP_BEFORE_RESTORE = [
    # Leaf tables (no incoming FKs)
    "ingest_conflicts",
    "series_classification_cache",
    "stack_fingerprint",
    # Instance depends on series and series_stack
    "instance",
    # Modality-specific details depend on series
    "mri_series_details",
    "ct_series_details",
    "pet_series_details",
    # series_stack depends on series
    "series_stack",
    # series depends on study and subject
    "series",
    # meg_channel depends on meg_acquisition
    "meg_channel",
    # meg_acquisition depends on study and subject
    "meg_acquisition",
    # study depends on subject
    "study",
    # Clinical/metadata tables
    "json_measures",
    "boolean_measures",
    "text_measures",
    "numeric_measures",
    "subject_disease_types",
    "subject_diseases",
    "disease_types",
    "diseases",
    "event",
    "observation_types",
    "subject_other_identifiers",
    "id_types",
    "subject_cohorts",
    "cohort",
    # Root table (no outgoing FKs to other data tables)
    "subject",
    # Schema versioning
    "schema_version",
]


class MetadataBackupManager(PostgresBackupManager):
    """Backward-compatible manager dedicated to the metadata database."""

    def __init__(self) -> None:
        self.backup_settings = get_backup_settings()
        super().__init__(get_backup_config(DatabaseKey.METADATA))

    def create_backup(self, directory: str | Path | None = None, *, note: str | None = None) -> Path:
        return super().create_backup(directory=directory, note=note)

    def _drop_tables_before_restore(self) -> None:
        """Drop all metadata tables to allow clean pg_restore.

        pg_restore --clean tries to DROP TABLE without CASCADE, which fails when
        tables have foreign key constraints. By dropping all tables first in the
        correct dependency order, we ensure pg_restore can recreate them cleanly.

        The table order in TABLES_TO_DROP_BEFORE_RESTORE is critical:
        - Children (tables with FKs) must be dropped before parents
        - This is the reverse of the creation order
        """
        from .session import SessionLocal
        from sqlalchemy import text

        logger.info("Dropping all metadata tables before restore...")

        with SessionLocal() as session:
            for table_name in TABLES_TO_DROP_BEFORE_RESTORE:
                try:
                    # Use CASCADE as a safety net for any unlisted dependencies
                    session.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                    logger.info("Dropped table: %s", table_name)
                except Exception as exc:
                    logger.warning("Failed to drop table %s: %s", table_name, exc)
            session.commit()

        logger.info("Dropped %d tables", len(TABLES_TO_DROP_BEFORE_RESTORE))

    def restore(self, dump_path: str | Path | None = None) -> Path:
        """Restore metadata database and apply migrations for schema compatibility.
        
        This ensures that restored backups (potentially from older versions)
        are automatically brought up-to-date with the current schema via migrations.
        
        The restore process:
        1. Drop tables created by migrations (to avoid FK conflicts)
        2. Run pg_restore --clean --if-exists
        3. Re-apply migrations to bring schema up to date
        
        Args:
            dump_path: Path to backup file (or None for latest)
            
        Returns:
            Path to the restored backup file
        """
        # Step 1: Drop all tables to avoid FK dependency errors during pg_restore --clean
        self._drop_tables_before_restore()
        
        def post_restore_migrations():
            """Apply any pending migrations to restored database."""
            from .lifecycle import ensure_schema
            
            logger.info("Applying migrations to restored database...")
            try:
                ensure_schema()
                logger.info("Migrations applied successfully - database schema is current")
            except Exception as exc:
                logger.error("Migration failed after restore: %s", exc)
                raise
        
        # Step 2 & 3: Restore and apply migrations
        return super().restore(dump_path, post_restore_hook=post_restore_migrations)

    def latest_backup(self) -> Path | None:
        return super().latest_backup()

    def auto_restore_if_empty(self, is_empty: bool) -> bool:
        if not is_empty or not self.backup_settings.auto_restore:
            return False
        try:
            latest = self.ensure_backup_path(None)
        except BackupError:
            return False
        try:
            super().restore(latest)
            return True
        except BackupError:
            return False

    def list_backups(self) -> Iterable[Path]:
        return super().list_backups()

    def clear_backups(self) -> None:
        for dump in list(self.list_backups()):
            try:
                Path(dump).unlink()
            except OSError:
                pass
