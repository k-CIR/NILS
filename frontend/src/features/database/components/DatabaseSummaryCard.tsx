import { Box, Card, Group, Loader, Paper, SimpleGrid, Stack, Text, ThemeIcon, Tooltip } from '@mantine/core';
import {
  IconUsers,
  IconFolder,
  IconId,
  IconLink,
  IconCalendar,
  IconTag,
  IconHeart,
  IconNotes,
  IconActivity,
  IconHash,
  IconFileText,
  IconCheck,
  IconCode,
  IconSearch,
  IconPhoto,
  IconStack,
  IconBox,
  IconAlertTriangle,
  IconHistory,
  IconBriefcase,
  IconPlayerPlay,
  IconShield,
  IconFile,
  IconArrowsRandom,
  IconChartBar,
  IconBrain,
} from '@tabler/icons-react';
import type { DatabaseSummary } from '../../../types';

interface DatabaseSummaryCardProps {
  summary: DatabaseSummary | undefined;
  isLoading?: boolean;
}

// Icon and color mapping for different table types
const TABLE_CONFIG: Record<string, { icon: React.ElementType; color: string; label: string }> = {
  // Metadata DB - Subjects & Cohorts
  subjects: { icon: IconUsers, color: 'blue', label: 'Subjects' },
  cohorts: { icon: IconFolder, color: 'cyan', label: 'Cohorts' },
  subject_cohorts: { icon: IconLink, color: 'teal', label: 'Subject Cohorts' },
  id_types: { icon: IconTag, color: 'indigo', label: 'ID Types' },
  subject_other_identifiers: { icon: IconId, color: 'violet', label: 'Other IDs' },
  
  // Metadata DB - Events & Diseases
  observation_types: { icon: IconTag, color: 'orange', label: 'Observation Types' },
  events: { icon: IconCalendar, color: 'yellow', label: 'Events' },
  diseases: { icon: IconHeart, color: 'red', label: 'Diseases' },
  disease_types: { icon: IconNotes, color: 'pink', label: 'Disease Types' },
  subject_diseases: { icon: IconActivity, color: 'grape', label: 'Subject Diseases' },
  subject_disease_types: { icon: IconNotes, color: 'grape', label: 'Subject Disease Types' },
  
  // Metadata DB - Clinical Measures
  numeric_measures: { icon: IconHash, color: 'green', label: 'Numeric Measures' },
  text_measures: { icon: IconFileText, color: 'lime', label: 'Text Measures' },
  boolean_measures: { icon: IconCheck, color: 'cyan', label: 'Boolean Measures' },
  json_measures: { icon: IconCode, color: 'indigo', label: 'JSON Measures' },
  
  // Metadata DB - Imaging
  studies: { icon: IconSearch, color: 'blue', label: 'Studies' },
  series: { icon: IconPhoto, color: 'violet', label: 'Series' },
  series_stacks: { icon: IconStack, color: 'grape', label: 'Stacks' },
  mri_series_details: { icon: IconBox, color: 'indigo', label: 'MRI Details' },
  ct_series_details: { icon: IconBox, color: 'cyan', label: 'CT Details' },
  pet_series_details: { icon: IconBox, color: 'teal', label: 'PET Details' },
  series_classification_cache: { icon: IconTag, color: 'orange', label: 'Classifications' },
  instances: { icon: IconPhoto, color: 'blue', label: 'Instances' },

  // Metadata DB - MEG
  meg_acquisitions: { icon: IconBrain, color: 'indigo', label: 'MEG Acquisitions' },
  meg_channels: { icon: IconActivity, color: 'violet', label: 'MEG Channels' },

  // Metadata DB - System
  ingest_conflicts: { icon: IconAlertTriangle, color: 'yellow', label: 'Conflicts' },
  schema_versions: { icon: IconHistory, color: 'gray', label: 'Schema Versions' },
  
  // Application DB
  jobs: { icon: IconBriefcase, color: 'blue', label: 'Jobs' },
  job_runs: { icon: IconPlayerPlay, color: 'green', label: 'Job Runs' },
  anonymize_study_audits: { icon: IconShield, color: 'violet', label: 'Anonymize Audits' },
  anonymize_leaf_summaries: { icon: IconFile, color: 'grape', label: 'Anonymize Summaries' },
  sorting_step_handovers: { icon: IconArrowsRandom, color: 'orange', label: 'Sorting Handovers' },
  sorting_step_metrics: { icon: IconChartBar, color: 'teal', label: 'Sorting Metrics' },
};

const getTableConfig = (key: string) => {
  const config = TABLE_CONFIG[key];
  if (config) return config;
  
  // Default fallback
  return {
    icon: IconFileText,
    color: 'gray',
    label: key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
  };
};

const formatCount = (value: number): string => {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toLocaleString();
};

interface StatCardProps {
  label: string;
  value: number;
  icon: React.ElementType;
  color: string;
}

const StatCard = ({ label, value, icon: Icon, color }: StatCardProps) => {
  const hasData = value > 0;
  
  return (
    <Tooltip label={`${value.toLocaleString()} ${label.toLowerCase()}`} position="top" withArrow>
      <Paper
        withBorder
        p="xs"
        radius="md"
        style={{
          opacity: hasData ? 1 : 0.5,
          cursor: 'default',
        }}
      >
        <Group gap="xs" wrap="nowrap">
          <ThemeIcon
            size="lg"
            radius="md"
            variant={hasData ? 'light' : 'subtle'}
            color={hasData ? color : 'gray'}
          >
            <Icon size={18} />
          </ThemeIcon>
          <Box style={{ minWidth: 0, flex: 1 }}>
            <Text size="xs" c="dimmed" truncate>
              {label}
            </Text>
            <Text fw={700} size="sm" truncate>
              {formatCount(value)}
            </Text>
          </Box>
        </Group>
      </Paper>
    </Tooltip>
  );
};

export const DatabaseSummaryCard = ({ summary, isLoading }: DatabaseSummaryCardProps) => {
  if (isLoading) {
    return (
      <Card withBorder radius="md" padding="lg">
        <Group justify="center" py="xl">
          <Loader size="sm" />
          <Text size="sm" c="dimmed">Loading database statistics...</Text>
        </Group>
      </Card>
    );
  }

  if (!summary) {
    return (
      <Card withBorder radius="md" padding="lg">
        <Text size="sm" c="dimmed" ta="center" py="md">
          Database statistics unavailable.
        </Text>
      </Card>
    );
  }

  const tableEntries = Object.entries(summary.tables);
  const totalRecords = tableEntries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <Card withBorder radius="md" padding="lg">
      <Stack gap="md">
        <Group justify="space-between" align="center">
          <Group gap="xs">
            <Text fw={600} size="lg">Database Overview</Text>
            <Text size="sm" c="dimmed">
              — {formatCount(totalRecords)} total records
            </Text>
          </Group>
        </Group>
        
        <SimpleGrid cols={{ base: 2, xs: 3, sm: 4, md: 5, lg: 6 }} spacing="sm">
          {tableEntries.map(([key, value]) => {
            const config = getTableConfig(key);
            return (
              <StatCard
                key={key}
                label={config.label}
                value={value}
                icon={config.icon}
                color={config.color}
              />
            );
          })}
        </SimpleGrid>
      </Stack>
    </Card>
  );
};

export default DatabaseSummaryCard;
