"""Service layer for pipeline operations.

This is the main interface for managing cohort pipeline state.
Use nils_pipeline_service singleton for all operations.

Example usage:
    from nils_dataset_pipeline import nils_pipeline_service
    
    # Initialize pipeline for new cohort
    nils_pipeline_service.initialize_for_cohort(cohort_id=1, anonymization_enabled=True)
    
    # Start a step
    nils_pipeline_service.start_step(cohort_id=1, stage_id="extract", job_id=42)
    
    # Complete a step
    nils_pipeline_service.complete_step(
        cohort_id=1, stage_id="extract",
        metrics={"subjects": 100, "studies": 500}
    )
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from db.session import SessionLocal

from . import repository
from .models import NilsDatasetPipelineStep
from .ordering import (
    _stages_for_modality,
    get_pipeline_items,
    get_step_ids_for_stage,
    is_multi_step_stage,
    get_previous_step_in_stage,
)

logger = logging.getLogger(__name__)


class NilsDatasetPipelineService:
    """Service for managing cohort pipeline state.
    
    This service provides a high-level interface for pipeline operations,
    handling session management and business logic internally.
    """
    
    # =========================================================================
    # READ OPERATIONS
    # =========================================================================
    
    def get_pipeline_for_cohort(self, cohort_id: int) -> list[NilsDatasetPipelineStep]:
        """Get all pipeline steps for a cohort.
        
        Args:
            cohort_id: The cohort ID.
            
        Returns:
            List of pipeline steps in execution order.
        """
        with SessionLocal() as session:
            steps = repository.get_steps_for_cohort(session, cohort_id)
            # Detach from session for safe return
            session.expunge_all()
            return steps
    
    def get_step(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
    ) -> Optional[NilsDatasetPipelineStep]:
        """Get a specific step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            
        Returns:
            The step, or None if not found.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                session.expunge(step)
            return step
    
    def get_steps_for_stage(
        self,
        cohort_id: int,
        stage_id: str,
    ) -> list[NilsDatasetPipelineStep]:
        """Get all steps for a specific stage.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            
        Returns:
            List of steps for the stage, ordered.
        """
        with SessionLocal() as session:
            steps = repository.get_steps_for_stage(session, cohort_id, stage_id)
            session.expunge_all()
            return steps
    
    def get_sorting_status(self, cohort_id: int) -> dict[str, Any]:
        """Get sorting status for a cohort.
        
        This is compatible with the old sort/repository get_sorting_status().
        
        Returns:
            Dict with steps status, metrics, and next_step.
        """
        with SessionLocal() as session:
            return repository.get_sorting_status(session, cohort_id)
    
    def get_handover(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: str,
    ) -> Optional[dict[str, Any]]:
        """Get handover data from a step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID.
            
        Returns:
            Handover data dict, or None if not available.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            return step.handover_data if step else None
    
    def get_metrics(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Get metrics from a step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            
        Returns:
            Metrics dict, or None if not available.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            return step.metrics if step else None
    
    def get_config(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Get configuration for a step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            
        Returns:
            Config dict, or None if not available.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            return step.config if step else None
    
    # =========================================================================
    # STATE TRANSITIONS
    # =========================================================================
    
    def start_step(
        self,
        cohort_id: int,
        stage_id: str,
        job_id: int,
        step_id: Optional[str] = None,
    ) -> None:
        """Mark a step as running with a job.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            job_id: The job ID that's running this step.
            step_id: The step ID (or None for simple stages).
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                step.status = "running"
                step.progress = 0
                step.current_job_id = job_id
                session.commit()
                logger.info(
                    "Started step: cohort=%d stage=%s step=%s job=%d",
                    cohort_id, stage_id, step_id, job_id
                )
    
    def complete_step(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
        metrics: Optional[dict[str, Any]] = None,
        handover: Optional[dict[str, Any]] = None,
    ) -> None:
        """Mark a step as completed and unlock next.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            metrics: Optional completion metrics.
            handover: Optional handover data for next step.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                step.status = "completed"
                step.progress = 100
                step.current_job_id = None
                if metrics:
                    step.metrics = metrics
                if handover:
                    step.handover_data = handover
                
                # Unlock next step
                repository.unlock_next_step(session, cohort_id, step.sort_order)
                session.commit()
                
                logger.info(
                    "Completed step: cohort=%d stage=%s step=%s",
                    cohort_id, stage_id, step_id
                )
    
    def fail_step(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark a step as failed.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            error: Optional error message to store in metrics.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                step.status = "failed"
                step.current_job_id = None
                if error:
                    existing_metrics = step.metrics or {}
                    existing_metrics["error"] = error
                    step.metrics = existing_metrics
                session.commit()
                
                logger.warning(
                    "Failed step: cohort=%d stage=%s step=%s error=%s",
                    cohort_id, stage_id, step_id, error
                )
    
    def update_progress(
        self,
        cohort_id: int,
        stage_id: str,
        progress: int,
        step_id: Optional[str] = None,
    ) -> None:
        """Update step progress.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            progress: Progress value (0-100).
            step_id: The step ID (or None for simple stages).
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                step.progress = max(0, min(100, progress))
                session.commit()
    
    def save_metrics(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str],
        metrics: dict[str, Any],
    ) -> None:
        """Save metrics for a step (without changing status).
        
        Useful for preview mode where metrics are saved but step isn't completed.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            metrics: Metrics dictionary.
        """
        with SessionLocal() as session:
            repository.save_metrics(session, cohort_id, stage_id, step_id, metrics)
            session.commit()
    
    def save_handover(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: str,
        handover_data: dict[str, Any],
    ) -> None:
        """Save handover data for a step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID.
            handover_data: Handover data dictionary.
        """
        with SessionLocal() as session:
            repository.save_handover(session, cohort_id, stage_id, step_id, handover_data)
            session.commit()
    
    def save_config(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str],
        config: dict[str, Any],
    ) -> None:
        """Save configuration for a step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            config: Configuration dictionary.
        """
        with SessionLocal() as session:
            repository.save_config(session, cohort_id, stage_id, step_id, config)
            session.commit()
    
    # =========================================================================
    # RERUN / CLEANUP
    # =========================================================================
    
    def clear_from_step(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: Optional[str] = None,
    ) -> int:
        """Clear data from a step and all downstream (for re-runs).
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID (or None for simple stages).
            
        Returns:
            Number of steps cleared.
        """
        with SessionLocal() as session:
            step = repository.get_step(session, cohort_id, stage_id, step_id)
            if step:
                count = repository.clear_downstream(session, cohort_id, step.sort_order)
                session.commit()
                logger.info(
                    "Cleared %d steps from cohort=%d stage=%s step=%s",
                    count, cohort_id, stage_id, step_id
                )
                return count
            return 0
    
    def delete_handover(
        self,
        cohort_id: int,
        stage_id: str,
        step_id: str,
    ) -> None:
        """Delete handover data for a specific step.
        
        Args:
            cohort_id: The cohort ID.
            stage_id: The stage ID.
            step_id: The step ID.
        """
        with SessionLocal() as session:
            repository.delete_step_handover(session, cohort_id, stage_id, step_id)
            session.commit()
    
    # =========================================================================
    # INITIALIZATION
    # =========================================================================
    
    def initialize_for_cohort(
        self,
        cohort_id: int,
        anonymization_enabled: bool,
        cohort_name: str = "",
        source_path: str = "",
        default_configs: Optional[dict[str, dict[str, Any]]] = None,
        modality: str = "imaging",
    ) -> list[NilsDatasetPipelineStep]:
        """Initialize pipeline steps for a new cohort.
        
        Args:
            cohort_id: The cohort ID.
            anonymization_enabled: Whether to include anonymize stage.
            cohort_name: Cohort name for default config generation.
            source_path: Source path for default config generation.
            default_configs: Optional dict of stage_id -> config overrides.
            modality: Cohort modality ("imaging" or "meg"). Defaults to
                "imaging" to preserve existing behaviour.
            
        Returns:
            List of created pipeline steps.
        """
        with SessionLocal() as session:
            steps = repository.initialize_pipeline(
                session,
                cohort_id,
                anonymization_enabled,
                cohort_name,
                source_path,
                default_configs,
                modality,
            )
            session.commit()
            session.expunge_all()
            logger.info(
                "Initialized pipeline for cohort=%d (modality=%s): %d steps",
                cohort_id, modality, len(steps)
            )
            return steps
    
    def reinitialize_pipeline(
        self,
        cohort_id: int,
        anonymization_enabled: bool,
        cohort_name: str = "",
        source_path: str = "",
        modality: str = "imaging",
    ) -> list[NilsDatasetPipelineStep]:
        """Reinitialize pipeline for a cohort (delete and recreate).
        
        WARNING: This will delete all existing pipeline state!
        
        Args:
            cohort_id: The cohort ID.
            anonymization_enabled: Whether to include anonymize stage.
            cohort_name: Cohort name for default config generation.
            source_path: Source path for default config generation.
            modality: Cohort modality ("imaging" or "meg"). Defaults to
                "imaging" to preserve existing behaviour.
            
        Returns:
            List of created pipeline steps.
        """
        with SessionLocal() as session:
            repository.delete_pipeline_for_cohort(session, cohort_id)
            steps = repository.initialize_pipeline(
                session,
                cohort_id,
                anonymization_enabled,
                cohort_name,
                source_path,
                modality=modality,
            )
            session.commit()
            session.expunge_all()
            logger.info(
                "Reinitialized pipeline for cohort=%d (modality=%s): %d steps",
                cohort_id, modality, len(steps)
            )
            return steps
    
    # =========================================================================
    # API RESPONSE BUILDERS
    # =========================================================================
    
    def build_stages_response(
        self,
        cohort_id: int,
        modality: str = "imaging",
    ) -> list[dict[str, Any]]:
        """Build frontend-compatible stages array from pipeline steps.
        
        This returns the same format as the old cohorts.stages JSON column.
        
        Args:
            cohort_id: The cohort ID.
            modality: Cohort modality ("imaging" or "meg"). Determines which
                stage-id family (DICOM vs MEG) to look for in `steps`; a
                mismatch here means every step is filtered out and this
                returns an empty list. Defaults to "imaging" to preserve
                existing behaviour for every caller that doesn't know about
                MEG yet.
            
        Returns:
            List of stage dictionaries for API response.
        """
        with SessionLocal() as session:
            all_steps = repository.get_steps_for_cohort(session, cohort_id)
            return self._build_stages_from_steps(all_steps, modality)
    
    def _build_stages_from_steps(
        self,
        steps: list[NilsDatasetPipelineStep],
        modality: str = "imaging",
    ) -> list[dict[str, Any]]:
        """Build stages array from step list.
        
        Args:
            steps: List of pipeline steps.
            modality: Cohort modality ("imaging" or "meg"); selects which
                stage-id family to match against `steps`.
            
        Returns:
            List of stage dictionaries.
        """
        stages = []
        
        for stage_config in _stages_for_modality(modality):
            stage_steps = [s for s in steps if s.stage_id == stage_config["id"]]
            if not stage_steps:
                continue
            
            if stage_config["steps"]:
                # Multi-step stage (sorting)
                overall_progress = (
                    sum(s.progress for s in stage_steps) // len(stage_steps)
                    if stage_steps else 0
                )
                overall_status = self._compute_stage_status(stage_steps)
                
                # Get job_id from running step if any
                job_id = None
                for s in stage_steps:
                    if s.current_job_id:
                        job_id = s.current_job_id
                        break
                
                stages.append({
                    "id": stage_config["id"],
                    "title": stage_config["title"],
                    "description": stage_config["description"],
                    "status": overall_status,
                    "progress": overall_progress,
                    "job_id": job_id,
                    "steps": {
                        s.step_id: (
                            "completed" if s.handover_data is not None else s.status
                        )
                        for s in stage_steps
                        if s.step_id
                    },
                    "config": stage_steps[0].config if stage_steps else {},
                })
            else:
                # Simple stage
                step = stage_steps[0]
                stages.append({
                    "id": step.stage_id,
                    "title": step.title,
                    "description": step.description,
                    "status": step.status,
                    "progress": step.progress,
                    "job_id": step.current_job_id,
                    "config": step.config,
                    "metrics": step.metrics,
                })
        
        return stages
    
    def _compute_stage_status(
        self,
        steps: list[NilsDatasetPipelineStep],
    ) -> str:
        """Compute overall status for a multi-step stage.
        
        Args:
            steps: List of steps in the stage.
            
        Returns:
            Overall status string.
        """
        if not steps:
            return "blocked"
        
        statuses = [s.status for s in steps]
        
        # If any running, stage is running
        if "running" in statuses:
            return "running"
        
        # If any failed, stage is failed
        if "failed" in statuses:
            return "failed"
        
        # If all completed, stage is completed
        # (check handover_data for true completion)
        all_completed = all(s.handover_data is not None for s in steps)
        if all_completed:
            return "completed"
        
        # If any pending, stage is pending
        if "pending" in statuses:
            return "pending"
        
        # Otherwise blocked
        return "blocked"


# Singleton instance
nils_pipeline_service = NilsDatasetPipelineService()
