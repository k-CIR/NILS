/**
 * Facility vault discovery types. Mirrors
 * `backend/src/facility_discovery/models.py` DTOs field-for-field
 * (snake_case, same as `Cohort` -- no camelCase serialization layer for
 * this feature).
 */

export type FacilityId = 'mrc' | 'natmeg';

export type DiscoveryStatus = 'pending' | 'confirmed' | 'rejected';

export interface FacilityDiscovery {
  id: number;
  facility: FacilityId;
  facility_id_value: string;
  session_number?: string | null;
  scan_date?: string | null;
  cir_id: string;
  cir_project: string;
  folder_path: string;
  status: DiscoveryStatus;
  cohort_id?: number | null;
  subject_id?: number | null;
  mapping_row_id?: number | null;
  discovered_at: string;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
}

export interface DiscoveryScanSummary {
  mappings_loaded: number;
  matched_new: number;
  matched_already_pending: number;
  matched_already_confirmed: number;
  matched_already_rejected: number;
  unmatched_folders: number;
}
