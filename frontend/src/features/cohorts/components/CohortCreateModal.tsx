import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Combobox,
  Group,
  Loader,
  Modal,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  TagsInput,
  Text,
  TextInput,
  Textarea,
  useCombobox,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import type { Cohort, CohortModality } from '../../../types/cohort';
import type { SubjectCohortMetadataCohort } from '../../database/api';
import { useCreateCohortMutation, useCohortsQuery } from '../api';
import { useMetadataCohorts, useUpsertMetadataCohort } from '../../database/api';
import { useDirectoryQuery, useDataRootsQuery } from '../../files/api';
import { buildDefaultAnonymizeConfig } from '../../anonymization/defaults';

const resolveDataRoot = () => {
  const envValue = (import.meta.env['VITE_DATA_ROOT'] ?? import.meta.env['VITE_DATA_PATH']) as string | undefined;
  if (!envValue) return '/data';
  const normalized = envValue.trim().replace(/\\/g, '/').replace(/\/+/g, '/');
  if (!normalized) return '/data';
  const trimmed = normalized.endsWith('/') && normalized !== '/' ? normalized.slice(0, -1) : normalized;
  if (trimmed.startsWith('/')) return trimmed || '/data';
  return `/${trimmed}`;
};

const normalizePath = (value: string) => {
  if (!value) return '';
  const replaced = value.replace(/\\/g, '/').replace(/\/+/g, '/').trim();
  if (!replaced) return '';
  if (replaced === '/') return '/';
  return replaced.endsWith('/') ? replaced.slice(0, -1) : replaced;
};

const clampToRoot = (value: string, root: string) => {
  const rootPath = root || '/data';
  const normalizedRoot = normalizePath(rootPath) || '/data';
  if (!value) return normalizedRoot;
  let candidate = normalizePath(value);
  if (!candidate.startsWith('/')) {
    candidate = normalizePath(`${normalizedRoot}/${candidate}`) || normalizedRoot;
  }
  if (normalizedRoot === '/') {
    return candidate || '/';
  }
  return candidate.startsWith(normalizedRoot) ? candidate : normalizedRoot;
};

const buildBreadcrumbs = (path: string, root: string) => {
  const normalizedRoot = root === '' ? '/' : root;
  const breadcrumbs: Array<{ label: string; path: string }> = [];
  const rootLabel = normalizedRoot === '/' ? '/' : normalizedRoot.split('/').filter(Boolean).pop() ?? normalizedRoot;
  breadcrumbs.push({ label: rootLabel, path: normalizedRoot });

  if (path === normalizedRoot) {
    return breadcrumbs;
  }

  const relative = normalizedRoot === '/'
    ? path.replace(/^\/+/, '')
    : path.startsWith(`${normalizedRoot}/`)
      ? path.slice(normalizedRoot.length + 1)
      : '';

  if (!relative) return breadcrumbs;

  const parts = relative.split('/').filter(Boolean);
  let current = normalizedRoot;
  for (const part of parts) {
    current = current === '/' ? `/${part}` : `${current}/${part}`;
    breadcrumbs.push({ label: part, path: current });
  }

  return breadcrumbs;
};

interface CohortCreateModalProps {
  opened: boolean;
  onClose: () => void;
}

interface CombinedCohortOption {
  value: string;
  normalized: string;
  hasMetadata: boolean;
  hasDraft: boolean;
}

export const CohortCreateModal = ({ opened, onClose }: CohortCreateModalProps) => {
  const fallbackRoot = useMemo(() => resolveDataRoot(), []);
  const { data: dataRoots } = useDataRootsQuery();
  const availableRoots = useMemo(() => {
    const roots = dataRoots && dataRoots.length ? dataRoots : [fallbackRoot];
    const normalized = roots.map((root) => normalizePath(root) || '/');
    const deduped = Array.from(new Set(normalized));
    return deduped.length ? deduped : [fallbackRoot];
  }, [dataRoots, fallbackRoot]);

  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
  });

  const { data: cohorts } = useCohortsQuery();
  const { data: metadataCohorts } = useMetadataCohorts();

  const cohortMap = useMemo(() => {
    const map = new Map<string, Cohort>();
    (cohorts ?? []).forEach((cohort) => {
      map.set(cohort.name.trim().toLowerCase(), cohort);
    });
    return map;
  }, [cohorts]);

  const metadataMap = useMemo(() => {
    const map = new Map<string, SubjectCohortMetadataCohort>();
    (metadataCohorts ?? []).forEach((cohort) => {
      map.set(cohort.name.trim().toLowerCase(), cohort);
    });
    return map;
  }, [metadataCohorts]);

  const combinedOptions = useMemo<CombinedCohortOption[]>(() => {
    const optionMap = new Map<string, CombinedCohortOption>();

    (metadataCohorts ?? []).forEach((cohort) => {
      const normalized = cohort.name.trim().toLowerCase();
      optionMap.set(normalized, {
        value: cohort.name,
        normalized,
        hasMetadata: true,
        hasDraft: false,
      });
    });

    (cohorts ?? []).forEach((cohort) => {
      const normalized = cohort.name.trim().toLowerCase();
      const existing = optionMap.get(normalized);
      if (existing) {
        existing.hasDraft = true;
        if (!existing.value) {
          existing.value = cohort.name;
        }
      } else {
        optionMap.set(normalized, {
          value: cohort.name,
          normalized,
          hasMetadata: false,
          hasDraft: true,
        });
      }
    });

    return Array.from(optionMap.values()).sort((a, b) => a.value.localeCompare(b.value));
  }, [metadataCohorts, cohorts]);

  const [name, setName] = useState('');
  const [currentRoot, setCurrentRoot] = useState(() => availableRoots[0] ?? fallbackRoot);
  const [sourcePathState, setSourcePathState] = useState(() => availableRoots[0] ?? fallbackRoot);
  const [description, setDescription] = useState('');
  const [owner, setOwner] = useState('');
  const [modality, setModality] = useState<CohortModality>('imaging');
  const [anonymizationEnabled, setAnonymizationEnabled] = useState(false);
  const [tags, setTags] = useState<string[]>(['demo']);

  useEffect(() => {
    if (!availableRoots.includes(currentRoot)) {
      const nextRoot = availableRoots[0] ?? fallbackRoot;
      setCurrentRoot(nextRoot);
      setSourcePathState(nextRoot);
    }
  }, [availableRoots, currentRoot, fallbackRoot]);

  const normalizedName = useMemo(() => name.trim().toLowerCase(), [name]);
  const selectedMetadata = useMemo(
    () => (normalizedName ? metadataMap.get(normalizedName) ?? null : null),
    [metadataMap, normalizedName],
  );
  const selectedDraft = useMemo(
    () => (normalizedName ? cohortMap.get(normalizedName) ?? null : null),
    [cohortMap, normalizedName],
  );

  const sourcePath = useMemo(() => clampToRoot(sourcePathState, currentRoot), [sourcePathState, currentRoot]);
  const { data: directoryEntries, isFetching: isLoadingDirectories } = useDirectoryQuery(sourcePath);
  const subdirectories = useMemo(
    () => (directoryEntries ?? []).filter((entry) => entry.type === 'directory'),
    [directoryEntries],
  );

  const filteredOptions = useMemo(() => {
    if (!name.trim()) return combinedOptions;
    return combinedOptions.filter((option) => option.normalized.includes(normalizedName));
  }, [combinedOptions, name, normalizedName]);

  const createCohort = useCreateCohortMutation();
  const upsertMetadataCohort = useUpsertMetadataCohort();
  const prefillRef = useRef<string | null>(null);

  const handlePathChange = (next: string) => {
    setSourcePathState(clampToRoot(next, currentRoot));
  };

  const handleRootChange = (newRoot: string | null) => {
    if (newRoot) {
      setCurrentRoot(newRoot);
      setSourcePathState(newRoot);
    }
  };

  const handleSubmit = async () => {
    const normalized = normalizedName;
    if (!normalized) {
      notifications.show({ color: 'red', message: 'Please provide a cohort name.' });
      return;
    }

    const trimmedOwner = owner.trim();
    if (!trimmedOwner) {
      notifications.show({ color: 'red', message: 'Please provide a cohort owner.' });
      return;
    }

    try {
      const sanitizedTags = Array.from(new Set((tags ?? []).map((tag) => tag.trim()).filter(Boolean)));
      const isUpdatingExisting = Boolean(selectedMetadata || selectedDraft);
      const defaultRoot = availableRoots[0] ?? fallbackRoot;
      const anonymize_config = anonymizationEnabled
        ? buildDefaultAnonymizeConfig({ cohortName: normalized, sourcePath })
        : undefined;
      const metadataDescription = description.trim();

      await upsertMetadataCohort.mutateAsync({
        name: normalized,
        owner: trimmedOwner,
        path: sourcePath,
        description: metadataDescription ? metadataDescription : null,
        isActive: selectedMetadata?.isActive ?? true,
      });

      await createCohort.mutateAsync({
        name: normalized,
        description,
        source_path: sourcePath,
        anonymization_enabled: anonymizationEnabled,
        modality,
        tags: sanitizedTags,
        anonymize_config,
      });
      notifications.show({
        color: 'teal',
        message: isUpdatingExisting
          ? 'Cohort metadata and draft updated successfully.'
          : 'Cohort metadata and draft created successfully.',
      });
      setName('');
      setDescription('');
      setOwner('');
      setTags(['demo']);
      setModality('imaging');
      setAnonymizationEnabled(false);
      setCurrentRoot(defaultRoot);
      setSourcePathState(defaultRoot);
      prefillRef.current = null;
      combobox.resetSelectedOption();
      combobox.closeDropdown();
      onClose();
    } catch (error) {
      notifications.show({ color: 'red', message: (error as Error).message });
    }
  };

  useEffect(() => {
    const metadataKey = selectedMetadata ? `metadata:${selectedMetadata.cohortId}` : 'metadata:none';
    const draftKey = selectedDraft ? `draft:${selectedDraft.id}` : 'draft:none';
    const compositeKey = `${metadataKey}|${draftKey}`;

    if (selectedMetadata || selectedDraft) {
      if (prefillRef.current === compositeKey) {
        return;
      }
      prefillRef.current = compositeKey;

      if (selectedMetadata) {
        const metadataOwner = selectedMetadata.owner ?? '';
        setOwner((current) => (current === metadataOwner ? current : metadataOwner));

        const metadataPath = selectedMetadata.path ?? '';
        if (metadataPath) {
          const normalized = normalizePath(metadataPath);
          if (normalized) {
            const matchedRoot = availableRoots.find((root) => normalized.startsWith(root));
            if (matchedRoot) {
              setCurrentRoot((current) => (current === matchedRoot ? current : matchedRoot));
            }
            setSourcePathState((current) => (current === normalized ? current : normalized));
          }
        }

        const metadataDescription = selectedMetadata.description ?? '';
        setDescription((current) => (current === metadataDescription ? current : metadataDescription));
      }

      if (selectedDraft) {
        const draftTags = selectedDraft.tags && selectedDraft.tags.length ? [...selectedDraft.tags] : ['demo'];
        setTags(draftTags);
        setAnonymizationEnabled(Boolean(selectedDraft.anonymization_enabled));
        setModality(selectedDraft.modality ?? 'imaging');

        if (!selectedMetadata || !(selectedMetadata.path && selectedMetadata.path.trim())) {
          const draftPath = selectedDraft.source_path;
          if (draftPath) {
            const normalized = normalizePath(draftPath);
            if (normalized) {
              const matchedRoot = availableRoots.find((root) => normalized.startsWith(root));
              if (matchedRoot) {
                setCurrentRoot((current) => (current === matchedRoot ? current : matchedRoot));
              }
              setSourcePathState((current) => (current === normalized ? current : normalized));
            }
          }
        }

        if (!selectedMetadata || !(selectedMetadata.description && selectedMetadata.description.trim())) {
          const draftDescription = selectedDraft.description ?? '';
          if (draftDescription) {
            setDescription((current) => (current === draftDescription ? current : draftDescription));
          }
        }
      } else if (!selectedMetadata) {
        setTags(['demo']);
        setAnonymizationEnabled(false);
        setModality('imaging');
      }
    } else if (prefillRef.current !== null) {
      prefillRef.current = null;
      const defaultRoot = availableRoots[0] ?? fallbackRoot;
      setOwner('');
      setDescription('');
      setTags(['demo']);
      setAnonymizationEnabled(false);
      setModality('imaging');
      setCurrentRoot(defaultRoot);
      setSourcePathState(defaultRoot);
    }
  }, [selectedMetadata, selectedDraft, availableRoots, fallbackRoot]);

  useEffect(() => {
    if (opened) {
      const firstRoot = availableRoots[0];
      setName('');
      setDescription('');
       setOwner('');
      setTags(['demo']);
      setCurrentRoot(firstRoot);
      setSourcePathState(firstRoot);
      setAnonymizationEnabled(false);
      setModality('imaging');
      prefillRef.current = null;
    }
  }, [opened, availableRoots]);

  return (
    <Modal opened={opened} onClose={onClose} title="Create new cohort" size="lg">
      <Stack>
        <Combobox
          store={combobox}
          withinPortal={false}
          onOptionSubmit={(value) => {
            setName(value);
            combobox.closeDropdown();
          }}
        >
          <Combobox.Target>
            <TextInput
              label="Cohort name"
              placeholder="stopms"
              value={name}
              onChange={(event) => {
                const next = event.currentTarget.value;
                setName(next);
                if (!combobox.dropdownOpened) {
                  combobox.openDropdown();
                }
              }}
              onFocus={() => combobox.openDropdown()}
              onBlur={() => combobox.closeDropdown()}
              autoComplete="off"
              required
            />
          </Combobox.Target>
          <Combobox.Dropdown>
            <Combobox.Options>
              {filteredOptions.length ? (
                filteredOptions.map((option) => (
                  <Combobox.Option key={option.normalized} value={option.value}>
                    <Group justify="space-between" gap="xs" align="center">
                      <Text>{option.value}</Text>
                      <Group gap={4}>
                        {option.hasMetadata ? (
                          <Badge color="teal" size="xs" variant="light">
                            Metadata
                          </Badge>
                        ) : null}
                        {option.hasDraft ? (
                          <Badge color="blue" size="xs" variant="light">
                            Draft
                          </Badge>
                        ) : null}
                      </Group>
                    </Group>
                  </Combobox.Option>
                ))
              ) : (
                <Combobox.Empty>No existing cohorts match "{name.trim()}"</Combobox.Empty>
              )}
            </Combobox.Options>
          </Combobox.Dropdown>
        </Combobox>
        <Stack gap={4}>
          <Group gap="xs" wrap="wrap">
            <Text size="xs" c="dimmed">
              Stored as: {normalizedName || '—'}
            </Text>
            {selectedMetadata ? (
              <Badge color="teal" variant="light">
                Metadata cohort
              </Badge>
            ) : null}
            {selectedDraft ? (
              <Badge color="blue" variant="light">
                Pipeline draft
              </Badge>
            ) : null}
          </Group>
          {selectedMetadata ? (
            <Text size="xs" c="dimmed">
              Metadata cohort will be updated when saved.
            </Text>
          ) : null}
          {selectedDraft ? (
            <Text size="xs" c="dimmed">
              Pipeline draft will be updated when saved.
            </Text>
          ) : null}
        </Stack>

        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Data modality
          </Text>
          <SegmentedControl
            value={modality}
            onChange={(value) => {
              const next = value as CohortModality;
              setModality(next);
              if (next === 'meg') {
                setAnonymizationEnabled(false);
              }
            }}
            disabled={Boolean(selectedDraft)}
            data={[
              { label: 'Imaging (DICOM)', value: 'imaging' },
              { label: 'MEG', value: 'meg' },
            ]}
          />
          <Text size="xs" c="dimmed">
            {selectedDraft
              ? 'Modality is fixed once a cohort pipeline has been created.'
              : 'Imaging cohorts run anonymize/extract/sort/BIDS; MEG cohorts run a separate ingest/scan/BIDS pipeline.'}
          </Text>
        </Stack>

        <TextInput
          label="Owner"
          placeholder="Clinical operations"
          value={owner}
          onChange={(event) => setOwner(event.currentTarget.value)}
          required
        />
        
        {availableRoots.length > 1 && (
          <Select
            label="Data root"
            description="Select which data directory to browse"
            value={currentRoot}
            onChange={handleRootChange}
            data={availableRoots.map((root) => {
              const parts = root.split('/').filter(Boolean);
              const label = parts.length > 2 ? `.../${parts.slice(-2).join('/')}` : root;
              return { value: root, label };
            })}
          />
        )}
        
        <TextInput
          label="Source path"
          placeholder={`${currentRoot}/raw-dicoms`}
          value={sourcePath}
          onChange={(event) => handlePathChange(event.currentTarget.value)}
        />
        <Paper withBorder radius="md" p="sm">
          <Stack gap="xs">
            <Group gap={6} wrap="wrap">
              {buildBreadcrumbs(sourcePath, currentRoot).map((crumb, index, list) => (
                <Button
                  key={crumb.path}
                  size="xs"
                  variant={index === list.length - 1 ? 'light' : 'subtle'}
                  onClick={() => handlePathChange(crumb.path)}
                >
                  {crumb.label}
                </Button>
              ))}
            </Group>
            {isLoadingDirectories ? (
              <Group justify="center" py="sm">
                <Loader size="sm" />
              </Group>
            ) : subdirectories.length > 0 ? (
              <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="xs">
                {subdirectories.map((entry) => (
                  <Button key={entry.path} variant="outline" onClick={() => handlePathChange(entry.path)}>
                    {entry.name}
                  </Button>
                ))}
              </SimpleGrid>
            ) : (
              <Text size="sm" c="dimmed">
                No subfolders detected in this location.
              </Text>
            )}
          </Stack>
        </Paper>
        <Textarea
          label="Description"
          placeholder="Optional notes"
          minRows={2}
          value={description}
          onChange={(event) => setDescription(event.currentTarget.value)}
        />
        <TagsInput label="Tags" data={tags} value={tags} onChange={setTags} placeholder="Add pipeline tags" />
        {modality === 'imaging' && (
          <Switch
            label="Add pseudo-anonymization stage"
            description="Include PHI scrubbing before metadata extraction."
            checked={anonymizationEnabled}
            onChange={(event) => setAnonymizationEnabled(event.currentTarget.checked)}
          />
        )}
        <Group justify="flex-end">
          <Button
            onClick={handleSubmit}
            loading={createCohort.isPending || upsertMetadataCohort.isPending}
          >
            Create draft cohort
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
};
