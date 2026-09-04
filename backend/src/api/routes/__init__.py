"""API route modules."""
from api.routes.system import router as system_router
from api.routes.files import router as files_router  
from api.routes.backups import router as backups_router
from api.routes.csv import router as csv_router
from api.routes.database import router as database_router
from api.routes.metadata_tables import router as metadata_tables_router
from api.routes.application_tables import router as application_tables_router
from api.routes.id_types import router as id_types_router
from api.routes.jobs import router as jobs_router
from api.routes.cohorts import router as cohorts_router
from api.routes.cohort_keywords import router as cohort_keywords_router
from api.routes.cohort_main_qc import router as cohort_main_qc_router
from api.routes.cohort_body_part_qc import router as cohort_body_part_qc_router
from api.routes.body_part_models import router as body_part_models_router
from api.routes.metadata_subjects import router as metadata_subjects_router
from api.routes.metadata_cohorts import router as metadata_cohorts_router
from api.routes.imports import router as imports_router
from api.routes.export import router as export_router
from api.routes.analysis_pipelines import router as analysis_pipelines_router
from api.routes.facility_discovery import router as facility_discovery_router

__all__ = [
    "system_router",
    "files_router",
    "backups_router",
    "csv_router",
    "database_router",
    "metadata_tables_router",
    "application_tables_router",
    "id_types_router",
    "jobs_router",
    "cohorts_router",
    "cohort_keywords_router",
    "cohort_main_qc_router",
    "cohort_body_part_qc_router",
    "body_part_models_router",
    "metadata_subjects_router",
    "metadata_cohorts_router",
    "imports_router",
    "export_router",
    "analysis_pipelines_router",
    "facility_discovery_router",
]
