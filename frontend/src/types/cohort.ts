import type { StageSummary, StageId, StageStatus } from './stage';
import type { AnonymizeStageConfig } from './anonymize';
import type { JobSummary } from './job';

/**
 * Cohort data modality/type: "imaging" (DICOM MR/CT/PET, the default
 * pipeline of anonymize -> extract -> sort -> bids) or "meg" (the parallel
 * MEG track: meg_ingest -> meg_scan -> meg_bids). Mirrors
 * `backend/src/cohorts/service.py::VALID_COHORT_MODALITIES`. Set once at
 * cohort creation and immutable afterward, since changing it after
 * pipeline initialization would require re-initializing an entirely
 * different stage list.
 */
export type CohortModality = 'imaging' | 'meg';

export interface Cohort {
  id: number;
  name: string;
  description?: string;
  source_path: string;
  created_at: string;
  updated_at: string;
  anonymization_enabled: boolean;
  modality: CohortModality;
  tags: string[];
  status: StageStatus;
  total_subjects: number;
  total_sessions: number;
  total_series: number;
  completion_percentage: number;
  stages: StageSummary[];
  anonymize_job?: JobSummary | null;
  anonymize_history?: JobSummary[];
  extract_job?: JobSummary | null;
  extract_history?: JobSummary[];
}

export interface CohortStageRequest {
  cohort_id: number;
  stage_id: StageId;
  config: Record<string, unknown>;
}

export interface CreateCohortPayload {
  name: string;
  description?: string;
  source_path: string;
  anonymization_enabled: boolean;
  /** Defaults to "imaging" on the backend when omitted. */
  modality?: CohortModality;
  tags: string[];
  anonymize_config?: AnonymizeStageConfig;
}

export type CohortSummary = Pick<
  Cohort,
  'id' | 'name' | 'tags' | 'created_at' | 'stages' | 'modality'
>;
