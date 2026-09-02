/**
 * MEG BIDS Stage Form Component
 *
 * Configuration form for the `meg_bids` stage: converts staged FIF
 * recordings into a BIDS dataset via mne-bids, reading conversion-table
 * rows produced by `meg_scan`. Mirrors
 * `backend/src/meg/config.py::MegBidsConfig`.
 */

import { NumberInput, SegmentedControl, Stack, Text, TextInput } from '@mantine/core';
import type { JobSummary } from '../../types';
import type { MegBidsStageConfig } from '../../types/meg';
import { SectionCard } from '../shared/components/SectionCard';
import { JobProgressCard } from '../shared/components/JobProgressCard';

interface MegBidsStageFormProps {
  config: MegBidsStageConfig;
  onChange: (key: keyof MegBidsStageConfig, value: unknown) => void;
  /** Cohort name, shown as the placeholder when datasetDescriptionName is unset. */
  cohortName?: string;
  activeJob?: JobSummary;
  lastJob?: JobSummary;
}

const bidsRunningStatuses = ['running', 'queued', 'paused'];

export const MegBidsStageForm = ({
  config,
  onChange,
  cohortName,
  activeJob,
  lastJob,
}: MegBidsStageFormProps) => {
  if (activeJob && bidsRunningStatuses.includes(activeJob.status)) {
    return (
      <Stack gap="md">
        <JobProgressCard job={activeJob} title="MEG BIDS export in progress" showJobsLink />
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <SectionCard title="Output" description="Configure the BIDS output directory and overwrite behavior">
        <Stack gap="sm">
          <TextInput
            label="BIDS root name"
            description="Name of the BIDS output directory, created under the cohort workspace derivatives"
            value={config.bidsRootName}
            onChange={(event) => onChange('bidsRootName', event.currentTarget.value)}
          />
          <SegmentedControl
            value={config.overwriteMode}
            onChange={(value) => onChange('overwriteMode', value ?? 'skip')}
            data={[
              { label: 'Skip existing', value: 'skip' },
              { label: 'Overwrite', value: 'overwrite' },
            ]}
          />
        </Stack>
      </SectionCard>

      <SectionCard title="Dataset description" description="Metadata written into dataset_description.json">
        <TextInput
          label="Dataset name"
          placeholder={cohortName || 'Cohort name'}
          description="Defaults to the cohort name when left empty"
          value={config.datasetDescriptionName}
          onChange={(event) => onChange('datasetDescriptionName', event.currentTarget.value)}
        />
      </SectionCard>

      <SectionCard title="Performance" description="Configure worker counts for parallel processing">
        <NumberInput
          label="Convert workers"
          min={1}
          max={32}
          value={config.convertWorkers}
          onChange={(value) => onChange('convertWorkers', Number(value ?? 4))}
        />
      </SectionCard>

      {lastJob && <JobProgressCard job={lastJob} title="Last MEG BIDS export" />}
      {!lastJob && (
        <Text size="xs" c="dimmed">
          MEG BIDS export has not run yet for this cohort.
        </Text>
      )}
    </Stack>
  );
};
