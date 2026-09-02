/**
 * MetadataTableExplorer - DataTables-based table explorer for metadata database.
 *
 * Performance optimized: Table buttons use useCallback for stable onClick handlers.
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Tabs,
  Text,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMetadataTables, type MetadataTableInfo } from '../api';
import SubjectImportPanel from './SubjectImportPanel';
import ObservationTypeImportTab from './ObservationTypeImportTab';
import EventImportTab from './EventImportTab';
import DiseaseImportTab from './DiseaseImportTab';
import DiseaseTypeImportTab from './DiseaseTypeImportTab';
import SubjectDiseaseImportTab from './SubjectDiseaseImportTab';
import SubjectDiseaseTypeImportTab from './SubjectDiseaseTypeImportTab';
import 'datatables.net-dt/css/dataTables.dataTables.css';
import 'datatables.net-fixedcolumns-dt/css/fixedColumns.dataTables.css';

type DataTablesOrderDirection = 'asc' | 'desc';

interface DataTablesColumn {
  data: string;
  name: string;
  searchable?: boolean;
  orderable?: boolean;
  title?: string;
}

interface DataTablesRequestPayload {
  draw?: number;
  start?: number;
  length?: number;
  columns?: DataTablesColumn[];
  search?: { value: string; regex: boolean };
  order?: Array<{ column: number; dir: DataTablesOrderDirection }>;
}

interface DataTablesResponsePayload {
  draw: number;
  recordsTotal: number;
  recordsFiltered: number;
  data: unknown[];
}

interface DataTablesOptions {
  ajax?: (request: DataTablesRequestPayload, callback: (data: DataTablesResponsePayload) => void) => void | Promise<void>;
  columns?: DataTablesColumn[];
  pageLength?: number;
  lengthMenu?: number[][];
  fixedColumns?: { left?: number };
  dom?: string;
  autoWidth?: boolean;
  [key: string]: unknown;
}

interface DataTablesInstance {
  destroy: () => void;
  table: () => { container: () => HTMLElement };
}

type DataTablesCtor = new (node: HTMLTableElement, options: DataTablesOptions) => DataTablesInstance;

const formatNumber = (value: number) => value.toLocaleString();

// Memoized table button component to prevent unnecessary re-renders
interface TableButtonProps {
  table: MetadataTableInfo;
  isActive: boolean;
  onSelect: (table: MetadataTableInfo) => void;
}

const TableButton = memo(({ table, isActive, onSelect }: TableButtonProps) => {
  const hasData = table.row_count > 0;
  return (
    <Button
      variant={isActive ? 'filled' : 'light'}
      size="compact-sm"
      onClick={() => onSelect(table)}
      styles={{
        root: {
          opacity: hasData ? 1 : 0.5,
        },
      }}
    >
      <Group gap={4} align="center" wrap="nowrap">
        <Text size="sm">{table.label}</Text>
        <Badge
          size="xs"
          color={hasData ? (isActive ? 'white' : 'gray') : 'dark'}
          variant={isActive ? 'light' : 'outline'}
        >
          {formatNumber(table.row_count)}
        </Badge>
      </Group>
    </Button>
  );
});
TableButton.displayName = 'TableButton';

export const MetadataTableExplorer = () => {
  const tablesQuery = useMetadataTables();
  const [selectedTable, setSelectedTable] = useState<MetadataTableInfo | null>(null);
  const tableHostRef = useRef<HTMLDivElement | null>(null);
  const tableElementRef = useRef<HTMLTableElement | null>(null);
  const dataTableRef = useRef<DataTablesInstance | null>(null);
  const dataTableCtorRef = useRef<DataTablesCtor | null>(null);
  const activeRequestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (tablesQuery.isLoading || tablesQuery.isError) {
      return;
    }
    if (!tablesQuery.data || tablesQuery.data.length === 0) {
      setSelectedTable(null);
      return;
    }
    setSelectedTable((current) => {
      if (current) {
        const updated = tablesQuery.data?.find((table) => table.name === current.name);
        if (updated) {
          return updated;
        }
      }
      return tablesQuery.data[0];
    });
  }, [tablesQuery.data, tablesQuery.isError, tablesQuery.isLoading]);

  useEffect(() => {
    return () => {
      if (activeRequestRef.current) {
        activeRequestRef.current.abort();
        activeRequestRef.current = null;
      }
      if (dataTableRef.current) {
        dataTableRef.current.destroy();
        dataTableRef.current = null;
      }
      if (tableElementRef.current && tableElementRef.current.parentNode) {
        tableElementRef.current.parentNode.removeChild(tableElementRef.current);
        tableElementRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    let disposed = false;

    const cleanup = () => {
      if (activeRequestRef.current) {
        activeRequestRef.current.abort();
        activeRequestRef.current = null;
      }
      if (dataTableRef.current) {
        dataTableRef.current.destroy();
        dataTableRef.current = null;
      }
      if (tableElementRef.current && tableElementRef.current.parentNode) {
        tableElementRef.current.parentNode.removeChild(tableElementRef.current);
        tableElementRef.current = null;
      }
    };

    if (!selectedTable) {
      cleanup();
      return () => {
        disposed = true;
        cleanup();
      };
    }

    const columns = selectedTable.columns.map((column) => ({
      data: column.name,
      name: column.name,
      title: column.label,
    }));

    const ajax = async (requestData: DataTablesRequestPayload, callback: (data: DataTablesResponsePayload) => void) => {
      if (activeRequestRef.current) {
        activeRequestRef.current.abort();
      }
      const controller = new AbortController();
      activeRequestRef.current = controller;

      const payload: DataTablesRequestPayload = {
        ...requestData,
        columns: requestData?.columns ?? columns.map((column) => ({
          data: column.data,
          name: column.name,
          searchable: true,
          orderable: true,
        })),
      };

      try {
        const response = await fetch(`/api/metadata/tables/${selectedTable.name}/query`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        if (!response.ok) {
          const message = await response.text();
          throw new Error(message || `Failed to load ${selectedTable.label}`);
        }

        const json: DataTablesResponsePayload = await response.json();
        callback(json);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        const message = error instanceof Error ? error.message : 'Failed to load table data';
        notifications.show({ color: 'red', message });
        callback({
          draw: requestData?.draw ?? 0,
          recordsTotal: 0,
          recordsFiltered: 0,
          data: [],
        });
      } finally {
        if (activeRequestRef.current === controller) {
          activeRequestRef.current = null;
        }
      }
    };

    const initialize = async () => {
      try {
        if (!dataTableCtorRef.current) {
          const module = await import('datatables.net-dt');
          dataTableCtorRef.current = module.default;
          await import('datatables.net-fixedcolumns');
        }

        if (disposed || !tableHostRef.current) {
          return;
        }

        cleanup();

        const table = document.createElement('table');
        table.className = 'display compact nowrap stripe hover row-border order-column';
        table.style.width = '100%';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        for (const column of selectedTable.columns) {
          const th = document.createElement('th');
          th.textContent = column.label;
          headerRow.appendChild(th);
        }
        thead.appendChild(headerRow);
        table.appendChild(thead);

        tableHostRef.current.innerHTML = '';
        tableHostRef.current.appendChild(table);
        tableElementRef.current = table;

        const instance = new dataTableCtorRef.current(table, {
          legacy: false,
          serverSide: true,
          processing: true,
          deferRender: true,
          scrollX: true,
          scrollCollapse: true,
          autoWidth: false,
          layout: {
            topStart: 'pageLength',
            topEnd: 'search',
            bottomStart: 'info',
            bottomEnd: 'paging',
          },
          pageLength: 10,
          lengthMenu: [
            [10, 25, 50, 100, 250],
            [10, 25, 50, 100, 250],
          ],
          fixedColumns: {
            left: 1,
          },
          columnDefs: [
            {
              targets: '_all',
              className: 'dt-body-nowrap',
            },
          ],
          ajax,
          columns,
          order: [],
        });

        dataTableRef.current = instance;
      } catch (error) {
        if (disposed) {
          return;
        }
        console.error('Failed to initialize metadata DataTable', error);
        notifications.show({ color: 'red', message: 'Failed to render metadata table.' });
      }
    };

    void initialize();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [selectedTable]);

  // Memoized table selection handler to prevent re-creating callbacks
  const handleTableSelect = useCallback((table: MetadataTableInfo) => {
    setSelectedTable(table);
  }, []);

  // Memoized render function for table buttons
  const renderTableButtons = useCallback(
    (tableNames: string[]) => {
      if (!tablesQuery.data) return null;
      const tables = tablesQuery.data.filter((t) => tableNames.includes(t.name));
      return (
        <Group gap="xs" wrap="wrap">
          {tables.map((table) => (
            <TableButton
              key={table.name}
              table={table}
              isActive={selectedTable?.name === table.name}
              onSelect={handleTableSelect}
            />
          ))}
        </Group>
      );
    },
    [tablesQuery.data, selectedTable?.name, handleTableSelect]
  );

  return (
    <Card withBorder radius="md" padding="lg">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start">
          <Stack gap={4}>
            <Text fw={600}>Metadata tables</Text>
            <Text size="sm" c="dimmed">
              Explore metadata records with server-side pagination and compact table view.
            </Text>
          </Stack>
          {tablesQuery.isLoading && <Loader size="sm" />}
        </Group>

        {tablesQuery.isError ? (
          <Text size="sm" c="red">
            {(tablesQuery.error as Error)?.message ?? 'Failed to load metadata tables.'}
          </Text>
        ) : tablesQuery.data && tablesQuery.data.length ? (
          <Stack gap="md">
            <Tabs defaultValue="subjects">
              <Tabs.List>
                <Tabs.Tab value="subjects">Subjects & Cohorts</Tabs.Tab>
                <Tabs.Tab value="events">Events & Clinical</Tabs.Tab>
                <Tabs.Tab value="imaging">Imaging</Tabs.Tab>
                <Tabs.Tab value="meg">MEG</Tabs.Tab>
                <Tabs.Tab value="system">System</Tabs.Tab>
              </Tabs.List>

              <Tabs.Panel value="subjects" pt="md">
                <Stack gap="md">
                  {renderTableButtons(['subject', 'subject_other_identifiers', 'cohort', 'subject_cohorts', 'id_types'])}
                  {selectedTable && <SubjectImportPanel tableName={selectedTable.name} />}
                </Stack>
              </Tabs.Panel>

              <Tabs.Panel value="events" pt="md">
                <Stack gap="md">
                  {renderTableButtons(['observation_types', 'event', 'diseases', 'disease_types', 'subject_diseases', 'subject_disease_types', 'numeric_measures', 'text_measures', 'boolean_measures', 'json_measures'])}
                  {selectedTable?.name === 'observation_types' && <ObservationTypeImportTab />}
                  {selectedTable?.name === 'event' && <EventImportTab />}
                  {selectedTable?.name === 'diseases' && <DiseaseImportTab />}
                  {selectedTable?.name === 'disease_types' && <DiseaseTypeImportTab />}
                  {selectedTable?.name === 'subject_diseases' && <SubjectDiseaseImportTab />}
                  {selectedTable?.name === 'subject_disease_types' && <SubjectDiseaseTypeImportTab />}
                </Stack>
              </Tabs.Panel>

              <Tabs.Panel value="imaging" pt="md">
                {renderTableButtons(['study', 'series', 'series_stack', 'stack_fingerprint', 'mri_series_details', 'ct_series_details', 'pet_series_details', 'series_classification_cache', 'instance'])}
              </Tabs.Panel>

              <Tabs.Panel value="meg" pt="md">
                {renderTableButtons(['meg_acquisition', 'meg_channel'])}
              </Tabs.Panel>

              <Tabs.Panel value="system" pt="md">
                {renderTableButtons(['ingest_conflicts', 'schema_version'])}
              </Tabs.Panel>
            </Tabs>

            {selectedTable ? (
              <Stack gap="sm">
                <Text size="xs" c="dimmed">
                  Showing data for <strong>{selectedTable.label}</strong> ({formatNumber(selectedTable.row_count)} rows).
                </Text>
                <ScrollArea>
                  <div key={selectedTable.name} ref={tableHostRef} className="metadata-table-container" />
                </ScrollArea>
              </Stack>
            ) : (
              <Text size="sm" c="dimmed">
                Select a table to view its contents.
              </Text>
            )}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            No metadata tables available.
          </Text>
        )}
      </Stack>
    </Card>
  );
};

export default MetadataTableExplorer;
