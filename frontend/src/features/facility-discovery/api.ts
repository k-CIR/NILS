import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { apiClient } from '../../utils/api-client';
import { QUERY_KEYS } from '../../constants/api';
import type { DiscoveryScanSummary, DiscoveryStatus, FacilityDiscovery, FacilityId } from '../../types';

const QUERY_KEY = QUERY_KEYS.facilityDiscoveries;

export interface DiscoveryFilters {
  status?: DiscoveryStatus;
  facility?: FacilityId;
}

const buildQuery = (filters: DiscoveryFilters): string => {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.facility) params.set('facility', filters.facility);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
};

export const useFacilityDiscoveriesQuery = (filters: DiscoveryFilters = {}) =>
  useQuery<FacilityDiscovery[]>({
    queryKey: [...QUERY_KEY, filters],
    queryFn: () => apiClient.get<FacilityDiscovery[]>(`/facility-discovery${buildQuery(filters)}`),
  });

export const useRunDiscoveryScan = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => apiClient.post<DiscoveryScanSummary>('/facility-discovery/scan'),
    onSuccess: (summary) => {
      notifications.show({
        title: 'Discovery scan complete',
        message: `${summary.matched_new} new, ${summary.matched_already_pending} still pending, ` +
          `${summary.unmatched_folders} unmatched folder(s) (loaded ${summary.mappings_loaded} mapping rows)`,
        color: 'green',
      });
    },
    onError: (error: Error) => {
      notifications.show({ title: 'Scan failed', message: error.message, color: 'red' });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
};

export const useConfirmDiscovery = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (discoveryId: number) =>
      apiClient.post<FacilityDiscovery>(`/facility-discovery/${discoveryId}/confirm`),
    onSuccess: () => {
      notifications.show({ title: 'Discovery confirmed', message: 'Project/cohort/subject linked', color: 'green' });
    },
    onError: (error: Error) => {
      notifications.show({ title: 'Confirm failed', message: error.message, color: 'red' });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
};

export const useRejectDiscovery = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (discoveryId: number) =>
      apiClient.post<FacilityDiscovery>(`/facility-discovery/${discoveryId}/reject`),
    onSuccess: () => {
      notifications.show({ title: 'Discovery rejected', message: '', color: 'gray' });
    },
    onError: (error: Error) => {
      notifications.show({ title: 'Reject failed', message: error.message, color: 'red' });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
  });
};
