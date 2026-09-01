import type { BidsStageConfig } from './bids';

// MEG stage ids belong to a separate, parallel stage family (see
// `MEG_PIPELINE_STAGES` in `nils_dataset_pipeline/ordering.py`): a cohort
// is either "imaging" (anonymize/extract/sort/bids) or "meg"
// (meg_ingest/meg_scan/meg_bids), never both. `STAGE_ORDER` below
// intentionally stays imaging-only for now; MEG-aware ordering/rendering
// lands with cohort modality support on the frontend.
export type StageId =
  | 'anonymize'
  | 'extract'
  | 'sort'
  | 'bids'
  | 'meg_ingest'
  | 'meg_scan'
  | 'meg_bids';

export type StageStatus =
  | 'idle'
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'blocked';

export interface StageRun {
  id: string;
  stageId: StageId;
  startedAt: string;
  finishedAt?: string;
  status: StageStatus;
  progress: number;
  configSnapshot: Record<string, unknown>;
  notes?: string;
  metrics?: import('./job').JobMetrics | null;
}

import type { AnonymizeStageConfig } from './anonymize';
import type { ExtractStageConfig } from './extract';
import type {
  MegBidsStageConfig,
  MegIngestStageConfig,
  MegScanStageConfig,
} from './meg';

export interface StageConfigById {
  anonymize: AnonymizeStageConfig;
  extract: ExtractStageConfig;
  sort: Record<string, unknown>;
  bids: BidsStageConfig;
  meg_ingest: MegIngestStageConfig;
  meg_scan: MegScanStageConfig;
  meg_bids: MegBidsStageConfig;
}

export interface StageSummary<Id extends StageId = StageId> {
  id: Id;
  title: string;
  description: string;
  status: StageStatus;
  progress: number;
  lastRunAt?: string;
  nextActionLabel?: string;
  jobId?: string;
  runs: StageRun[];
  artifacts?: Array<{
    id: string;
    name: string;
    type: 'table' | 'file' | 'log';
    previewPath?: string;
  }>;
  config?: StageConfigById[Id];
}

export const STAGE_LABELS: Record<string, string> = {
  anonymize: 'Anonymization',
  extract: 'Metadata Extraction',
  sort: 'Sorting',
  bids: 'BIDS Export',
  export: 'Export',
  subset_export: 'Subset Export', // legacy alias for pre-unification export jobs
  meg_ingest: 'MEG Ingest',
  meg_scan: 'MEG Scan',
  meg_bids: 'MEG BIDS Export',
};

// Imaging (DICOM MR/CT/PET) stage order only. MEG cohorts use a disjoint
// stage-id family (meg_ingest/meg_scan/meg_bids) and are not driven by this
// constant; MEG-specific ordering/rendering is added alongside frontend
// cohort-modality support.
export const STAGE_ORDER: StageId[] = [
  'anonymize',
  'extract',
  'sort',
  'bids',
];

// MEG (phase 1) stage order, parallel to STAGE_ORDER above. Not yet wired
// into any cohort-modality-aware rendering path.
export const MEG_STAGE_ORDER: StageId[] = [
  'meg_ingest',
  'meg_scan',
  'meg_bids',
];
