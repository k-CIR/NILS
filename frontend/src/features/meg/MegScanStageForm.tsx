/**
 * MEG Scan Stage Form Component
 *
 * Configuration form for the `meg_scan` stage: reads FIF headers only,
 * resolves subject identity, upserts one `study` row per logical MEG
 * session, and (re)generates the BIDS conversion table consumed by
 * `meg_bids`. Mirrors `backend/src/meg/config.py::MegScanConfig`.
 */

import { Select, Stack, Switch, Text, TextInput } from '@mantine/core';
import type { JobSummary } from '../../types';
import type { MegScanStageConfig } from '../../types/meg';
import { SectionCard } from '../shared/components/SectionCard';
import { JobProgressCard } from '../shared/components/JobProgressCard';

interface MegScanStageFormProps {
  config: MegScanStageConfig;
  onChange: (key: keyof MegScanStageConfig, value: unknown) => void;
  /** id_types options for subject identity resolution (excludes the implicit "use subject_code" option). */
  subjectIdTypeOptions: Array<{ value: string; label: string }>;
  activeJob?: JobSummary;
  lastJob?: JobSummary;
}

const scanRunningStatuses = ['running', 'queued', 'paused'];

const NONE_ID_TYPE_VALUE = '__none__';

export const MegScanStageForm = ({
  config,
  onChange,
  subjectIdTypeOptions,
  activeJob,
  lastJob,
}: MegScanStageFormProps) => {
  if (activeJob && scanRunningStatuses.includes(activeJob.status)) {
    return (
      <Stack gap="md">
        <JobProgressCard job={activeJob} title="MEG scan in progress" showJobsLink />
      </Stack>
    );
  }

  const subjectIdTypeValue =
    config.subjectIdTypeId === null || config.subjectIdTypeId === undefined
      ? NONE_ID_TYPE_VALUE
      : String(config.subjectIdTypeId);

  return (
    <Stack gap="md">
      <SectionCard
        title="Subject identity"
        description="Resolve each recording's subject via an id_types lookup, with a CSV mapping fallback"
      >
        <Stack gap="sm">
          <Select
            label="Subject identifier type"
            description="None falls back to the CSV mapping (or the raw FIF subject code) below"
            value={subjectIdTypeValue}
            data={[{ value: NONE_ID_TYPE_VALUE, label: 'None (use subject code)' }, ...subjectIdTypeOptions]}
            onChange={(value) =>
              onChange('subjectIdTypeId', value && value !== NONE_ID_TYPE_VALUE ? Number(value) : null)
            }
          />
          <TextInput
            label="CSV mapping path (fallback)"
            description="Path to an uploaded CSV mapping recording/session identifiers to subject_code"
            placeholder="/data/cohort/subject_mapping.csv"
            value={config.subjectCsvMappingPath ?? ''}
            onChange={(event) => onChange('subjectCsvMappingPath', event.currentTarget.value || null)}
          />
        </Stack>
      </SectionCard>

      <SectionCard title="Parsing" description="Filename/task/run/acquisition parsing convention">
        <TextInput
          label="Naming convention"
          description="Vendored cir-utils parser to apply (e.g. natmeg)"
          value={config.namingConvention}
          onChange={(event) => onChange('namingConvention', event.currentTarget.value)}
        />
      </SectionCard>

      <SectionCard title="Validation" description="Behavior when required companion files are missing">
        <Switch
          label="Require calibration files"
          description="Fail scan (route recording to ingest_conflicts) if calibration/crosstalk files are missing"
          checked={config.requireCalibrationFiles}
          onChange={(event) => onChange('requireCalibrationFiles', event.currentTarget.checked)}
        />
      </SectionCard>

      {lastJob && <JobProgressCard job={lastJob} title="Last MEG scan run" />}
      {!lastJob && (
        <Text size="xs" c="dimmed">
          MEG scan has not run yet for this cohort.
        </Text>
      )}
    </Stack>
  );
};
