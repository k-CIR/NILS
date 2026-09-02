import {
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Collapse,
  Group,
  Loader,
  NumberInput,
  Progress,
  Select,
  SimpleGrid,
  SegmentedControl,
  Stack,
  Switch,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconExternalLink } from '@tabler/icons-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useCohortQuery, useRunStageMutation } from '../api';
import { useSystemResources } from '../queries';
import { PipelineStepper } from '../../shared/components/PipelineStepper';
import { findSuggestedActiveIndex } from '../../shared/components/pipelineStepperUtils';
import { StageCard } from '../../shared/components/StageCard';
import {
  STAGE_ORDER,
  MEG_STAGE_ORDER,
  type AnonymizeStageConfig,
  type ExtractStageConfig,
  type StageConfigById,
  type StageId,
  type StageSummary,
  type JobAction,
  type SystemResources,
} from '../../../types';
import { formatDateTime } from '../../../utils/formatters';
import { AnonymizeStageForm } from '../../anonymization/AnonymizeStageForm';
import { buildAnonymizeConfigFromExisting, buildDefaultAnonymizeConfig } from '../../anonymization/defaults';
import { ExtractStageForm } from '../../extraction/ExtractStageForm';
import { buildDefaultExtractConfig } from '../../extraction/defaults';
import { buildNonAnonymizeStageDefaults, type NonAnonymizeStageConfigDefaults } from '../../stages/defaults';
import type { JobSummary } from '../../../types';
import { ApiError } from '../../../utils/api-client';
import { useJobAction } from '../../jobs/api';
import { useJobsQuery } from '../../jobs/api';
import { useQueryClient } from '@tanstack/react-query';
import { SortingPipelineSimple } from '../../sorting/components/SortingPipelineSimple';
import { useRunSortingStep, sortingKeys, type SortingConfig } from '../../sorting';
import { useIdTypes } from '../../database/api';
import { BidsStageForm, type BidsConfig } from '../../bids/BidsStageForm';
import { MegIngestStageForm } from '../../meg/MegIngestStageForm';
import { MegScanStageForm } from '../../meg/MegScanStageForm';
import { MegBidsStageForm } from '../../meg/MegBidsStageForm';
import { KeywordSettingsTab } from '../keywords/KeywordSettingsTab';

// Debug logging disabled for production - uncomment for local debugging
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const debugLog = (_hypothesisId: string, _location: string, _message: string, _data: Record<string, unknown>) => {
  // No-op in production
};

type StageConfigState = Partial<StageConfigById> & NonAnonymizeStageConfigDefaults;
type GenericStageId = 'sort' | 'bids' | 'meg_ingest' | 'meg_scan' | 'meg_bids';
// Stage ids driven by the generic single-job-per-run pattern (job_service +
// JobSummary polling), as opposed to 'sort' which uses its own bespoke
// streaming step system (SortingPipelineSimple).
const JOB_DRIVEN_STAGE_IDS: ReadonlySet<StageId> = new Set(['bids', 'meg_ingest', 'meg_scan', 'meg_bids']);
// Stage ids whose config is read/written generically via `configs[stageId]`
// and `handleGenericConfigChange`, without stage-specific state/handlers
// (unlike 'anonymize' and 'extract').
const GENERIC_CONFIG_STAGE_IDS: ReadonlySet<StageId> = new Set([
  'sort',
  'bids',
  'meg_ingest',
  'meg_scan',
  'meg_bids',
]);

interface SortingState {
  config: SortingConfig;
  jobId: number | null;
  streamUrl: string | null;
}

export const CohortDetailPage = () => {
  const { cohortId } = useParams<{ cohortId: string }>();
  const { data: cohort, isLoading, isError, error } = useCohortQuery(cohortId);
  const { data: jobs } = useJobsQuery();
  const queryClient = useQueryClient();
  const systemResourcesQuery = useSystemResources();
  const { data: systemResources, isFetching: systemResourcesLoading, refetch: fetchSystemResources } =
    systemResourcesQuery;
  const [configs, setConfigs] = useState<StageConfigState>(() => ({
    ...buildNonAnonymizeStageDefaults(),
  } as StageConfigState));
  const [activeStageIndex, setActiveStageIndex] = useState(0);
  const runStageMutation = useRunStageMutation();
  const jobActionMutation = useJobAction();
  const [anonymizeConflict, setAnonymizeConflict] = useState<{ message: string; path?: string } | null>(null);
  const [configInitialized, setConfigInitialized] = useState(false);
  const lastInitializedCohortIdRef = useRef<number | null>(null);
  const [stageSelectionInitialized, setStageSelectionInitialized] = useState(false);

  // Sorting state
  const runSortingStepMutation = useRunSortingStep();
  const [sortingState, setSortingState] = useState<SortingState>({
    config: {
      skipClassified: true,
      forceReprocess: false,
      profile: 'standard',
      selectedModalities: ['MR', 'CT', 'PT'],
      previewMode: false,  // Will be set to true for Step 2's first run
    },
    jobId: null,
    streamUrl: null,
  });

  const intentOptions = [
    { label: 'Anatomical (anat)', value: 'anat' },
    { label: 'Diffusion (dwi)', value: 'dwi' },
    { label: 'Functional (func)', value: 'func' },
    { label: 'Field map (fmap)', value: 'fmap' },
    { label: 'Perfusion (perf)', value: 'perf' },
    { label: 'Localizer', value: 'localizer' },
    { label: 'Misc', value: 'misc' },
  ];

  const defaultIntentSelection = intentOptions
    .filter((option) => option.value !== 'localizer' && option.value !== 'misc')
    .map((option) => option.value);

  const defaultProvenanceSelection = ['SyMRI', 'SWIRecon', 'EPIMix'];

  const provenanceOptions = [
    { label: 'SyMRI', value: 'SyMRI' },
    { label: 'SWI', value: 'SWIRecon' },
    { label: 'EPIMix', value: 'EPIMix' },
    { label: 'Projections/MPRs', value: 'ProjectionDerived' },
  ];

  const fieldStrengthOptions = [
    { label: '1.5T', value: '1.5' },
    { label: '3T', value: '3' },
    { label: '7T', value: '7' },
  ];

  // Fetch available identifier types for BIDS subject naming
  const { data: idTypesResponse } = useIdTypes();
  const subjectIdentifierOptions = useMemo(() => {
    const options = [{ label: 'Subject Code (default)', value: 'subject_code' }];
    if (idTypesResponse?.items) {
      for (const idType of idTypesResponse.items) {
        options.push({ label: idType.name, value: String(idType.id) });
      }
    }
    return options;
  }, [idTypesResponse]);

  // Same id_types list, without the DICOM-specific "subject_code" pseudo-option:
  // MEG scan's subjectIdTypeId is `Optional[int]` (None means "use subject
  // code" already, handled separately by MegScanStageForm).
  const megSubjectIdTypeOptions = useMemo(() => {
    if (!idTypesResponse?.items) return [];
    return idTypesResponse.items.map((idType) => ({ label: idType.name, value: String(idType.id) }));
  }, [idTypesResponse]);

  useEffect(() => {
    debugLog('H-empty', 'CohortDetailPage', 'status', {
      cohortIdParam: cohortId,
      isLoading,
      isError,
      hasCohort: Boolean(cohort),
      errorMessage: error ? String((error as Error).message ?? error) : null,
    });
  }, [cohortId, cohort, isLoading, isError, error]);

  useEffect(() => {
    if (isError) {
      debugLog('H-empty', 'CohortDetailPage', 'error-state', {
        cohortIdParam: cohortId,
        errorMessage: error ? String((error as Error).message ?? error) : 'unknown',
      });
    }
  }, [isError, error, cohortId]);

  useEffect(() => {
    if (!cohort) {
      return;
    }
    if (lastInitializedCohortIdRef.current !== cohort.id) {
      setConfigInitialized(false);
      setStageSelectionInitialized(false);
      lastInitializedCohortIdRef.current = cohort.id;
    }
    debugLog('H-empty', 'CohortDetailPage', 'cohort-loaded', {
      cohortId: cohort.id,
      stageCount: Array.isArray(cohort.stages) ? cohort.stages.length : null,
    });
  }, [cohort]);

  useEffect(() => {
    if (!cohort || configInitialized) {
      return;
    }

    const base = {
      ...buildNonAnonymizeStageDefaults(),
    } as StageConfigState;

    debugLog('H3', 'CohortDetailPage', 'cohort-load', {
      cohortId,
      hasCohort: Boolean(cohort),
      isLoading,
      isError,
    });

    const stagesArray = Array.isArray(cohort.stages) ? cohort.stages : [];
    const anonymizeStage = stagesArray.find((stage) => stage.id === 'anonymize');

    const recommendationContext = systemResources
      ? {
        recommendedProcesses: systemResources.recommended_processes,
        recommendedWorkers: systemResources.recommended_workers,
      }
      : undefined;

    stagesArray.forEach((stage) => {
      if (stage.id === 'anonymize') {
        base.anonymize = buildAnonymizeConfigFromExisting(
          stage.config as AnonymizeStageConfig | undefined,
          {
            cohortName: cohort.name,
            sourcePath: cohort.source_path,
          },
          recommendationContext,
        );
      } else if (stage.id === 'extract') {
        const defaultExtract = buildDefaultExtractConfig();
        const existingExtract = (stage.config as Partial<ExtractStageConfig> | undefined) ?? {};
        base.extract = {
          ...defaultExtract,
          ...existingExtract,
          resumeByPath: existingExtract.resumeByPath ?? (existingExtract.resume ?? defaultExtract.resumeByPath),
        };
      } else if (stage.config) {
        base[stage.id] = {
          ...(base[stage.id] as any),
          ...(stage.config as any),
        } as any;
      }
    });

    if (!anonymizeStage) {
      base.anonymize = buildDefaultAnonymizeConfig(
        {
          cohortName: cohort.name,
          sourcePath: cohort.source_path,
        },
        recommendationContext,
      );
    }

    setConfigs(base);
    setConfigInitialized(true);
  }, [cohort, systemResources, configInitialized]);

  const orderedStages = useMemo(() => {
    if (!cohort) return [];
    const stagesArray = Array.isArray(cohort.stages) ? cohort.stages : [];
    const stageById = Object.fromEntries(stagesArray.map((stage) => [stage.id, stage]));
    const stageOrder = cohort.modality === 'meg' ? MEG_STAGE_ORDER : STAGE_ORDER;
    return stageOrder.map((id) => stageById[id]).filter((stage): stage is StageSummary => Boolean(stage));
  }, [cohort]);

  const suggestedStageIndex = useMemo(() => findSuggestedActiveIndex(orderedStages), [orderedStages]);

  useEffect(() => {
    if (orderedStages.length === 0) {
      setActiveStageIndex(0);
      return;
    }

    // On initial load for this cohort, always use the suggested index
    if (!stageSelectionInitialized) {
      setActiveStageIndex(suggestedStageIndex);
      setStageSelectionInitialized(true);
      return;
    }

    // After initial load, only change if current selection is invalid
    setActiveStageIndex((prev) => (orderedStages[prev] ? prev : suggestedStageIndex));
  }, [orderedStages, suggestedStageIndex, stageSelectionInitialized]);

  const resolvedActiveIndex = orderedStages[activeStageIndex] ? activeStageIndex : suggestedStageIndex;
  const activeStage = orderedStages[resolvedActiveIndex];
  const activeStageConfig = activeStage ? configs[activeStage.id] : undefined;
  const activeGenericConfig =
    activeStage && GENERIC_CONFIG_STAGE_IDS.has(activeStage.id)
      ? (activeStageConfig as Record<string, unknown> | undefined)
      : undefined;

  // Generic job lookup for any stage that uses the single-job-per-run
  // pattern (bids, meg_ingest, meg_scan, meg_bids). Only the currently
  // active stage's jobs are computed, mirroring how each was previously
  // scoped individually for 'bids'.
  const jobDrivenStageId =
    activeStage && JOB_DRIVEN_STAGE_IDS.has(activeStage.id) ? activeStage.id : null;

  const jobDrivenStageJobId =
    jobDrivenStageId && activeStage?.jobId ? Number(activeStage.jobId) : null;

  const jobDrivenStageJobs = useMemo(() => {
    if (!jobs || !jobDrivenStageId) return [];
    return jobs.filter((job) => {
      if (job.stageId !== jobDrivenStageId) return false;
      const matchesCohort = cohort ? job.cohortId === cohort.id : true;
      const matchesStageJob = jobDrivenStageJobId != null ? job.id === jobDrivenStageJobId : false;
      return matchesCohort || matchesStageJob;
    });
  }, [jobs, cohort, jobDrivenStageId, jobDrivenStageJobId]);

  const activeBidsJob = useMemo(
    () => jobDrivenStageJobs.find((job) => ['running', 'queued', 'paused'].includes(job.status)),
    [jobDrivenStageJobs],
  );

  const lastBidsJob = useMemo(() => {
    if (!jobDrivenStageJobs.length) return null;
    return [...jobDrivenStageJobs].sort(
      (a, b) => new Date(b.submittedAt).getTime() - new Date(a.submittedAt).getTime(),
    )[0];
  }, [jobDrivenStageJobs]);

  const anonymizeConfig = configs.anonymize as AnonymizeStageConfig | undefined;

  const stageBlocked = activeStage?.status === 'blocked';
  const blockingStage = stageBlocked
    ? orderedStages.slice(0, resolvedActiveIndex).find((stage) => stage.status !== 'completed')
    : undefined;
  const blockedReason = stageBlocked
    ? blockingStage
      ? `${blockingStage.title} must complete before this step becomes available.`
      : 'Complete the previous stage to unlock this step.'
    : undefined;

  const anonymizeJob = (cohort?.anonymize_job as JobSummary | undefined) ?? null;
  const anonymizeHistory = (cohort?.anonymize_history as JobSummary[] | undefined) ?? [];
  const extractJob = (cohort?.extract_job as JobSummary | undefined) ?? null;
  const extractHistory = (cohort?.extract_history as JobSummary[] | undefined) ?? [];
  const anonymizeJobStatus = anonymizeJob?.status;
  const anonymizeBusy = anonymizeJobStatus === 'running' || anonymizeJobStatus === 'queued';
  const extractBusy = extractJob ? ['queued', 'running', 'paused'].includes(extractJob.status) : false;
  const [showAnonymizeProgress, setShowAnonymizeProgress] = useState(false);
  useEffect(() => {
    if (anonymizeBusy) {
      setShowAnonymizeProgress(true);
    }
  }, [anonymizeBusy]);

  const handleGenericConfigChange = (
    stageId: GenericStageId,
    key: string,
    value: string | number | boolean | string[] | null,
  ) => {
    setConfigs((prev) => {
      const nextStageConfig = {
        ...(prev[stageId] as Record<string, unknown> | undefined),
      };
      nextStageConfig[key] = value;
      return {
        ...prev,
        [stageId]: nextStageConfig,
      } as StageConfigState;
    });
  };

  const handleAnonymizeConfigChange = (next: AnonymizeStageConfig) => {
    setConfigs((prev) => ({
      ...prev,
      anonymize: next,
    }));
  };

  const handleExtractConfigChange = (next: ExtractStageConfig) => {
    setConfigs((prev) => ({
      ...prev,
      extract: next,
    }));
  };

  const applySystemRecommendations = async (apply: (resources: SystemResources) => void) => {
    const result = await fetchSystemResources();
    if (result.error) {
      notifications.show({
        color: 'red',
        message: result.error instanceof Error ? result.error.message : 'Unable to fetch system resources.',
      });
      return;
    }
    const resources = result.data ?? systemResources;
    if (!resources) return;
    apply(resources);
  };

  const handleRecommendAnonymizeResources = () =>
    applySystemRecommendations((resources) => {
      const recommendedProcesses = Math.max(1, resources.recommended_processes ?? 1);
      const recommendedWorkers = Math.max(1, resources.recommended_workers ?? recommendedProcesses);
      setConfigs((prev) => {
        const current = prev.anonymize as AnonymizeStageConfig | undefined;
        if (!current) {
          return prev;
        }
        if (current.processCount === recommendedProcesses && current.workerCount === recommendedWorkers) {
          return prev;
        }
        return {
          ...prev,
          anonymize: {
            ...current,
            processCount: recommendedProcesses,
            workerCount: recommendedWorkers,
          },
        } as StageConfigState;
      });
    });

  const handleRecommendExtractResources = () =>
    applySystemRecommendations((resources) => {
      setConfigs((prev) => {
        const current = prev.extract as ExtractStageConfig | undefined;
        if (!current) {
          return prev;
        }
        const workerCap = resources.max_workers_cap ?? 128;
        const batchCap = resources.max_batch_cap ?? 5000;
        const queueCap = resources.max_queue_cap ?? 500;
        const adaptiveCap = resources.max_adaptive_batch_cap ?? 20000;
        const safeBatchCap = resources.safe_instance_batch_rows ?? batchCap;
        const dbWriterPoolCap = resources.max_db_writer_pool_cap ?? 16;
        const recommendedWorkers = Math.min(workerCap, resources.recommended_workers ?? current.maxWorkers);
        const recommendedProcesses = Math.min(workerCap, resources.recommended_processes ?? recommendedWorkers);
        const recommendedBatch = Math.min(
          batchCap,
          safeBatchCap,
          resources.recommended_batch_size ?? current.batchSize,
        );
        const recommendedQueue = Math.min(queueCap, resources.recommended_queue_depth ?? current.queueSize);
        const recommendedAdaptiveMin = Math.min(
          recommendedBatch,
          safeBatchCap,
          resources.recommended_adaptive_min_batch ?? current.adaptiveMinBatchSize,
        );
        const recommendedAdaptiveMax = Math.min(
          adaptiveCap,
          safeBatchCap,
          resources.recommended_adaptive_max_batch ?? current.adaptiveMaxBatchSize,
        );

        const next: ExtractStageConfig = {
          ...current,
          maxWorkers: recommendedWorkers,
          processPoolWorkers: recommendedProcesses,
          batchSize: recommendedBatch,
          queueSize: recommendedQueue,
          adaptiveMinBatchSize: recommendedAdaptiveMin,
          adaptiveMaxBatchSize: Math.max(recommendedAdaptiveMin, recommendedAdaptiveMax),
          seriesWorkersPerSubject:
            resources.recommended_series_workers_per_subject ?? current.seriesWorkersPerSubject,
          dbWriterPoolSize: Math.min(
            dbWriterPoolCap,
            resources.recommended_db_writer_pool ?? current.dbWriterPoolSize ?? 3,
          ),
        };
        return {
          ...prev,
          extract: next,
        } as StageConfigState;
      });
    });

  const handleRunStage = (stageId: StageId, retryMode?: 'clean' | 'overwrite') => {
    if (!cohort) return;

    const targetStage = orderedStages.find((stage) => stage?.id === stageId);
    if (targetStage?.status === 'blocked') {
      notifications.show({ color: 'gray', message: 'Complete the previous stage to unlock this step.' });
      return;
    }

    const baseConfig = (configs[stageId] as Record<string, unknown> | undefined) ?? {};
    const payloadConfig: Record<string, unknown> = { ...baseConfig };

    if (stageId === 'anonymize') {
      setAnonymizeConflict(null);
      payloadConfig.derivativesRetryMode = retryMode ?? 'prompt';
      if (retryMode === 'overwrite') {
        payloadConfig.resume = true;
      } else if (!retryMode) {
        payloadConfig.resume = false;
      }
    }
    if (stageId === 'extract') {
      const resumeValue = typeof payloadConfig['resume'] === 'boolean' ? (payloadConfig['resume'] as boolean) : true;
      payloadConfig.resumeByPath = resumeValue;
    }


    runStageMutation.mutate(
      {
        cohort_id: cohort.id,
        stage_id: stageId,
        config: payloadConfig,
      },
      {
        onSuccess: () => {
          notifications.show({ color: 'teal', message: `${stageId} queued.` });
          if (stageId === 'anonymize') {
            setShowAnonymizeProgress(true);
          }
        },
        onError: (error) => {
          if (stageId === 'anonymize' && error instanceof ApiError && error.status === 409 && !retryMode) {
            const detail = (error.body ?? {}) as { message?: string; path?: string };
            setAnonymizeConflict({
              message:
                detail?.message ??
                'Existing anonymized files were detected under derivatives/dcm-raw. Choose how to proceed.',
              path: detail?.path,
            });
            setShowAnonymizeProgress(false);
            return;
          }
          notifications.show({ color: 'red', message: (error as Error).message });
        },
      },
    );
  };

  const handleExtractionAction = (action: JobAction) => {
    if (!extractJob || jobActionMutation.isPending) {
      return;
    }
    jobActionMutation.mutate({ jobId: extractJob.id, action });
  };

  const handlePauseExtraction = () => handleExtractionAction('pause');
  const handleResumeExtraction = () => handleExtractionAction('resume');
  const handleCancelExtraction = () => handleExtractionAction('cancel');

  const renderAnonymizeConflict = () => {
    if (!anonymizeConflict) return null;
    return (
      <Card withBorder radius="md" padding="md" bg="rgba(248, 81, 73, 0.1)">
        <Stack gap="sm">
          <Text fw={600}>Existing anonymized files detected</Text>
          <Text size="sm">
            {anonymizeConflict.message}
            {anonymizeConflict.path ? ` (Path: ${anonymizeConflict.path})` : ''}
          </Text>
          <Group gap="sm">
            <Button color="red" variant="filled" onClick={() => handleRunStage('anonymize', 'clean')}>
              Clean processed folder &amp; retry
            </Button>
            <Button color="blue" variant="light" onClick={() => handleRunStage('anonymize', 'overwrite')}>
              Continue and skip existing files
            </Button>
            <Button variant="default" onClick={() => setAnonymizeConflict(null)}>
              Cancel
            </Button>
          </Group>
        </Stack>
      </Card>
    );
  };

  const renderAnonymizeExecution = () => {
    if (!anonymizeJob || !anonymizeBusy) return null;
    const jobConfig = (anonymizeJob.config ?? {}) as Record<string, unknown>;
    const sourceRoot = (jobConfig.source_root as string | undefined) ?? cohort?.source_path;
    const outputRoot = jobConfig.output_root as string | undefined;
    const strategy = (jobConfig.patient_id as { strategy?: string } | undefined)?.strategy ?? 'unknown';

    return (
      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Group justify="space-between" align="center">
            <Text fw={600}>Anonymization in progress</Text>
            <Badge color={anonymizeJob.status === 'running' ? 'blue' : 'yellow'}>
              {anonymizeJob.status.toUpperCase()}
            </Badge>
          </Group>
          <Stack gap={4}>
            <Text size="sm">
              <strong>Source:</strong> {sourceRoot}
            </Text>
            {outputRoot && (
              <Text size="sm">
                <strong>Output:</strong> {outputRoot}
              </Text>
            )}
            <Text size="sm">
              <strong>Patient ID strategy:</strong> {strategy}
            </Text>
          </Stack>
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              Job progress
            </Text>
            <Progress value={anonymizeJob.progress} size="lg" radius="md" transitionDuration={200} />
            <Text size="sm" fw={600}>
              {anonymizeJob.progress}%
            </Text>
          </Stack>
          <Group justify="space-between" align="center">
            <Text size="xs" c="dimmed">
              Started {anonymizeJob.startedAt ? formatDateTime(anonymizeJob.startedAt) : 'pending'}
            </Text>
            <Button size="xs" variant="light" component={Link} to="/jobs">
              View all jobs
            </Button>
          </Group>
        </Stack>
      </Card>
    );
  };

  const renderAnonymizeHistory = () => {
    if (!anonymizeHistory.length) return null;
    return (
      <Card withBorder padding="md" radius="md">
        <Stack gap="xs">
          <Text fw={600}>Recent anonymization runs</Text>
          {anonymizeHistory.slice(0, 5).map((job) => (
            <Group key={job.id} justify="space-between" align="center">
              <Stack gap={0}>
                <Text size="sm" fw={500}>
                  Job #{job.id} · {job.status}
                </Text>
                <Text size="xs" c="dimmed">
                  Started {job.startedAt ? formatDateTime(job.startedAt) : formatDateTime(job.submittedAt)}
                </Text>
              </Stack>
              <Badge color={job.status === 'completed' || job.status === 'completed_with_warnings' ? 'teal' : job.status === 'failed' ? 'red' : 'blue'}>
                {job.progress}%
              </Badge>
            </Group>
          ))}
        </Stack>
      </Card>
    );
  };

  const renderExtractSummary = () => {
    if (!extractHistory.length) return null;
    const latest = extractHistory[0];
    const badgeColor =
      latest.status === 'completed' || latest.status === 'completed_with_warnings'
        ? 'teal'
        : latest.status === 'failed'
          ? 'red'
          : latest.status === 'running'
            ? 'blue'
            : latest.status === 'queued'
              ? 'yellow'
              : 'gray';
    const metrics = latest.metrics;

    return (
      <Card withBorder padding="md" radius="md">
        <Stack gap="sm">
          <Group justify="space-between" align="center">
            <Text fw={600}>Last extraction summary</Text>
            <Badge color={badgeColor}>{latest.status.toUpperCase()}</Badge>
          </Group>
          <Stack gap={0}>
            <Text size="xs" c="dimmed">
              Started {latest.startedAt ? formatDateTime(latest.startedAt) : formatDateTime(latest.submittedAt)}
            </Text>
            {latest.finishedAt && (
              <Text size="xs" c="dimmed">
                Finished {formatDateTime(latest.finishedAt)}
              </Text>
            )}
          </Stack>
          {metrics ? (
            <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="md">
              {[
                { label: 'Subjects', value: metrics.subjects },
                { label: 'Studies', value: metrics.studies },
                { label: 'Series', value: metrics.series },
                { label: 'Instances', value: metrics.instances },
              ].map((entry) => (
                <Stack key={entry.label} gap={2} align="flex-start">
                  <Text size="xs" c="dimmed">
                    {entry.label}
                  </Text>
                  <Text fw={600}>{entry.value.toLocaleString()}</Text>
                </Stack>
              ))}
            </SimpleGrid>
          ) : (
            <Text size="xs" c="dimmed">
              Metrics unavailable for the latest run.
            </Text>
          )}
        </Stack>
      </Card>
    );
  };

  if (isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>
    );
  }

  if (isError || !cohort) {
    return (
      <Stack gap="sm" p="md">
        <Title order={3}>Cohort not found</Title>
        <Text c="dimmed">The requested cohort does not exist in the mock dataset.</Text>
        <Anchor component={Link} to="/cohorts">
          <Group gap={4} wrap="nowrap">
            <IconExternalLink size={14} />
            <Text size="sm">Back to cohorts</Text>
          </Group>
        </Anchor>
      </Stack>
    );
  }

  return (
    <Stack gap="lg" p="md">
      <Stack gap={2}>
        <Title order={2}>{cohort.name}</Title>
        <Text c="dimmed" size="sm">
          Source path: {cohort.source_path}
        </Text>
        <Text size="xs" c="dimmed">
          Last updated {formatDateTime(cohort.updated_at)}
        </Text>
      </Stack>

      <Tabs defaultValue="pipeline" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="pipeline">Pipeline</Tabs.Tab>
          <Tabs.Tab value="keywords">Keywords</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="keywords" pt="md">
          <KeywordSettingsTab cohortId={cohort.id} />
        </Tabs.Panel>

        <Tabs.Panel value="pipeline" pt="md">
          <Stack gap="lg">

      <PipelineStepper
        stages={orderedStages}
        activeStageIndex={resolvedActiveIndex}
        onStageClick={(index) => {
          if (index >= 0 && index < orderedStages.length) {
            setActiveStageIndex(index);
          }
        }}
      />

      <Card withBorder radius="md" padding="md">
        <Group justify="space-between">
          <Stack gap={2}>
            <Text size="sm" c="dimmed">
              Metrics
            </Text>
            <Text fw={600}>
              {cohort.total_subjects} subjects · {cohort.total_sessions} sessions
            </Text>
          </Stack>
          <Anchor component={Link} to="/jobs" size="sm">
            View job history
          </Anchor>
        </Group>
      </Card>

      {activeStage && (
        <StageCard
          stage={activeStage}
          loading={runStageMutation.isPending}
          disabled={
            stageBlocked ||
            (activeStage.id === 'anonymize' && (anonymizeBusy || Boolean(anonymizeConflict)))
          }
          onRun={activeStage.id === 'sort' ? undefined : () => handleRunStage(activeStage.id)}
          blockedReason={blockedReason}
          onPause={
            activeStage.id === 'extract' &&
              extractJob &&
              extractJob.status === 'running' &&
              !jobActionMutation.isPending
              ? handlePauseExtraction
              : undefined
          }
        >
          {activeStage.id === 'anonymize' && (
            <Stack gap="md">
              {renderAnonymizeConflict()}
              {anonymizeBusy && renderAnonymizeExecution()}
              <Collapse in={!anonymizeBusy && !anonymizeConflict && (!showAnonymizeProgress || anonymizeJobStatus === 'completed')}>
                {anonymizeConfig && (
                  <AnonymizeStageForm
                    cohortName={cohort.name}
                    cohortId={cohort.id}
                    config={anonymizeConfig}
                    onChange={handleAnonymizeConfigChange}
                    onRecommendResources={handleRecommendAnonymizeResources}
                    recommendLoading={systemResourcesLoading}
                    recommendation={systemResources}
                  />
                )}
              </Collapse>
              {!anonymizeBusy && showAnonymizeProgress && anonymizeJob && (
                <Card withBorder padding="md" radius="md">
                  <Stack gap="sm">
                    <Group justify="space-between" align="center">
                      <Text fw={600}>Last anonymization summary</Text>
                      <Button size="xs" variant="light" onClick={() => setShowAnonymizeProgress(false)}>
                        Show configuration
                      </Button>
                    </Group>
                    <Text size="sm">Status: {anonymizeJob.status}</Text>
                    <Text size="sm">
                      Started {anonymizeJob.startedAt ? formatDateTime(anonymizeJob.startedAt) : formatDateTime(anonymizeJob.submittedAt)}
                    </Text>
                    {anonymizeJob.finishedAt && (
                      <Text size="sm">Finished {formatDateTime(anonymizeJob.finishedAt)}</Text>
                    )}
                  </Stack>
                </Card>
              )}
              {renderAnonymizeHistory()}
            </Stack>
          )}

          {activeStage.id === 'extract' && cohort && configs.extract && (
            <Stack gap="md">
              <ExtractStageForm
                sourcePath={cohort.source_path}
                config={configs.extract}
                job={extractJob}
                onChange={handleExtractConfigChange}
                onRecommendResources={handleRecommendExtractResources}
                recommendLoading={systemResourcesLoading}
                recommendation={systemResources ?? undefined}
                onPauseJob={extractJob ? handlePauseExtraction : undefined}
                onResumeJob={extractJob ? handleResumeExtraction : undefined}
                onCancelJob={extractJob ? handleCancelExtraction : undefined}
                jobActionPending={jobActionMutation.isPending}
              />
              {!extractBusy && renderExtractSummary()}
            </Stack>
          )}

          {activeStage.id === 'sort' && cohort && (
            <SortingPipelineSimple
              cohortId={cohort.id}
              config={sortingState.config}
              onConfigChange={(config) => setSortingState(prev => ({ ...prev, config }))}
              onRunStep={(stepId) => {
                console.log('[Sort] Running step:', stepId);

                // Invalidate sorting status cache so we get fresh data
                queryClient.invalidateQueries({ queryKey: sortingKeys.status(cohort.id) });

                // Always run individual step (step-wise is the only mode)
                runSortingStepMutation.mutate(
                  { cohortId: cohort.id, stepId, config: sortingState.config },
                  {
                    onSuccess: (result) => {
                      setSortingState(prev => ({
                        ...prev,
                        jobId: result.job_id,
                        streamUrl: result.stream_url,
                      }));
                    },
                    onError: (error) => {
                      notifications.show({
                        color: 'red',
                        title: 'Step Execution Failed',
                        message: (error as Error).message
                      });
                    },
                  }
                );
              }}
              isLoading={runSortingStepMutation.isPending}
              disabled={stageBlocked}
              jobId={sortingState.jobId}
              streamUrl={sortingState.streamUrl}
            />
          )}

          {activeStage.id === 'bids' && activeGenericConfig && (
            <BidsStageForm
              config={activeGenericConfig as BidsConfig}
              onChange={(key, value) => handleGenericConfigChange('bids', key, value)}
              subjectIdentifierOptions={subjectIdentifierOptions}
              intentOptions={intentOptions}
              provenanceOptions={provenanceOptions}
              fieldStrengthOptions={fieldStrengthOptions}
              defaultIntentSelection={defaultIntentSelection}
              defaultProvenanceSelection={defaultProvenanceSelection}
              activeBidsJob={activeBidsJob}
              lastBidsJob={lastBidsJob}
            />
          )}

          {activeStage.id === 'meg_ingest' && activeGenericConfig && cohort && (
            <MegIngestStageForm
              config={activeGenericConfig as unknown as StageConfigById['meg_ingest']}
              onChange={(key, value) =>
                handleGenericConfigChange('meg_ingest', key, value as string | number | boolean | string[] | null)
              }
              cohortSourcePath={cohort.source_path}
              activeJob={activeBidsJob}
              lastJob={lastBidsJob ?? undefined}
            />
          )}

          {activeStage.id === 'meg_scan' && activeGenericConfig && (
            <MegScanStageForm
              config={activeGenericConfig as unknown as StageConfigById['meg_scan']}
              onChange={(key, value) =>
                handleGenericConfigChange('meg_scan', key, value as string | number | boolean | string[] | null)
              }
              subjectIdTypeOptions={megSubjectIdTypeOptions}
              activeJob={activeBidsJob}
              lastJob={lastBidsJob ?? undefined}
            />
          )}

          {activeStage.id === 'meg_bids' && activeGenericConfig && cohort && (
            <MegBidsStageForm
              config={activeGenericConfig as unknown as StageConfigById['meg_bids']}
              onChange={(key, value) =>
                handleGenericConfigChange('meg_bids', key, value as string | number | boolean | string[] | null)
              }
              cohortName={cohort.name}
              activeJob={activeBidsJob}
              lastJob={lastBidsJob ?? undefined}
            />
          )}
        </StageCard>
      )}
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
};
