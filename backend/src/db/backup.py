"""Application database backup management with migration support."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from db.config import get_backup_settings

from backup.manager import BackupError, DatabaseKey, PostgresBackupManager, get_backup_config

logger = logging.getLogger(__name__)


class ApplicationBackupManager(PostgresBackupManager):
    """Application database backup manager with automatic post-restore migrations."""

    def __init__(self) -> None:
        self.backup_settings = get_backup_settings()
        super().__init__(get_backup_config(DatabaseKey.APPLICATION))

    def create_backup(self, directory: str | Path | None = None, *, note: str | None = None) -> Path:
        return super().create_backup(directory=directory, note=note)

    def _drop_dependent_tables(self) -> None:
        """Drop tables with FK dependencies that may block pg_restore.

        When restoring old backups that don't have certain tables (like job_runs),
        pg_restore fails because it can't drop tables that have dependents.

        We drop these newer tables first, then let migrations recreate them.
        """
        from sqlalchemy import text
        from db.session import engine

        # Tables to drop (in dependency order - children first)
        tables_to_drop = [
            "qc_draft_changes",  # Has FK to qc_items (QC pipeline, added Dec 2025)
            "qc_items",  # Has FK to qc_sessions (QC pipeline, added Dec 2025)
            "qc_sessions",  # QC pipeline (added Dec 2025)
            "job_runs",  # Has FK to jobs table
            "nils_dataset_pipeline_steps",  # Added in later migration
        ]
        
        logger.info("Dropping dependent tables before restore...")
        
        with engine.begin() as conn:
            for table in tables_to_drop:
                try:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
                    logger.info("Dropped table: %s", table)
                except Exception as e:
                    logger.warning("Failed to drop table %s: %s", table, e)

    def restore(self, dump_path: str | Path | None = None) -> Path:
        """Restore application database and apply migrations for schema compatibility.
        
        This ensures that restored backups (potentially from older versions)
        are automatically brought up-to-date with the current schema via migrations.
        
        The restore process:
        1. Drop tables with FK dependencies that may block pg_restore
        2. Run pg_restore to restore the backup
        3. Apply migrations to bring schema up-to-date
        
        Args:
            dump_path: Path to backup file (or None for latest)
            
        Returns:
            Path to the restored backup file
        """
        # Pre-restore: drop dependent tables
        self._drop_dependent_tables()
        
        def post_restore_migrations():
            """Apply any pending migrations to restored database."""
            logger.info("Applying migrations to restored application database...")
            try:
                _ensure_application_schema()
                logger.info("Migrations applied successfully - application database schema is current")
            except Exception as exc:
                logger.error("Migration failed after restore: %s", exc)
                raise
        
        return super().restore(dump_path, post_restore_hook=post_restore_migrations)

    def latest_backup(self) -> Path | None:
        return super().latest_backup()

    def auto_restore_if_empty(self, is_empty: bool) -> bool:
        """Auto-restore from latest backup if database is empty."""
        if not is_empty or not self.backup_settings.auto_restore:
            return False
        try:
            latest = self.ensure_backup_path(None)
        except BackupError:
            return False
        try:
            # Uses our overridden restore() which includes post_restore_hook
            self.restore(latest)
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


def _ensure_application_schema() -> None:
    """Ensure application database schema is up-to-date by running migrations.

    This recreates tables that may have been dropped during restore or
    weren't in older backups:
    - jobs and job_runs tables (jobs module has its own Base)
    - nils_dataset_pipeline_steps table (with all steps synced from ordering.py)
    - qc_sessions, qc_items, qc_draft_changes tables (QC pipeline)
    - Missing columns on existing tables (e.g., cohorts.total_subjects)

    Important: jobs/models.py has its own Base class separate from cohorts/models.py.
    We must create jobs tables FIRST since other tables (like nils_dataset_pipeline_steps)
    have foreign keys to the jobs table.
    """
    from sqlalchemy import text
    from db.session import engine

    # IMPORTANT: Create jobs tables FIRST - they have their own Base class
    # and other tables depend on them via foreign keys
    from jobs.models import Base as JobsBase
    JobsBase.metadata.create_all(engine, checkfirst=True)
    logger.info("Ensured jobs tables exist (jobs, job_runs)")

    # Now create cohorts-related tables (cohorts, nils_dataset_pipeline_steps, qc tables)
    from cohorts.models import Base as CohortsBase

    # Import QC models to ensure they're registered with CohortsBase.metadata
    try:
        from qc.models import QCSession, QCItem, QCDraftChange  # noqa: F401
    except ImportError:
        logger.warning("QC models not available - skipping QC table creation")

    # Import analysis-pipeline models to ensure they're registered with
    # CohortsBase.metadata (analysis_pipeline_repos / analysis_pipelines /
    # analysis_pipeline_runs). No Alembic — create_all(checkfirst=True) below.
    try:
        from analysis_pipeline import models as _ap_models  # noqa: F401
    except ImportError:
        logger.warning(
            "Analysis-pipeline models not available - skipping table creation"
        )

    # Import facility-discovery models to ensure they're registered with
    # CohortsBase.metadata (facility_subject_mappings / facility_discoveries),
    # so a restore of a backup taken before this feature shipped still gets
    # these tables recreated. (`projects.models.Project` is already
    # guaranteed to be registered here too, since `cohorts.models` imports it
    # at its own module level for `Cohort.project_id`'s foreign key.)
    try:
        from facility_discovery import models as _facility_discovery_models  # noqa: F401
    except ImportError:
        logger.warning(
            "Facility-discovery models not available - skipping table creation"
        )

    CohortsBase.metadata.create_all(engine, checkfirst=True)
    logger.info("Ensured cohorts and related tables exist")

    # Add missing columns to cohorts table (for backward compatibility with old backups)
    with engine.begin() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE cohorts ADD COLUMN IF NOT EXISTS total_subjects INTEGER NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "ALTER TABLE cohorts ADD COLUMN IF NOT EXISTS total_sessions INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Ensured cohorts table has all required columns")
        except Exception as exc:
            logger.warning("Could not add missing cohorts columns: %s", exc)

    # Use the proper pipeline migration which:
    # 1. Creates the nils_dataset_pipeline_steps table with correct schema
    # 2. Syncs any missing steps to existing cohorts (backward compatibility)
    try:
        from nils_dataset_pipeline.migrations import ensure_migrated
        ensure_migrated()
        logger.info("Pipeline steps table ensured and synced")
    except Exception as exc:
        logger.error("Pipeline migration failed: %s", exc)
        raise RuntimeError(f"Pipeline migration failed: {exc}") from exc
