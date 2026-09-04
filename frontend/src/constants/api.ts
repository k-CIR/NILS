/**
 * Shared constants for API configuration.
 */

/** Refetch intervals in milliseconds for different data types
 * NOTE: These are fallback intervals - SSE provides real-time updates for active jobs.
 * Increased from original values to reduce server load during long operations.
 */
export const REFETCH_INTERVALS = {
  /** Fast polling for active jobs (3s) - was 2s, SSE provides real-time updates */
  fast: 3000,
  /** Normal polling for cohorts (5s) - was 3s */
  normal: 5000,
  /** Slow polling for backups (10s) */
  slow: 10000,
  /** Very slow polling for summaries (15s) */
  verySlow: 15000,
} as const;

/** Stale time in milliseconds for cached data */
export const STALE_TIMES = {
  /** Short stale time for frequently changing data (30s) */
  short: 30 * 1000,
  /** Medium stale time for moderately changing data (1min) */
  medium: 60 * 1000,
  /** Long stale time for rarely changing data (5min) */
  long: 5 * 60 * 1000,
} as const;

/** Query keys for React Query */
export const QUERY_KEYS = {
  // Cohorts
  cohorts: ['cohorts'] as const,
  cohort: (id: string) => ['cohorts', id] as const,
  
  // Jobs
  jobs: ['jobs'] as const,
  
  // Database
  databaseBackups: ['database-backups'] as const,
  databaseSummary: ['database-summary'] as const,
  
  // Metadata
  metadataTables: ['metadata-tables'] as const,
  metadataCohorts: ['metadata-cohorts'] as const,
  idTypes: ['id-types'] as const,
  
  // Application
  applicationTables: ['application-tables'] as const,
  
  // Import fields
  subjectImportFields: ['subject-import-fields'] as const,
  cohortImportFields: ['cohort-import-fields'] as const,
  subjectCohortImportFields: ['subject-cohort-import-fields'] as const,
  subjectIdentifierFields: ['subject-identifier-import-fields'] as const,
  observationTypeImportFields: ['observation-type-import-fields'] as const,
  eventImportFields: ['event-import-fields'] as const,
  diseaseImportFields: ['disease-import-fields'] as const,
  diseaseTypeFields: ['disease-type-fields'] as const,
  subjectDiseaseFields: ['subject-disease-fields'] as const,
  sdtFields: ['sdt-fields'] as const,
  
  // Details
  subjectDetail: (code: string) => ['subject-detail', code] as const,
  cohortDetail: (name: string) => ['cohort-detail', name] as const,
  subjectCohortMemberships: (code: string) => ['subject-cohort-memberships', code] as const,
  subjectIdentifiers: (code: string) => ['subject-identifiers', code] as const,
  observationTypeDetail: (id: string) => ['observation-type-detail', id] as const,
  
  // System
  systemResources: ['system-resources'] as const,
  health: ['health'] as const,
  ready: ['ready'] as const,
  
  // Filesystem
  filesystem: ['filesystem'] as const,
  dataRoots: ['filesystem', 'data-roots'] as const,
  directory: (path: string) => ['filesystem', path] as const,

  // Facility discovery
  facilityDiscoveries: ['facility-discoveries'] as const,
} as const;
