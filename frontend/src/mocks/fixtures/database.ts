import type {
  AnonymizeStageConfig,
  ExtractStageConfig,
  Cohort,
  Job,
  StageId,
  StageRun,
  StageStatus,
  StageSummary,
} from '../../types';
import { STAGE_LABELS, STAGE_ORDER } from '../../types';
import { buildDefaultAnonymizeConfig } from '../../features/anonymization/defaults';
import { buildDefaultExtractConfig } from '../../features/extraction/defaults';
import { buildNonAnonymizeStageDefaults } from '../../features/stages/defaults';

const stageDescriptions: Record<StageId, string> = {
  anonymize: 'Remove PHI from DICOM headers with configurable strategies.',
  extract: 'Parse DICOM metadata into the staging catalog for downstream sorting.',
  sort: 'Label, group, and QC imaging sequences using curated heuristics.',
  bids: 'Organize series into BIDS-compliant DICOM or NIfTI outputs.',
  // MEG cohorts are not covered by these mock/dev fixtures yet (STAGE_ORDER
  // above stays imaging-only); entries below only satisfy Record<StageId>
  // exhaustiveness.
  meg_ingest: 'Discover and stage raw FIF files into the cohort workspace.',
  meg_scan: 'Read FIF headers, resolve subject identity, and build the BIDS conversion table.',
  meg_bids: 'Convert staged FIF recordings into a BIDS dataset via mne-bids.',
};

const nowIso = () => new Date().toISOString();

const randomId = (prefix: string) => `${prefix}-${Math.random().toString(36).slice(2, 9)}`;

const createRun = (stageId: StageId, status: StageStatus, overrides?: Partial<StageRun>): StageRun => ({
  id: randomId('run'),
  stageId,
  startedAt: nowIso(),
  status,
  progress: status === 'completed' ? 100 : 0,
  configSnapshot: {},
  ...overrides,
});

const createStage = (stageId: StageId, status: StageStatus, progress: number, overrides?: Partial<StageSummary>): StageSummary => ({
  id: stageId,
  title: STAGE_LABELS[stageId],
  description: stageDescriptions[stageId],
  status,
  progress,
  lastRunAt: progress > 0 ? nowIso() : undefined,
  runs: [],
  config: undefined,
  ...overrides,
});

let nextCohortId = 1;

const initialCohorts: Cohort[] = [
  {
    id: nextCohortId++,
    name: 'STOPMS',
    description: 'Longitudinal MS cohort with head and spine imaging.',
    source_path: '/data/stopms',
    created_at: nowIso(),
    updated_at: nowIso(),
    anonymization_enabled: true,
    tags: ['ms', 'longitudinal'],
    status: 'running',
    total_subjects: 12,
    total_sessions: 42,
    total_series: 528,
    completion_percentage: 55,
    stages: [
      createStage('anonymize', 'completed', 100, {
        lastRunAt: nowIso(),
        runs: [
          createRun('anonymize', 'completed', {
            startedAt: nowIso(),
            finishedAt: nowIso(),
            progress: 100,
            configSnapshot: { keepDates: false },
          }),
        ],
        config: buildDefaultAnonymizeConfig({ cohortName: 'STOPMS', sourcePath: '/data/stopms' }),
      }),
      createStage('extract', 'running', 60, {
        runs: [createRun('extract', 'running', { progress: 60 })],
        jobId: '1',
        config: buildDefaultExtractConfig(),
      }),
      createStage('sort', 'pending', 5, {
        config: {
          profile: 'standard',
          applyLLMAssist: true,
          allowManualOverrides: true,
        },
      }),
      createStage('bids', 'blocked', 0, {
        config: {
          outputModes: ['dcm'],
          outputMode: 'dcm',
          layout: 'bids',
          overwriteMode: 'skip',
          includeIntents: [],
          includeProvenance: [],
          excludeProvenance: ['ProjectionDerived'],
          groupSyMRI: true,
          copyWorkers: 8,
          convertWorkers: 8,
          bidsDcmRootName: 'bids-dcm',
          bidsNiftiRootName: 'bids-nifti',
          flatDcmRootName: 'dcm-flat',
          flatNiftiRootName: 'nii-flat',
        },
      }),
    ],
  },
  {
    id: nextCohortId++,
    name: 'GENEURO Prospective',
    description: 'Prospective study focusing on gene therapies.',
    source_path: '/data/geneuro',
    created_at: nowIso(),
    updated_at: nowIso(),
    anonymization_enabled: false,
    tags: ['gene-therapy', 'prospective'],
    status: 'pending',
    total_subjects: 6,
    total_sessions: 18,
    total_series: 210,
    completion_percentage: 20,
    stages: [
      createStage('extract', 'pending', 5, {
        config: buildDefaultExtractConfig(),
      }),
      createStage('sort', 'blocked', 0, {
        config: {
          profile: 'standard',
          applyLLMAssist: true,
          allowManualOverrides: true,
        },
      }),
      createStage('bids', 'blocked', 0, {
        config: {
          outputModes: ['dcm'],
          outputMode: 'dcm',
          layout: 'bids',
          overwriteMode: 'skip',
          includeIntents: [],
          includeProvenance: [],
          excludeProvenance: ['ProjectionDerived'],
          groupSyMRI: true,
          copyWorkers: 8,
          convertWorkers: 8,
          bidsDcmRootName: 'bids-dcm',
          bidsNiftiRootName: 'bids-nifti',
          flatDcmRootName: 'dcm-flat',
          flatNiftiRootName: 'nii-flat',
        },
      }),
    ],
  },
];

let nextJobId = 1;

const jobs: Job[] = [
  {
    id: nextJobId++,
    cohortId: initialCohorts[0]?.id ?? null,
    cohortName: initialCohorts[0]?.name ?? null,
    stageId: 'extract',
    status: 'running',
    progress: 60,
    submittedAt: nowIso(),
    startedAt: nowIso(),
    config: {},
  },
];

if (initialCohorts[0]) {
  initialCohorts[0].stages[1] = {
    ...initialCohorts[0].stages[1],
    jobId: String(jobs[0].id),
  } as StageSummary;
}

const activeIntervals = new Map<number, number>();

const updateCohortStatus = (cohort: Cohort) => {
  if (cohort.stages.every((stage) => stage.status === 'completed')) {
    cohort.status = 'completed';
    cohort.completion_percentage = 100;
    return;
  }

  if (cohort.stages.some((stage) => stage.status === 'failed')) {
    cohort.status = 'failed';
    return;
  }

  if (cohort.stages.some((stage) => stage.status === 'running')) {
    cohort.status = 'running';
    cohort.completion_percentage = Math.round(
      cohort.stages.reduce<number>((acc, stage) => acc + stage.progress, 0) /
        Math.max(cohort.stages.length, 1),
    );
    return;
  }

  cohort.status = 'pending';
};

const refreshStageDependencies = (cohort: Cohort) => {
  const stageEntries: Array<[StageId, StageSummary]> = cohort.stages.map((stage) => [stage.id, stage]);
  const stageMap = new Map<StageId, StageSummary>(stageEntries);

  let previousCompleted = true;
  STAGE_ORDER.forEach((stageId: StageId) => {
    const stage = stageMap.get(stageId);
    if (!stage) return;

    if (!previousCompleted && stage.status !== 'completed') {
      stage.status = 'blocked';
    } else if (previousCompleted && stage.status === 'blocked') {
      stage.status = 'pending';
    }

    previousCompleted = previousCompleted && stage.status === 'completed';
  });
};

const finishJob = (job: Job, stage: StageSummary) => {
  stage.progress = 100;
  stage.status = 'completed';
  stage.lastRunAt = nowIso();
  stage.jobId = undefined;
  const finishedAt = nowIso();
  const lastIndex = stage.runs.length - 1;
  if (lastIndex >= 0 && stage.runs[lastIndex].status === 'running') {
    stage.runs[lastIndex] = {
      ...stage.runs[lastIndex],
      status: 'completed',
      finishedAt,
      progress: 100,
    };
  } else {
    stage.runs = [
      ...stage.runs,
      createRun(stage.id, 'completed', {
        startedAt: job.startedAt ?? nowIso(),
        finishedAt,
        progress: 100,
      }),
    ];
  }

  job.status = 'completed';
  job.progress = 100;
  job.finishedAt = finishedAt;
  activeIntervals.delete(job.id);

  const cohort = findCohort(job.cohortId);
  if (cohort) {
    refreshStageDependencies(cohort);
    updateCohortStatus(cohort);
  }
};

const tickJob = (job: Job, stage: StageSummary) => {
  stage.progress = Math.min(stage.progress + 15, 100);
  job.progress = stage.progress;

  if (stage.progress >= 100) {
    finishJob(job, stage);
  } else {
    const cohort = findCohort(job.cohortId);
    if (cohort) {
      updateCohortStatus(cohort);
    }
  }
};

const startInterval = (job: Job, stage: StageSummary) => {
  const timerId = globalThis.setInterval(() => tickJob(job, stage), 1200) as unknown as number;
  activeIntervals.set(job.id, timerId);
};

export const db = {
  cohorts: initialCohorts,
  jobs,
};

export const findCohort = (cohortId: number | null | undefined) =>
  db.cohorts.find((cohort) => cohort.id === cohortId);

export const runStage = (cohortId: number, stageId: StageId, config: Record<string, unknown>) => {
  const cohort = findCohort(cohortId);
  if (!cohort) throw new Error('Cohort not found');

  const stage = cohort.stages.find((entry) => entry.id === stageId);
  if (!stage) throw new Error('Stage not found');

  if (stage.status === 'running') {
    return stage;
  }

  if (stageId === 'anonymize') {
    stage.config = {
      ...(stage.config as AnonymizeStageConfig | undefined),
      ...(config as Partial<AnonymizeStageConfig>),
    } as AnonymizeStageConfig;
  } else if (stageId === 'extract') {
    stage.config = {
      ...(stage.config as ExtractStageConfig | undefined),
      ...(config as Partial<ExtractStageConfig>),
    } as ExtractStageConfig;
  } else {
    stage.config = {
      ...(stage.config as Record<string, unknown> | undefined),
      ...config,
    };
  }

  stage.status = 'running';
  stage.progress = Math.max(stage.progress, 5);
  const jobId = nextJobId++;
  stage.jobId = String(jobId);

  const job: Job = {
    id: jobId,
    cohortId,
    cohortName: cohort.name,
    stageId,
    status: 'running',
    progress: stage.progress,
    submittedAt: nowIso(),
    startedAt: nowIso(),
    config: { ...config },
  };

  stage.runs = [
    ...stage.runs,
    createRun(stageId, 'running', {
      startedAt: job.startedAt ?? nowIso(),
      progress: stage.progress,
      configSnapshot: { ...config },
    }),
  ];

  db.jobs = [job, ...db.jobs];
  startInterval(job, stage);
  refreshStageDependencies(cohort);
  updateCohortStatus(cohort);
  return stage;
};

export const createCohort = (payload: Partial<Cohort> & { anonymizeConfig?: AnonymizeStageConfig }): Cohort => {
  const cohortId = nextCohortId++;
  const now = nowIso();

  const includeAnonymize = payload.anonymization_enabled ?? false;
  const baseDefaults = buildNonAnonymizeStageDefaults();
  const computedAnonymizeConfig = includeAnonymize
    ? payload.anonymizeConfig ?? buildDefaultAnonymizeConfig({
        cohortName: payload.name ?? 'New cohort',
        sourcePath: payload.source_path ?? '/data',
      })
    : undefined;

  const stages: StageSummary[] = [];
  STAGE_ORDER.forEach((stageId: StageId) => {
    if (stageId === 'anonymize') {
      if (!includeAnonymize || !computedAnonymizeConfig) {
        return;
      }
      stages.push(
        createStage(stageId, 'pending', 5, {
          config: computedAnonymizeConfig,
        }),
      );
      return;
    }

    const isFirstStage = stages.length === 0;
    const status: StageStatus = isFirstStage ? 'pending' : 'blocked';
    const progress = status === 'pending' ? 5 : 0;
    const defaultConfig = baseDefaults[stageId as keyof typeof baseDefaults];
    stages.push(
      createStage(stageId, status, progress, {
        config: defaultConfig ? { ...defaultConfig } : {},
      }),
    );
  });

  const cohort: Cohort = {
    id: cohortId,
    name: payload.name ?? 'New cohort',
    description: payload.description,
    source_path: payload.source_path ?? '/data',
    created_at: now,
    updated_at: now,
    anonymization_enabled: includeAnonymize,
    tags: payload.tags ?? [],
    status: 'pending',
    total_subjects: 0,
    total_sessions: 0,
    total_series: 0,
    completion_percentage: stages.length > 0 ? stages[0].progress : 0,
    stages,
  };

  refreshStageDependencies(cohort);
  updateCohortStatus(cohort);
  db.cohorts = [cohort, ...db.cohorts];
  return cohort;
};

export const updateJobState = (jobId: number, action: 'pause' | 'resume' | 'cancel' | 'retry') => {
  const job = db.jobs.find((entry) => entry.id === jobId);
  if (!job) throw new Error('Job not found');

  const cohort = findCohort(job.cohortId);
  if (!cohort) throw new Error('Cohort not found');

  const stage = cohort.stages.find((entry) => entry.id === job.stageId);
  if (!stage) throw new Error('Stage not found');

  switch (action) {
    case 'pause': {
      const intervalId = activeIntervals.get(job.id);
      if (intervalId) {
        clearInterval(intervalId);
        activeIntervals.delete(job.id);
      }
      job.status = 'paused';
      stage.status = 'paused';
      break;
    }
    case 'resume': {
      if (job.status === 'paused') {
        job.status = 'running';
        stage.status = 'running';
        startInterval(job, stage);
      }
      break;
    }
    case 'cancel': {
      const intervalId = activeIntervals.get(job.id);
      if (intervalId) {
        clearInterval(intervalId);
        activeIntervals.delete(job.id);
      }
      job.status = 'failed';
      job.errorMessage = 'Cancelled by user';
      job.finishedAt = nowIso();
      stage.status = 'failed';
      stage.jobId = undefined;
      break;
    }
    case 'retry': {
      const intervalId = activeIntervals.get(job.id);
      if (intervalId) {
        clearInterval(intervalId);
        activeIntervals.delete(job.id);
      }
      stage.status = 'pending';
      stage.progress = 0;
      stage.jobId = undefined;
      job.status = 'completed';
      job.progress = 100;
      job.finishedAt = nowIso();
      const newStage = runStage(cohort.id, stage.id, {});
      const newJobId = newStage.jobId ? Number(newStage.jobId) : undefined;
      return db.jobs.find((entry) => entry.id === newJobId) ?? job;
    }
    default:
      break;
  }

  refreshStageDependencies(cohort);
  updateCohortStatus(cohort);
  return job;
};
