import type { StageConfigById } from '../../types';
import { buildDefaultExtractConfig } from '../extraction/defaults';

export type NonAnonymizeStageConfigDefaults = Pick<
  StageConfigById,
  'extract' | 'sort' | 'bids' | 'meg_ingest' | 'meg_scan' | 'meg_bids'
>;

// Mirrors `nils_dataset_pipeline.ordering.get_default_stage_config` for the
// MEG stage ids (meg_ingest/meg_scan/meg_bids). Backend-provided stage
// config (already seeded with cohort-specific sourcePath/datasetDescriptionName
// at pipeline-init time) is merged over these client-side placeholders once
// the cohort loads.
export const buildNonAnonymizeStageDefaults = (): NonAnonymizeStageConfigDefaults => ({
  extract: buildDefaultExtractConfig(),
  sort: {
    profile: 'standard',
    applyLLMAssist: true,
    allowManualOverrides: true,
  },
  meg_ingest: {
    sourcePath: '',
    copyCalibrationFiles: true,
    copyCrosstalkFiles: true,
    preserveSplitFiles: true,
    copyWorkers: 4,
  },
  meg_scan: {
    subjectIdTypeId: null,
    subjectCsvMappingPath: null,
    namingConvention: 'natmeg',
    requireCalibrationFiles: false,
  },
  meg_bids: {
    bidsRootName: 'bids-meg',
    overwriteMode: 'skip',
    datasetDescriptionName: '',
    convertWorkers: 4,
  },
  bids: {
    outputModes: ['dcm'],
    outputMode: 'dcm',
    layout: 'bids',
    overwriteMode: 'skip',
    includeIntents: ['anat', 'dwi', 'func', 'fmap', 'perf'],
    includeProvenance: ['SyMRI', 'SWIRecon', 'EPIMix'],
    excludeProvenance: [],
    groupSyMRI: true,
    copyWorkers: 8,
    convertWorkers: 8,
    bidsDcmRootName: 'bids-dcm',
    bidsNiftiRootName: 'bids-nifti',
    flatDcmRootName: 'flat-dcm',
    flatNiftiRootName: 'flat-nifti',
    subjectIdentifierSource: 'subject_code',
    includeFieldStrengths: [],
    includeAccelerationInName: true,
  },
});
