/**
 * MEG Ingest Stage Form Component
 *
 * Configuration form for the `meg_ingest` stage: discovers and stages raw
 * FIF recordings (and split sets) into the cohort workspace, optionally
 * copying calibration/crosstalk files alongside them. Mirrors
 * `backend/src/meg/config.py::MegIngestConfig`.
 */

import { NumberInput, Stack, Switch, Text, TextInput } from '@mantine/core';
import type { JobSummary } from '../../types';
import type { MegIngestStageConfig } from '../../types/meg';
import { SectionCard } from '../shared/components/SectionCard';
import { JobProgressCard } from '../shared/components/JobProgressCard';

interface MegIngestStageFormProps {
  config: MegIngestStageConfig;
  onChange: (key: keyof MegIngestStageConfig, value: unknown) => void;
  /** Cohort source path, shown as the placeholder when sourcePath is unset. */
  cohortSourcePath?: string;
  activeJob?: JobSummary;
  lastJob?: JobSummary;
}

const ingestRunningStatuses = ['running', 'queued', 'paused'];

export const MegIngestStageForm = ({
  config,
  onChange,
  cohortSourcePath,
  activeJob,
  lastJob,
}: MegIngestStageFormProps) => {
  if (activeJob && ingestRunningStatuses.includes(activeJob.status)) {
    return (
      <Stack gap="md">
        <JobProgressCard job={activeJob} title="MEG ingest in progress" showJobsLink />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <SectionCard title="Source" description="Root directory to scan for raw FIF recordings">
        <TextInput
          label="Source path"
          placeholder={cohortSourcePath || '/data/cohort'}
          description="Defaults to the cohort source path when left empty"
          value={config.sourcePath}
          onChange={(event) => onChange('sourcePath', event.currentTarget.value)}
        />
      </SectionCard>

      <SectionCard title="Companion files" description="Copy calibration/crosstalk files alongside staged recordings">
        <Stack gap="sm">
          <Switch
            label="Copy calibration files"
            description="Copy MaxFilter calibration (sss_cal) files when present"
            checked={config.copyCalibrationFiles}
            onChange={(event) => onChange('copyCalibrationFiles', event.currentTarget.checked)}
          />
          <Switch
            label="Copy crosstalk files"
            description="Copy MaxFilter crosstalk (ct_sparse) files when present"
            checked={config.copyCrosstalkFiles}
            onChange={(event) => onChange('copyCrosstalkFiles', event.currentTarget.checked)}
          />
          <Switch
            label="Preserve split files"
            description="Keep multi-part split FIF sets (e.g. _raw.fif, _raw-1.fif, ...) together during staging"
            checked={config.preserveSplitFiles}
            onChange={(event) => onChange('preserveSplitFiles', event.currentTarget.checked)}
          />
        </Stack>
      </SectionCard>

      <SectionCard title="Performance" description="Configure worker counts for parallel processing">
        <NumberInput
          label="Copy workers"
          min={1}
          max={32}
          value={config.copyWorkers}
          onChange={(value) => onChange('copyWorkers', Number(value ?? 4))}
        />
      </SectionCard>

      {lastJob && <JobProgressCard job={lastJob} title="Last MEG ingest run" />}
      {!lastJob && (
        <Text size="xs" c="dimmed">
          MEG ingest has not run yet for this cohort.
        </Text>
      )}
    </Stack>
  );
};
