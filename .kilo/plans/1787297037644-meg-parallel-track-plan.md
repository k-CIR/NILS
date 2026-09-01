# MEG Parallel Track Implementation Plan

## Goal
Add a first-class MEG pipeline to NILS as a parallel track, reusing cohort/job/pipeline infrastructure without forcing FIF/MEG into the current DICOM-centric `extract -> sort -> bids` path.

## Decisions
- Use a separate MEG stage family for MEG cohorts.
- Add a cohort-level modality/type field so pipeline initialization can choose DICOM vs MEG stages.
- Vendor the required MEG code from `cir-utils` and `SESHAT` into NILS under `backend/src/meg/`.
- Phase 1 scope is limited to `meg_ingest`, `meg_scan`, and `meg_bids`.
- Subject resolution for MEG uses NILS-native identifier lookup via `subject_id_type_id` with CSV mapping fallback.
- `meg_maxfilter` and `meg_qc` are explicitly deferred follow-up stages.
- Reuse the existing shared `study` table for MEG sessions (`modality="MEG"`) instead of a disconnected MEG-only session table, so subject/session linkage, `subject_cohorts`, and the existing `event`/`observation_types` session-grouping invariant keep working the same way they do for MR/CT/PET. See `variable_tables/02-study.md`, which already marks almost all `study.*` fields as relevant to MEG.
- Do **not** reuse or extend `series`, `instance`, `series_stack`, or `stack_fingerprint` for MEG. `variable_tables/03-series.md` explicitly documents these as DICOM-series based and not relevant to MEG. Add MEG-specific tables instead, named after `variable_tables/12-proposed-meg-fields.md` (`meg_acquisition`, `meg_channel`, `meg_epoch`) rather than a single flat `meg_recording` table.
- `meg_epoch` (proposed in `variable_tables/12-proposed-meg-fields.md`) is reserved but deferred: phase 1 stages only handle continuous raw data, so there is no phase-1 producer of epoch-level rows. Populate it only when `meg_qc`/`meg_maxfilter` land.
- Reuse the existing shared `ingest_conflicts` support table (`variable_tables/11-support-system.md`) for `meg_ingest` discovery/copy/integrity failures instead of adding bespoke MEG-only failure columns.

## Affected Boundaries
- Backend cohort creation and pipeline initialization (`backend/src/cohorts/models.py` Postgres `cohorts` table — distinct from the `cohort` table inside the SQLite/Postgres metadata DB used for subject/study linkage).
- Pipeline ordering/default config definitions (`backend/src/nils_dataset_pipeline/ordering.py`, which today takes no modality parameter at all).
- Job launch and stage execution API.
- Metadata DB schema and migrations (`backend/src/metadata_db/schema.py`, `backend/src/metadata_db/migrations/`), including the shared `study`/`event`/`observation_types` tables, not just new MEG-only tables.
- Cohort stats aggregation (`backend/src/cohorts/stats.py`), which currently only counts DICOM `series_stack` rows for `total_series`.
- MEG-specific backend modules for ingest, scan, and BIDS export.
- Frontend stage typing, defaults, cohort creation/edit flow, and MEG stage UI.
- Dependency manifest for `mne`, `mne-bids`, and validator support.

## Implementation Tasks
1. Extend cohort model and creation payload for modality.
- Add `modality` or `data_type` to the `cohorts` table in `backend/src/cohorts/models.py` (Postgres, `information_schema`-style migration like `add_body_part_stage_columns.py`), `CohortDTO`, and `CreateCohortPayload`.
- Note this `cohorts` table (app/orchestration DB) is distinct from the `cohort` table defined in `backend/src/metadata_db/schema.py` (metadata DB, used for `subject_cohorts` linkage and stats queries in `backend/src/cohorts/stats.py`). The app-side column is what drives pipeline stage selection; the metadata-side `cohort` table does not need a modality column since MEG vs. imaging can be distinguished downstream via `study.modality`.
- Default existing cohorts to the current DICOM/imaging path.
- Preserve current behavior for cohorts without MEG modality.

2. Make pipeline ordering modality-aware.
- `ordering.py` today has no modality concept at all: `PIPELINE_STAGES` is a single flat list, and `get_pipeline_items(anonymization_enabled)` / `get_default_stage_config(stage_id, cohort_name, source_path)` take no modality argument. Add an explicit `modality: str = "imaging"` parameter to these (and `get_stage_ids`, `is_multi_step_stage`, etc. as needed) so every existing caller that omits the argument keeps producing today's DICOM order unchanged.
- Introduce a second stage list (e.g. `MEG_PIPELINE_STAGES`) selected when `modality == "meg"`, containing:
  - `meg_ingest`
  - `meg_scan`
  - `meg_bids`
- Add default config builders for these MEG stages in `get_default_stage_config`.
- Update pipeline initialization/reinitialization call sites in `cohorts/service.py` (`_initialize_pipeline_steps` / `_reinitialize_pipeline_steps`, both currently `(cohort_id, anonymization_enabled, cohort_name, source_path)`) to also pass the cohort's modality through to `nils_pipeline_service.initialize_for_cohort` / `reinitialize_pipeline`.

3. Expand stage ID typing and labeling across backend/frontend.
- Extend backend stage dispatch in `api/routes/cohorts.py` to recognize the new MEG stage IDs.
- Extend frontend `StageId`, `StageConfigById`, `STAGE_LABELS`, defaults, and any hard-coded stage lists.
- Ensure existing DICOM pages still render unchanged.

4. Add MEG config models.
- Create `backend/src/meg/config.py` with Pydantic models for:
  - ingest paths/options
  - scan options
  - BIDS output options
  - subject resolution config (`subject_id_type_id`, CSV mapping)
- Add matching TypeScript types in `frontend/src/types/meg.ts`.
- Keep config naming aligned with existing frontend camelCase/backend snake_case conventions.

5. Vendor and adapt MEG code under `backend/src/meg/`.
- Vendor the minimal `cir-utils` MEG BIDS modules needed for conversion-table generation and `mne_bids.write_raw_bids()` export.
- Vendor the minimal `SESHAT` copy logic needed for FIF discovery, split-file handling, and integrity checks.
- Trim UI/CLI-only code during vendoring.
- Add a short provenance comment at the top of vendored modules.

6. Implement `meg_ingest` stage.
- Create `backend/src/meg/ingest.py`.
- Responsibilities:
  - discover FIF files under cohort source path
  - copy or stage them into cohort raw workspace
  - preserve split FIF sets
  - copy calibration/crosstalk files when configured
  - emit progress and summary metrics
- Job output should include counts for discovered files, copied files, skipped files, and copy/check failures.

7. Implement `meg_scan` stage.
- Create `backend/src/meg/scanner.py`, `extractor.py`, and `models.py`.
- Read MEG headers with `mne.io.read_info()` only; avoid loading full recordings.
- Parse filename/task/run/acquisition metadata using vendored `cir-utils` parsing logic where valid.
- Resolve subject identity using:
  - primary: `subject_id_type_id` against `subject_other_identifiers`
  - fallback: uploaded CSV mapping
- Upsert one `study` row per logical MEG session (per `subject_id` + session label/date), mirroring how DICOM extraction populates `study`:
  - `modality = "MEG"`
  - `study_date` / `study_time` from the FIF measurement date
  - `study_description`, `manufacturer`, `manufacturer_model_name`, `station_name`, `institution_name` from FIF header info where available
  - `study_instance_uid` has a `nullable=False, unique=True` constraint today (see `backend/src/metadata_db/schema.py`), so MEG has no natural DICOM UID to put there. Synthesize a stable, deterministic value (e.g. a `uuid5`/hash over `subject_code`, session label, and acquisition date) so re-running `meg_scan` upserts the same `study` row instead of creating duplicates, the same way DICOM extraction dedupes on UID.
  - Link `event_id` the same way DICOM studies do, using a new MEG row in `observation_types` (next free id is `16`, category `"Imaging"`, name e.g. `"MEG Scan"`) so the existing one-event-per-`(subject_id, observation_type_id, event_date)` invariant from `migrate_observation_types.py` / `backfill_study_events.py` keeps holding for MEG sessions too. Add `"MEG": 16` to **both** places that currently hardcode this mapping (`backend/src/extract/writer.py::_MODALITY_TO_OBSERVATION_TYPE` and `backend/src/metadata_db/migrations/backfill_study_events.py::MODALITY_TO_OBSERVATION_TYPE`) — these are two separate hardcoded copies today and will silently diverge if only one is updated.
- Generate or refresh the conversion table used by `meg_bids`.
- Persist per-recording metadata in the new `meg_acquisition` table (see task 8), and per-channel metadata in `meg_channel`.

8. Add metadata DB schema for MEG recordings.
- Add new ORM models and migration(s) in `backend/src/metadata_db/` for phase 1 minimum storage, following the existing modality-details pattern (`MRISeriesDetails` / `CTSeriesDetails` / `PETSeriesDetails` each FK'd to `series.series_id`) but rooted in `study` instead of `series`/`instance`/`series_stack`/`stack_fingerprint`, which stay untouched (see Decisions).
- Tables (naming aligned with `variable_tables/12-proposed-meg-fields.md`):
  - `meg_acquisition`: one row per logical recording/split-file group — the MEG equivalent of `series`. FK to `study.study_id` and `subject.subject_id` (mirrors the redundant `study_id` + `subject_id` FKs already used on `Series`), not directly to `cohort_id` (cohort is reached transitively via `subject_cohorts`, same as every other modality).
  - `meg_channel`: one row per channel (BIDS `channels.tsv` equivalent), FK to `meg_acquisition.meg_acquisition_id`.
  - `meg_epoch`: reserved column set only (`t_min`, `t_max`, `n_epochs`) per the proposed-fields doc; do not create/populate this table in phase 1 (see Decisions) — reserve it for the `meg_qc`/`meg_maxfilter` follow-up.
  - optional `meg_cohort_config` only if config must be queryable outside pipeline-step config; otherwise keep config in pipeline step JSON for phase 1.
- `meg_acquisition` columns:
  - `meg_acquisition_id`
  - `study_id`, `subject_id`
  - session label
  - `bids_task`, `bids_run`, `bids_acq_label` (BIDS entities — kept distinct from `device`/manufacturer to avoid name collision with the DICOM sense of "acquisition")
  - `bids_processing_label`
  - `bids_datatype` (e.g. `"meg"`)
  - `fif_file_path`
  - `split_count`
  - `acquisition_date` (measurement date)
  - `duration_seconds`
  - `sampling_frequency`
  - `n_channels`
  - `device` (manufacturer/system)
  - `highpass_hz`, `lowpass_hz`, `notch_filter_hz`
  - `bids_status` / `bids_path` / `bids_name`
  - created/updated timestamps
- `meg_channel` columns: `meg_channel_id`, `meg_acquisition_id` (FK), `channel_name`, `channel_type`, `unit`, `is_bad`, `location_x`, `location_y`, `location_z`.
- Reuse the existing shared `ingest_conflicts` table for `meg_ingest`/`meg_scan` failures (missing companion split files, unreadable FIF, unresolved subject identity, missing calibration/crosstalk) instead of adding MEG-only failure columns — this table is already documented as modality-shared in `variable_tables/11-support-system.md`.
- Add indexes: `meg_acquisition(study_id)`, `meg_acquisition(subject_id)`, `meg_acquisition(bids_status)`, `meg_channel(meg_acquisition_id)`.

8b. Extend cohort stats for MEG.
- `backend/src/cohorts/stats.py::get_cohort_stats` / `get_all_cohort_stats` currently compute `total_series` by joining `series_stack -> series -> study -> subject_cohorts`. Since MEG never populates `series`/`series_stack`, MEG cohorts would always show `total_series = 0` even after a successful ingest/scan/BIDS run.
- `total_subjects` and `total_sessions` already work unmodified for MEG, since both are computed purely from `subject_cohorts` and `study.study_date`, which `meg_scan` now populates (task 7).
- Extend both stats queries to also count `meg_acquisition` rows reachable via `study -> subject_cohorts` for the same cohort, and combine with the existing stack count (e.g. `stack_count + meg_acquisition_count`, or branch by cohort modality) so the "series" figure on a MEG cohort card reflects real ingested recordings.

9. Implement `meg_bids` stage.
- Create `backend/src/meg/bids_bridge.py` as the adapter between vendored `bidsify()` and NILS jobs.
- Run the synchronous MEG BIDS writer in a worker thread/process and stream progress through an async queue.
- Convert vendored progress callbacks into the same event shape used by NILS stage streaming.
- Read `dataset_description.json` / top-level sidecar fields from the `study` row already populated by `meg_scan` (task 7) rather than re-deriving them, keeping parity with how the DICOM BIDS exporter sources study-level metadata.
- Generate/update:
  - `dataset_description.json`
  - calibration/crosstalk sidecars
  - conversion table statuses
  - per-run BIDS summary metrics, written back onto `meg_acquisition.bids_status` / `bids_path` / `bids_name`
- Keep this stage isolated from the existing DICOM `backend/src/bids/exporter.py` implementation.

10. Hook MEG stages into the job and API flow.
- Add stage handlers in `api/routes/cohorts.py` similar to current `_run_extract_stage` / `_run_bids_stage` structure.
- Reuse `start_pipeline_step`, `complete_pipeline_step`, and `fail_pipeline_step` for MEG stages.
- Add SSE/stream endpoints if current MEG job UX needs live progress separate from generic job polling.
- Ensure job `stage` values exactly match the new pipeline stage IDs so restart/reconciliation works.

11. Update frontend cohort and stage UX.
- Extend cohort creation/editing to choose modality/type.
- Render the correct stage list for MEG cohorts.
- Add MEG config forms for:
  - ingest paths/options
  - subject resolution
  - BIDS output root and overwrite behavior
- Add recording/conversion-table views only if needed for phase 1 execution; otherwise expose summaries through job metrics first and keep editable conversion-table UI for follow-up.
- Do not disturb existing imaging cohort workflows.

12. Add migrations/backfill behavior.
- App cohorts table migration (`backend/src/cohorts/models.py`, Postgres):
  - add modality/type column
  - backfill existing cohorts to imaging/DICOM default
- Metadata DB migrations (`backend/src/metadata_db/migrations/`), each following the existing `_needs_migration()`/idempotent pattern:
  - seed the new MEG `observation_types` row (id `16`)
  - create `meg_acquisition` and `meg_channel` tables (reserve but do not create `meg_epoch`)
  - no schema change needed for `study` itself (`modality` and `event_id` columns already exist and are already nullable/generic); only application code needs to start writing `modality="MEG"` rows with synthesized `study_instance_uid` values
- Pipeline behavior:
  - existing cohorts keep current ordering
  - only MEG cohorts initialize the MEG stage list

## Data Flow
1. User creates a cohort with MEG modality.
2. Pipeline initialization creates `meg_ingest -> meg_scan -> meg_bids` steps.
3. `meg_ingest` stages raw FIF files into the cohort workspace and records ingest metrics (failures go to `ingest_conflicts`).
4. `meg_scan` reads FIF headers, resolves subject identity, upserts one `study` row per session (`modality="MEG"`, synthesized `study_instance_uid`, linked `event`/`observation_type`), writes `meg_acquisition` and `meg_channel` rows, and creates/updates the conversion table.
5. `meg_bids` reads the conversion table, writes BIDS output via `mne-bids`, updates `meg_acquisition` row statuses, and emits progress through jobs/SSE.
6. Frontend shows stage progress and final metrics through the standard cohort/job views; cohort stats (`total_subjects`/`total_sessions`/`total_series`) now also reflect `meg_acquisition` counts for MEG cohorts.

## Failure Modes To Handle
- Source directory contains non-FIF or partially copied split FIF sets.
- FIF file is unreadable or missing companion split parts.
- Subject resolution fails for some recordings.
- Conversion table has unresolved/manual-review rows.
- Calibration/crosstalk files are missing.
- BIDS export partially succeeds and must be resumed safely.
- Backend restart interrupts a running MEG job; orphaned job reconciliation must mark it failed like existing stages.
- Vendored parsing logic assumes NatMEG naming; cohorts with different naming need clear failure/reporting rather than silent mislabeling.

## Rollout Notes
- Keep MEG behind the new cohort modality path only.
- Do not refactor existing DICOM extract/sort/bids logic during phase 1.
- Keep `meg_maxfilter` and richer QC/reporting out of the first implementation, but reserve stage IDs and ordering extension points for later.
- Prefer storing phase 1 config in pipeline-step config JSON unless a new DB table is required by a concrete query/API need.

## Validation Plan
- Unit tests for modality-aware pipeline ordering and default config selection.
- Unit tests for subject resolution priority: id-type lookup first, CSV fallback second.
- Unit tests for FIF split-file discovery and conversion-table generation.
- Unit tests that synthesized `study_instance_uid` generation is deterministic and idempotent across repeated `meg_scan` runs for the same session (no duplicate `study` rows).
- Unit test that the MEG `observation_types` seed row exists and that `event`'s one-event-per-`(subject_id, observation_type_id, event_date)` constraint holds for MEG sessions.
- Integration test creating an MEG cohort and confirming pipeline steps are `meg_ingest`, `meg_scan`, `meg_bids`.
- Integration test that `meg_scan` upserts a `study` row (`modality="MEG"`) and writes `meg_acquisition`/`meg_channel` rows from sample FIF headers.
- Integration test that `meg_bids` can convert a small fixture dataset and updates progress/metrics.
- Integration test that `get_cohort_stats`/`get_all_cohort_stats` report non-zero `total_series` for a MEG cohort after `meg_scan` has run.
- Regression test ensuring existing imaging cohorts still initialize and run the original stage set, and that `series`/`instance`/`series_stack`/`stack_fingerprint` remain completely unwritten for MEG cohorts.

## Deferred Follow-up
- `meg_maxfilter` stage based on SESHAT MaxFilter support.
- `meg_qc` stage with validator/report UI.
- Editable conversion-table UI beyond phase 1 summaries.
- Site-specific filename parsers beyond the initial naming conventions supported by vendored `cir-utils` logic.
