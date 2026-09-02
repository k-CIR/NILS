import { Badge, Button, Card, Group, Progress, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';
import type { JobSummary } from '../../../types';
import { JOB_STATUS_CONFIG } from '../../../constants/status';
import { formatDateTime } from '../../../utils/formatters';

interface JobProgressCardProps {
  job: JobSummary;
  title: string;
  /** Show a "View all jobs" link (only meaningful while a job is active). */
  showJobsLink?: boolean;
}

/**
 * Generic job progress/summary card shared by stage forms that run a single
 * synchronous backend job per stage (e.g. bids, meg_ingest, meg_scan,
 * meg_bids). Mirrors the bespoke `renderBidsJobCard` pattern from
 * `BidsStageForm`, extracted so new stage forms don't duplicate it.
 */
export const JobProgressCard = ({ job, title, showJobsLink }: JobProgressCardProps) => {
  const statusConfig = JOB_STATUS_CONFIG[job.status];
  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap="xs">
        <Group justify="space-between" align="center">
          <Text fw={600}>{title}</Text>
          <Badge color={statusConfig?.mantineColor ?? 'gray'}>{job.status.toUpperCase()}</Badge>
        </Group>
        <Progress value={job.progress ?? 0} size="lg" radius="md" transitionDuration={200} />
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            Started {job.startedAt ? formatDateTime(job.startedAt) : formatDateTime(job.submittedAt)}
          </Text>
          <Text size="xs" c="dimmed">
            Job #{job.id}
          </Text>
        </Group>
        {job.finishedAt && (
          <Text size="xs" c="dimmed">
            Finished {formatDateTime(job.finishedAt)}
          </Text>
        )}
        {job.errorMessage && (
          <Text size="xs" c="red">
            Error: {job.errorMessage}
          </Text>
        )}
        {showJobsLink && (
          <Group justify="flex-end">
            <Button size="xs" variant="light" component={Link} to="/jobs">
              View all jobs
            </Button>
          </Group>
        )}
      </Stack>
    </Card>
  );
};
