/**
 * CohortCard component - displays a cohort summary in the list view.
 *
 * Performance optimized:
 * - Wrapped in React.memo to prevent re-renders when other cohorts change
 * - Prefetches cohort data on hover for faster navigation
 */
import { memo, useCallback, useMemo } from 'react';
import { Badge, Box, Card, Group, Stack, Text, Tooltip } from '@mantine/core';
import { IconShieldCheck } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import type { Cohort, StageStatus, StageSummary, StageId } from '../../../types';
import { STAGE_ORDER } from '../../../types';
import { STAGE_STATUS_CONFIG } from '../../../constants/status';
import { usePrefetchCohort } from '../api';
import { formatDateTime } from '../../../utils/formatters';

interface CohortCardProps {
  cohort: Cohort;
}

// Note: MEG cohorts never populate STAGE_ORDER-derived stages (see below),
// so the meg_* entries here exist only to satisfy Record<StageId, string>
// exhaustiveness and are otherwise unused today.
const stageAbbreviations: Record<StageId, string> = {
  anonymize: 'A',
  extract: 'E',
  sort: 'S',
  bids: 'B',
  meg_ingest: 'MI',
  meg_scan: 'MS',
  meg_bids: 'MB',
};

const stageLabels: Record<StageId, string> = {
  anonymize: 'Anonymize',
  extract: 'Extract',
  sort: 'Sort',
  bids: 'BIDS',
  meg_ingest: 'MEG Ingest',
  meg_scan: 'MEG Scan',
  meg_bids: 'MEG BIDS',
};

interface PipelineAnalysis {
  stages: Array<{
    id: StageId;
    status: StageStatus;
    label: string;
    abbrev: string;
  }>;
  currentStageText: string;
  hasRunningJob: boolean;
}

function analyzePipeline(cohort: Cohort): PipelineAnalysis {
  const stageMap = new Map<StageId, StageSummary>();
  cohort.stages.forEach(stage => stageMap.set(stage.id, stage));
  
  // Filter stages based on whether anonymization is enabled
  const relevantStages = cohort.anonymization_enabled 
    ? STAGE_ORDER 
    : STAGE_ORDER.filter(id => id !== 'anonymize');
  
  const stages = relevantStages.map(id => {
    const stage = stageMap.get(id);
    return {
      id,
      status: stage?.status ?? 'idle',
      label: stageLabels[id],
      abbrev: stageAbbreviations[id],
    };
  });
  
  // Determine current stage text
  let currentStageText = 'Idle';
  let hasRunningJob = false;
  
  const runningStage = stages.find(s => s.status === 'running');
  if (runningStage) {
    currentStageText = `${runningStage.label}...`;
    hasRunningJob = true;
  } else {
    const failedStage = stages.find(s => s.status === 'failed');
    if (failedStage) {
      currentStageText = `${failedStage.label} failed`;
    } else {
      const allCompleted = stages.every(s => s.status === 'completed');
      if (allCompleted) {
        currentStageText = 'Complete';
      } else {
        // Find first non-completed stage
        const nextStage = stages.find(s => s.status !== 'completed');
        if (nextStage) {
          if (nextStage.status === 'idle') {
            currentStageText = `Ready for ${nextStage.label}`;
          } else if (nextStage.status === 'pending') {
            currentStageText = `${nextStage.label} pending`;
          } else if (nextStage.status === 'paused') {
            currentStageText = `${nextStage.label} paused`;
          } else {
            currentStageText = nextStage.label;
          }
        }
      }
    }
  }
  
  return { stages, currentStageText, hasRunningJob };
}

const CohortCardInner = ({ cohort }: CohortCardProps) => {
  const pipeline = useMemo(() => analyzePipeline(cohort), [cohort]);
  const prefetchCohort = usePrefetchCohort();

  // Prefetch cohort data when user hovers over the card
  const handleMouseEnter = useCallback(() => {
    prefetchCohort(cohort.id);
  }, [cohort.id, prefetchCohort]);

  return (
    <Card
      component={Link}
      to={`/cohorts/${cohort.id}`}
      padding="lg"
      onMouseEnter={handleMouseEnter}
      style={{
        backgroundColor: 'var(--nils-bg-secondary)',
        border: '1px solid var(--nils-border-subtle)',
        borderRadius: 'var(--nils-radius-lg)',
        textDecoration: 'none',
        transition: 'all 150ms ease',
        cursor: 'pointer',
      }}
      styles={{
        root: {
          '&:hover': {
            borderColor: 'var(--nils-border)',
            transform: 'translateY(-2px)',
          },
        },
      }}
    >
      <Stack gap="sm">
        {/* Header */}
        <Group justify="space-between" align="flex-start">
          <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
            <Text fw={600} size="md" c="var(--nils-text-primary)" truncate>
              {cohort.name}
            </Text>
            <Text size="xs" c="var(--nils-text-tertiary)" lineClamp={1}>
              {cohort.description || cohort.source_path}
            </Text>
          </Stack>
          {cohort.anonymization_enabled && (
            <Tooltip label="PHI Protected">
              <Box
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: 'var(--nils-radius-sm)',
                  backgroundColor: 'rgba(63, 185, 80, 0.15)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <IconShieldCheck size={12} color="var(--nils-success)" />
              </Box>
            </Tooltip>
          )}
        </Group>

        {/* Pipeline Progress */}
        <Stack gap={6}>
          <Group justify="space-between" align="center">
            <Group gap={4}>
              {pipeline.stages.map((stage) => {
                const stageStatus = STAGE_STATUS_CONFIG[stage.status];
                
                // Determine background and text colors based on status
                let bgColor = 'var(--nils-bg-tertiary)';
                let textColor = 'var(--nils-text-tertiary)';
                
                if (stage.status === 'completed') {
                  bgColor = 'rgba(63, 185, 80, 0.2)';
                  textColor = 'var(--nils-success)';
                } else if (stage.status === 'running') {
                  bgColor = 'rgba(88, 166, 255, 0.2)';
                  textColor = 'var(--nils-accent-primary)';
                } else if (stage.status === 'failed') {
                  bgColor = 'rgba(248, 81, 73, 0.2)';
                  textColor = 'var(--nils-error)';
                } else if (stage.status === 'pending') {
                  bgColor = 'rgba(163, 113, 247, 0.2)';
                  textColor = 'var(--nils-stage-pending)';
                } else if (stage.status === 'paused') {
                  bgColor = 'rgba(210, 153, 34, 0.2)';
                  textColor = 'var(--nils-stage-paused)';
                }
                
                return (
                  <Tooltip key={stage.id} label={`${stage.label}: ${stageStatus.label}`}>
                    <Box
                      style={{
                        width: 18,
                        height: 18,
                        borderRadius: 'var(--nils-radius-sm)',
                        backgroundColor: bgColor,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        position: 'relative',
                      }}
                    >
                      <Text 
                        size="xs" 
                        fw={600} 
                        c={textColor}
                        style={{ 
                          fontSize: '9px',
                          lineHeight: 1,
                        }}
                      >
                        {stage.abbrev}
                      </Text>
                      {stage.status === 'running' && (
                        <Box
                          style={{
                            position: 'absolute',
                            inset: 0,
                            borderRadius: 'var(--nils-radius-sm)',
                            border: '1px solid var(--nils-accent-primary)',
                            animation: 'pulse 2s infinite',
                          }}
                        />
                      )}
                    </Box>
                  </Tooltip>
                );
              })}
            </Group>
            <Text 
              size="xs" 
              fw={500} 
              c={pipeline.hasRunningJob 
                ? 'var(--nils-accent-primary)' 
                : pipeline.currentStageText === 'Complete'
                  ? 'var(--nils-success)'
                  : pipeline.currentStageText.includes('failed')
                    ? 'var(--nils-error)'
                    : 'var(--nils-text-secondary)'}
            >
              {pipeline.currentStageText}
            </Text>
          </Group>
        </Stack>

        {/* Stats Row */}
        <Group gap="md">
          <Stack gap={0}>
            <Text size="xs" c="var(--nils-text-tertiary)">Subjects</Text>
            <Text size="sm" fw={600} c="var(--nils-text-primary)">
              {cohort.total_subjects?.toLocaleString() ?? '—'}
            </Text>
          </Stack>
          <Stack gap={0}>
            <Text size="xs" c="var(--nils-text-tertiary)">Sessions</Text>
            <Text size="sm" fw={600} c="var(--nils-text-primary)">
              {cohort.total_sessions?.toLocaleString() ?? '—'}
            </Text>
          </Stack>
        </Group>

        {/* Tags */}
        {cohort.tags.length > 0 && (
          <Group gap={6}>
            {cohort.tags.slice(0, 3).map((tag) => (
              <Badge
                key={tag}
                variant="light"
                size="xs"
                color="gray"
                styles={{
                  root: {
                    backgroundColor: 'var(--nils-bg-tertiary)',
                    color: 'var(--nils-text-secondary)',
                  },
                }}
              >
                {tag}
              </Badge>
            ))}
            {cohort.tags.length > 3 && (
              <Text size="xs" c="var(--nils-text-tertiary)">
                +{cohort.tags.length - 3}
              </Text>
            )}
          </Group>
        )}

        {/* Footer */}
        <Group justify="flex-end" pt={4} style={{ borderTop: '1px solid var(--nils-border-subtle)' }}>
          <Text size="xs" c="var(--nils-text-tertiary)">
            {formatDateTime(cohort.updated_at)}
          </Text>
        </Group>
      </Stack>
    </Card>
  );
};

// Memoize to prevent re-renders when other cohorts in the list change
export const CohortCard = memo(CohortCardInner);
CohortCard.displayName = 'CohortCard';
