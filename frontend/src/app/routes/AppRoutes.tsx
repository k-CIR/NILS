import { Navigate, useRoutes } from 'react-router-dom';
import { CohortsListPage } from '../../features/cohorts/pages/CohortsListPage';
import { CohortDetailPage } from '../../features/cohorts/pages/CohortDetailPage';
import { JobsPage } from '../../features/jobs/pages/JobsPage';
import { DatabaseManagementPage } from '../../features/database/pages/DatabaseManagementPage';
import { SettingsPage } from '../../features/settings/pages/SettingsPage';
import { QCPipelinePage } from '../../features/qc/pages/QCPipelinePage';
import { ExportPage } from '../../features/export/pages/ExportPage';
import { ExportDetailPage } from '../../features/export/pages/ExportDetailPage';
import { PipelinesPage } from '../../features/pipelines/pages/PipelinesPage';
import { PipelineDetailPage } from '../../features/pipelines/pages/PipelineDetailPage';
import { FacilityDiscoveryPage } from '../../features/facility-discovery/pages/FacilityDiscoveryPage';
import { NotFoundPage } from '../../features/shared/pages/NotFoundPage';
import { AppLayout } from '../layout/AppLayout';

export const AppRoutes = () =>
  useRoutes([
    {
      element: <AppLayout />,
      children: [
        { index: true, element: <Navigate to="cohorts" replace /> },
        { path: 'cohorts', element: <CohortsListPage /> },
        { path: 'cohorts/:cohortId', element: <CohortDetailPage /> },
        { path: 'jobs', element: <JobsPage /> },
        { path: 'qc', element: <QCPipelinePage /> },
        { path: 'export', element: <ExportPage /> },
        { path: 'export/:jobId', element: <ExportDetailPage /> },
        { path: 'pipelines', element: <PipelinesPage /> },
        { path: 'pipelines/:id', element: <PipelineDetailPage /> },
        { path: 'facility-discovery', element: <FacilityDiscoveryPage /> },
        { path: 'database', element: <DatabaseManagementPage /> },
        { path: 'settings', element: <SettingsPage /> },
        { path: '*', element: <NotFoundPage /> },
      ],
    },
  ]);
