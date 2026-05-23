import { lazy, Suspense } from 'react';
import type {
  VkpiDashboardData,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiStaffMember,
} from '../vkpiTypes';
import type { VkpiDashboardProps } from '../VkpiDashboard';

const AttributionPage = lazy(() => import('./AttributionPage').then((module) => ({ default: module.AttributionPage })));
const AgentsPage = lazy(() => import('./AgentsPage').then((module) => ({ default: module.AgentsPage })));
const AuditPage = lazy(() => import('./AuditPage').then((module) => ({ default: module.AuditPage })));
const CampaignsPage = lazy(() => import('./CampaignsPage').then((module) => ({ default: module.CampaignsPage })));
const ChannelsPage = lazy(() => import('./ChannelsPage').then((module) => ({ default: module.ChannelsPage })));
const CostsPage = lazy(() => import('./CostsPage').then((module) => ({ default: module.CostsPage })));
const DataAnalysisPage = lazy(() => import('./DataAnalysisPage').then((module) => ({ default: module.DataAnalysisPage })));
const DataQualityPage = lazy(() => import('./DataQualityPage').then((module) => ({ default: module.DataQualityPage })));
const DiscoverPage = lazy(() => import('./DiscoverPage').then((module) => ({ default: module.DiscoverPage })));
const LinksPage = lazy(() => import('./LinksPage').then((module) => ({ default: module.LinksPage })));
const ProductBattlePage = lazy(() => import('./ProductBattlePage').then((module) => ({ default: module.ProductBattlePage })));
const ProjectsPage = lazy(() => import('./ProjectsPage').then((module) => ({ default: module.ProjectsPage })));
const ReportsPage = lazy(() => import('./ReportsPage').then((module) => ({ default: module.ReportsPage })));
const SettingsPage = lazy(() => import('./SettingsPage').then((module) => ({ default: module.SettingsPage })));

export interface WorkspacePageProps {
  page: VkpiPageKey;
  data: VkpiDashboardData;
  query: string;
  filteredProjects: VkpiProjectRow[];
  selectedProject?: VkpiProjectRow;
  selectedProjectId?: string;
  viewMode: 'manager' | 'employee';
  onSelectProject: (project: VkpiProjectRow) => void;
  onLookupKol?: VkpiDashboardProps['onLookupKol'];
  onScanKolAccount?: VkpiDashboardProps['onScanKolAccount'];
  onClaimKol?: VkpiDashboardProps['onClaimKol'];
  onUpdateKol?: VkpiDashboardProps['onUpdateKol'];
  onUploadEvidenceFile?: VkpiDashboardProps['onUploadEvidenceFile'];
  onCreateProject?: VkpiDashboardProps['onCreateProject'];
  onUpdateProject?: VkpiDashboardProps['onUpdateProject'];
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: VkpiDashboardProps['onDeleteProject'];
  onAddProjectCost?: VkpiDashboardProps['onAddProjectCost'];
  onUpdateCost?: VkpiDashboardProps['onUpdateCost'];
  onApproveCost?: VkpiDashboardProps['onApproveCost'];
  onVoidCost?: VkpiDashboardProps['onVoidCost'];
  onUpsertProjectTerms?: VkpiDashboardProps['onUpsertProjectTerms'];
  onAddProjectShipment?: VkpiDashboardProps['onAddProjectShipment'];
  onCreateLink?: VkpiDashboardProps['onCreateLink'];
  onPauseLink?: VkpiDashboardProps['onPauseLink'];
  onArchiveLink?: VkpiDashboardProps['onArchiveLink'];
  onHealthCheckLink?: VkpiDashboardProps['onHealthCheckLink'];
  onInviteStaff?: VkpiDashboardProps['onInviteStaff'];
  onUpdateStaffPermission?: VkpiDashboardProps['onUpdateStaffPermission'];
  onRunKpiRollup?: VkpiDashboardProps['onRunKpiRollup'];
  onUpsertProductCost?: VkpiDashboardProps['onUpsertProductCost'];
  onCreateAttribution?: VkpiDashboardProps['onCreateAttribution'];
  onImportAmazonRows?: VkpiDashboardProps['onImportAmazonRows'];
  onUploadAmazonReport?: VkpiDashboardProps['onUploadAmazonReport'];
  onExportPDF?: () => void;
  onExportCSV?: () => void;
  onGenerateWeeklyReport?: () => void;
  onRefreshData?: () => void;
  onOpenEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  apiToken?: string;
}

function WorkspaceLoadingFallback() {
  return (
    <div className="vkpi-workspace-loading" aria-live="polite">
      <div>
        <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
        <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
      </div>
      <div className="vkpi-workspace-loading__grid">
        {[0, 1, 2].map((item) => (
          <article key={item}>
            <span className="vkpi-skeleton vkpi-skeleton-avatar" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          </article>
        ))}
      </div>
    </div>
  );
}

export function WorkspacePage(props: WorkspacePageProps) {
  let page = <SettingsPage {...props} />;
  if (props.page === 'agents') page = <AgentsPage {...props} />;
  if (props.page === 'discover') page = <DiscoverPage {...props} />;
  if (props.page === 'projects') page = <ProjectsPage {...props} />;
  if (props.page === 'links') page = <LinksPage {...props} />;
  if (props.page === 'attribution') page = <AttributionPage {...props} />;
  if (props.page === 'costs') page = <CostsPage {...props} />;
  if (props.page === 'productBattle' || props.page === 'analytics') page = <ProductBattlePage {...props} />;
  // 数据分析 - 双兼容: 旧 industryData 路由 + 新 dataAnalysis 路由都进 DataAnalysisPage
  if (props.page === 'dataAnalysis' || props.page === 'industryData') page = <DataAnalysisPage {...props} />;
  if (props.page === 'channels') page = <ChannelsPage {...props} />;
  if (props.page === 'campaigns') page = <CampaignsPage {...props} />;
  if (props.page === 'dataQuality') page = <DataQualityPage {...props} />;
  if (props.page === 'audit') page = <AuditPage {...props} />;
  if (props.page === 'reports') page = <ReportsPage {...props} />;
  return <Suspense fallback={<WorkspaceLoadingFallback />}>{page}</Suspense>;
}
