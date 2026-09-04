/**
 * Facility vault discovery review page.
 *
 * Lists `facility_discoveries` (pending by default, with facility/status
 * filters), a "Run discovery scan" button surfacing the scan summary
 * counts, and per-row Confirm/Reject actions. No changes to existing
 * cohort/extraction/MEG stage UI -- this is a fully additive new section.
 */

import {
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconCheck, IconRefresh, IconX } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import {
  useConfirmDiscovery,
  useFacilityDiscoveriesQuery,
  useRejectDiscovery,
  useRunDiscoveryScan,
} from '../api';
import type { DiscoveryStatus, FacilityId } from '../../../types';
import { formatDateTime } from '../../../utils/formatters';

const STATUS_COLORS: Record<DiscoveryStatus, string> = {
  pending: 'yellow',
  confirmed: 'green',
  rejected: 'gray',
};

export const FacilityDiscoveryPage = () => {
  const [status, setStatus] = useState<DiscoveryStatus | null>('pending');
  const [facility, setFacility] = useState<FacilityId | null>(null);

  const filters = useMemo(
    () => ({
      status: status ?? undefined,
      facility: facility ?? undefined,
    }),
    [status, facility],
  );

  const { data: discoveries, isLoading } = useFacilityDiscoveriesQuery(filters);
  const runScan = useRunDiscoveryScan();
  const confirmDiscovery = useConfirmDiscovery();
  const rejectDiscovery = useRejectDiscovery();

  return (
    <Stack gap="lg" p="md">
      <Group justify="space-between" align="flex-start">
        <Stack gap={4}>
          <Title order={2} fw={600} c="var(--nils-text-primary)">
            Facility Discovery
          </Title>
          <Text size="sm" c="var(--nils-text-secondary)">
            Subjects/sessions auto-detected on disk for the <code>mrc</code>/<code>natmeg</code>{' '}
            facilities, staged here for review before any project/cohort/subject is created.
          </Text>
        </Stack>
        <Button
          leftSection={<IconRefresh size={16} />}
          loading={runScan.isPending}
          onClick={() => runScan.mutate()}
        >
          Run discovery scan
        </Button>
      </Group>

      <Group gap="sm">
        <Select
          placeholder="Status"
          clearable
          data={['pending', 'confirmed', 'rejected']}
          value={status}
          onChange={(value) => setStatus(value as DiscoveryStatus | null)}
          w={160}
        />
        <Select
          placeholder="Facility"
          clearable
          data={['mrc', 'natmeg']}
          value={facility}
          onChange={(value) => setFacility(value as FacilityId | null)}
          w={160}
        />
      </Group>

      {isLoading && (
        <Box py="xl" style={{ display: 'flex', justifyContent: 'center' }}>
          <Loader size="md" color="var(--nils-accent-primary)" />
        </Box>
      )}

      {!isLoading && (!discoveries || discoveries.length === 0) && (
        <Box
          py="xl"
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '200px',
            backgroundColor: 'var(--nils-bg-secondary)',
            borderRadius: 'var(--nils-radius-lg)',
            border: '1px solid var(--nils-border-subtle)',
          }}
        >
          <Text fw={600} size="md" c="var(--nils-text-primary)" mb={4}>
            No discoveries
          </Text>
          <Text size="sm" c="var(--nils-text-secondary)">
            Run a discovery scan to detect facility subjects/sessions already on disk.
          </Text>
        </Box>
      )}

      {!isLoading && discoveries && discoveries.length > 0 && (
        <Table striped highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Facility</Table.Th>
              <Table.Th>cir_id</Table.Th>
              <Table.Th>cir_project</Table.Th>
              <Table.Th>Facility ID</Table.Th>
              <Table.Th>Session</Table.Th>
              <Table.Th>Scan date</Table.Th>
              <Table.Th>Folder</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Discovered</Table.Th>
              <Table.Th>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {discoveries.map((row) => (
              <Table.Tr key={row.id}>
                <Table.Td>
                  <Badge variant="light">{row.facility}</Badge>
                </Table.Td>
                <Table.Td>{row.cir_id}</Table.Td>
                <Table.Td>{row.cir_project}</Table.Td>
                <Table.Td>{row.facility_id_value}</Table.Td>
                <Table.Td>{row.session_number ?? '-'}</Table.Td>
                <Table.Td>{row.scan_date ?? '-'}</Table.Td>
                <Table.Td>
                  <Tooltip label={row.folder_path}>
                    <Text size="xs" c="var(--nils-text-secondary)" style={{ maxWidth: 220 }} truncate>
                      {row.folder_path}
                    </Text>
                  </Tooltip>
                </Table.Td>
                <Table.Td>
                  <Badge color={STATUS_COLORS[row.status]}>{row.status}</Badge>
                  {row.status === 'confirmed' && row.cohort_id != null && (
                    <Text size="xs" c="var(--nils-text-tertiary)">
                      cohort #{row.cohort_id}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="xs" c="var(--nils-text-secondary)">
                    {formatDateTime(row.discovered_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {row.status === 'pending' && (
                    <Group gap={4}>
                      <Button
                        size="xs"
                        color="green"
                        variant="light"
                        leftSection={<IconCheck size={14} />}
                        loading={confirmDiscovery.isPending}
                        onClick={() => confirmDiscovery.mutate(row.id)}
                      >
                        Confirm
                      </Button>
                      <Button
                        size="xs"
                        color="red"
                        variant="light"
                        leftSection={<IconX size={14} />}
                        loading={rejectDiscovery.isPending}
                        onClick={() => rejectDiscovery.mutate(row.id)}
                      >
                        Reject
                      </Button>
                    </Group>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
};
