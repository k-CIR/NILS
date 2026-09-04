# Facility Vault Discovery + Project Mapping Implementation Plan

## Goal
Auto-detect subjects/sessions already present on disk for two facilities (`mrc` = MRI, `natmeg` = MEG) under a shared vault layout, resolve their true cross-facility subject identity and project via a facility-maintained mapping CSV, and stage them for human review before creating/linking any `project`/`cohort`/`subject` records — without modifying the existing DICOM/MEG extraction pipelines or disturbing any non-facility cohort.

## Real-World Layout
- MRI: `<root>/vault/mrc/sub-<id>` — flat, no project segmentation, one-off per-visit folder (not person-stable).
- MEG: `<root>/vault/natmeg/<project>/raw/<acquisition>/sub-<id>/<date>` — project is already a real physical directory.

## Mapping CSV (facility-maintained export, not invented by NILS)
Columns: `cir_id, cir_project, session_number, scan_date, cir_facility, natmeg_id, mrc_id, bmic_id, bmic_radioligande_factor, tester_name, tester_kiid, persnr_check, sub_id, export_time`.

- `cir_id` = canonical, stable, cross-facility subject identity -> becomes NILS `subject_code` for facility-discovered subjects.
- `cir_project` = project code -> resolves/creates the `project` row.
- `mrc_id` = literal folder id under `vault/mrc/sub-<id>`; `natmeg_id` = literal folder id under `vault/natmeg/.../sub-<id>`. `sub_id` is a separate legacy/generic field, NOT used for folder matching.
- Natural key per row = `(facility_id_column [mrc_id|natmeg_id], session_number)` — the same facility id can repeat across rows for repeat visits/sessions. For `natmeg`, `scan_date` (present in the folder path) is used to match `session_number`/`scan_date` in the sheet.
- `bmic_id`/`bmic_radioligande_factor` (a third facility, PET/radioligand-related) are out of scope: read but ignored/not persisted; importer must tolerate extra/unknown columns without breaking.
- `persnr_check` is not sensitive PII — store or ignore, no special access control needed.

## Decisions
1. New first-class `project` DB entity + nullable `project_id` FK on `cohorts`. Lives in the **app DB** (same physical DB as `cohorts`, so the FK is a real, enforced FK) — see DB Placement below.
2. Real subject identity for `mrc` comes only from the mapping CSV's `cir_id`, never from the `sub-<id>` folder name or DICOM `PatientID` alone (DICOM `PatientID` may be unreliable/not person-stable at this facility).
3. Mapping CSV delivery for v1: a server-side file path configured via `.env` (`FACILITY_MAPPING_CSV_PATH`), fully reloaded (delete+reinsert) on every discovery scan. No upload endpoint/UI.
4. Discovery scan trigger for v1: manual only, via `POST /api/facility-discovery/scan`. No scheduled/cron job.
5. Newly discovered subjects/sessions are staged in a new dedicated review table (not `ingest_conflicts` — separate lifecycle) before any cohort/project/subject linkage is created. Applies uniformly to `mrc` and `natmeg` (not split by modality).
6. Confirming a discovery only creates/links records (project if missing, cohort if missing, subject link) — it does **not** auto-queue extraction. Extraction remains a fully separate, explicit manual step exactly as today.
7. MEG raw-root path: change the hardcoded default globally in `get_meg_raw_root()` from `<cohort.source_path>/derivatives/meg-raw` to `<cohort.source_path>/raw`. Verified safe: `discover_fif_files()` (`backend/src/meg/ingest.py:84-104`) is a fully recursive, depth-agnostic `os.walk`, and subject/session parsing is filename/parent-dir based (`backend/src/meg/parsing.py`), not tied to a fixed depth — so the extra `<acquisition>` directory level in the real `natmeg` layout requires no code changes. Existing on-disk data already organized under `derivatives/meg-raw` needs manual filesystem migration — call out as a deployment note, not solved in code.
8. MRI per-project routing: since `vault/mrc` has no project subfolder (project only knowable via the mapping sheet) and the existing extraction pipeline (`extract/scanner.py::discover_subjects()`) does a flat, unfiltered scan of a cohort's entire `source_path`, use a **symlink staging directory per project** (e.g. `<MRC_STAGING_ROOT>/<project_code>/mrc/` containing symlinks only to confirmed `sub-<id>` folders for that project); the project's MRI cohort's `source_path` points at this staging dir. This leaves `discover_subjects()`/extraction entirely untouched. New `.env` var `MRC_STAGING_ROOT`.
9. For `natmeg`, no symlink staging is needed — the project is already a real physical directory in the vault, so `cohort.source_path` is set directly to `<root>/vault/natmeg/<project>` and `get_meg_raw_root()` naturally resolves to `<source_path>/raw`.
10. `subject_code` injection mechanism (resolved below under "Subject Code Injection Mechanism"): reuse the **existing, unmodified** CSV-override mechanisms in `extract/subject_mapping.py` (DICOM) and `meg/extractor.py` (MEG) by having facility-discovery **generate and wire per-cohort override CSVs**, rather than touching `SubjectResolver`/extraction/scan code at all.

## DB Placement (confirmed via research)
The cohorts app and metadata DB are two physically separate Postgres databases:
- App DB `neurotoolkit` (port 5432, `DATABASE_URL`, `backend/src/db/session.py`/`config.py`) holds `cohorts` (plural), `jobs`, `nils_dataset_pipeline_step` (`backend/src/nils_dataset_pipeline/models.py`, real FK `ForeignKey("cohorts.id")`) — i.e. this is where app-facing cohort/workflow/config lifecycle tables live.
- Metadata DB `neurotoolkit_metadata` (port 5433, `METADATA_DATABASE_URL`, `backend/src/metadata_db/session.py`/`config.py`) holds `subject`, `study`, `subject_cohorts`, `ingest_conflicts`, and a second, separate `cohort` (singular) table (`backend/src/metadata_db/schema.py:54-55`, PK `cohort_id`) — a distinct row-set from the app DB's `cohorts`, kept in sync only by matching on `name`.
- `ingest_conflicts.cohort_id` (`backend/src/metadata_db/schema.py:825-834`) is a plain `Integer` with **no declared `ForeignKey()`** — an unenforceable, plain-integer cross-database reference. This is the established convention for any new table that must reference an entity living in the other physical DB.

New tables, all placed in the **app DB** (same `Base`/engine as `cohorts`, `jobs`, `nils_dataset_pipeline_step` — consistent with those tables already being the app-facing workflow layer):
- `project` — real FK target for `cohorts.project_id` (same DB, real FK).
- `facility_subject_mappings` — imported CSV rows. No FK to metadata-DB entities needed (rows are pre-linking, raw import data); optional real FK to `project.project_id` once `cir_project` is resolved.
- `facility_discoveries` — human-review queue. `cohort_id` is a **real FK** to app-DB `cohorts.cohort_id` (nullable until a cohort is created for a newly-seen project/facility pairing). `subject_id` (populated only after confirm creates/finds the metadata-DB `subject` row) is a **plain integer, no declared FK** — following the exact `ingest_conflicts.cohort_id` convention, since `subject` lives in the other physical DB.

## Subject Code Injection Mechanism (resolved)
Investigated the exact wiring used today so facility-discovery can plug into it with zero changes to `SubjectResolver`/extraction/MEG-scan code:

- **DICOM extract stage** (`backend/src/api/routes/cohorts.py::_run_extract_stage`, `_load_subject_code_mapping`, lines 55-91 & 1470-1525): the extract-stage config (`merged_config['subjectCodeCsv']`) accepts `{filePath | fileToken, patientColumn, subjectCodeColumn}`. `filePath` mode reads an arbitrary server-side path via `extract.subject_mapping.load_subject_code_csv(path, patient_column, subject_column)` — the same function `SubjectResolver` is built from (`extract/core.py:161,469` pass `config.subject_code_map` straight through). The map is keyed by the DICOM `PatientID` **tag value actually present in the files**, not by folder name (confirmed in `extract/subject_mapping.py::SubjectResolver.resolve`, `load_subject_code_csv`).
- **MEG scan stage** (`backend/src/api/routes/cohorts.py::_run_meg_scan_stage`, lines 338-398): `merged_config['subjectCsvMappingPath']` is a plain server-side path loaded via `meg.extractor.load_participant_subject_code_csv(path)`, a thin wrapper fixing columns to `participant`/`subject_code`. `participant` is parsed in `meg/parsing.py:60` via regex `(NatMEG_|sub-)(\d+)` directly from the FIF **filename** — i.e. for `natmeg`, `participant_from` is exactly the numeric `natmeg_id` folder value (no DICOM-style ambiguity; no header-peek needed).
- **Per-cohort stage config is DB-backed and directly writable in code**: `nils_dataset_pipeline/repository.py::save_config(session, cohort_id, stage_id, step_id, config)` persists `NilsDatasetPipelineStep.config` (app DB). Facility-discovery confirmation can call this directly to point a cohort's `extract` (or `meg_scan`) stage config at a generated override CSV — no new stage-config UI required for the override wiring itself (v1 can still surface it read-only in the UI).

Design (uses only existing mechanisms, no `SubjectResolver`/extraction code changes):
1. New `.env` var `FACILITY_SUBJECT_CODE_CSV_ROOT` (directory, analogous in spirit to `CSV_UPLOAD_DIR` in `api/utils/csv.py`), one generated CSV per cohort: `<FACILITY_SUBJECT_CODE_CSV_ROOT>/<cohort_id>.csv`.
2. **mrc (DICOM) — requires a DICOM header peek.** Because `PatientID` may not equal the `mrc_id` folder name and may be unreliable, discovery must read the *actual* `PatientID` tag that the extract stage will later read for that folder, so the CSV key matches exactly what `SubjectResolver` sees at extraction time. During `mrc` discovery, for each candidate `sub-<id>` folder, open one representative DICOM file (reuse the existing `pydicom.dcmread(path, force=True, stop_before_pixels=True)` pattern from `extract/worker.py`/`extract/process_pool.py`) and read `getattr(dataset, "PatientID", None)`. Write a row `PatientID -> cir_id` into the cohort's override CSV (columns `PatientID,subject_code` to match the extract stage's configurable `patientColumn`/`subjectCodeColumn`, defaulted to those names). On confirm, `save_config(...)` sets that cohort's `extract` stage config `subjectCodeCsv = {filePath: <generated csv path>, patientColumn: "PatientID", subjectCodeColumn: "subject_code"}`.
3. **natmeg (MEG) — no header peek needed.** `participant_from` is derived purely from the folder/filename numeric id, which is exactly `natmeg_id`. Write a row `natmeg_id -> cir_id` into the cohort's override CSV (columns `participant,subject_code`, matching `load_participant_subject_code_csv`'s fixed column names). On confirm, `save_config(...)` sets that cohort's `meg_scan` stage config `subjectCsvMappingPath = <generated csv path>`.
4. Both override CSVs are regenerated/appended incrementally as new discoveries are confirmed for a cohort (read-modify-write, keyed by the natural key so repeat confirmations are idempotent).
5. This fully explains why no ambiguity question about "does DICOM `PatientID` equal the `mrc_id` folder name" needs to be asked: discovery always peeks the true tag value and writes whatever it actually is, so the mechanism is correct whether or not the two happen to match.

## Affected Boundaries
- App DB: new `project` table + migration, new nullable `cohorts.project_id` FK column + migration (`backend/src/cohorts/models.py:32-60`, migration pattern per `backend/src/cohorts/migrations/add_cohort_main_qc_ack_table.py`).
- App DB: two new tables `facility_subject_mappings`, `facility_discoveries` (draft names), following the `ingest_conflicts` plain-integer cross-DB reference convention for any metadata-DB-side ids.
- New API routes: `POST /api/facility-discovery/scan` (manual trigger, full CSV reload + directory scan), plus review/confirm/list/reject endpoints for `facility_discoveries` (new router, e.g. `backend/src/api/routes/facility_discovery.py`).
- `backend/src/meg/ingest.py` (lines 69-81 `get_meg_raw_root`): global default path-suffix change `derivatives/meg-raw` -> `raw`.
- `backend/src/api/routes/cohorts.py` (lines 271-273, 349-352, 462-465): all 3 callers of `get_meg_raw_root()` — no code change needed beyond the function's own default, but verify none of the 3 call sites pass an explicit override that would need updating too.
- `backend/src/extract/scanner.py::discover_subjects()`: must remain completely untouched — the symlink-staging design for `mrc` depends on this flat-scan behavior continuing to work unmodified against a directory of symlinks.
- `backend/src/nils_dataset_pipeline/repository.py::save_config`: reused as-is (no signature change) by the discovery-confirm flow to wire per-cohort subject-code-override CSV paths.
- `.env`/config: `FACILITY_MAPPING_CSV_PATH`, `MRC_STAGING_ROOT`, `FACILITY_SUBJECT_CODE_CSV_ROOT` — three new environment variables, all server-side paths, no new secrets.
- Frontend: new facility-discovery review screen (list pending discoveries, show resolved `cir_id`/`cir_project`/facility/session_number/scan_date, confirm/reject actions); manual "Run discovery scan" trigger button. No changes required to existing cohort/extraction/MEG stage UI beyond optionally surfacing the generated override-CSV path read-only on the relevant stage config panel.

## Implementation Tasks

1. Add the `project` entity and `cohorts.project_id` FK (app DB).
- New `backend/src/projects/models.py` (or alongside `cohorts/models.py`): `project` table — `project_id` (PK), `code` (unique, from `cir_project`), `name`/`display_name` (nullable, editable later), created/updated timestamps.
- Migration under `backend/src/cohorts/migrations/` (or new `backend/src/projects/migrations/`) following the idempotent `information_schema` + raw `text()` DDL pattern from `add_cohort_main_qc_ack_table.py`: create `project` table if missing.
- Second migration: add nullable `cohorts.project_id` integer column + `FOREIGN KEY (project_id) REFERENCES project(project_id)`, idempotent existence check first. Existing cohorts stay `NULL` — no backfill.
- Extend `CohortDTO`/serializers minimally to expose `project_id` (and optionally a joined `project_code`) without changing any existing required fields or default cohort-creation behavior for non-facility cohorts.

2. Add `facility_subject_mappings` (app DB) + CSV importer.
- Table columns: mirror the CSV 1:1 (`cir_id, cir_project, session_number, scan_date, cir_facility, natmeg_id, mrc_id, bmic_id, bmic_radioligande_factor, tester_name, tester_kiid, persnr_check, sub_id, export_time`), plus `id` (PK) and `imported_at`.
- Natural key enforced at the application layer (not necessarily a DB unique constraint) as `(facility_id_column_value, session_number)` where `facility_id_column_value` is `mrc_id` or `natmeg_id` depending on `cir_facility`; `bmic_id`/`bmic_radioligande_factor`/`sub_id` are stored as-is but never read by discovery logic (kept only for traceability/debugging, matching the "tolerate but ignore" requirement).
- Importer (`backend/src/facility_discovery/mapping_importer.py`, new module): reads `FACILITY_MAPPING_CSV_PATH` via a tolerant CSV reader (e.g. `csv.DictReader`, ignoring unknown/extra columns, not erroring if `bmic_*` columns are absent or extra columns appear) and performs a full delete+reinsert of `facility_subject_mappings` inside one transaction (delete-all, bulk insert, commit) every time a scan runs — matches the "fully reloaded on each discovery scan" decision; no incremental diffing needed for v1.
- Do not create/resolve `project` rows during import itself; resolution happens during the scan/match step (task 4) so import stays a pure, side-effect-free reload of the raw sheet.

3. Add `facility_discoveries` (app DB) — the human-review queue.
- Columns: `id` (PK), `facility` (`mrc`|`natmeg`), `facility_id_value` (the `mrc_id`/`natmeg_id`), `session_number`, `scan_date`, `cir_id`, `cir_project`, `folder_path` (absolute path of the discovered `sub-<id>` folder, or for `natmeg` the discovered `<date>` session directory), `status` (`pending`|`confirmed`|`rejected`), `cohort_id` (real FK to app-DB `cohorts.cohort_id`, nullable until a cohort exists for that project+facility), `subject_id` (plain integer, no FK — metadata-DB `subject.subject_id`, populated only after confirm), `mapping_row_id` (FK to `facility_subject_mappings.id`), `discovered_at`, `reviewed_at`, `reviewed_by` (nullable, if NILS has a notion of current user).
- Uniqueness at the application layer on `(facility, facility_id_value, session_number)` so re-running the scan does not create duplicate pending rows for a folder/session already staged (or already confirmed) — update `folder_path`/`scan_date` on the existing row instead of inserting a duplicate when status is still `pending`.

4. Implement the discovery scan (`POST /api/facility-discovery/scan`).
- New module `backend/src/facility_discovery/scanner.py`. Steps, run synchronously in-request for v1 (no job/queue infra needed — same "manual only" simplicity as the CSV reload):
  1. Reload `facility_subject_mappings` from `FACILITY_MAPPING_CSV_PATH` (task 2).
  2. **mrc pass:** list immediate subdirectories of `vault/mrc` (`sub-<id>`), extract `<id>`, look up matching `facility_subject_mappings` rows where `mrc_id == <id>` (one row per `session_number`, since the same folder can correspond to multiple visits recorded in the sheet — for v1, if a folder maps to more than one distinct `session_number`/`cir_id` pair, stage each as its own `facility_discoveries` row rather than guessing which one is "current"). For unmatched folders (no `mrc_id` hit), skip silently (out of scope for facility-discovery; not an error).
  3. **natmeg pass:** walk `vault/natmeg/<project>/raw/<acquisition>/sub-<id>/<date>`, extract `<id>` and `<date>`, look up matching rows where `natmeg_id == <id>` and `scan_date == <date>` (falling back to `natmeg_id`-only match plus nearest `session_number` if a folder has no exact `scan_date` row, logged as a lower-confidence match — exact matching semantics to be finalized during implementation, but exact `(natmeg_id, scan_date)` match is the primary path per the confirmed natural-key decision).
  4. Upsert `facility_discoveries` rows per the task-3 natural key/uniqueness rule; never touch `project`/`cohort`/`subject`/`subject_cohorts` in this step.
  5. Return a summary (counts: matched-new, matched-already-pending, matched-already-confirmed, unmatched-folders) for the API response.
- For `mrc`, no DICOM header peek happens during the scan step itself (that is deferred to confirm time, task 5) — the scan only needs the folder name (`mrc_id`), not file contents, keeping the scan fast and side-effect-free.

5. Implement discovery review/confirm endpoints.
- `GET /api/facility-discovery` (list, filterable by `status`/`facility`), `POST /api/facility-discovery/{id}/reject` (mark `rejected`, no side effects), `POST /api/facility-discovery/{id}/confirm` (the linkage-creation path).
- Confirm flow, `backend/src/facility_discovery/confirm.py`:
  1. Resolve/create `project` row for `cir_project` (app DB) if missing.
  2. Resolve/create the facility+project's `cohort` row if missing:
     - `mrc`: cohort `source_path` = `<MRC_STAGING_ROOT>/<project_code>/mrc` (create the directory if missing); modality = existing MRI/DICOM value.
     - `natmeg`: cohort `source_path` = `<root>/vault/natmeg/<project_code>` directly; modality = MEG (per the existing MEG parallel-track plan's cohort modality field).
     - New cohort's `project_id` set to the resolved `project.project_id`.
  3. `mrc` only: symlink the confirmed `sub-<id>` folder into `<MRC_STAGING_ROOT>/<project_code>/mrc/sub-<id>` (idempotent — skip if the symlink already exists and points at the same target); peek one representative DICOM file's `PatientID` (task-described mechanism) and append/update the `PatientID -> cir_id` row in that cohort's override CSV at `FACILITY_SUBJECT_CODE_CSV_ROOT/<cohort_id>.csv`; call `save_config(...)` to set/refresh that cohort's `extract` stage config `subjectCodeCsv`.
  4. `natmeg` only: append/update the `natmeg_id -> cir_id` row in that cohort's override CSV; call `save_config(...)` to set/refresh that cohort's `meg_scan` stage config `subjectCsvMappingPath`. No symlinking needed (task 9's decision).
  5. Ensure a metadata-DB `subject` row exists for `cir_id` as `subject_code` (reuse existing "find-or-create subject by subject_code" pattern already used elsewhere, e.g. `MegExtractor._ensure_subject_row` in `meg/extractor.py:207-228`, as the reference implementation to mirror — do not import across the extract/meg boundary, duplicate the small find-or-create query the same way the codebase already tolerates for the observation-type mapping per the MEG parallel-track plan's precedent) and a `subject_cohorts` link for the resolved cohort; record the resulting `subject_id` back onto the `facility_discoveries` row (plain integer, no FK, per DB-placement decision).
  6. Mark the `facility_discoveries` row `confirmed`, set `reviewed_at`.
  7. Explicitly do **not** create or queue any extraction/`meg_scan`/`meg_ingest` job — confirm only creates/links `project`/`cohort`/`subject`/override-CSV state; running the pipeline stages remains a fully separate manual action exactly as for any other cohort today.

6. Global MEG raw-root path-suffix change.
- Change the hardcoded default in `get_meg_raw_root()` (`backend/src/meg/ingest.py:69-81`) from `<source_path>/derivatives/meg-raw` to `<source_path>/raw`, globally (not facility-discovery-gated, not a new flag) per the confirmed decision.
- Audit all 3 call sites in `backend/src/api/routes/cohorts.py` (lines 271-273, 349-352, 462-465) to confirm none hardcode the old suffix independently of the function (they call `get_meg_raw_root()` so should pick up the new default automatically); update any that duplicate the suffix literal if found.
- Add a **deployment note** (not a code change): any existing on-disk MEG cohort data already organized under `derivatives/meg-raw` must be manually moved/relinked to `raw` (or the affected cohort's `source_path` restructured) before its next `meg_ingest`/`meg_scan` run after this change ships; this migration is out of scope for the code change itself.

7. `.env`/config wiring.
- `FACILITY_MAPPING_CSV_PATH` (file path, required for discovery scan to run; scan endpoint returns a clear 400/404 if unset/missing rather than crashing).
- `MRC_STAGING_ROOT` (directory, auto-created if missing, used only for confirmed `mrc` project symlink staging).
- `FACILITY_SUBJECT_CODE_CSV_ROOT` (directory, auto-created if missing, used only for generated per-cohort subject-code override CSVs).
- Document all three alongside existing `.env` documentation (wherever `DATABASE_URL`/`METADATA_DATABASE_URL`/`CSV_UPLOAD_DIR` are already documented).

8. Frontend: facility-discovery review UI.
- New page/section listing `facility_discoveries` (pending by default, with facility/status filters), showing resolved `cir_id`, `cir_project`, `facility_id_value`, `session_number`, `scan_date`, `folder_path`.
- "Run discovery scan" button calling `POST /api/facility-discovery/scan`, surfacing the summary counts from task 4.
- Per-row Confirm/Reject actions calling the task-5 endpoints; confirmed rows show the resolved `project`/`cohort` they were linked to.
- No changes to existing cohort creation/edit or extraction/MEG stage screens beyond this new section (kept additive, per the "leave existing pipelines completely undisturbed" requirement).

## Data Flow
1. Operator triggers `POST /api/facility-discovery/scan`.
2. Scan reloads `facility_subject_mappings` from `FACILITY_MAPPING_CSV_PATH` (full delete+reinsert), then walks `vault/mrc` and `vault/natmeg/<project>/raw/<acquisition>` folders, matching folder ids/dates against mapping rows by the confirmed natural key, and upserts `facility_discoveries` rows with `status="pending"`.
3. Operator reviews pending discoveries in the new frontend screen and confirms (or rejects) each one.
4. Confirm resolves/creates `project` (app DB) and the facility+project's `cohort` (app DB, `project_id` set), performs `mrc` symlink staging or sets `natmeg` `source_path` directly, peeks DICOM `PatientID` for `mrc` (or uses the folder-derived `natmeg_id` directly for `natmeg`) to append a row to that cohort's subject-code override CSV, wires that CSV path into the cohort's `extract`/`meg_scan` stage config via `save_config(...)`, ensures a metadata-DB `subject` row + `subject_cohorts` link exist for `cir_id`, and marks the discovery `confirmed`.
5. Operator later runs the cohort's `extract`/`meg_ingest`/`meg_scan`/`meg_bids` stages exactly as for any other cohort — those stages read the already-wired override CSV via their existing, unmodified `subjectCodeCsv`/`subjectCsvMappingPath` config mechanisms and resolve `cir_id` as `subject_code` with no extraction-code changes.
6. Non-facility cohorts, and any cohort whose `project_id` is `NULL`, are completely unaffected at every step above.

## Failure Modes To Handle
- `FACILITY_MAPPING_CSV_PATH` missing/unset, unreadable, or missing required columns — scan endpoint returns a clear error and makes no `facility_subject_mappings`/`facility_discoveries` changes (reload is transactional).
- Mapping CSV row references a `mrc_id`/`natmeg_id` with no corresponding on-disk folder (sheet ahead of vault) — no discovery row created; not an error, just unmatched.
- Vault folder with no corresponding mapping row (vault ahead of sheet, or a non-facility/manual folder) — skipped silently, no discovery row created, no `ingest_conflicts` entry (this is expected/normal, not a failure).
- Same `mrc_id`/`natmeg_id` folder mapping to more than one `session_number`/`cir_id` in the sheet — staged as multiple distinct `facility_discoveries` rows for human disambiguation rather than auto-resolved.
- `mrc` DICOM header peek at confirm time finds no readable DICOM file in the candidate folder, or the file has no `PatientID` tag — confirm fails clearly for that discovery (does not silently fall back to folder name), leaving the discovery `pending` for retry/manual investigation.
- Confirming the same discovery twice (idempotency) — must not create duplicate `project`/`cohort`/`subject_cohorts` rows or duplicate override-CSV entries; find-or-create semantics throughout.
- Re-running the scan after some discoveries are already `confirmed` — must not resurrect them as new `pending` rows or overwrite their resolved `cohort_id`/`subject_id`.
- `MRC_STAGING_ROOT`/`FACILITY_SUBJECT_CODE_CSV_ROOT` not writable/misconfigured — confirm endpoint fails clearly rather than partially linking records.
- Existing MEG cohort with on-disk data still under `derivatives/meg-raw` runs `meg_ingest`/`meg_scan` after the global path-suffix change ships without the manual filesystem migration having been done — `get_meg_raw_root()` now points at a nonexistent/empty `raw` directory, so ingest finds zero files; this must be documented prominently as a deployment/rollout step, not silently tolerated.

## Rollout Notes
- Ship the global MEG raw-root path-suffix change (`derivatives/meg-raw` -> `raw`) together with an explicit migration checklist for any existing MEG cohort's on-disk layout, communicated before deploy (not solved in code).
- Facility-discovery is entirely additive: no existing cohort, extraction stage, or MEG stage behavior changes for cohorts with `project_id IS NULL` or outside the `mrc`/`natmeg` facility scope.
- `bmic` (third facility, PET/radioligand) is explicitly out of scope for this iteration — the importer must not fail on its columns, but no `bmic` discovery/matching logic is implemented.
- No scheduled/cron discovery scan in v1 — purely operator-triggered.
- No CSV-upload UI for the mapping sheet in v1 — purely a server-side configured path, fully reloaded each scan.

## Validation Plan
- Unit tests for the mapping CSV importer: tolerant of extra/missing `bmic_*` columns, full delete+reinsert semantics, natural-key handling for repeat `mrc_id`/`natmeg_id` rows across different `session_number`s.
- Unit tests for the `mrc` scan-matching logic: folder-name-to-`mrc_id` matching, multiple-session-number-per-folder staging as separate discoveries, unmatched folders skipped.
- Unit tests for the `natmeg` scan-matching logic: `(natmeg_id, scan_date)` exact match against `session_number`, unmatched folders skipped.
- Unit test that confirming a discovery is idempotent (repeat confirm of an already-`confirmed` row is a no-op or clear error, never a duplicate project/cohort/subject_cohorts row).
- Unit test for the DICOM `PatientID` header-peek step in isolation (given a folder with a readable DICOM file, extracts the correct tag value; given an unreadable/missing-tag file, fails clearly).
- Unit test that the generated per-cohort override CSV round-trips correctly through the **existing, unmodified** `extract.subject_mapping.load_subject_code_csv` / `meg.extractor.load_participant_subject_code_csv` functions.
- Integration test: end-to-end `mrc` discovery -> confirm -> symlink staging directory populated correctly -> existing (unmodified) `extract` stage run against the staging cohort resolves `subject_code = cir_id` via the wired override CSV.
- Integration test: end-to-end `natmeg` discovery -> confirm -> cohort `source_path` set to the real project directory -> existing (unmodified) `meg_scan` stage run resolves `subject_code = cir_id` via the wired override CSV, reading from `<source_path>/raw`.
- Regression test: existing non-facility cohorts' `extract`/`meg_ingest`/`meg_scan`/`meg_bids` behavior is completely unchanged (no `project_id`, no override CSV wired, no interaction with any new table).
- Regression test: `get_meg_raw_root()` default change does not affect any cohort whose stage config explicitly overrides the raw root (if such an override path exists) — verify the 3 call sites in `cohorts.py` still behave correctly end-to-end.

## Deferred Follow-up
- Mapping CSV upload endpoint/UI (v1 is server-side path only).
- Scheduled/cron discovery scans (v1 is manual-trigger only).
- `bmic` (PET/radioligand) facility discovery and mapping.
- Auto-queuing extraction immediately after confirm (v1 keeps confirm and extraction fully decoupled, by design).
- Automated migration tooling for existing MEG cohorts' on-disk `derivatives/meg-raw` -> `raw` restructuring (v1 is a manual deployment step).
- Bulk/batch confirm UI for large numbers of pending discoveries (v1 is per-row confirm/reject).

