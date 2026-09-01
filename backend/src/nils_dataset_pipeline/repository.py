"""Database operations for pipeline steps.

This module provides low-level database operations for the pipeline steps table.
It's used by the service layer and should not be called directly from API routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import NilsDatasetPipelineStep
from .ordering import get_pipeline_items, get_step_ids_for_stage, get_default_stage_config


# =============================================================================
# READ OPERATIONS
# =============================================================================


def get_steps_for_cohort(
    session: Session,
    cohort_id: int,
) -> list[NilsDatasetPipelineStep]:
    """Get all pipeline steps for a cohort, ordered by sort_order.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        
    Returns:
        List of pipeline steps in execution order.
    """
    return list(session.scalars(
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .order_by(NilsDatasetPipelineStep.sort_order)
    ))


def get_step(
    session: Session,
    cohort_id: int,
    stage_id: str,
    step_id: Optional[str] = None,
) -> Optional[NilsDatasetPipelineStep]:
    """Get a specific pipeline step.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID (e.g., 'anonymize', 'extract', 'sort').
        step_id: The step ID for multi-step stages (e.g., 'checkup'), or None.
        
    Returns:
        The pipeline step, or None if not found.
    """
    stmt = (
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .where(NilsDatasetPipelineStep.stage_id == stage_id)
    )
    if step_id:
        stmt = stmt.where(NilsDatasetPipelineStep.step_id == step_id)
    else:
        stmt = stmt.where(NilsDatasetPipelineStep.step_id.is_(None))
    return session.scalar(stmt)


def get_step_by_id(
    session: Session,
    step_pk: int,
) -> Optional[NilsDatasetPipelineStep]:
    """Get a pipeline step by its primary key.
    
    Args:
        session: Database session.
        step_pk: The step's primary key ID.
        
    Returns:
        The pipeline step, or None if not found.
    """
    return session.get(NilsDatasetPipelineStep, step_pk)


def get_steps_for_stage(
    session: Session,
    cohort_id: int,
    stage_id: str,
) -> list[NilsDatasetPipelineStep]:
    """Get all steps for a specific stage (for multi-step stages).
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        
    Returns:
        List of steps for the stage, ordered by sort_order.
    """
    return list(session.scalars(
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .where(NilsDatasetPipelineStep.stage_id == stage_id)
        .order_by(NilsDatasetPipelineStep.sort_order)
    ))


def get_running_steps(session: Session) -> list[NilsDatasetPipelineStep]:
    """Get all currently running steps across all cohorts.
    
    Useful for dashboards and monitoring.
    
    Returns:
        List of all running pipeline steps.
    """
    return list(session.scalars(
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.status == "running")
    ))


def get_next_pending_step(
    session: Session,
    cohort_id: int,
) -> Optional[NilsDatasetPipelineStep]:
    """Get the next step that can be run for a cohort.
    
    Returns the first step with status 'pending' or 'failed'.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        
    Returns:
        The next runnable step, or None if all steps are blocked/running/completed.
    """
    return session.scalar(
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .where(NilsDatasetPipelineStep.status.in_(["pending", "failed"]))
        .order_by(NilsDatasetPipelineStep.sort_order)
        .limit(1)
    )


# =============================================================================
# WRITE OPERATIONS
# =============================================================================


def update_step_status(
    session: Session,
    step_pk: int,
    status: str,
    progress: Optional[int] = None,
) -> None:
    """Update step status and optionally progress.
    
    Args:
        session: Database session.
        step_pk: The step's primary key ID.
        status: New status value.
        progress: Optional new progress value (0-100).
    """
    values: dict[str, Any] = {"status": status}
    if progress is not None:
        values["progress"] = progress
    session.execute(
        update(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.id == step_pk)
        .values(**values)
    )
    session.flush()


def set_step_job(
    session: Session,
    step_pk: int,
    job_id: Optional[int],
) -> None:
    """Set or clear the current job for a step.
    
    Args:
        session: Database session.
        step_pk: The step's primary key ID.
        job_id: The job ID to set, or None to clear.
    """
    session.execute(
        update(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.id == step_pk)
        .values(current_job_id=job_id)
    )
    session.flush()


def save_config(
    session: Session,
    cohort_id: int,
    stage_id: str,
    step_id: Optional[str],
    config: dict[str, Any],
) -> None:
    """Save configuration for a step.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        step_id: The step ID (or None for simple stages).
        config: Configuration dictionary.
    """
    step = get_step(session, cohort_id, stage_id, step_id)
    if step:
        step.config = config
        session.flush()


def save_handover(
    session: Session,
    cohort_id: int,
    stage_id: str,
    step_id: Optional[str],
    handover_data: dict[str, Any],
) -> None:
    """Save handover data for a step.
    
    Handover data is used to pass information between steps in a pipeline.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        step_id: The step ID (or None for simple stages).
        handover_data: Handover data dictionary.
    """
    step = get_step(session, cohort_id, stage_id, step_id)
    if step:
        step.handover_data = handover_data
        session.flush()


def save_metrics(
    session: Session,
    cohort_id: int,
    stage_id: str,
    step_id: Optional[str],
    metrics: dict[str, Any],
) -> None:
    """Save metrics for a step.
    
    Metrics are completion/summary data displayed in the UI.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        step_id: The step ID (or None for simple stages).
        metrics: Metrics dictionary.
    """
    step = get_step(session, cohort_id, stage_id, step_id)
    if step:
        step.metrics = metrics
        session.flush()


def unlock_next_step(
    session: Session,
    cohort_id: int,
    completed_sort_order: int,
) -> Optional[NilsDatasetPipelineStep]:
    """Unlock the next blocked step after a completion.
    
    Finds the first blocked step after the completed step and changes
    its status to 'pending'.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        completed_sort_order: The sort_order of the just-completed step.
        
    Returns:
        The unlocked step, or None if no blocked step was found.
    """
    next_step = session.scalar(
        select(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .where(NilsDatasetPipelineStep.sort_order > completed_sort_order)
        .where(NilsDatasetPipelineStep.status == "blocked")
        .order_by(NilsDatasetPipelineStep.sort_order)
        .limit(1)
    )
    if next_step:
        next_step.status = "pending"
        next_step.progress = 5  # Show as "ready to run" in UI
        session.flush()
    return next_step


def clear_downstream(
    session: Session,
    cohort_id: int,
    from_sort_order: int,
) -> int:
    """Clear handover/metrics for steps at or after a given order.
    
    Used when re-running a step to invalidate all dependent data.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        from_sort_order: Clear steps starting from this sort_order.
        
    Returns:
        Number of steps cleared.
    """
    result = session.execute(
        update(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
        .where(NilsDatasetPipelineStep.sort_order >= from_sort_order)
        .values(
            handover_data=None,
            metrics=None,
            status="pending",
            progress=0,
            current_job_id=None,
        )
    )
    session.flush()
    return result.rowcount


def delete_step_handover(
    session: Session,
    cohort_id: int,
    stage_id: str,
    step_id: Optional[str],
) -> None:
    """Delete handover data for a specific step.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        step_id: The step ID (or None for simple stages).
    """
    step = get_step(session, cohort_id, stage_id, step_id)
    if step:
        step.handover_data = None
        session.flush()


# =============================================================================
# INITIALIZATION
# =============================================================================


def initialize_pipeline(
    session: Session,
    cohort_id: int,
    anonymization_enabled: bool,
    cohort_name: str = "",
    source_path: str = "",
    default_configs: Optional[dict[str, dict[str, Any]]] = None,
    modality: str = "imaging",
) -> list[NilsDatasetPipelineStep]:
    """Create all pipeline steps for a new cohort.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        anonymization_enabled: Whether to include the anonymize stage.
        cohort_name: Cohort name for default config generation.
        source_path: Source path for default config generation.
        default_configs: Optional dict of stage_id -> config overrides.
        modality: Cohort modality ("imaging" or "meg"). Selects which
            stage list (DICOM vs. MEG) is initialized. Defaults to
            "imaging" to preserve existing behaviour.
        
    Returns:
        List of created pipeline steps.
    """
    items = get_pipeline_items(anonymization_enabled, modality)
    created_steps = []
    is_first = True
    
    for item in items:
        # Get config for this stage
        config = None
        if default_configs and item["stage_id"] in default_configs:
            config = default_configs[item["stage_id"]]
        else:
            # Only set config for the first step of each stage
            # (for multi-step stages, config is on the first step)
            stage_step_ids = get_step_ids_for_stage(item["stage_id"], modality)
            if not stage_step_ids or item["step_id"] == stage_step_ids[0]:
                config = get_default_stage_config(
                    item["stage_id"],
                    cohort_name,
                    source_path,
                    modality,
                )
        
        step = NilsDatasetPipelineStep(
            cohort_id=cohort_id,
            stage_id=item["stage_id"],
            step_id=item["step_id"],
            title=item["title"],
            description=item["description"],
            status="pending" if is_first else "blocked",
            progress=5 if is_first else 0,
            sort_order=item["sort_order"],
            config=config,
        )
        session.add(step)
        created_steps.append(step)
        is_first = False
    
    session.flush()
    return created_steps


def delete_pipeline_for_cohort(
    session: Session,
    cohort_id: int,
) -> int:
    """Delete all pipeline steps for a cohort.
    
    Note: This is usually handled by CASCADE delete on the cohort FK,
    but provided for explicit cleanup if needed.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        
    Returns:
        Number of steps deleted.
    """
    result = session.execute(
        delete(NilsDatasetPipelineStep)
        .where(NilsDatasetPipelineStep.cohort_id == cohort_id)
    )
    session.flush()
    return result.rowcount


# =============================================================================
# STATUS QUERIES
# =============================================================================


def get_sorting_status(
    session: Session,
    cohort_id: int,
) -> dict[str, Any]:
    """Get complete sorting status for a cohort.
    
    This replaces the old sort/repository.py get_sorting_status function.
    
    Returns:
        {
            "steps": {"checkup": "completed", "stack_discovery": "pending", ...},
            "metrics": {"checkup": {...}, ...},
            "next_step": "stack_discovery" or None if all done
        }
    """
    # Get all sort steps for this cohort
    sort_steps = get_steps_for_stage(session, cohort_id, "sort")
    
    steps_status = {}
    all_metrics = {}
    next_step = None
    
    for step in sort_steps:
        if step.step_id:
            # Determine completed status based on handover presence
            # (preview mode has metrics but no handover, so still "pending")
            if step.handover_data is not None:
                steps_status[step.step_id] = "completed"
            else:
                steps_status[step.step_id] = "pending"
                if next_step is None:
                    next_step = step.step_id
            
            # Include metrics if available
            if step.metrics:
                all_metrics[step.step_id] = step.metrics
    
    return {
        "steps": steps_status,
        "metrics": all_metrics,
        "next_step": next_step,
    }


def get_completed_step_ids(
    session: Session,
    cohort_id: int,
    stage_id: str,
) -> list[str]:
    """Get list of completed step IDs for a stage.
    
    Args:
        session: Database session.
        cohort_id: The cohort ID.
        stage_id: The stage ID.
        
    Returns:
        List of completed step IDs in pipeline order.
    """
    steps = get_steps_for_stage(session, cohort_id, stage_id)
    return [
        step.step_id
        for step in steps
        if step.step_id and step.handover_data is not None
    ]
