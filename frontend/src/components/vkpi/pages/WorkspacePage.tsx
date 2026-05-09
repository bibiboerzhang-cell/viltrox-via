import { AnalyticsPage } from './AnalyticsPage';
import { AttributionPage } from './AttributionPage';
import { AuditPage } from './AuditPage';
import { CampaignsPage } from './CampaignsPage';
import { ChannelsPage } from './ChannelsPage';
import { CostsPage } from './CostsPage';
import { DataAnalysisPage } from './DataAnalysisPage';
import { DataQualityPage } from './DataQualityPage';
import { DiscoverPage } from './DiscoverPage';
import { LinksPage } from './LinksPage';
import { ProjectsPage } from './ProjectsPage';
import { ProductBattlePage } from './ProductBattlePage';
import { ReportsPage } from './ReportsPage';
import { SettingsPage } from './SettingsPage';
import type {
  VkpiDashboardData,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiStaffMember,
} from '../vkpiTypes';
import type { VkpiDashboardProps } from '../VkpiDashboard';

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
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: VkpiDashboardProps['onDeleteProject'];
  onAddProjectCost?: VkpiDashboardProps['onAddProjectCost'];
  onUpdateCost?: VkpiDashboardProps['onUpdateCost'];
  onApproveCost?: VkpiDashboardProps['onApproveCost'];
  onVoidCost?: VkpiDashboardProps['onVoidCost'];
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
  onOpenEvidence: (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => void;
  onOpenKolProfile?: (project: VkpiProjectRow) => void | Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  apiToken?: string;
}

export function WorkspacePage(props: WorkspacePageProps) {
  if (props.page === 'discover') return <DiscoverPage {...props} />;
  if (props.page === 'projects') return <ProjectsPage {...props} />;
  if (props.page === 'links') return <LinksPage {...props} />;
  if (props.page === 'attribution') return <AttributionPage {...props} />;
  if (props.page === 'costs') return <CostsPage {...props} />;
  if (props.page === 'productBattle' || props.page === 'analytics') return <ProductBattlePage {...props} />;
  // 数据分析 - 双兼容: 旧 industryData 路由 + 新 dataAnalysis 路由都进 DataAnalysisPage
  if (props.page === 'dataAnalysis' || props.page === 'industryData') return <DataAnalysisPage {...props} />;
  if (props.page === 'channels') return <ChannelsPage {...props} />;
  if (props.page === 'campaigns') return <CampaignsPage {...props} />;
  if (props.page === 'dataQuality') return <DataQualityPage {...props} />;
  if (props.page === 'audit') return <AuditPage {...props} />;
  if (props.page === 'reports') return <ReportsPage {...props} />;
  return <SettingsPage {...props} />;
}
