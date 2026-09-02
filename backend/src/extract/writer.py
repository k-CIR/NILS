"""Database writer for extraction batches."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import AbstractAsyncContextManager
from typing import Awaitable, Callable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert

from metadata_db import bootstrap
from metadata_db.schema import (
    Cohort,
    CTSeriesDetails,
    Event,
    IngestConflict,
    Instance,
    MRISeriesDetails,
    PETSeriesDetails,
    Series,
    SeriesStack,
    Study,
    Subject,
    SubjectCohort,
    SubjectOtherIdentifier,
)

from .stack_utils import compute_stack_signature, build_stack_row, signature_from_stack_record
from .dicom_mappings import STACK_DEFINING_FIELDS
from metadata_db.session import SessionLocal

from .batching import BatchSizeController
from .config import DuplicatePolicy, ExtractionConfig
from .limits import build_parameter_chunk_plan, calculate_safe_instance_batch_rows
from .resume_index import ExistingPathIndex, split_subject_relative
from .profiler import get_global_profiler
from .subject_mapping import subject_code_gen
from .worker import InstancePayload, normalize_modality
from jobs.control import JobControl


logger = logging.getLogger(__name__)


ProgressCallback = Callable[[int, int], Awaitable[None] | None]
_CONTROL_POLL_SECONDS = 0.5

# Imaging modality -> observation_type_id for session event creation
_MODALITY_TO_OBSERVATION_TYPE: dict[str, int] = {"MR": 11, "CT": 12, "PT": 13, "PET": 13, "MEG": 16}

# Retry configuration for transient database errors (e.g., OOM under memory pressure)
# We retry indefinitely for transient errors - never skip data
_INITIAL_RETRY_DELAY_SECONDS = 2.0
_MAX_RETRY_DELAY_SECONDS = 120.0  # Cap backoff at 2 minutes
_RETRY_BACKOFF_MULTIPLIER = 2.0
_MIN_SUB_BATCH_SIZE = 10  # Minimum sub-batch size during retries


class Writer(AbstractAsyncContextManager["Writer"]):
    def __init__(
        self,
        *,
        config: ExtractionConfig,
        queue: asyncio.Queue,
        job_id: Optional[int],
        progress_cb: Optional[ProgressCallback],
        batch_controller: BatchSizeController,
        control: Optional[JobControl] = None,
        path_index: Optional[ExistingPathIndex] = None,
    ) -> None:
        self.config = config
        self.queue = queue
        self.job_id = job_id
        self.progress_cb = progress_cb
        self._batch_controller = batch_controller
        self._control = control
        self._path_index = path_index
        self._session = None
        self._cohort_id: Optional[int] = None
        self._normalized_cohort_name = (config.cohort_name or "").strip().lower()
        self._subject_cache: dict[tuple[str, str], int] = {}
        self._study_cache: dict[str, int] = {}
        self._event_cache: dict[tuple[int, int, str], int] = {}  # (subject_id, obs_type_id, date_str) -> event_id
        self._series_cache: dict[str, int] = {}
        # Stack caches: keyed by series_instance_uid for stable lookups
        self._stack_cache: dict[tuple, int] = {}  # signature -> series_stack_id
        self._series_stack_counter: dict[str, int] = {}  # series_instance_uid -> next stack_index
        self._subject_identifier_cache: set[int] = set()
        # Memory management: periodic cache pruning for long-running extractions
        self._subjects_completed = 0
        self._PRUNE_INTERVAL_SUBJECTS = 100  # Prune caches and update metrics every N subjects
        self._modality_fallback_logged: set[str] = set()
        self._subject_id_type_id = config.subject_id_type_id
        bootstrap()
        self._reported_safe_batch_rows = calculate_safe_instance_batch_rows()
        self._subjects_inserted = 0
        self._studies_inserted = 0
        self._series_inserted = 0
        self._stacks_inserted = 0
        self._instances_inserted = 0

    async def __aenter__(self) -> "Writer":
        self._session = SessionLocal()
        self._cohort_id = self._ensure_cohort(self._session)
        # Commit the cohort creation/lookup to release any row locks
        # This allows multiple writers to initialize concurrently
        self._session.commit()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        if self._session is not None:
            if exc:
                self._session.rollback()
            self._session.close()

    async def consume(self, total_subjects: int) -> None:
        assert self._session is not None
        session = self._session
        profiler = get_global_profiler()
        current_subject_key: str | None = None
        current_batch: list | None = None

        try:
            while True:
                await self._checkpoint()
                queue_start = time.perf_counter()
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=_CONTROL_POLL_SECONDS)
                    if profiler:
                        profiler.record("queue_get", time.perf_counter() - queue_start)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                subject_key, series_uid, batch, last_instance, completed = item
                current_subject_key = subject_key
                current_batch = batch
                if batch:
                    await self._checkpoint()
                    start = time.perf_counter()
                    # Use retry wrapper to handle transient errors (OOM, timeouts)
                    await self._write_batch_with_retry(session, batch)
                    self._update_path_index(batch)
                    write_duration = time.perf_counter() - start

                    commit_start = time.perf_counter()
                    session.commit()
                    commit_duration = time.perf_counter() - commit_start

                    total_duration = time.perf_counter() - start
                    self._batch_controller.record(len(batch), total_duration)

                    if profiler:
                        profiler.record("db_write_batch", write_duration)
                        profiler.record("db_commit", commit_duration)
                if completed:
                    await self._checkpoint()
                    commit_start = time.perf_counter()
                    session.commit()
                    if profiler:
                        profiler.record("db_commit_final", time.perf_counter() - commit_start)
                    self._prune_caches_if_needed()

        except Exception as exc:
            import traceback
            sample_paths: list[str] = []
            if current_batch:
                sample_paths = [p.dicom_file_path for p in current_batch[:5] if hasattr(p, "dicom_file_path") and p.dicom_file_path]
            logger.error(
                "Writer task crashed job_id=%s subject=%s batch_size=%s sample_files=%s\n%s",
                self.job_id,
                current_subject_key,
                len(current_batch) if current_batch else 0,
                sample_paths,
                traceback.format_exc(),
            )
            raise

    def _prune_caches_if_needed(self) -> None:
        """Prune lookup caches periodically to prevent unbounded memory growth.
        
        Called after each subject completion. Pruning is subject-based (not batch
        or entry-count based) to maintain cache efficiency during subject processing.
        
        For long-running extractions (days, millions of files), caches can grow to
        several GB. This method caps memory usage by clearing caches periodically,
        accepting the trade-off of occasional cache misses requiring DB lookups.
        
        Also saves current metrics to the job record, so frontend polling gets
        accurate counts without running expensive COUNT(*) queries on the metadata DB.
        """
        self._subjects_completed += 1
        if self._subjects_completed < self._PRUNE_INTERVAL_SUBJECTS:
            return
        
        # Save metrics to job record - eliminates need for COUNT(*) queries during polling
        # This is the KEY FIX for PostgreSQL OOM: frontend gets metrics from job record
        # instead of running expensive COUNT(*) on 30M+ rows
        if self.job_id is not None:
            from jobs.service import job_service
            try:
                job_service.update_metrics(self.job_id, self.snapshot_metrics())
            except Exception as exc:
                # Best-effort - don't crash extraction if metrics update fails
                logger.warning("Failed to update job metrics: %s", exc)
        
        logger.info(
            "Pruning caches after %d subjects: stacks=%d, series=%d, studies=%d, events=%d, subjects=%d, counters=%d",
            self._subjects_completed,
            len(self._stack_cache),
            len(self._series_cache),
            len(self._study_cache),
            len(self._event_cache),
            len(self._subject_cache),
            len(self._series_stack_counter),
        )
        
        # Clear all lookup caches - they will be rebuilt from DB on cache miss
        self._subject_cache.clear()
        self._study_cache.clear()
        self._event_cache.clear()
        self._series_cache.clear()
        self._stack_cache.clear()
        self._series_stack_counter.clear()
        self._subject_identifier_cache.clear()
        
        self._subjects_completed = 0

    def _invalidate_caches_for_batch(self, batch: list[InstancePayload]) -> None:
        """Remove cache entries for items in a rolled-back batch.
        
        After a transaction rollback, any IDs we cached during that transaction
        are now invalid (the rows don't exist in the database). We must remove
        them so the next retry will re-query or re-insert properly.
        
        Without this, the retry would use stale IDs from the cache, causing
        FK violations and an infinite retry loop.
        """
        for payload in batch:
            # Clear series cache entry
            self._series_cache.pop(payload.series_uid, None)
            
            # Clear study cache entry
            self._study_cache.pop(payload.study_uid, None)
            
            # Clear stack cache entry
            sig = compute_stack_signature(payload.series_uid, payload.instance_fields)
            self._stack_cache.pop(sig, None)
            
            # Clear subject cache entry
            subject_key = (payload.patient_id, self._normalized_cohort_name)
            self._subject_cache.pop(subject_key, None)
        
        logger.debug(
            "Invalidated cache entries for %d items after rollback job_id=%s",
            len(batch),
            self.job_id,
        )

    async def _write_batch_with_retry(self, session, batch: list[InstancePayload]) -> bool:
        """Write a batch with retry logic and adaptive batch size reduction.
        
        When transient errors occur (e.g., PostgreSQL OOM due to system memory pressure),
        this method will:
        1. Rollback the failed transaction
        2. Reduce batch size to ease memory pressure
        3. Wait with exponential backoff
        4. Retry the smaller sub-batches
        
        Returns True if batch was written (fully or partially), False if completely failed.
        """
        # Try writing the full batch first
        try:
            self._write_batch(session, batch)
            return True
        except OperationalError as exc:
            # Check if it's a transient error worth retrying
            error_msg = str(exc).lower()
            is_transient = "out of memory" in error_msg or "could not extend" in error_msg
            if not is_transient:
                raise
            
            sample = batch[0]
            logger.warning(
                "Transient DB error, will retry with smaller batches: job_id=%s subject=%s error=%s",
                self.job_id,
                sample.subject_key,
                str(exc)[:200],
            )
            session.rollback()
            self._invalidate_caches_for_batch(batch)  # Clear stale IDs from rolled-back transaction
        except SQLAlchemyError:
            # Non-transient errors - rollback and re-raise
            session.rollback()
            self._invalidate_caches_for_batch(batch)  # Clear stale IDs from rolled-back transaction
            raise
        
        # Retry with progressively smaller sub-batches
        return await self._retry_with_smaller_batches(session, batch)

    async def _retry_with_smaller_batches(self, session, batch: list[InstancePayload]) -> bool:
        """Retry a failed batch by splitting into smaller sub-batches with backoff.
        
        Uses exponential backoff between retries and progressively smaller batch sizes
        to reduce memory pressure on the database.
        
        CRITICAL: This method retries indefinitely until ALL data is written.
        We never skip data - the extraction will wait as long as needed for
        system resources to become available.
        """
        original_batch_size = len(batch)
        remaining = list(batch)  # Items still to be written
        delay = _INITIAL_RETRY_DELAY_SECONDS
        attempt = 0
        
        while remaining:
            attempt += 1
            
            # Reduce batch size progressively, but cap at minimum
            # After several attempts, we'll be at minimum size
            current_batch_size = max(
                _MIN_SUB_BATCH_SIZE,
                original_batch_size // (2 ** min(attempt, 6))  # Cap reduction at 64x
            )
            
            logger.info(
                "Retry attempt %d: %d items remaining, sub-batch size %d (delay=%.1fs) job_id=%s",
                attempt,
                len(remaining),
                current_batch_size,
                delay,
                self.job_id,
            )
            
            # Wait for memory pressure to ease
            await asyncio.sleep(delay)
            await self._checkpoint()  # Check for cancellation during wait
            
            # Try to write remaining items in sub-batches
            still_remaining = []
            
            for i in range(0, len(remaining), current_batch_size):
                sub_batch = remaining[i:i + current_batch_size]
                try:
                    self._write_batch(session, sub_batch)
                    session.commit()
                    # Success! These items are done
                except OperationalError as exc:
                    error_msg = str(exc).lower()
                    is_transient = "out of memory" in error_msg or "could not extend" in error_msg
                    session.rollback()
                    self._invalidate_caches_for_batch(sub_batch)  # Clear stale IDs
                    
                    if is_transient:
                        # Keep these items for retry
                        still_remaining.extend(sub_batch)
                        logger.warning(
                            "Sub-batch failed (transient), will retry: %d items, error=%s",
                            len(sub_batch),
                            str(exc)[:100],
                        )
                    else:
                        # Non-transient error - still keep for retry, might be temporary
                        still_remaining.extend(sub_batch)
                        logger.warning(
                            "Sub-batch failed (non-transient), will retry: %d items, error=%s",
                            len(sub_batch),
                            str(exc)[:200],
                        )
                except SQLAlchemyError as exc:
                    session.rollback()
                    self._invalidate_caches_for_batch(sub_batch)  # Clear stale IDs
                    # Keep for retry - we never give up
                    still_remaining.extend(sub_batch)
                    logger.warning(
                        "Sub-batch failed (SQLAlchemy error), will retry: %d items, error=%s",
                        len(sub_batch),
                        str(exc)[:200],
                    )
            
            # Update remaining items
            written_count = len(remaining) - len(still_remaining)
            if written_count > 0:
                logger.info(
                    "Progress: wrote %d/%d items this attempt, %d remaining job_id=%s",
                    written_count,
                    len(remaining),
                    len(still_remaining),
                    self.job_id,
                )
                # Reset delay when making progress
                delay = _INITIAL_RETRY_DELAY_SECONDS
            else:
                # No progress - increase delay (exponential backoff with cap)
                delay = min(delay * _RETRY_BACKOFF_MULTIPLIER, _MAX_RETRY_DELAY_SECONDS)
                logger.warning(
                    "No progress this attempt, increasing delay to %.1fs job_id=%s",
                    delay,
                    self.job_id,
                )
            
            remaining = still_remaining
        
        logger.info(
            "Successfully recovered full batch after %d retry attempts job_id=%s",
            attempt,
            self.job_id,
        )
        return True

    def _ensure_subject_cohort_membership(self, session, batch: list[InstancePayload]) -> None:
        """Ensure every subject in the batch is linked to the current cohort.

        Called unconditionally before SOP deduplication so that cross-cohort
        subjects (same subject_code, different cohort) are always registered,
        even when all their instance SOPs already exist in the DB.
        """
        seen: dict[str, InstancePayload] = {}
        for payload in batch:
            if payload.subject_code and payload.subject_code not in seen:
                seen[payload.subject_code] = payload

        for subject_code, payload in seen.items():
            # Resolve subject_id — check subject_cache first, then DB by subject_code
            subject_id = self._subject_cache.get((payload.patient_id, self._normalized_cohort_name))
            if subject_id is None:
                subject_id = session.execute(
                    select(Subject.subject_id).where(Subject.subject_code == subject_code)
                ).scalar_one_or_none()
            if subject_id is None:
                continue  # subject doesn't exist yet — _bulk_ensure_subjects will create it

            if subject_id in self._subject_identifier_cache:
                continue  # already handled this session

            # Link to current cohort (idempotent)
            if self._cohort_id is not None:
                session.execute(
                    insert(SubjectCohort)
                    .values(subject_id=subject_id, cohort_id=self._cohort_id)
                    .on_conflict_do_nothing(
                        index_elements=[SubjectCohort.subject_id, SubjectCohort.cohort_id]
                    )
                )

            # Ensure cohort-specific identifier (idempotent)
            self._ensure_subject_identifier(session, subject_id, payload)
            self._subject_identifier_cache.add(subject_id)

    def _write_batch(self, session, batch: list[InstancePayload]) -> None:
        """Write a batch of instances using parents-first pattern for efficiency.
        
        This method uses a parents-first insertion pattern within a single transaction:
        1. Ensure subject-cohort linkage (always, even for fully-duplicate batches)
        2. Pre-filter batch to only new SOPs (SELECT existing, filter out duplicates)
        3. Create parent records (subject/study/series/stack) for new instances
        4. Insert instances WITH correct series_id and stack_id already set
        
        Benefits over instance-first pattern:
        - No dead rows: Instances are inserted once with correct FK, no UPDATE needed
        - No orphans: If INSERT fails, entire transaction rolls back including parents
        - ~50% less storage: No MVCC dead tuple overhead
        
        This is safe for single-writer mode (db_writer_pool_size=1) which is the
        recommended configuration for large extractions.
        """
        if not batch:
            return

        # Step 1: Always ensure subject-cohort linkage before SOP deduplication.
        # A subject from cohort A appearing in cohort B has all SOPs already in DB,
        # but must still be linked to the new cohort and have its identifier recorded.
        self._ensure_subject_cohort_membership(session, batch)

        # Step 2: Pre-filter to only new SOPs (no INSERT yet)
        existing_sops = self._get_existing_sops(session, batch)
        new_batch = [p for p in batch if p.sop_uid not in existing_sops]
        
        if not new_batch:
            # All instances were duplicates - log if needed
            self._log_duplicate_sops(session, batch, existing_sops)
            return
        
        # Step 3: Create parent hierarchy FIRST (in same transaction)
        # If INSERT fails later, these will rollback too - no orphans
        subject_ids = self._bulk_ensure_subjects(session, new_batch)
        study_ids = self._bulk_ensure_studies(session, new_batch, subject_ids)
        self._link_studies_to_events(session, new_batch, study_ids, subject_ids)
        series_ids = self._bulk_ensure_series(session, new_batch, subject_ids, study_ids)
        stack_ids = self._bulk_ensure_stacks(session, new_batch, series_ids)
        
        # Step 4: Insert instances WITH correct FKs (no NULL, no UPDATE needed)
        self._insert_instances_with_fks(session, new_batch, series_ids, stack_ids)
        
        # Log any duplicates we skipped
        if existing_sops:
            self._log_duplicate_sops(session, batch, existing_sops)

    def _update_path_index(self, batch: list[InstancePayload]) -> None:
        if not self._path_index:
            return
        for payload in batch:
            subject_key, subject_relative = split_subject_relative(payload.file_path)
            if subject_key:
                self._path_index.add(subject_key, subject_relative)

    def _ensure_cohort(self, session) -> int:
        desired_path = str(self.config.raw_root)
        # Use case-insensitive lookup to match existing cohorts regardless of stored case
        stmt = select(Cohort).where(func.lower(Cohort.name) == self._normalized_cohort_name)
        cohort = session.execute(stmt).scalar_one_or_none()
        if cohort:
            if getattr(cohort, "path", None) != desired_path:
                session.execute(
                    update(Cohort)
                    .where(Cohort.cohort_id == cohort.cohort_id)
                    .values(path=desired_path)
                )
                session.flush()
            return cohort.cohort_id

        insert_stmt = insert(Cohort).values(
            name=self._normalized_cohort_name,
            owner="system",
            path=desired_path,
        )
        try:
            result = session.execute(insert_stmt.returning(Cohort.cohort_id))
            cohort_id = result.scalar_one()
            session.flush()
            return cohort_id
        except IntegrityError:
            session.rollback()
            # Re-query with case-insensitive lookup in case of race condition
            cohort = session.execute(stmt).scalar_one_or_none()
            if cohort is None:
                # Fallback: try direct lookup if case-insensitive didn't work
                fallback_stmt = select(Cohort).where(Cohort.name == self._normalized_cohort_name)
                cohort = session.execute(fallback_stmt).scalar_one_or_none()
            if cohort is None:
                raise RuntimeError(f"Failed to find or create cohort '{self._normalized_cohort_name}'")
            if getattr(cohort, "path", None) != desired_path:
                session.execute(
                    update(Cohort)
                    .where(Cohort.cohort_id == cohort.cohort_id)
                    .values(path=desired_path)
                )
                session.flush()
            return cohort.cohort_id

    def _bulk_ensure_subjects(self, session, batch: list[InstancePayload]) -> list[int]:
        subject_ids: list[int] = [0] * len(batch)
        pending: dict[tuple[str, str], dict] = {}
        representative_payloads: dict[int, InstancePayload] = {}

        for idx, payload in enumerate(batch):
            if not payload.patient_id:
                raise ValueError("PatientID is required for subject ingestion")
            cache_key = (payload.patient_id, self._normalized_cohort_name)
            cached = self._subject_cache.get(cache_key)
            if cached is not None:
                subject_ids[idx] = cached
                representative_payloads.setdefault(cached, payload)
                continue
            entry = pending.setdefault(cache_key, {"indices": [], "payload": payload})
            entry["indices"].append(idx)

        # Existing mappings via subject_other_identifiers
        if pending and self._subject_id_type_id is not None:
            patient_ids = [key[0] for key in pending.keys()]
            identifier_stmt = (
                select(SubjectOtherIdentifier.other_identifier, SubjectOtherIdentifier.subject_id)
                .where(SubjectOtherIdentifier.id_type_id == self._subject_id_type_id)
                .where(SubjectOtherIdentifier.other_identifier.in_(patient_ids))
            )
            identifier_rows = session.execute(identifier_stmt).all()
            if identifier_rows:
                for row in identifier_rows:
                    cache_key = (row.other_identifier, self._normalized_cohort_name)
                    if cache_key not in pending:
                        continue
                    subject_id = row.subject_id
                    subject_ids_for_entry = pending.pop(cache_key)
                    for idx in subject_ids_for_entry["indices"]:
                        subject_ids[idx] = subject_id
                    self._subject_cache[cache_key] = subject_id
                    representative_payloads.setdefault(subject_id, subject_ids_for_entry["payload"])

        if pending:
            insert_rows = []
            code_to_key: dict[str, tuple[str, str]] = {}
            for cache_key, entry in pending.items():
                payload = entry["payload"]
                subject_code = payload.subject_code or subject_code_gen(payload.patient_id, self._normalized_cohort_name)
                code_to_key[subject_code] = cache_key
                insert_rows.append(
                    {
                        "subject_code": subject_code,
                        "patient_name": payload.patient_name,
                        "patient_sex": None,
                        "patient_birth_date": None,
                    }
                )

            inserted = (
                session.execute(
                    insert(Subject)
                    .values(insert_rows)
                    .on_conflict_do_nothing()
                    .returning(Subject.subject_code, Subject.subject_id)
                )
                .mappings()
                .all()
            )
            self._subjects_inserted += len(inserted)
            code_to_id = {row["subject_code"]: row["subject_id"] for row in inserted}
            missing_codes = [code for code in code_to_key.keys() if code not in code_to_id]
            if missing_codes:
                existing = (
                    session.execute(
                        select(Subject.subject_code, Subject.subject_id).where(Subject.subject_code.in_(missing_codes))
                    )
                    .mappings()
                    .all()
                )
                for row in existing:
                    code_to_id[row["subject_code"]] = row["subject_id"]

            for code, subject_id in code_to_id.items():
                cache_key = code_to_key[code]
                entry = pending.pop(cache_key, None)
                if not entry:
                    continue
                for idx in entry["indices"]:
                    subject_ids[idx] = subject_id
                self._subject_cache[cache_key] = subject_id
                representative_payloads.setdefault(subject_id, entry["payload"])

        resolved_ids = {sid for sid in subject_ids if sid}
        if not resolved_ids:
            raise RuntimeError("Failed to resolve any subjects in batch")

        if self._cohort_id is not None:
            rows = [
                {"subject_id": sid, "cohort_id": self._cohort_id}
                for sid in resolved_ids
            ]
            insert_stmt = insert(SubjectCohort).values(rows)
            bind = session.get_bind()
            dialect_name = bind.dialect.name if bind is not None else ""

            if dialect_name in {"postgresql", "sqlite"}:
                insert_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=[SubjectCohort.subject_id, SubjectCohort.cohort_id]
                )
                session.execute(insert_stmt)
            else:
                existing_pairs = {
                    (row.subject_id, row.cohort_id)
                    for row in session.execute(
                        select(SubjectCohort.subject_id, SubjectCohort.cohort_id)
                        .where(SubjectCohort.cohort_id == self._cohort_id)
                        .where(SubjectCohort.subject_id.in_(resolved_ids))
                    )
                }
                pending_rows = [
                    row for row in rows if (row["subject_id"], row["cohort_id"]) not in existing_pairs
                ]
                if pending_rows:
                    session.execute(insert(SubjectCohort).values(pending_rows))

        if self._subject_id_type_id is not None:
            for sid, payload in representative_payloads.items():
                if sid in self._subject_identifier_cache:
                    continue
                self._ensure_subject_identifier(session, sid, payload)
                self._subject_identifier_cache.add(sid)

        return subject_ids

    def _ensure_subject_identifier(self, session, subject_id: int, payload: InstancePayload) -> None:
        if self._subject_id_type_id is None or not payload.patient_id:
            return

        stmt = select(SubjectOtherIdentifier).where(
            SubjectOtherIdentifier.subject_id == subject_id,
            SubjectOtherIdentifier.id_type_id == self._subject_id_type_id,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        if existing:
            current_value = getattr(existing, "other_identifier", None)
            if current_value != payload.patient_id:
                self._log_conflict(
                    session,
                    "subject_identifier",
                    payload.subject_code,
                    "Conflicting patient identifier for subject",
                    payload.file_path,
                )
                session.execute(
                    update(SubjectOtherIdentifier)
                    .where(SubjectOtherIdentifier.subject_other_identifier_id == existing.subject_other_identifier_id)
                    .values(other_identifier=payload.patient_id)
                )
            return

        insert_stmt = (
            insert(SubjectOtherIdentifier)
            .values(
                subject_id=subject_id,
                id_type_id=self._subject_id_type_id,
                other_identifier=payload.patient_id,
            )
            .on_conflict_do_nothing()
        )
        session.execute(insert_stmt)

    def _bulk_ensure_studies(
        self,
        session,
        batch: list[InstancePayload],
        subject_ids: list[int],
    ) -> list[int]:
        study_ids: list[int] = [0] * len(batch)
        pending: dict[str, dict] = {}

        for idx, payload in enumerate(batch):
            cached = self._study_cache.get(payload.study_uid)
            if cached:
                study_ids[idx] = cached
                continue
            entry = pending.setdefault(
                payload.study_uid,
                {"indices": [], "payload": payload, "subject_id": subject_ids[idx]},
            )
            entry["indices"].append(idx)

        if pending:
            stmt = select(Study).where(Study.study_instance_uid.in_(pending.keys()))
            for existing in session.execute(stmt).scalars():
                entry = pending.pop(existing.study_instance_uid, None)
                if entry is None:
                    continue
                study_id = existing.study_id
                if existing.subject_id != entry["subject_id"]:
                    self._log_conflict(
                        session,
                        "study",
                        existing.study_instance_uid,
                        "Study assigned to a different subject; re-linking based on DICOM tags",
                        entry["payload"].file_path,
                    )
                    session.execute(
                        update(Study)
                        .where(Study.study_id == study_id)
                        .values(subject_id=entry["subject_id"])
                    )
                    session.flush()
                self._study_cache[existing.study_instance_uid] = study_id
                for idx in entry["indices"]:
                    study_ids[idx] = study_id

        if pending:
            rows = []
            for uid, entry in pending.items():
                values = {"study_instance_uid": uid, "subject_id": entry["subject_id"]}
                values.update(entry["payload"].study_fields)
                rows.append(values)
            inserted = (
                session.execute(
                    insert(Study)
                    .values(rows)
                    .on_conflict_do_nothing()
                    .returning(Study.study_instance_uid, Study.study_id)
                )
                .mappings()
                .all()
            )
            self._studies_inserted += len(inserted)
            inserted_map = {row["study_instance_uid"]: row["study_id"] for row in inserted}
            remaining = [uid for uid in pending.keys() if uid not in inserted_map]
            if remaining:
                existing_rows = (
                    session.execute(
                        select(Study.study_instance_uid, Study.study_id).where(Study.study_instance_uid.in_(remaining))
                    )
                    .mappings()
                    .all()
                )
                for row in existing_rows:
                    inserted_map[row["study_instance_uid"]] = row["study_id"]
            for uid, entry in pending.items():
                study_id = inserted_map[uid]
                self._study_cache[uid] = study_id
                for idx in entry["indices"]:
                    study_ids[idx] = study_id

        if any(study_id == 0 for study_id in study_ids):
            raise RuntimeError("Failed to resolve study identifiers for batch")

        return study_ids

    def _link_studies_to_events(
        self,
        session,
        batch: list[InstancePayload],
        study_ids: list[int],
        subject_ids: list[int],
    ) -> None:
        """Ensure each study has a corresponding imaging session event.

        A session is defined as (subject_id, observation_type_id, study_date).
        Multiple studies on the same day for the same subject and modality share
        one event row.
        """
        # Collect unique sessions needing events from this batch
        # session_key = (subject_id, obs_type_id, date_key_str)
        # We also track the original date value for insertion.
        sessions_needed: dict[tuple[int, int, str], list[int]] = {}
        session_date_values: dict[tuple[int, int, str], object] = {}  # key -> original date value
        for idx, payload in enumerate(batch):
            obs_type_id = _MODALITY_TO_OBSERVATION_TYPE.get(payload.modality)
            if obs_type_id is None:
                continue
            study_date = (payload.study_fields or {}).get("study_date")
            if study_date is None:
                continue
            date_key = str(study_date)
            key = (subject_ids[idx], obs_type_id, date_key)
            sessions_needed.setdefault(key, []).append(study_ids[idx])
            if key not in session_date_values:
                session_date_values[key] = study_date

        if not sessions_needed:
            return

        # Resolve event_ids from cache or DB
        event_map: dict[tuple[int, int, str], int] = {}
        uncached_keys: list[tuple[int, int, str]] = []
        for key in sessions_needed:
            cached = self._event_cache.get(key)
            if cached:
                event_map[key] = cached
            else:
                uncached_keys.append(key)

        # Batch-lookup existing events for uncached keys
        if uncached_keys:
            from sqlalchemy import and_, or_, cast, Date
            conditions = [
                and_(
                    Event.subject_id == k[0],
                    Event.observation_type_id == k[1],
                    Event.event_date == cast(session_date_values.get(k, k[2]), Date),
                )
                for k in uncached_keys
            ]
            if conditions:
                existing = session.execute(
                    select(Event).where(or_(*conditions))
                ).scalars().all()
                for evt in existing:
                    k = (evt.subject_id, evt.observation_type_id, str(evt.event_date))
                    event_map[k] = evt.event_id
                    self._event_cache[k] = evt.event_id

        # Insert missing events
        still_missing = [k for k in uncached_keys if k not in event_map]
        if still_missing:
            event_rows = [
                {
                    "subject_id": k[0],
                    "observation_type_id": k[1],
                    "event_date": session_date_values.get(k, k[2]),
                }
                for k in still_missing
            ]
            try:
                inserted = (
                    session.execute(
                        insert(Event)
                        .values(event_rows)
                        .on_conflict_do_nothing()
                        .returning(Event.event_id, Event.subject_id, Event.observation_type_id, Event.event_date)
                    )
                    .mappings()
                    .all()
                )
                for row in inserted:
                    k = (row["subject_id"], row["observation_type_id"], str(row["event_date"]))
                    event_map[k] = row["event_id"]
                    self._event_cache[k] = row["event_id"]
            except Exception:
                # Fallback: ON CONFLICT may not return rows; fetch them
                session.flush()
                pass

            # Fetch any that were skipped by ON CONFLICT DO NOTHING
            refetch_keys = [k for k in still_missing if k not in event_map]
            if refetch_keys:
                conditions = [
                    and_(
                        Event.subject_id == k[0],
                        Event.observation_type_id == k[1],
                        Event.event_date == session_date_values.get(k, k[2]),
                    )
                    for k in refetch_keys
                ]
                existing = session.execute(
                    select(Event).where(or_(*conditions))
                ).scalars().all()
                for evt in existing:
                    k = (evt.subject_id, evt.observation_type_id, str(evt.event_date))
                    event_map[k] = evt.event_id
                    self._event_cache[k] = evt.event_id

        # Update study.event_id for all studies in this batch
        for key, sids in sessions_needed.items():
            event_id = event_map.get(key)
            if event_id is None:
                continue
            for study_id in sids:
                session.execute(
                    update(Study)
                    .where(Study.study_id == study_id)
                    .where(Study.event_id.is_(None))
                    .values(event_id=event_id)
                )

        session.flush()

    def _bulk_ensure_series(
        self,
        session,
        batch: list[InstancePayload],
        subject_ids: list[int],
        study_ids: list[int],
    ) -> list[int]:
        series_ids: list[int] = [0] * len(batch)
        pending: dict[str, dict] = {}

        for idx, payload in enumerate(batch):
            cached = self._series_cache.get(payload.series_uid)
            if cached:
                series_ids[idx] = cached
                continue
            entry = pending.setdefault(
                payload.series_uid,
                {
                    "indices": [],
                    "payload": payload,
                    "subject_id": subject_ids[idx],
                    "study_id": study_ids[idx],
                },
            )
            entry["indices"].append(idx)

        if pending:
            stmt = select(Series).where(Series.series_instance_uid.in_(pending.keys()))
            for existing in session.execute(stmt).scalars():
                entry = pending.pop(existing.series_instance_uid, None)
                if entry is None:
                    continue
                series_id = existing.series_id
                updates = {}
                if existing.subject_id != entry["subject_id"]:
                    updates["subject_id"] = entry["subject_id"]
                if existing.study_id != entry["study_id"]:
                    updates["study_id"] = entry["study_id"]
                if updates:
                    self._log_conflict(
                        session,
                        "series",
                        existing.series_instance_uid,
                        "Series metadata linked to a different subject/study; re-linking",
                        entry["payload"].file_path,
                    )
                    session.execute(update(Series).where(Series.series_id == series_id).values(**updates))
                    session.flush()
                self._series_cache[existing.series_instance_uid] = series_id
                for idx in entry["indices"]:
                    series_ids[idx] = series_id

        if pending:
            rows = []
            for uid, entry in pending.items():
                payload = entry["payload"]
                values = {
                    "series_instance_uid": uid,
                    "modality": self._resolve_series_modality(payload),
                    "study_id": entry["study_id"],
                    "subject_id": entry["subject_id"],
                }
                for key, value in payload.series_fields.items():
                    if key == "modality":
                        continue
                    values[key] = value
                rows.append(values)
            inserted = (
                session.execute(
                    insert(Series)
                    .values(rows)
                    .on_conflict_do_nothing()
                    .returning(Series.series_instance_uid, Series.series_id)
                )
                .mappings()
                .all()
            )
            self._series_inserted += len(inserted)
            inserted_map = {row["series_instance_uid"]: row["series_id"] for row in inserted}
            remaining = [uid for uid in pending.keys() if uid not in inserted_map]
            if remaining:
                existing_rows = (
                    session.execute(
                        select(Series.series_instance_uid, Series.series_id).where(Series.series_instance_uid.in_(remaining))
                    )
                    .mappings()
                    .all()
                )
                for row in existing_rows:
                    inserted_map[row["series_instance_uid"]] = row["series_id"]
            for uid, entry in pending.items():
                series_id = inserted_map[uid]
                self._series_cache[uid] = series_id
                for idx in entry["indices"]:
                    series_ids[idx] = series_id

        if any(series_id == 0 for series_id in series_ids):
            raise RuntimeError("Failed to resolve series identifiers for batch")

        # Update modality-specific detail tables per unique series
        unique_series_payloads = {}
        for idx, payload in enumerate(batch):
            series_id = series_ids[idx]
            if series_id not in unique_series_payloads:
                unique_series_payloads[series_id] = payload
        for series_id, payload in unique_series_payloads.items():
            self._upsert_modality_details(session, series_id, payload)

        return series_ids

    def _resolve_series_modality(self, payload: InstancePayload) -> str:
        normalized = normalize_modality(payload.modality) or normalize_modality(payload.series_fields.get("modality"))
        if normalized:
            return normalized
        if payload.series_uid not in self._modality_fallback_logged:
            logger.warning(
                "Missing modality for series %s; defaulting to OT (file=%s)",
                payload.series_uid,
                payload.file_path,
            )
            self._modality_fallback_logged.add(payload.series_uid)
        return "OT"

    def _bulk_ensure_stacks(
        self,
        session,
        batch: list[InstancePayload],
        series_ids: list[int],
    ) -> list[int]:
        """Ensure series_stack records exist for each instance in the batch.
        
        Uses the same Cache → Bulk Query → Bulk Insert pattern as other bulk_ensure methods.
        Stack signatures are keyed by series_instance_uid (stable, available from payload).
        
        Args:
            session: Database session
            batch: List of InstancePayload objects
            series_ids: Corresponding series_id for each payload
            
        Returns:
            List of series_stack_id for each instance in batch
        """
        stack_ids: list[int] = [0] * len(batch)
        pending: dict[tuple, dict] = {}
        
        # 1. Check cache first
        for idx, (payload, series_id) in enumerate(zip(batch, series_ids)):
            sig = compute_stack_signature(payload.series_uid, payload.instance_fields)
            
            cached = self._stack_cache.get(sig)
            if cached:
                stack_ids[idx] = cached
                continue
            
            entry = pending.setdefault(sig, {
                "indices": [],
                "series_id": series_id,
                "series_uid": payload.series_uid,
                "payload": payload,
            })
            entry["indices"].append(idx)
        
        # 2. Bulk query existing stacks for series in this batch (JOIN with Series for UID)
        if pending:
            series_ids_to_check = {entry["series_id"] for entry in pending.values()}
            # JOIN with Series to get series_instance_uid directly, eliminating the need
            # for _series_id_to_uid cache (saves ~850MB for large cohorts)
            stmt = (
                select(SeriesStack, Series.series_instance_uid)
                .join(Series, SeriesStack.series_id == Series.series_id)
                .where(SeriesStack.series_id.in_(series_ids_to_check))
            )
            
            for existing, series_uid in session.execute(stmt):
                # Reconstruct signature from DB record using series_uid from JOIN
                db_sig = signature_from_stack_record(
                    series_uid,
                    existing.stack_echo_time,
                    existing.stack_inversion_time,
                    existing.stack_echo_numbers,
                    existing.stack_echo_train_length,
                    existing.stack_repetition_time,
                    existing.stack_flip_angle,
                    existing.stack_receive_coil_name,
                    existing.stack_xray_exposure,
                    existing.stack_kvp,
                    existing.stack_tube_current,
                    existing.stack_pet_bed_index,
                    existing.stack_pet_frame_type,
                    existing.stack_image_orientation,
                    existing.stack_image_type,
                )
                
                if db_sig in pending:
                    self._stack_cache[db_sig] = existing.series_stack_id
                    # Also update the counter if we found a higher index
                    current_max = self._series_stack_counter.get(series_uid, 0)
                    if existing.stack_index >= current_max:
                        self._series_stack_counter[series_uid] = existing.stack_index + 1
                    
                    for idx in pending[db_sig]["indices"]:
                        stack_ids[idx] = existing.series_stack_id
                    del pending[db_sig]
        
        # 3. Bulk insert new stacks
        if pending:
            rows = []
            sig_to_stack_key: dict[tuple, tuple[int, int]] = {}  # sig -> (series_id, stack_index)
            
            for sig, entry in pending.items():
                series_uid = entry["series_uid"]
                series_id = entry["series_id"]
                payload = entry["payload"]
                
                # Get next stack_index for this series
                stack_index = self._series_stack_counter.get(series_uid, 0)
                self._series_stack_counter[series_uid] = stack_index + 1
                
                row = build_stack_row(
                    series_id=series_id,
                    stack_index=stack_index,
                    modality=payload.modality,
                    instance_fields=payload.instance_fields,
                )
                rows.append(row)
                sig_to_stack_key[sig] = (series_id, stack_index)
            
            # Insert with ON CONFLICT DO NOTHING, return IDs
            inserted = (
                session.execute(
                    insert(SeriesStack)
                    .values(rows)
                    .on_conflict_do_nothing()
                    .returning(SeriesStack.series_stack_id, SeriesStack.series_id, SeriesStack.stack_index)
                )
                .mappings()
                .all()
            )
            self._stacks_inserted += len(inserted)
            
            # Build lookup: (series_id, stack_index) -> series_stack_id
            inserted_lookup = {
                (row["series_id"], row["stack_index"]): row["series_stack_id"]
                for row in inserted
            }
            
            # Map inserted stacks to cache and stack_ids
            for sig, (series_id, stack_index) in sig_to_stack_key.items():
                stack_id = inserted_lookup.get((series_id, stack_index))
                if stack_id:
                    self._stack_cache[sig] = stack_id
                    for idx in pending[sig]["indices"]:
                        stack_ids[idx] = stack_id
            
            # Handle entries that weren't inserted (conflict) - query them
            remaining_sigs = [sig for sig in pending if sig not in self._stack_cache]
            if remaining_sigs:
                # Re-query to get IDs for stacks that hit conflict (JOIN with Series for UID)
                remaining_series_ids = {pending[sig]["series_id"] for sig in remaining_sigs}
                stmt = (
                    select(SeriesStack, Series.series_instance_uid)
                    .join(Series, SeriesStack.series_id == Series.series_id)
                    .where(SeriesStack.series_id.in_(remaining_series_ids))
                )
                
                for existing, series_uid in session.execute(stmt):
                    db_sig = signature_from_stack_record(
                        series_uid,
                        existing.stack_echo_time,
                        existing.stack_inversion_time,
                        existing.stack_echo_numbers,
                        existing.stack_echo_train_length,
                        existing.stack_repetition_time,
                        existing.stack_flip_angle,
                        existing.stack_receive_coil_name,
                        existing.stack_xray_exposure,
                        existing.stack_kvp,
                        existing.stack_tube_current,
                        existing.stack_pet_bed_index,
                        existing.stack_pet_frame_type,
                        existing.stack_image_orientation,
                        existing.stack_image_type,
                    )
                    
                    if db_sig in pending and db_sig not in self._stack_cache:
                        self._stack_cache[db_sig] = existing.series_stack_id
                        for idx in pending[db_sig]["indices"]:
                            stack_ids[idx] = existing.series_stack_id
        
        if any(stack_id == 0 for stack_id in stack_ids):
            # Log which ones failed
            failed_indices = [i for i, sid in enumerate(stack_ids) if sid == 0]
            if failed_indices:
                sample = batch[failed_indices[0]]
                logger.error(
                    "Failed to resolve stack for %d instances. Sample: series=%s, file=%s",
                    len(failed_indices),
                    sample.series_uid,
                    sample.file_path,
                )
            raise RuntimeError("Failed to resolve stack identifiers for batch")
        
        return stack_ids

    def _get_existing_sops(self, session, batch: list[InstancePayload]) -> set[str]:
        """Query which SOP UIDs from the batch already exist in the database.
        
        This is used to pre-filter batches before creating parent records,
        ensuring we only create parents for instances that will actually be inserted.
        
        Returns:
            Set of sop_instance_uid values that already exist in the database.
        """
        if not batch:
            return set()
        
        # Query which SOP UIDs already exist in the database
        batch_sops = {p.sop_uid for p in batch}
        
        # Query existing SOPs in chunks to avoid parameter limits
        existing_sops: set[str] = set()
        sop_list = list(batch_sops)
        # Use chunks of 1000 to stay well under PostgreSQL limits
        for i in range(0, len(sop_list), 1000):
            chunk_sops = sop_list[i:i + 1000]
            stmt = select(Instance.sop_instance_uid).where(
                Instance.sop_instance_uid.in_(chunk_sops)
            )
            result = session.execute(stmt)
            existing_sops.update(row[0] for row in result)
        
        return existing_sops

    def _log_duplicate_sops(
        self, session, batch: list[InstancePayload], existing_sops: set[str]
    ) -> None:
        """Log duplicate SOP conflicts if configured to do so."""
        log_duplicates = (
            not self.config.resume
            and self.config.duplicate_policy in {DuplicatePolicy.SKIP, DuplicatePolicy.APPEND_SERIES}
        )
        if not log_duplicates:
            return
        
        for payload in batch:
            if payload.sop_uid in existing_sops:
                self._log_conflict(
                    session,
                    "instance",
                    payload.sop_uid,
                    "Duplicate SOP Instance",
                    payload.file_path,
                )

    def _insert_instances_with_fks(
        self,
        session,
        batch: list[InstancePayload],
        series_ids: list[int],
        stack_ids: list[int],
    ) -> None:
        """Insert instances with correct series_id and stack_id already set.
        
        This is the final step of the parents-first pattern. Instances are inserted
        with their foreign keys already populated, avoiding the need for a subsequent
        UPDATE and eliminating dead rows.
        """
        if not batch:
            return
        
        # Build rows for insertion with correct FKs
        rows = []
        for payload, series_id, stack_id in zip(batch, series_ids, stack_ids):
            values = {
                "series_id": series_id,
                "series_stack_id": stack_id,
                "series_instance_uid": payload.series_uid,
                "sop_instance_uid": payload.sop_uid,
                "dicom_file_path": payload.file_path,
            }
            # Add instance fields but exclude stack-defining fields
            for key, value in payload.instance_fields.items():
                if key not in STACK_DEFINING_FIELDS:
                    values[key] = value
            rows.append(values)

        chunks, params_per_row, chunk_limit = build_parameter_chunk_plan(rows)
        
        if len(chunks) > 1:
            logger.info(
                "Splitting instance insert of %d rows into %d chunks (<= %d rows, %d params/row)",
                len(rows),
                len(chunks),
                chunk_limit,
                params_per_row,
            )

        # Insert with correct FKs - no UPDATE needed later
        for chunk in chunks:
            stmt = insert(Instance).values(chunk)
            
            if self.config.duplicate_policy == DuplicatePolicy.OVERWRITE:
                # For OVERWRITE, update existing records
                excluded = stmt.excluded
                update_values = {column: getattr(excluded, column) for column in chunk[0].keys()}
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Instance.sop_instance_uid],
                    set_=update_values,
                )
            else:
                # For SKIP/APPEND_SERIES, use ON CONFLICT DO NOTHING
                # (shouldn't hit conflicts since we pre-filtered, but just in case)
                stmt = stmt.on_conflict_do_nothing()
            
            session.execute(stmt)
        
        # Track metrics
        self._instances_inserted += len(batch)

    def _update_instance_fks(
        self,
        session,
        batch: list[InstancePayload],
        series_ids: list[int],
        stack_ids: list[int],
    ) -> None:
        """DEPRECATED: No longer used by _write_batch.
        
        This was part of the instance-first pattern where instances were inserted
        with NULL FKs and then updated. The new parents-first pattern inserts
        instances with correct FKs from the start, eliminating dead rows.
        
        Kept for backward compatibility but should not be called.
        """
        if not batch:
            return
        
        # Use individual UPDATE statements - this is still efficient for typical batch sizes
        # and avoids SQLAlchemy ORM bulk update limitations
        from sqlalchemy import text
        
        # Get the underlying connection for raw SQL
        conn = session.connection()
        
        for payload, series_id, stack_id in zip(batch, series_ids, stack_ids):
            stmt = text(
                "UPDATE instance SET series_id = :series_id, series_stack_id = :stack_id "
                "WHERE sop_instance_uid = :sop"
            )
            conn.execute(stmt, {"series_id": series_id, "stack_id": stack_id, "sop": payload.sop_uid})

    def _bulk_ensure_instances(self, session, batch: list[InstancePayload], series_ids: list[int], stack_ids: list[int]) -> None:
        """Legacy method - kept for backward compatibility but no longer used by _write_batch."""
        rows = []
        for payload, series_id, stack_id in zip(batch, series_ids, stack_ids):
            values = {
                "series_id": series_id,
                "series_stack_id": stack_id,  # Set FK at insert time!
                "series_instance_uid": payload.series_uid,
                "sop_instance_uid": payload.sop_uid,
                "dicom_file_path": payload.file_path,
            }
            # Add instance fields but exclude stack-defining fields (stored in series_stack only)
            for key, value in payload.instance_fields.items():
                if key not in STACK_DEFINING_FIELDS:
                    values[key] = value
            rows.append(values)

        if not rows:
            return
        chunks, params_per_row, chunk_limit = build_parameter_chunk_plan(rows)
        if len(chunks) > 1:
            logger.info(
                "Splitting instance batch of %d rows into %d chunks (<= %d rows, %d params/row) to satisfy PostgreSQL limits",
                len(rows),
                len(chunks),
                chunk_limit,
                params_per_row,
            )

        for chunk in chunks:
            stmt = insert(Instance).values(chunk)
            if self.config.duplicate_policy == DuplicatePolicy.OVERWRITE:
                excluded = stmt.excluded
                update_values = {column: getattr(excluded, column) for column in chunk[0].keys()}
                stmt = stmt.on_conflict_do_update(
                    index_elements=[Instance.sop_instance_uid],
                    set_=update_values,
                )
                session.execute(stmt)
            else:
                # Determine if we need to track individual duplicates for logging
                log_duplicates = (
                    not self.config.resume
                    and self.config.duplicate_policy in {DuplicatePolicy.SKIP, DuplicatePolicy.APPEND_SERIES}
                )
                
                if log_duplicates:
                    # Need RETURNING to identify which rows were actually inserted vs skipped
                    # This uses more PostgreSQL memory but is required for duplicate logging
                    stmt = stmt.on_conflict_do_nothing().returning(Instance.sop_instance_uid)
                    inserted = {row[0] for row in session.execute(stmt)}
                    self._instances_inserted += len(inserted)
                    for payload in chunk:
                        if payload.sop_uid not in inserted:
                            self._log_conflict(
                                session,
                                "instance",
                                payload.sop_uid,
                                "Duplicate SOP Instance",
                                payload.file_path,
                            )
                else:
                    # Optimized path: skip RETURNING clause to reduce PostgreSQL memory usage
                    # This is critical for long-running extractions with millions of instances
                    # Without RETURNING, PostgreSQL doesn't need to materialize the result set
                    stmt = stmt.on_conflict_do_nothing()
                    result = session.execute(stmt)
                    # rowcount gives us the number of inserted rows without RETURNING overhead
                    self._instances_inserted += result.rowcount

    def _upsert_modality_details(self, session, series_id: int, payload: InstancePayload) -> None:
        modality = (payload.modality or "").upper()
        if modality == "MR":
            self._upsert_detail_record(session, series_id, MRISeriesDetails, payload.series_uid, payload.mri_fields)
        elif modality == "CT":
            self._upsert_detail_record(session, series_id, CTSeriesDetails, payload.series_uid, payload.ct_fields)
        elif modality in {"PT", "PET"}:
            self._upsert_detail_record(session, series_id, PETSeriesDetails, payload.series_uid, payload.pet_fields)

    def _upsert_detail_record(self, session, series_id: int, model, series_uid: str, fields: dict) -> None:
        if not fields or all(value is None for value in fields.values()):
            return
        values = {"series_id": series_id, "series_instance_uid": series_uid}
        values.update(fields)
        # Use series_instance_uid as conflict target since it has a unique constraint.
        # This handles the case where a series was previously processed with a different
        # series_id (e.g., after a partial rollback or data inconsistency in PACS).
        # We update the series_id to the new value along with all other fields.
        stmt = (
            insert(model)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[model.series_instance_uid],
                set_=values,  # Update all fields including series_id
            )
        )
        session.execute(stmt)

    def _log_conflict(self, session, scope: str, uid: str, message: str, file_path: Optional[str]) -> None:
        if self._cohort_id is None:
            return
        stmt = (
            insert(IngestConflict)
            .values(
                cohort_id=self._cohort_id,
                scope=scope,
                uid=uid,
                message=message,
                file_path=file_path,
            )
            .on_conflict_do_nothing()
        )
        session.execute(stmt)

    def snapshot_metrics(self) -> dict[str, int]:
        return {
            "subjects": self._subjects_inserted,
            "studies": self._studies_inserted,
            "series": self._series_inserted,
            "stacks": self._stacks_inserted,
            "instances": self._instances_inserted,
            "safe_batch_rows": self._reported_safe_batch_rows,
        }

    async def _checkpoint(self) -> None:
        if self._control is None:
            return
        await self._control.checkpoint(self.job_id)
