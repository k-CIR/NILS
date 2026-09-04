import {
  ActionIcon,
  AppShell,
  Box,
  Burger,
  Divider,
  Group,
  NavLink,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconSettings,
  IconStack2,
  IconPlayerPlay,
  IconDatabase,
  IconActivity,
  IconShieldCheck,
  IconFileExport,
  IconBinaryTree2,
  IconFolderSearch,
} from '@tabler/icons-react';
import { useMemo } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useJobsQuery } from '../../features/jobs/api';

const navigationItems = [
  {
    label: 'Facility Discovery',
    icon: IconFolderSearch,
    to: '/facility-discovery',
    description: 'Vault subject review',
  },
  { label: 'Cohorts', icon: IconStack2, to: '/cohorts', description: 'Manage datasets' },
  { label: 'QC Pipeline', icon: IconShieldCheck, to: '/qc', description: 'Quality control' },
  { label: 'Export', icon: IconFileExport, to: '/export', description: 'Subset export' },
  { label: 'Pipelines', icon: IconBinaryTree2, to: '/pipelines', description: 'Analysis pipelines' },
  { label: 'Database', icon: IconDatabase, to: '/database', description: 'Data management' },
];

export const AppLayout = () => {
  const [opened, { toggle, close }] = useDisclosure();
  const location = useLocation();
  const { data: jobs } = useJobsQuery();

  const runningJobs = useMemo(() => jobs?.filter((job) => job.status === 'running') ?? [], [jobs]);
  const queuedJobs = useMemo(() => jobs?.filter((job) => job.status === 'queued') ?? [], [jobs]);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
      styles={{
        header: {
          backgroundColor: 'var(--nils-bg-secondary)',
          borderBottom: '1px solid var(--nils-border-subtle)',
        },
        navbar: {
          backgroundColor: 'var(--nils-bg-secondary)',
          borderRight: '1px solid var(--nils-border-subtle)',
        },
        main: {
          backgroundColor: 'var(--nils-bg-primary)',
        },
      }}
    >
      <AppShell.Header>
        <Group justify="space-between" h="100%" px="md">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Link to="/cohorts" style={{ textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Box
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 8,
                  backgroundColor: 'var(--nils-accent-primary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text fw={800} c="white" style={{ fontSize: 22, lineHeight: 1 }}>
                  N
                </Text>
              </Box>
              <Stack gap={0}>
                <Text fw={700} size="md" c="var(--nils-text-primary)" style={{ letterSpacing: '-0.02em', lineHeight: 1.2 }}>
                  NILS
                </Text>
                <Text size="xs" c="var(--nils-text-tertiary)" style={{ lineHeight: 1 }}>
                  Neuroimaging Intelligent Linked System
                </Text>
              </Stack>
            </Link>
          </Group>

          <Group gap="xs">
            {(runningJobs.length > 0 || queuedJobs.length > 0) && (
              <Tooltip label={`${runningJobs.length} running, ${queuedJobs.length} queued`}>
                <Box
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '4px 10px',
                    borderRadius: '6px',
                    backgroundColor: 'var(--nils-bg-tertiary)',
                  }}
                >
                  <Box
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      backgroundColor: runningJobs.length > 0 ? 'var(--nils-stage-running)' : 'var(--nils-stage-pending)',
                      animation: runningJobs.length > 0 ? 'pulse 2s infinite' : 'none',
                    }}
                  />
                  <Text size="xs" fw={500} c="var(--nils-text-secondary)">
                    {runningJobs.length + queuedJobs.length}
                  </Text>
                </Box>
              </Tooltip>
            )}
            <Tooltip label="System activity">
              <ActionIcon variant="subtle" size="lg" color="gray">
                <IconActivity size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section grow component={ScrollArea}>
          <Stack gap="xs">
            <Text size="xs" fw={600} c="var(--nils-text-tertiary)" tt="uppercase" px="sm" pt="xs">
              Pipeline
            </Text>
            {navigationItems.map((item) => {
              const active = location.pathname === item.to || location.pathname.startsWith(`${item.to}/`);
              return (
                <NavLink
                  key={item.to}
                  component={Link}
                  to={item.to}
                  label={
                    <Text size="sm" fw={active ? 600 : 500}>
                      {item.label}
                    </Text>
                  }
                  description={
                    <Text size="xs" c="var(--nils-text-tertiary)">
                      {item.description}
                    </Text>
                  }
                  leftSection={<item.icon size={18} stroke={1.5} />}
                  active={active}
                  onClick={close}
                  styles={{
                    root: {
                      borderRadius: 'var(--nils-radius-md)',
                      backgroundColor: active ? 'var(--nils-bg-tertiary)' : 'transparent',
                      borderLeft: active ? '2px solid var(--nils-accent-primary)' : '2px solid transparent',
                      '&:hover': {
                        backgroundColor: 'var(--nils-bg-tertiary)',
                      },
                    },
                  }}
                />
              );
            })}
          </Stack>
        </AppShell.Section>

        <AppShell.Section>
          <Stack gap="sm">
            <Divider color="var(--nils-border-subtle)" />
            <NavLink
              component={Link}
              to="/jobs"
              label={
                <Text size="sm" fw={location.pathname === '/jobs' || location.pathname.startsWith('/jobs/') ? 600 : 500}>
                  Jobs
                </Text>
              }
              description={
                <Text size="xs" c="var(--nils-text-tertiary)">
                  Pipeline queue
                </Text>
              }
              leftSection={<IconPlayerPlay size={18} stroke={1.5} />}
              active={location.pathname === '/jobs' || location.pathname.startsWith('/jobs/')}
              onClick={close}
              styles={{
                root: {
                  borderRadius: 'var(--nils-radius-md)',
                  backgroundColor:
                    location.pathname === '/jobs' || location.pathname.startsWith('/jobs/')
                      ? 'var(--nils-bg-tertiary)'
                      : 'transparent',
                  borderLeft:
                    location.pathname === '/jobs' || location.pathname.startsWith('/jobs/')
                      ? '2px solid var(--nils-accent-primary)'
                      : '2px solid transparent',
                  '&:hover': {
                    backgroundColor: 'var(--nils-bg-tertiary)',
                  },
                },
              }}
            />
            <NavLink
              component={Link}
              to="/settings"
              label={
                <Text size="sm" fw={location.pathname === '/settings' ? 600 : 500}>
                  Settings
                </Text>
              }
              leftSection={<IconSettings size={18} stroke={1.5} />}
              active={location.pathname === '/settings' || location.pathname.startsWith('/settings/')}
              onClick={close}
              styles={{
                root: {
                  borderRadius: 'var(--nils-radius-md)',
                  backgroundColor: location.pathname === '/settings' ? 'var(--nils-bg-tertiary)' : 'transparent',
                  '&:hover': {
                    backgroundColor: 'var(--nils-bg-tertiary)',
                  },
                },
              }}
            />
            
            {/* Quick Stats */}
            <Box
              p="sm"
              style={{
                backgroundColor: 'var(--nils-bg-tertiary)',
                borderRadius: 'var(--nils-radius-md)',
              }}
            >
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="xs" c="var(--nils-text-tertiary)">
                    Active Jobs
                  </Text>
                  <Text size="sm" fw={600} c={runningJobs.length > 0 ? 'var(--nils-accent-primary)' : 'var(--nils-text-secondary)'}>
                    {runningJobs.length}
                  </Text>
                </Group>
                <Group justify="space-between">
                  <Text size="xs" c="var(--nils-text-tertiary)">
                    Queued
                  </Text>
                  <Text size="sm" fw={600} c="var(--nils-text-secondary)">
                    {queuedJobs.length}
                  </Text>
                </Group>
              </Stack>
            </Box>
          </Stack>
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        <Box component="main" mx="auto" maw={1400} w="100%">
          <Outlet />
        </Box>
      </AppShell.Main>
    </AppShell>
  );
};
