/**
 * MEG (magnetoencephalography) parallel processing track config types.
 *
 * Mirrors `backend/src/meg/config.py`. Phase 1 covers three stages:
 * `meg_ingest`, `meg_scan`, and `meg_bids` (`meg_maxfilter`/`meg_qc` are
 * deferred and have no config types yet). Field names are camelCase here
 * and snake_case on the backend, matching the existing convention (see
 * `ExtractStageConfig` / `extract.config.ExtractionConfig`).
 */

export interface MegIngestStageConfig {
  sourcePath: string;
  copyCalibrationFiles: boolean;
  copyCrosstalkFiles: boolean;
  preserveSplitFiles: boolean;
  copyWorkers: number;
}

/** Subject identity resolution shared by MEG stages that need it. */
export interface MegSubjectResolutionConfig {
  subjectIdTypeId?: number | null;
  subjectCsvMappingPath?: string | null;
}

export interface MegScanStageConfig extends MegSubjectResolutionConfig {
  namingConvention: string;
  requireCalibrationFiles: boolean;
}

export type MegBidsOverwriteMode = 'skip' | 'overwrite';

export interface MegBidsStageConfig {
  bidsRootName: string;
  overwriteMode: MegBidsOverwriteMode;
  datasetDescriptionName: string;
  convertWorkers: number;
}
