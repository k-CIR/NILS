"""High-level cohort service orchestrating persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from db.session import engine, session_scope
from metadata_db.session import engine as metadata_engine

from .models import Base, Cohort, CohortDTO, CreateCohortPayload
from . import repository
from .stats import get_cohort_stats, get_all_cohort_stats

logger = logging.getLogger(__name__)


def _normalize_cohort_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("Cohort name cannot be blank")
    return normalized


VALID_COHORT_MODALITIES = {"imaging", "meg"}


def _normalize_cohort_modality(value: str) -> str:
    normalized = (value or "imaging").strip().lower()
    if normalized not in VALID_COHORT_MODALITIES:
        raise ValueError(
            f"Unknown cohort modality '{value}'. Expected one of: "
            f"{sorted(VALID_COHORT_MODALITIES)}"
        )
    return normalized


class CohortService:
    def __init__(self) -> None:
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        try:
            # Create only cohort-specific tables (not pipeline_steps which needs jobs table)
            # Pipeline steps table is created by the migration script with raw SQL
            # Use .tables.values() instead of .sorted_tables to avoid FK resolution errors
            tables_to_create = [
                t
                for t in Base.metadata.tables.values()
                if t.name != "nils_dataset_pipeline_steps"
            ]
            Base.metadata.create_all(engine, tables=tables_to_create)

            # Ensure new pipeline steps table exists
            try:
                from nils_dataset_pipeline.migrations import ensure_migrated

                ensure_migrated()
            except Exception as e:
                logger.warning("Pipeline migration failed (non-fatal): %s", e)

            # Cohort modality/type column (imaging vs. MEG parallel track).
            try:
                from cohorts.migrations.add_cohort_modality_column import (
                    ensure_migrated as ensure_cohort_modality_column,
                )

                ensure_cohort_modality_column()
            except Exception as e:
                logger.warning(
                    "Cohort modality-column migration failed (non-fatal): %s",
                    e,
                )

            # Body Part QC: stage/commit columns (Milestone C).
            try:
                from cohorts.migrations.add_body_part_stage_columns import (
                    ensure_migrated as ensure_body_part_stage_columns,
                )

                ensure_body_part_stage_columns()
            except Exception as e:
                logger.warning(
                    "Body Part QC stage-columns migration failed (non-fatal): %s",
                    e,
                )

            # MASQC saved sessions: display_id_type / only_multi_stack columns.
            try:
                from cohorts.migrations.add_masqc_session_columns import (
                    ensure_migrated as ensure_masqc_session_columns,
                )

                ensure_masqc_session_columns()
            except Exception as e:
                logger.warning(
                    "MASQC saved-sessions columns migration failed (non-fatal): %s",
                    e,
                )

            # Cohort Main QC: per-session acknowledgement table.
            try:
                from cohorts.migrations.add_cohort_main_qc_ack_table import (
                    ensure_migrated as ensure_cohort_main_qc_ack_table,
                )

                ensure_cohort_main_qc_ack_table()
            except Exception as e:
                logger.warning(
                    "Cohort Main QC ack-table migration failed (non-fatal): %s",
                    e,
                )

            # Global Body Part QC: sample pool + model registry.
            try:
                from cohorts.migrations.add_global_body_part_tables import (
                    ensure_migrated as ensure_global_body_part_tables,
                )

                ensure_global_body_part_tables()
            except Exception as e:
                logger.warning(
                    "Global Body Part QC tables migration failed (non-fatal): %s",
                    e,
                )

        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Failed to initialize cohort tables") from exc
        self._initialized = True

    def create_cohort(self, payload: CreateCohortPayload) -> CohortDTO:
        self._ensure_initialized()
        normalized_name = _normalize_cohort_name(payload.name)
        tags = [tag for tag in payload.tags if tag.strip()] if payload.tags else []

        # Track what we need to initialize after commit
        pipeline_init_params: tuple[int, bool, str, str, str] | None = None
        is_existing = False
        dto: CohortDTO

        with session_scope() as session:
            existing = repository.get_cohort_by_name(session, normalized_name)
            if existing:
                is_existing = True
                existing.source_path = payload.source_path
                existing.description = payload.description
                existing.tags = tags
                existing.anonymization_enabled = payload.anonymization_enabled
                # Modality is fixed at creation and intentionally not
                # updated here: changing it after pipeline initialization
                # would invalidate already-created pipeline steps.
                existing.updated_at = datetime.now(timezone.utc)
                session.flush()
                session.refresh(existing)
                dto = CohortDTO.model_validate(existing)
                pipeline_init_params = (
                    existing.id,
                    payload.anonymization_enabled,
                    normalized_name,
                    payload.source_path,
                    existing.modality,
                )
            else:
                normalized_modality = _normalize_cohort_modality(payload.modality)
                cohort = repository.create_cohort(
                    session,
                    name=normalized_name,
                    source_path=payload.source_path,
                    description=payload.description,
                    tags=tags,
                    anonymization_enabled=payload.anonymization_enabled,
                    modality=normalized_modality,
                )
                session.refresh(cohort)
                dto = CohortDTO.model_validate(cohort)
                pipeline_init_params = (
                    cohort.id,
                    payload.anonymization_enabled,
                    normalized_name,
                    payload.source_path,
                    normalized_modality,
                )

        # Initialize pipeline AFTER the cohort is committed
        # (pipeline service uses its own session, needs cohort to exist in DB)
        if pipeline_init_params:
            if is_existing:
                self._reinitialize_pipeline_steps(*pipeline_init_params)
            else:
                self._initialize_pipeline_steps(*pipeline_init_params)

            # Enrich DTO with the newly created stages
            dto.stages = self.get_stages_from_pipeline(dto.id)

        return dto

    def get_cohort(self, cohort_id: int) -> Optional[CohortDTO]:
        self._ensure_initialized()
        with session_scope() as session:
            cohort = repository.get_cohort(session, cohort_id)
            if not cohort:
                return None
            dto = CohortDTO.model_validate(cohort)

            # Try to get stages from new pipeline system first
            pipeline_stages = self.get_stages_from_pipeline(cohort_id)
            if pipeline_stages:
                dto.stages = pipeline_stages
            # else: keep stages from JSON column (migration not yet complete)

            # Enrich with stats from metadata database
            try:
                stats = get_cohort_stats(cohort.name, engine=metadata_engine)
                dto.total_subjects = stats["total_subjects"]
                dto.total_sessions = stats["total_sessions"]
                dto.total_series = stats["total_series"]
            except Exception:
                # If metadata DB is unavailable, keep defaults
                pass
            return dto

    def get_cohort_by_name(self, name: str) -> Optional[CohortDTO]:
        self._ensure_initialized()
        with session_scope() as session:
            cohort = repository.get_cohort_by_name(session, name)
            if not cohort:
                return None
            return CohortDTO.model_validate(cohort)

    def list_cohorts(self) -> list[CohortDTO]:
        self._ensure_initialized()
        with session_scope() as session:
            cohorts = repository.list_cohorts(session)
            dtos = [CohortDTO.model_validate(c) for c in cohorts]

            # Enrich with stages from new pipeline system
            for dto in dtos:
                pipeline_stages = self.get_stages_from_pipeline(dto.id)
                if pipeline_stages:
                    dto.stages = pipeline_stages

            # Enrich with stats from metadata database
            try:
                all_stats = get_all_cohort_stats(engine=metadata_engine)
                for dto in dtos:
                    cohort_name = dto.name.lower()
                    if cohort_name in all_stats:
                        dto.total_subjects = all_stats[cohort_name]["total_subjects"]
                        dto.total_sessions = all_stats[cohort_name]["total_sessions"]
                        dto.total_series = all_stats[cohort_name]["total_series"]
            except Exception:
                # If metadata DB is unavailable, keep defaults
                pass

            return dtos

    # =========================================================================
    # Pipeline Step Management
    # =========================================================================

    def _initialize_pipeline_steps(
        self,
        cohort_id: int,
        anonymization_enabled: bool,
        cohort_name: str,
        source_path: str,
        modality: str = "imaging",
    ) -> None:
        """Initialize pipeline steps for a new cohort."""
        try:
            from nils_dataset_pipeline import nils_pipeline_service

            nils_pipeline_service.initialize_for_cohort(
                cohort_id=cohort_id,
                anonymization_enabled=anonymization_enabled,
                cohort_name=cohort_name,
                source_path=source_path,
                modality=modality,
            )
        except Exception as e:
            logger.warning(
                "Failed to initialize pipeline steps for cohort %d: %s", cohort_id, e
            )

    def _reinitialize_pipeline_steps(
        self,
        cohort_id: int,
        anonymization_enabled: bool,
        cohort_name: str,
        source_path: str,
        modality: str = "imaging",
    ) -> None:
        """Reinitialize pipeline steps for an existing cohort (recreates all steps)."""
        try:
            from nils_dataset_pipeline import nils_pipeline_service

            nils_pipeline_service.reinitialize_pipeline(
                cohort_id=cohort_id,
                anonymization_enabled=anonymization_enabled,
                cohort_name=cohort_name,
                source_path=source_path,
                modality=modality,
            )
        except Exception as e:
            logger.warning(
                "Failed to reinitialize pipeline steps for cohort %d: %s", cohort_id, e
            )

    def get_stages_from_pipeline(self, cohort_id: int) -> list[dict]:
        """Get stages array from pipeline steps (new system).

        Returns the same format as the old cohorts.stages JSON column
        for backward compatibility with the API.
        """
        try:
            from nils_dataset_pipeline import nils_pipeline_service

            return nils_pipeline_service.build_stages_response(cohort_id)
        except Exception as e:
            logger.warning(
                "Failed to get stages from pipeline for cohort %d: %s", cohort_id, e
            )
            return []


cohort_service = CohortService()
