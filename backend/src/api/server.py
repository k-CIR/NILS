"""FastAPI application factory for NILS (Neuroimaging Intelligent Linked System) API."""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from logging_config import configure_logging

# Patchable imports for testing
from metadata_db.lifecycle import bootstrap as metadata_bootstrap
from api.stage_sync import reconcile_stage_jobs
from db.backup import _ensure_application_schema

# Re-exports for backward compatibility with tests
from api.utils.csv import CSV_UPLOAD_DIR
from metadata_db.session import SessionLocal as MetadataSessionLocal

configure_logging()
logger = logging.getLogger(__name__)

# Thread pool for background jobs (restore, backup, bids/subset export)
_job_executor = ThreadPoolExecutor(max_workers=4)

# Backward-compatible alias; prefer ``_job_executor`` for new code.
_restore_executor = _job_executor


def parse_data_roots() -> List[Path]:
    """Parse DATA_ROOTS from env (JSON array) or fallback to single DATA_ROOT."""
    roots_json = os.getenv("DATA_ROOTS")
    if roots_json:
        try:
            roots_list = json.loads(roots_json)
            return [Path(r).resolve() for r in roots_list]
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Fallback to single DATA_ROOT
    single_root = os.getenv("DATA_ROOT", "/app/data")
    return [Path(single_root).resolve()]


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NILS API",
        description="Backend API for neuroimaging data management and processing pipelines",
        version="0.1.0",
    )
    
    # Store executor in app state for background jobs (restore/backup/export)
    app.state.job_executor = _job_executor
    app.state.restore_executor = _job_executor

    # Ensure application database schema (cohorts/jobs/qc/pipeline steps) exists before routes
    try:
        _ensure_application_schema()
    except Exception:
        logger.exception("Application database bootstrap failed")
        raise

    # Bootstrap metadata database
    try:
        metadata_bootstrap()
    except Exception:
        logger.exception("Metadata bootstrap failed")
        raise

    # Enable CORS for frontend development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:5174"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Enable GZip compression for responses (60-80% size reduction for JSON)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Parse data roots for file browsing
    data_roots = parse_data_roots()

    # Import all route modules
    from api.routes import (
        system_router,
        files_router,
        backups_router,
        csv_router,
        database_router,
        metadata_tables_router,
        application_tables_router,
        id_types_router,
        jobs_router,
        cohorts_router,
        cohort_keywords_router,
        cohort_main_qc_router,
        cohort_body_part_qc_router,
        body_part_models_router,
        metadata_subjects_router,
        metadata_cohorts_router,
        imports_router,
        export_router,
        analysis_pipelines_router,
        facility_discovery_router,
    )
    from api.routes.qc import router as qc_router
    from api.routes.files import set_data_roots
    
    # Configure file browsing data roots
    set_data_roots(data_roots)
    
    # Register all routers
    app.include_router(system_router)
    app.include_router(files_router)
    app.include_router(backups_router)
    app.include_router(csv_router)
    app.include_router(database_router)
    app.include_router(metadata_tables_router)
    app.include_router(application_tables_router)
    app.include_router(id_types_router)
    app.include_router(jobs_router)
    app.include_router(cohorts_router)
    app.include_router(cohort_keywords_router)
    app.include_router(cohort_main_qc_router)
    app.include_router(cohort_body_part_qc_router)
    app.include_router(body_part_models_router)
    app.include_router(metadata_subjects_router)
    app.include_router(metadata_cohorts_router)
    app.include_router(imports_router)
    app.include_router(export_router)
    app.include_router(analysis_pipelines_router)
    app.include_router(facility_discovery_router)
    app.include_router(qc_router)

    # Reconcile stage-job status at startup
    reconcile_stage_jobs()

    return app


def main():
    """Run the API server."""
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
