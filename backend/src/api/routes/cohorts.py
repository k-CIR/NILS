"""Cohort management and stage execution API routes."""
from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse

from cohorts.service import cohort_service
from cohorts.models import CreateCohortPayload
from jobs.service import job_service
from jobs.models import JobStatus
from jobs.control import JobControl
from jobs.errors import JobCancelledError
from extract import DuplicatePolicy, ExtensionMode, ExtractionConfig, run_extraction
from extract.progress import ExtractionProgressTracker
from extract.subject_mapping import load_subject_code_csv
from metadata_db.schema import IdType
from metadata_db.session import SessionLocal as MetadataSessionLocal
from sqlalchemy import select

from api.utils.serializers import serialize_job, serialize_jobs, serialize_jobs_slim
from api.utils.csv import csv_file_path, sanitize_csv_token
from api.stage_sync import start_pipeline_step, complete_pipeline_step, fail_pipeline_step
from nils_dataset_pipeline import nils_pipeline_service
from nils_dataset_pipeline.ordering import get_step_ids_for_stage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cohorts", tags=["cohorts"])


def _get_cohort_metrics():
    """Get cohort metrics function for job serialization."""
    from metadata_db.metrics import get_cohort_metrics
    return get_cohort_metrics


def _validate_subject_id_type_id(subject_id_type_id: int | None) -> int | None:
    """Validate that subject ID type exists."""
    if subject_id_type_id is None:
        return None
    with MetadataSessionLocal() as session:
        stmt = select(IdType).where(IdType.id_type_id == subject_id_type_id)
        record = session.execute(stmt).scalar_one_or_none()
        if record is None:
            raise HTTPException(status_code=400, detail=f"Unknown subject ID type: {subject_id_type_id}")
    return subject_id_type_id


def _load_subject_code_mapping(config: dict | None) -> tuple[dict[str, str], str | None]:
    """Load subject code CSV mapping."""
    if not config:
        return {}, None

    token = config.get('fileToken') or config.get('file_token')
    path_value = config.get('filePath') or config.get('file_path') or config.get('path')
    csv_path: Path
    filename: str

    if token:
        csv_path = csv_file_path(sanitize_csv_token(token))
        if not csv_path.exists():
            raise HTTPException(status_code=404, detail="Subject code CSV not found")
        filename = csv_path.name
    elif path_value:
        csv_path = Path(path_value).expanduser().resolve()
        if not csv_path.exists():
            raise HTTPException(status_code=404, detail="Subject code CSV not found")
        filename = csv_path.name
    else:
        raise HTTPException(status_code=400, detail="Subject code CSV requires fileToken or filePath")

    patient_column = (config.get('patientColumn') or config.get('patient_column') or '').strip()
    subject_column = (config.get('subjectCodeColumn') or config.get('subject_code_column') or '').strip()

    if not patient_column or not subject_column:
        raise HTTPException(status_code=400, detail="Subject code CSV requires patientColumn and subjectCodeColumn")

    try:
        mapping = load_subject_code_csv(csv_path, patient_column, subject_column)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Subject code CSV not found") from None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse subject code CSV: {exc}") from exc

    return mapping, filename


def parse_tag_codes(scrubbed_codes: list[str]) -> list[tuple[int, int]]:
    """Parse frontend tag codes like '0008,0090' into tuples."""
    tags = []
    for code in scrubbed_codes:
        clean = code.strip().replace('(', '').replace(')', '').replace(' ', '')
        if ',' in clean:
            parts = clean.split(',')
            if len(parts) == 2:
                try:
                    group = int(parts[0], 16)
                    element = int(parts[1], 16)
                    tags.append((group, element))
                except ValueError:
                    continue
    return tags


@router.get("")
def list_cohorts():
    """List all cohorts."""
    cohorts = cohort_service.list_cohorts()
    return JSONResponse([c.model_dump(mode="json") for c in cohorts])


@router.post("")
def create_cohort(payload: CreateCohortPayload):
    """Create a new cohort."""
    try:
        cohort = cohort_service.create_cohort(payload)
        return JSONResponse(cohort.model_dump(mode="json"), status_code=201)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{cohort_id}")
def get_cohort(cohort_id: int):
    """Get a single cohort by ID with job history.

    Performance optimized:
    - Uses get_cohort_metrics_fast() to skip expensive instance COUNT on 30M rows
    - Uses serialize_jobs_slim() to reduce response payload size by ~90%
    - Jobs are serialized without full config/metrics (use /jobs/{id} for full details)
    """
    from metadata_db.metrics import get_cohort_metrics_fast

    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    payload = cohort.model_dump(mode="json")

    # Add anonymize job history (slim format - no full config/metrics)
    anonymize_history_models = job_service.list_jobs_for_stage(cohort_id, "anonymize", limit=10)
    anonymize_history = serialize_jobs_slim(anonymize_history_models)
    payload["anonymize_job"] = anonymize_history[0] if anonymize_history else None
    payload["anonymize_history"] = anonymize_history

    # Add extract job history (slim format - no full config/metrics)
    extract_history_models = job_service.list_jobs_for_stage(cohort_id, "extract", limit=10)
    extract_history = serialize_jobs_slim(extract_history_models)
    payload["extract_job"] = extract_history[0] if extract_history else None
    payload["extract_history"] = extract_history

    return JSONResponse(payload)


@router.get("/{cohort_id}/examples/folders")
def get_cohort_folder_examples(
    cohort_id: int,
    limit: int = Query(default=1, ge=1, le=10),
    include_files: bool = Query(default=True),
):
    """Get example folder/file paths from cohort source for anonymization config."""
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    selected_root = Path(cohort.source_path)
    if not selected_root.exists() or not selected_root.is_dir():
        return JSONResponse({"paths": []})

    from anonymize.config import setup_derivatives_folders

    setup = setup_derivatives_folders(selected_root)
    source_root = setup.source_path
    if not source_root.exists():
        return JSONResponse({"paths": []})

    def sample_subject_with_file(root: Path) -> list[str]:
        subjects = [p for p in root.iterdir() if p.is_dir()]
        random.shuffle(subjects)
        results: list[str] = []

        for subject in subjects:
            subject_rel = subject.relative_to(root)
            subject_path = str(subject_rel).replace(os.sep, '/')
            chosen_file: Optional[str] = None
            if include_files:
                file_candidates = []
                for dirpath, _, filenames in os.walk(subject):
                    for filename in filenames:
                        rel_file = (Path(dirpath) / filename).relative_to(root)
                        file_candidates.append(str(rel_file).replace(os.sep, '/'))
                if file_candidates:
                    chosen_file = random.choice(file_candidates)

            if chosen_file:
                results.append(chosen_file)
            else:
                results.append(subject_path)
            if len(results) >= limit:
                break
        return results

    samples = sample_subject_with_file(source_root)

    if not samples:
        fallback = []
        for dirpath, dirnames, filenames in os.walk(source_root):
            rel_dir = Path(dirpath).relative_to(source_root)
            if rel_dir != Path('.'):
                fallback.append(str(rel_dir).replace(os.sep, '/'))
            if include_files:
                for filename in filenames:
                    rel_file = (Path(dirpath) / filename).relative_to(source_root)
                    fallback.append(str(rel_file).replace(os.sep, '/'))
            if fallback:
                break
        samples = fallback or ['']

    unique = list(dict.fromkeys(samples))[:limit]
    return JSONResponse({"paths": unique})


@router.post("/{cohort_id}/stages/{stage_id}/run")
def run_cohort_stage(cohort_id: int, stage_id: str, config: dict = Body(default={})):
    """Trigger a pipeline stage for a cohort."""
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Find stage in list
    stage_idx = next((i for i, s in enumerate(cohort.stages) if s.get('id') == stage_id), None)
    if stage_idx is None:
        raise HTTPException(status_code=404, detail=f"Stage '{stage_id}' not found")
    
    stage_config = cohort.stages[stage_idx].get('config', {})
    merged_config = {**stage_config, **config}

    if stage_id == 'anonymize':
        return _run_anonymize_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'extract':
        return _run_extract_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'sort':
        return _run_sort_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'bids':
        return _run_bids_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'meg_ingest':
        return _run_meg_ingest_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'meg_scan':
        return _run_meg_scan_stage(cohort, stage_idx, merged_config)
    elif stage_id == 'meg_bids':
        return _run_meg_bids_stage(cohort, stage_idx, merged_config)
    else:
        raise HTTPException(status_code=501, detail=f"Stage '{stage_id}' not implemented yet")


def _run_meg_ingest_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the MEG ingest stage.

    Stage is recognized (found via `cohort.stages`/pipeline steps for
    modality="meg" cohorts) but not yet implemented; the real handler lands
    with `backend/src/meg/ingest.py` (see the MEG parallel track plan, task 6).
    Config shape: `meg.config.MegIngestConfig`.
    """
    raise HTTPException(status_code=501, detail="MEG ingest stage not implemented yet")


def _run_meg_scan_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the MEG scan stage.

    Reads FIF headers from the cohort's staged raw workspace
    (`meg.ingest.get_meg_raw_root`), resolves subject identity, upserts one
    `study` row per session, and persists `meg_acquisition`/`meg_channel`
    rows via `meg.extractor.MegExtractor`. Config shape:
    `meg.config.MegScanConfig`.
    """
    from meg.config import MegScanConfig
    from meg.extractor import MegExtractor, load_participant_subject_code_csv
    from meg.ingest import get_meg_raw_root
    from meg.scanner import run_meg_scan

    raw_root = get_meg_raw_root(cohort.source_path)

    subject_id_type_id_raw = merged_config.get('subjectIdTypeId')
    if subject_id_type_id_raw in (None, "", "null"):
        subject_id_type_id = None
    else:
        try:
            subject_id_type_id = int(subject_id_type_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="subjectIdTypeId must be an integer")
        subject_id_type_id = _validate_subject_id_type_id(subject_id_type_id)

    subject_code_map: dict[str, str] = {}
    csv_mapping_path = merged_config.get('subjectCsvMappingPath')
    if csv_mapping_path:
        csv_path = Path(csv_mapping_path).expanduser().resolve()
        if not csv_path.exists():
            raise HTTPException(status_code=404, detail="Subject code CSV not found")
        try:
            subject_code_map = load_participant_subject_code_csv(csv_path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Subject code CSV not found") from None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse subject code CSV: {exc}") from exc

    try:
        scan_config = MegScanConfig(
            subject_id_type_id=subject_id_type_id,
            subject_csv_mapping_path=csv_mapping_path,
            naming_convention=merged_config.get('namingConvention', 'natmeg'),
            require_calibration_files=bool(merged_config.get('requireCalibrationFiles', False)),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid MEG scan config: {exc}")

    job = job_service.create_job(
        stage='meg_scan',
        config=scan_config.model_dump(mode="json"),
        name=f"{cohort.name} - meg_scan",
    )
    job_service.mark_running(job.id)
    start_pipeline_step(cohort.id, 'meg_scan', job.id)

    control = JobControl()
    job_service.register_control(job.id, control)

    extractor = MegExtractor(cohort.id, scan_config, subject_code_map=subject_code_map)

    def progress_cb(payload: dict) -> None:
        total = payload.get("total") or 0
        processed = payload.get("processed")
        if total and processed is not None:
            try:
                job_service.update_progress(job.id, min(100, int(processed * 100 / total)))
            except Exception:
                pass

    try:
        scan_result = run_meg_scan(
            scan_config,
            raw_root,
            processor=extractor.scan_recording,
            progress_callback=progress_cb,
            control=control,
            job_id=job.id,
        )
    except JobCancelledError:
        canceled_job = job_service.get_job(job.id)
        if canceled_job and canceled_job.status != JobStatus.CANCELED:
            job_service.cancel_job(job.id)
            canceled_job = job_service.get_job(job.id)
        fail_pipeline_step(cohort.id, 'meg_scan', error="Job cancelled")
        return JSONResponse({"job": serialize_job(canceled_job)})
    except Exception as exc:
        job_service.mark_failed(job.id, str(exc))
        fail_pipeline_step(cohort.id, 'meg_scan', error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        job_service.unregister_control(job.id)

    # scan_result tracks file-discovery/skip/failure counts; extractor.result
    # tracks DB-write counts (subjects/studies/acquisitions/channels). Merge
    # explicitly rather than blindly spreading both dicts: they share a
    # "failure_count" key, and only scan_result's value (which reflects every
    # processor exception, including subject-resolution failures raised from
    # MegExtractor.scan_recording) is meaningful — extractor.result.failures
    # is never populated internally, since raised exceptions propagate up to
    # run_meg_scan's per-file try/except instead of being appended there.
    metrics = scan_result.as_metrics()
    extractor_metrics = extractor.result.as_metrics()
    del extractor_metrics["failure_count"]
    metrics.update(extractor_metrics)
    job_service.update_progress(job.id, 100)
    job_service.update_metrics(job.id, metrics)
    job_service.mark_completed(job.id)
    complete_pipeline_step(cohort.id, 'meg_scan', metrics=metrics)

    return JSONResponse({"job": serialize_job(job_service.get_job(job.id))})


def _run_meg_bids_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the MEG BIDS export stage.

    Stage is recognized but not yet implemented; the real handler lands with
    `backend/src/meg/bids_bridge.py` (see the MEG parallel track plan, task 9).
    Config shape: `meg.config.MegBidsConfig`.
    """
    raise HTTPException(status_code=501, detail="MEG BIDS export stage not implemented yet")


def _run_anonymize_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the anonymization stage."""
    from anonymize.config import (
        AnonymizeConfig,
        DerivativesStatus,
        clean_dcm_raw,
        setup_derivatives_folders,
    )

    selected_root = Path(cohort.source_path)
    derivatives_setup = setup_derivatives_folders(selected_root)
    source_path = derivatives_setup.source_path
    output_path = derivatives_setup.output_path

    # Handle derivatives retry mode
    retry_mode_value = merged_config.get('derivativesRetryMode') or 'prompt'
    retry_mode = str(retry_mode_value).lower()
    if retry_mode not in {'clean', 'overwrite'}:
        retry_mode = 'prompt'
    
    if derivatives_setup.status == DerivativesStatus.RAW_EXISTS_WITH_CONTENT:
        if retry_mode == 'clean':
            clean_dcm_raw(output_path)
            derivatives_setup = setup_derivatives_folders(selected_root)
            source_path = derivatives_setup.source_path
            output_path = derivatives_setup.output_path
        elif retry_mode != 'overwrite':
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "dcm_raw_not_empty",
                    "message": "Existing anonymized data detected under derivatives/dcm-raw. Choose to clean it or overwrite.",
                    "options": ["clean", "overwrite"],
                    "path": str(output_path),
                },
            )
    elif derivatives_setup.status == DerivativesStatus.RAW_EXISTS_EMPTY and retry_mode == 'clean':
        clean_dcm_raw(output_path)
        derivatives_setup = setup_derivatives_folders(selected_root)
        source_path = derivatives_setup.source_path
        output_path = derivatives_setup.output_path

    # Build patient ID config
    patient_id_enabled = merged_config.get('updatePatientIds', True)
    incoming_strategy = merged_config.get('patientIdStrategy', 'sequential')
    strategy_aliases = {
        'csv_mapping': 'csv',
        'path': 'folder',
        'increment': 'sequential',
    }
    patient_id_strategy = strategy_aliases.get(incoming_strategy, incoming_strategy)

    patient_id_config = {
        "enabled": patient_id_enabled,
        "strategy": patient_id_strategy,
    }

    if patient_id_strategy == 'folder':
        patient_id_config['folder'] = {
            "strategy": merged_config.get('folderStrategy', 'depth'),
            "depth_after_root": merged_config.get('folderDepthAfterRoot', 2),
            "regex": merged_config.get('folderRegex', r'\b(\d+)[-_](?:[Mm]\d+|\d+)'),
            "fallback_template": merged_config.get('folderFallbackTemplate', 'COHORTXXXX'),
        }
    elif patient_id_strategy == 'csv':
        csv_map = merged_config.get('csvMapping', {})
        token = csv_map.get('fileToken') or csv_map.get('file_token')
        file_path = csv_map.get('filePath') or csv_map.get('path')
        if token:
            csv_path = csv_file_path(sanitize_csv_token(token))
            if not csv_path.exists():
                raise HTTPException(status_code=400, detail="Uploaded CSV mapping file not found. Please re-upload.")
            file_path = str(csv_path)
        elif file_path:
            resolved_path = Path(file_path)
            if not resolved_path.exists():
                raise HTTPException(status_code=400, detail="CSV mapping path does not exist")
            file_path = str(resolved_path.resolve())
        if not file_path:
            raise HTTPException(status_code=400, detail="CSV mapping requires an uploaded file.")
        source_column = csv_map.get('sourceColumn', '').strip()
        target_column = csv_map.get('targetColumn', '').strip()
        if not source_column or not target_column:
            raise HTTPException(status_code=400, detail="CSV mapping requires both source and target columns.")
        patient_id_config['csv_mapping'] = {
            "path": file_path,
            "source_column": source_column,
            "target_column": target_column,
            "missing_mode": csv_map.get('missingMode', 'hash'),
            "missing_pattern": csv_map.get('missingPattern', 'MISSEDXXXXX'),
            "missing_salt": csv_map.get('missingSalt', 'csv-missed'),
            "preserve_top_folder_order": csv_map.get('preserveTopFolderOrder', True),
        }
    elif patient_id_strategy == 'deterministic':
        patient_id_config['deterministic'] = {
            "pattern": merged_config.get('deterministicPattern', 'ALSXXXX'),
            "salt": merged_config.get('deterministicSalt', 'als-2025'),
        }
    elif patient_id_strategy == 'sequential':
        patient_id_config['sequential'] = {
            "pattern": merged_config.get('sequentialPattern', merged_config.get('patientIdPrefixTemplate', 'SUBJXXXX')),
            "starting_number": merged_config.get('sequentialStartingNumber', merged_config.get('patientIdStartingNumber', 1)),
            "discovery": merged_config.get('sequentialDiscovery', 'per_top_folder'),
        }

    # Study dates config
    study_dates_config = {
        "enabled": merged_config.get('updateStudyDates', False),
        "snap_to_six_months": merged_config.get('snapToSixMonths', True),
        "minimum_offset_months": merged_config.get('minimumOffsetMonths', 0),
    }

    # Audit export config
    audit_export_config = {
        "enabled": True,
        "format": merged_config.get('outputFormat', 'encrypted_excel'),
        "filename": merged_config.get('metadataFilename') or f"{cohort.name}_audit.xlsx",
        "excel_password": merged_config.get('excelPassword', 'neuroimaging2025'),
    }

    # Parse scrubbed tags
    scrubbed_codes = merged_config.get('scrubbedTagCodes', [])
    scrub_tags = parse_tag_codes(scrubbed_codes)

    # Audit resume flag
    audit_resume_flag = merged_config.get('auditResumePerLeaf')
    if audit_resume_flag is None:
        audit_resume_flag = True
    audit_resume_per_leaf = bool(audit_resume_flag)

    # Count subjects
    try:
        total_subjects = sum(1 for entry in source_path.iterdir() if entry.is_dir())
    except FileNotFoundError:
        total_subjects = 0

    # Build backend config
    backend_config = {
        'source_root': str(source_path),
        'output_root': str(output_path),
        'cohort_name': cohort.name,
        'cohort_id': cohort.id,
        'scrub_tags': scrub_tags,
        'anonymize_categories': [],
        'patient_id': patient_id_config,
        'study_dates': study_dates_config,
        'audit_export': audit_export_config,
        'concurrent_processes': merged_config.get('processCount', 32),
        'worker_threads': merged_config.get('workerCount', 32),
        'preserve_uids': merged_config.get('preserveUids', True),
        'rename_patient_folders': merged_config.get('renamePatientFolders', False),
        'resume': bool(merged_config.get('resume', False)),
        'audit_resume_per_leaf': audit_resume_per_leaf,
        'total_subjects': total_subjects,
    }

    try:
        anon_config = AnonymizeConfig.model_validate(backend_config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}")

    # Create and start job
    job = job_service.create_job(stage='anonymize', config=backend_config, name=f"{cohort.name} - anonymize")
    job_service.mark_running(job.id)

    # Track pipeline step
    start_pipeline_step(cohort.id, 'anonymize', job.id)

    # Run anonymization
    from jobs.runner import run_anonymize_job, run_compress_job
    try:
        run_anonymize_job(job.id, anon_config)

        # Optional compression
        compression_cfg = merged_config.get('compression') or {}
        if compression_cfg.get('enabled'):
            from compress.config import CompressionConfig

            if compression_cfg.get('promptPassword'):
                raise HTTPException(status_code=400, detail="Prompting for passwords is not supported for API-triggered compression jobs")
            if compression_cfg.get('dryRun'):
                raise HTTPException(status_code=400, detail="Compression dry-run is not supported when launching via API")

            password_value = compression_cfg.get('password')
            if not password_value:
                raise HTTPException(status_code=400, detail="Compression password is required when compression is enabled")

            compression_root = selected_root / "derivatives" / "dcm-original"
            compression_out_dir = selected_root / "derivatives" / "archives"

            compression_config = CompressionConfig(
                root=compression_root,
                out_dir=compression_out_dir,
                chunk=compression_cfg.get('chunk', '100GB'),
                strategy=compression_cfg.get('strategy', 'ordered'),
                compression=int(compression_cfg.get('compression', 3)),
                workers=int(compression_cfg.get('workers', 2)),
                password=password_value,
                verify=bool(compression_cfg.get('verify', True)),
                par2=int(compression_cfg.get('par2', 0)),
            )

            try:
                run_compress_job(job.id, compression_config)
            except Exception as compression_error:
                raise HTTPException(status_code=500, detail=f"Compression failed: {compression_error}") from compression_error

        # Mark complete
        complete_pipeline_step(cohort.id, 'anonymize')
        
    except HTTPException:
        raise
    except Exception as exc:
        job_service.mark_failed(job.id, str(exc))
        fail_pipeline_step(cohort.id, 'anonymize', error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"job": serialize_job(job_service.get_job(job.id))})


# =============================================================================
# SORTING STAGE ROUTES
# =============================================================================

def _run_sort_stage(cohort, stage_idx: int, merged_config: dict):
    """Start the sorting stage and return job info with stream URL.
    
    Unlike anonymize/extract which run synchronously, sorting uses SSE streaming.
    This function creates the job and returns immediately with a stream URL.
    
    If any steps were previously completed, this clears them first (for redo).
    """
    from sort.models import SortingConfig
    
    # Parse config
    skip_classified = merged_config.get('skipClassified', merged_config.get('skip_classified', True))
    force_reprocess = merged_config.get('forceReprocess', merged_config.get('force_reprocess', False))
    profile = merged_config.get('profile', 'standard')
    selected_modalities = merged_config.get('selectedModalities', merged_config.get('selected_modalities', ['MR', 'CT', 'PT']))
    
    config = SortingConfig(
        skip_classified=skip_classified,
        force_reprocess=force_reprocess,
        profile=profile,
        selected_modalities=selected_modalities,
    )
    
    # Check if this is a re-run and clear previous data if needed
    sort_step_ids = get_step_ids_for_stage('sort')
    sorting_status = nils_pipeline_service.get_sorting_status(cohort.id)
    completed_steps = [
        step_id for step_id, status in sorting_status['steps'].items() 
        if status == 'completed'
    ]
    if completed_steps:
        # Clear all step data from the first step
        nils_pipeline_service.clear_from_step(cohort.id, 'sort', sort_step_ids[0] if sort_step_ids else 'checkup')
    
    # Create job
    job_config = {
        'cohort_id': cohort.id,
        'skip_classified': config.skip_classified,
        'force_reprocess': config.force_reprocess,
        'profile': config.profile,
        'selected_modalities': config.selected_modalities,
    }
    job = job_service.create_job(stage="sort", config=job_config, name=f"{cohort.name} - sort")
    
    # Track in pipeline
    start_pipeline_step(cohort.id, 'sort', job.id)
    
    # Return job info with stream URL (frontend will connect to SSE)
    return JSONResponse({
        "job": serialize_job(job, _get_cohort_metrics()),
        "stream_url": f"/api/cohorts/{cohort.id}/stages/sort/stream/{job.id}",
    })


@router.get("/{cohort_id}/stages/sort/stream/{job_id}")
async def sort_progress_stream(cohort_id: int, job_id: int):
    """SSE endpoint for sorting progress streaming.
    
    This endpoint streams progress events as the sorting pipeline executes.
    The frontend should connect with EventSource.
    """
    from fastapi.responses import StreamingResponse
    from sort.service import sorting_service
    from sort.models import SortingConfig
    
    # Get cohort and job
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Parse config from job - cohort_id is stored in config, not as a field
    job_config = job.config or {}
    job_cohort_id = job_config.get('cohort_id')
    if job_cohort_id is not None and job_cohort_id != cohort_id:
        raise HTTPException(status_code=400, detail="Job does not belong to this cohort")
    config = SortingConfig(
        skip_classified=job_config.get('skip_classified', True),
        force_reprocess=job_config.get('force_reprocess', False),
        profile=job_config.get('profile', 'standard'),
        selected_modalities=job_config.get('selectedModalities', job_config.get('selected_modalities', ['MR', 'CT', 'PT'])),
    )
    
    # Mark job as running
    job_service.mark_running(job_id)
    
    # Track step completion for overall progress
    # 5 steps total: checkup(20%), classification(40%), stacking(60%), deduplication(80%), verification(100%)
    STEP_WEIGHTS = {
        "checkup": (0, 20),         # 0-20%
        "classification": (20, 40), # 20-40%  
        "stacking": (40, 60),       # 40-60%
        "deduplication": (60, 80),  # 60-80%
        "verification": (80, 100),  # 80-100%
    }
    completed_steps = set()
    current_step_id = None
    
    async def event_generator():
        """Generate SSE events from the sorting pipeline."""
        nonlocal completed_steps, current_step_id
        
        try:
            async for event in sorting_service.run_pipeline(cohort_id, job_id, config):
                # Format as SSE
                event_type = event.type
                event_data = event.model_dump_json()
                yield f"event: {event_type}\ndata: {event_data}\n\n"
                
                # Track current step
                if event.type == "step_start":
                    current_step_id = event.step_id
                
                # Calculate overall job progress based on step progress
                if event.type == "step_progress" and event.progress is not None and current_step_id:
                    step_range = STEP_WEIGHTS.get(current_step_id, (0, 20))
                    step_start, step_end = step_range
                    # Map step progress (0-100) to step's range in overall progress
                    overall_progress = step_start + (event.progress / 100) * (step_end - step_start)
                    job_service.update_progress(job_id, int(overall_progress))
                    
                elif event.type == "step_complete":
                    if current_step_id:
                        completed_steps.add(current_step_id)
                        # Set progress to end of this step's range
                        step_range = STEP_WEIGHTS.get(current_step_id, (0, 20))
                        job_service.update_progress(job_id, step_range[1])
                    if event.metrics:
                        # Add current_step to metrics for display
                        metrics_with_step = dict(event.metrics)
                        metrics_with_step['current_step'] = f"{len(completed_steps)}/5"
                        metrics_with_step['completed_steps'] = list(completed_steps)
                        job_service.update_metrics(job_id, metrics_with_step)
                        
                elif event.type == "pipeline_complete":
                    job_service.update_progress(job_id, 100)
                    job_service.mark_completed(job_id)
                    _update_sort_stage_status(cohort_id, "completed")
                elif event.type == "pipeline_error":
                    job_service.mark_failed(job_id, event.error or "Unknown error")
                    _update_sort_stage_status(cohort_id, "failed")
                elif event.type == "pipeline_cancelled":
                    job_service.cancel_job(job_id)
                    _update_sort_stage_status(cohort_id, "canceled")
        except Exception as e:
            job_service.mark_failed(job_id, str(e))
            _update_sort_stage_status(cohort_id, "failed")
            yield f"event: pipeline_error\ndata: {{\"error\": \"{str(e)}\"}}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def _update_sort_stage_status(cohort_id: int, status: str) -> None:
    """Update the sort stage status for a cohort via pipeline service.
    
    Note: For 'completed' status, we check if ALL steps are actually completed.
    If only some steps are done, the stage remains 'running' until all finish.
    """
    sort_step_ids = get_step_ids_for_stage('sort')
    
    if status == 'completed':
        # Check how many steps are actually completed
        sorting_status = nils_pipeline_service.get_sorting_status(cohort_id)
        completed_count = sum(
            1 for step_id in sort_step_ids 
            if sorting_status['steps'].get(step_id) == 'completed'
        )
        
        # Only mark complete if ALL steps are done
        if completed_count == len(sort_step_ids):
            complete_pipeline_step(cohort_id, 'sort')
        # Otherwise just let it stay in running state
    elif status == 'failed':
        fail_pipeline_step(cohort_id, 'sort', error="Sorting pipeline failed")
    elif status == 'canceled':
        fail_pipeline_step(cohort_id, 'sort', error="Sorting canceled by user")


@router.get("/{cohort_id}/stages/sort/steps")
def get_sorting_steps(cohort_id: int):
    """Get metadata about sorting steps for UI display."""
    from sort.service import sorting_service
    
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    return JSONResponse({
        "steps": sorting_service.get_step_metadata(),
    })


@router.get("/{cohort_id}/stages/sort/status")
def get_sorting_status(cohort_id: int):
    """Get the current sorting status including completed steps and their metrics.
    
    This endpoint is used by the frontend to restore state when returning to a cohort.
    It returns:
    - steps: dict mapping step_id -> "completed" | "pending"
    - metrics: dict mapping step_id -> metrics dict for completed steps
    - next_step: the next step to run, or null if all done
    """
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Get sorting status from pipeline service
    status = nils_pipeline_service.get_sorting_status(cohort_id)
    return JSONResponse(status)


@router.get("/{cohort_id}/stages/sort/steps/{step_id}/metrics")
def get_sorting_step_metrics(cohort_id: int, step_id: str):
    """Get stored metrics for a specific sorting step.
    
    Returns the metrics that were saved when the step completed.
    """
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    metrics = nils_pipeline_service.get_metrics(cohort_id, 'sort', step_id)
    
    if metrics is None:
        raise HTTPException(status_code=404, detail=f"No metrics found for step '{step_id}'")
    
    return JSONResponse({"step_id": step_id, "metrics": metrics})


@router.get("/{cohort_id}/stages/sort/steps/stack_fingerprint/preview")
def get_stack_fingerprint_preview(
    cohort_id: int,
    start: int = Query(0, ge=0, description="Starting row index"),
    length: int = Query(100, ge=1, le=1000, description="Number of rows to return"),
    search_value: str = Query("", alias="search[value]", description="Search term"),
    order_column: int = Query(0, alias="order[0][column]", description="Column index to sort by"),
    order_dir: str = Query("asc", alias="order[0][dir]", description="Sort direction"),
):
    """
    Get paginated preview data from Step 2 fingerprint generation.
    
    Compatible with DataTables server-side processing.
    Returns stack fingerprint results before they're pushed to the database.
    """
    import polars as pl
    from pathlib import Path
    import hashlib
    
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Build parquet path (same logic as in step2)
    cache_dir = Path("/app/resource/cache/sorting")
    cohort_name_safe = cohort.name.replace(" ", "_").replace("/", "-")
    hash_value = hashlib.md5(str(cohort_id).encode()).hexdigest()[:8]
    parquet_filename = f"series_stack_{cohort_name_safe}_{hash_value}.parquet"
    parquet_path = cache_dir / parquet_filename
    
    if not parquet_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Preview data not found. Please run Step 2 in preview mode first."
        )
    
    # Read parquet
    try:
        df = pl.read_parquet(parquet_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read parquet file: {str(e)}"
        )
    
    # Apply search filter
    if search_value:
        # Search across text columns
        search_cols = ["stack_modality", "stack_key"]
        filters = []
        for col in search_cols:
            if col in df.columns:
                filters.append(
                    pl.col(col).cast(pl.Utf8).str.contains(f"(?i){search_value}")
                )
        if filters:
            df = df.filter(pl.any_horizontal(filters))
    
    # Get total counts
    records_total = df.height if not search_value else pl.read_parquet(parquet_path).height
    records_filtered = df.height
    
    # Apply sorting
    column_names = df.columns
    if 0 <= order_column < len(column_names):
        col_name = column_names[order_column]
        df = df.sort(col_name, descending=(order_dir == "desc"))
    
    # Apply pagination
    df_page = df.slice(start, length)
    
    # Convert to records
    data = df_page.to_dicts()
    
    return JSONResponse({
        "draw": 1,
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "data": data,
    })


@router.post("/{cohort_id}/stages/sort/recover-dates")
async def recover_missing_study_dates(
    cohort_id: int,
    min_year: int = Body(default=1980),
    max_year: Optional[int] = Body(default=None)
):
    """Attempt to recover missing study dates from DICOM UIDs.
    
    This endpoint scans DICOM UIDs (StudyInstanceUID, SeriesInstanceUID, etc.)
    for embedded dates when standard date fields are NULL. Only processes studies
    that belong to the specified cohort and are missing study_date.
    
    Args:
        cohort_id: ID of the cohort
        min_year: Minimum acceptable year (filters out random numbers)
        max_year: Maximum acceptable year (defaults to current year + 1)
    
    Returns:
        recovered_count: Number of studies with dates recovered
        failed_count: Number of studies still missing dates
        recovered_study_ids: List of study IDs that were updated (limited to 100)
    """
    from datetime import datetime
    from sort.date_recovery import recover_study_date_from_metadata
    from metadata_db.session import engine as metadata_engine
    
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Default max_year to current year + 1
    if max_year is None:
        max_year = datetime.now().year + 1
    
    # Validate year range
    if min_year >= max_year:
        raise HTTPException(
            status_code=400,
            detail=f"min_year ({min_year}) must be less than max_year ({max_year})"
        )
    
    recovered = []
    failed = []
    
    # Resolve metadata DB cohort_id by name (app DB and metadata DB IDs can differ)
    from metadata_db.resolve import resolve_metadata_cohort_id
    meta_cohort_id = resolve_metadata_cohort_id(cohort_id)
    if meta_cohort_id is None:
        raise HTTPException(status_code=404, detail="Cohort not found in metadata database")

    # Get studies missing dates for this cohort
    with metadata_engine.connect() as conn:
        from sqlalchemy import text
        
        query = text("""
        SELECT s.study_id, s.study_instance_uid
        FROM study s
        JOIN subject_cohorts sc ON s.subject_id = sc.subject_id
        WHERE sc.cohort_id = :cohort_id AND s.study_date IS NULL
        """)
        result = conn.execute(query, {"cohort_id": meta_cohort_id})
        missing_studies = result.fetchall()
        
        for study_id, study_uid in missing_studies:
            recovered_date, source = recover_study_date_from_metadata(
                conn, study_id, min_year, max_year
            )
            
            if recovered_date:
                # Update study date and add QC note
                qc_note = f"study_date recovered from {source} (year range {min_year}-{max_year})"
                conn.execute(
                    text("UPDATE study SET study_date = :study_date, quality_control = :qc_note WHERE study_id = :study_id"),
                    {"study_date": recovered_date, "qc_note": qc_note, "study_id": study_id}
                )
                recovered.append(study_id)
            else:
                failed.append(study_id)
        
        conn.commit()
    
    # Calculate updated metrics after recovery
    updated_metrics = None
    
    if recovered:
        # Get current Step 1 metrics from pipeline service
        current_metrics = nils_pipeline_service.get_metrics(cohort_id, 'sort', 'checkup')
        
        if current_metrics:
            # Update metrics with recovery results
            updated_metrics = dict(current_metrics)
            updated_metrics['studies_with_valid_date'] += len(recovered)
            updated_metrics['studies_excluded_no_date'] = len(failed)
            
            # Re-query and filter series for recovered studies
            modality_counts_update = None
            filtered_series_count = 0
            
            with metadata_engine.connect() as meta_conn:
                from sort.queries import filter_series_by_modality, get_series_for_studies
                
                # Get series for recovered studies
                recovered_series_rows = get_series_for_studies(meta_conn, recovered)
                
                if recovered_series_rows:
                    # Get current config's selected modalities from stored metrics
                    # (or default to all three if not available)
                    selected_modalities = current_metrics.get('selected_modalities', ['MR', 'CT', 'PT'])
                    
                    # Filter series by selected modalities
                    filtered_series, recovered_modality_counts = filter_series_by_modality(
                        recovered_series_rows,
                        selected_modalities
                    )
                    
                    # Count FILTERED series, not all series
                    filtered_series_count = len(filtered_series)
                    
                    # Merge with existing modality counts
                    existing_modality_counts = current_metrics.get('series_by_modality', {})
                    for modality, count in recovered_modality_counts.items():
                        existing_modality_counts[modality] = existing_modality_counts.get(modality, 0) + count
                    
                    modality_counts_update = existing_modality_counts
            
            # Apply updates after connection closes (use FILTERED count)
            updated_metrics['total_series'] += filtered_series_count
            updated_metrics['series_to_process_count'] += filtered_series_count
            
            if modality_counts_update is not None:
                updated_metrics['series_by_modality'] = modality_counts_update
            
            # Clear warnings if all recovered
            if len(failed) == 0:
                updated_metrics['warnings'] = []
            else:
                # Update warning message
                updated_metrics['warnings'] = [
                    f"{len(failed)} studies excluded due to missing dates"
                ]
            
            # Save updated metrics via pipeline service
            nils_pipeline_service.save_metrics(
                cohort_id=cohort_id,
                stage_id='sort',
                step_id='checkup',
                metrics=updated_metrics
            )
            
            # CRITICAL: Clear step data after date recovery to force re-run
            # The handover contains series_to_process list from the ORIGINAL run
            # which doesn't include series from newly recovered studies.
            logger.info(
                "Date recovery: Clearing step data for cohort %d to force re-run "
                "(recovered %d studies with new series)",
                cohort_id,
                len(recovered)
            )
            
            # Clear from checkup to invalidate all downstream steps
            nils_pipeline_service.clear_from_step(cohort_id, 'sort', 'checkup')
    
    return JSONResponse({
        "recovered_count": len(recovered),
        "failed_count": len(failed),
        "recovered_study_ids": recovered[:100],  # Limit for UI
        "updated_metrics": updated_metrics,  # NEW: Return updated metrics
        "requires_step1_rerun": len(recovered) > 0,  # Flag for frontend
    })


@router.post("/{cohort_id}/stages/sort/rerun/{step_id}")
async def rerun_sorting_step(cohort_id: int, step_id: str, config: dict = Body(default={})):
    """Re-run a specific sorting step.
    
    This allows re-running a completed step without starting from scratch.
    """
    from fastapi.responses import StreamingResponse
    from sort.service import sorting_service
    from sort.models import SortingConfig
    
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Parse config
    skip_classified = config.get('skipClassified', config.get('skip_classified', True))
    force_reprocess = config.get('forceReprocess', config.get('force_reprocess', False))
    profile = config.get('profile', 'standard')
    selected_modalities = config.get('selectedModalities', config.get('selected_modalities', ['MR', 'CT', 'PT']))
    
    sort_config = SortingConfig(
        skip_classified=skip_classified,
        force_reprocess=force_reprocess,
        profile=profile,
        selected_modalities=selected_modalities,
    )
    
    # Create new job for re-run
    job_config = {
        'cohort_id': cohort.id,
        'skip_classified': sort_config.skip_classified,
        'force_reprocess': sort_config.force_reprocess,
        'profile': sort_config.profile,
        'selected_modalities': sort_config.selected_modalities,
        'rerun_step': step_id,
        'step_id': step_id,
    }
    job = job_service.create_job(stage="sort", config=job_config, name=f"{cohort.name} - sort/{step_id}")
    job_service.mark_running(job.id)
    
    async def event_generator():
        """Generate SSE events for step re-run."""
        try:
            async for event in sorting_service.run_step(cohort_id, job.id, step_id, sort_config):
                event_type = event.type
                event_data = event.model_dump_json()
                yield f"event: {event_type}\ndata: {event_data}\n\n"
                
                if event.type == "pipeline_complete":
                    job_service.mark_completed(job.id)
                elif event.type in ("pipeline_error", "step_error"):
                    job_service.mark_failed(job.id, event.error or "Unknown error")
        except Exception as e:
            job_service.mark_failed(job.id, str(e))
            yield f"event: pipeline_error\ndata: {{\"error\": \"{str(e)}\"}}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{cohort_id}/stages/sort/run-step/{step_id}")
async def run_sorting_step(cohort_id: int, step_id: str, config: dict = Body(default={})):
    """Run a single sorting step independently (step-wise execution).
    
    This enables the new step-wise execution pattern where:
    - User controls each step individually
    - Can review results before proceeding to next step
    - Can change config and re-run from any step
    
    The step will load handover data from the previous step automatically.
    If the previous step hasn't been run, this will return an error.
    
    Config options:
    - previewMode (bool): If true, generate preview without DB insert (Step 2 only)
    - skipClassified (bool): Skip already classified series
    - forceReprocess (bool): Force reprocessing
    - profile (str): Processing profile
    - selectedModalities (list): Modalities to process
    """
    from fastapi.responses import StreamingResponse
    from sort.service import sorting_service
    from sort.models import SortingConfig
    
    cohort = cohort_service.get_cohort(cohort_id)
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    # Parse config
    skip_classified = config.get('skipClassified', config.get('skip_classified', True))
    force_reprocess = config.get('forceReprocess', config.get('force_reprocess', False))
    profile = config.get('profile', 'standard')
    selected_modalities = config.get('selectedModalities', config.get('selected_modalities', ['MR', 'CT', 'PT']))
    preview_mode = config.get('previewMode', config.get('preview_mode', False))
    
    # Create new job for this step
    job_config = {
        'cohort_id': cohort.id,
        'skip_classified': skip_classified,
        'force_reprocess': force_reprocess,
        'profile': profile,
        'selected_modalities': selected_modalities,
        'preview_mode': preview_mode,
        'step_id': step_id,
        'execution_mode': 'step-wise',
    }
    job = job_service.create_job(stage="sort", config=job_config, name=f"{cohort.name} - sort/{step_id}")
    
    # Return job info immediately (don't start execution yet - client will connect to stream)
    return {
        "job_id": job.id,
        "stream_url": f"/api/cohorts/{cohort_id}/stages/sort/stream-step/{step_id}/{job.id}"
    }


@router.get("/{cohort_id}/stages/sort/stream-step/{step_id}/{job_id}")
async def stream_sorting_step(cohort_id: int, step_id: str, job_id: int):
    """Stream SSE events for a sorting step execution.
    
    This is the streaming endpoint that the frontend connects to after
    calling the run-step endpoint.
    """
    from fastapi.responses import StreamingResponse
    from sort.service import sorting_service
    from sort.models import SortingConfig
    from jobs.models import JobStatus
    
    # Get job config
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # ═══════════════════════════════════════════════════════════════════════
    # IDEMPOTENCY CHECK: Prevent duplicate execution on SSE reconnection
    # ═══════════════════════════════════════════════════════════════════════
    
    # If job already completed, return cached completion (don't re-run)
    if job.status == JobStatus.COMPLETED:
        logger.info(
            "SSE reconnect for completed job %d (step=%s) - returning cached completion",
            job_id, step_id
        )
        async def completed_generator():
            yield f'event: step_complete\ndata: {{"step_id": "{step_id}", "cached": true, "message": "Job already completed"}}\n\n'
        return StreamingResponse(
            completed_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    
    # If job already running, prevent duplicate execution
    if job.status == JobStatus.RUNNING:
        logger.warning(
            "SSE reconnect for running job %d (step=%s) - rejecting duplicate execution",
            job_id, step_id
        )
        async def already_running_generator():
            yield f'event: step_error\ndata: {{"step_id": "{step_id}", "error": "Job is already running. Please wait for it to complete."}}\n\n'
        return StreamingResponse(
            already_running_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    
    # Only proceed if job is QUEUED (fresh job ready to run)
    job_config = job.config or {}
    preview_mode = job_config.get('preview_mode', False)
    
    sort_config = SortingConfig(
        skip_classified=job_config.get('skip_classified', True),
        force_reprocess=job_config.get('force_reprocess', False),
        profile=job_config.get('profile', 'standard'),
        selected_modalities=job_config.get('selected_modalities', ['MR', 'CT', 'PT']),
    )
    
    job_service.mark_running(job.id)
    
    async def event_generator():
        """Generate SSE events for step execution."""
        try:
            async for event in sorting_service.run_single_step(cohort_id, job.id, step_id, sort_config, preview_mode=preview_mode):
                event_type = event.type
                event_data = event.model_dump_json()
                yield f"event: {event_type}\ndata: {event_data}\n\n"
                
                if event.type == "step_complete":
                    job_service.mark_completed(job.id)
                elif event.type == "step_error":
                    job_service.mark_failed(job.id, event.error or "Unknown error")
                elif event.type == "step_cancelled":
                    job_service.mark_cancelled(job.id)
        except Exception as e:
            job_service.mark_failed(job.id, str(e))
            yield f"event: error\ndata: {{\"error\": \"{str(e)}\"}}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _run_extract_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the extraction stage."""
    from anonymize.config import setup_derivatives_folders

    selected_root = Path(cohort.source_path)
    derivatives_setup = setup_derivatives_folders(selected_root)
    raw_root = derivatives_setup.output_path

    # Load subject code mapping if provided
    subject_code_cfg = merged_config.get('subjectCodeCsv') or {}
    subject_code_map: dict[str, str] = {}
    subject_code_map_name: str | None = None
    if subject_code_cfg:
        subject_code_map, subject_code_map_name = _load_subject_code_mapping(subject_code_cfg)

    # Validate subject ID type
    subject_id_type_id_raw = merged_config.get('subjectIdTypeId')
    if subject_id_type_id_raw in (None, "", "null"):
        subject_id_type_id = None
    else:
        try:
            subject_id_type_id = int(subject_id_type_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="subjectIdTypeId must be an integer")
        subject_id_type_id = _validate_subject_id_type_id(subject_id_type_id)

    subject_code_seed = merged_config.get('subjectCodeSeed')
    if subject_code_seed is not None:
        subject_code_seed = str(subject_code_seed)

    # Build extraction config
    try:
        series_workers = int(merged_config.get('seriesWorkersPerSubject', 1))
        adaptive_enabled = bool(merged_config.get('adaptiveBatchingEnabled', False))
        adaptive_target = int(merged_config.get('adaptiveTargetTxMs', 200))
        adaptive_min = int(merged_config.get('adaptiveMinBatchSize', 50))
        adaptive_max = int(merged_config.get('adaptiveMaxBatchSize', 1000))
        resume_flag = bool(merged_config.get('resume', True))
        resume_by_path_flag = bool(merged_config.get('resumeByPath', False))
        use_process_pool = bool(merged_config.get('useProcessPool', True))
        process_pool_workers_raw = merged_config.get('processPoolWorkers')
        process_pool_workers = int(process_pool_workers_raw) if process_pool_workers_raw is not None else None
        db_writer_pool_size = int(merged_config.get('dbWriterPoolSize', 1))
        requested_policy = merged_config.get('duplicatePolicy')
        if resume_flag:
            duplicate_policy = DuplicatePolicy(requested_policy or 'skip')
        else:
            duplicate_policy = DuplicatePolicy.OVERWRITE

        extraction_config = ExtractionConfig(
            cohort_id=cohort.id,
            cohort_name=cohort.name,
            raw_root=raw_root,
            max_workers=int(merged_config.get('maxWorkers', 4)),
            batch_size=int(merged_config.get('batchSize', 100)),
            queue_size=int(merged_config.get('queueSize', 10)),
            extension_mode=ExtensionMode(merged_config.get('extensionMode', 'all')),
            duplicate_policy=duplicate_policy,
            resume=resume_flag,
            resume_by_path=resume_flag and resume_by_path_flag,
            subject_id_type_id=subject_id_type_id,
            subject_code_map=subject_code_map,
            subject_code_seed=subject_code_seed,
            subject_code_map_name=subject_code_map_name,
            series_workers_per_subject=series_workers,
            adaptive_batching_enabled=adaptive_enabled,
            target_tx_ms=adaptive_target,
            min_batch_size=adaptive_min,
            max_batch_size=adaptive_max,
            use_process_pool=use_process_pool,
            process_pool_workers=process_pool_workers,
            db_writer_pool_size=db_writer_pool_size,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid extraction config: {exc}")

    # Create and start job
    job = job_service.create_job(
        stage='extract',
        config=extraction_config.model_dump(mode="json"),
        name=f"{cohort.name} - extract"
    )
    job_service.mark_running(job.id)

    # Track pipeline step
    start_pipeline_step(cohort.id, 'extract', job.id)

    # Setup progress tracking and job control
    tracker = ExtractionProgressTracker(lambda percent: job_service.update_progress(job.id, percent))
    control = JobControl()
    job_service.register_control(job.id, control)

    def progress_cb(processed: int, total: int) -> None:
        tracker.update(processed, total)

    # Run extraction
    try:
        result = run_extraction(
            extraction_config,
            progress=progress_cb,
            job_id=job.id,
            control=control,
        )
    except JobCancelledError:
        tracker.finalize()
        canceled_job = job_service.get_job(job.id)
        if canceled_job and canceled_job.status != JobStatus.CANCELED:
            job_service.cancel_job(job.id)
            canceled_job = job_service.get_job(job.id)
        fail_pipeline_step(cohort.id, 'extract', error="Job cancelled")
        return JSONResponse({"job": serialize_job(canceled_job)})
    except Exception as exc:
        tracker.finalize()
        job_service.mark_failed(job.id, str(exc))
        fail_pipeline_step(cohort.id, 'extract', error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        job_service.unregister_control(job.id)

    # Finalize
    tracker.finalize()
    if result.metrics:
        job_service.update_metrics(job.id, result.metrics)

    job_service.mark_completed(job.id)
    complete_pipeline_step(cohort.id, 'extract')
    
    return JSONResponse({"job": serialize_job(job_service.get_job(job.id))})


def _run_bids_stage(cohort, stage_idx: int, merged_config: dict):
    """Run the unified BIDS export stage (bids-dcm or bids-nifti)."""
    from anonymize.config import setup_derivatives_folders

    selected_root = Path(cohort.source_path)
    derivatives_setup = setup_derivatives_folders(selected_root)
    raw_root = derivatives_setup.output_path
    derivatives_root = derivatives_setup.output_path.parent

    try:
        # Cohort scope: filter the shared export engine by metadata-DB cohort
        # name (critical for query performance on large DBs). Config
        # construction is shared with the standalone export path via
        # build_export_config (single source of truth).
        from api.services.export_runner import build_export_config

        config_model = build_export_config(merged_config, cohort_name=cohort.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config: {exc}")

    job_config = config_model.model_dump(mode="json")
    # Attach cohort context for frontend filtering / job serialization
    job_config["cohort_id"] = cohort.id
    job_config["cohort_name"] = cohort.name

    job = job_service.create_job(
        stage='bids',
        config=job_config,
        name=f"{cohort.name} - bids",
    )
    job_service.mark_running(job.id)
    start_pipeline_step(cohort.id, 'bids', job.id)
    try:
        job_service.update_progress(job.id, 1)
    except Exception:
        pass

    # Run the export in the shared background executor. The runner updates
    # progress/metrics/terminal status and syncs the bids pipeline step.
    from api.server import _job_executor
    from api.services.export_runner import run_export_job

    _job_executor.submit(
        run_export_job,
        job.id,
        raw_root,
        derivatives_root,
        config_model,
        pipeline_cohort_id=cohort.id,
        pipeline_stage='bids',
    )

    return JSONResponse(
        status_code=202,
        content={"job": serialize_job(job_service.get_job(job.id))},
    )
