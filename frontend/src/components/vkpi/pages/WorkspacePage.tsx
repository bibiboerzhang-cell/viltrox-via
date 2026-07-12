import { lazy, Suspense, useEffect, useState } from 'react';
import type {
  VkpiDashboardData,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiStaffMember,
} from '../vkpiTypes';
import type { VkpiDashboardProps } from '../VkpiDashboard';
import {
  clearTaskBridgeFocus,
  readTaskBridgeFocus,
  type IntelligenceTaskFocusPayload,
} from '../intelligence/intelligenceTaskDraft';
// C7-B1 成员个人工作台:员工视图工作页顶部的个人四卡(我的 KOL/项目/官号/提醒)
import { MemberWorkbench } from './MemberWorkbench';

const AttributionPage = lazy(() => import('./AttributionPage').then((module) => ({ default: module.AttributionPage })));
const AgentsPage = lazy(() => import('./AgentsPage').then((module) => ({ default: module.AgentsPage })));
const AuditPage = lazy(() => import('./AuditPage').then((module) => ({ default: module.AuditPage })));
const CampaignsPage = lazy(() => import('./CampaignsPage').then((module) => ({ default: module.CampaignsPage })));
const CostsPage = lazy(() => import('./CostsPage').then((module) => ({ default: module.CostsPage })));
const DataAnalysisPage = lazy(() => import('./DataAnalysisPage').then((module) => ({ default: module.DataAnalysisPage })));
const IndustryBoardPage = lazy(() => import('./IndustryBoardPage').then((module) => ({ default: module.IndustryBoardPage })));
const DataExportPage = lazy(() => import('./DataExportPage').then((module) => ({ default: module.DataExportPage })));
const DataQueryPage = lazy(() => import('./DataQueryPage').then((module) => ({ default: module.DataQueryPage })));
const MarketTrendsPage = lazy(() => import('./MarketTrendsPage').then((module) => ({ default: module.MarketTrendsPage })));
const LearningPage = lazy(() => import('./LearningPage').then((module) => ({ default: module.LearningPage })));
const CapabilitiesPage = lazy(() => import('./CapabilitiesPage').then((module) => ({ default: module.CapabilitiesPage })));
const SkillStudioPage = lazy(() => import('./SkillStudioPage').then((module) => ({ default: module.SkillStudioPage })));
const DiscoveryPage = lazy(() => import('./DiscoveryPage').then((module) => ({ default: module.DiscoveryPage })));
const DataQualityPage = lazy(() => import('./DataQualityPage').then((module) => ({ default: module.DataQualityPage })));
const DiscoverPage = lazy(() => import('./DiscoverPage').then((module) => ({ default: module.DiscoverPage })));
const IntelligenceCenterPage = lazy(() => import('./IntelligenceCenterPage').then((module) => ({ default: module.IntelligenceCenterPage })));
const KolPoolV2Page = lazy(() => import('../kol-pool-v2/KolPoolV2Page').then((module) => ({ default: module.KolPoolV2Page })));
const LinksPage = lazy(() => import('./LinksPage').then((module) => ({ default: module.LinksPage })));
const ProductBattlePage = lazy(() => import('./ProductBattlePage').then((module) => ({ default: module.ProductBattlePage })));
// 旧 ./ProjectsPage 已退役于 4bcc5c68c 后收尾波(2026-07-12,git 历史可找回);改挂新版 ProjectsBoardPage(props 同形 ProjectsPageProps,CockpitApp 同款切换)。
const ProjectsPage = lazy(() => import('../cockpit/pages/ProjectsBoardPage').then((module) => ({ default: module.ProjectsBoardPage })));
const ReportCenterV2Page = lazy(() => import('../report-v2/ReportCenterV2Page').then((module) => ({ default: module.ReportCenterV2Page })));
const SettingsPage = lazy(() => import('./SettingsPage').then((module) => ({ default: module.SettingsPage })));
// 旧 ./myKol/MyKolPage 已退役于 4bcc5c68c 后收尾波(2026-07-12,git 历史可找回);改挂新版 MyKolBoardPage(CockpitApp 同款切换)。
const MyKolPage = lazy(() => import('../cockpit/pages/MyKolBoardPage').then((module) => ({ default: module.MyKolBoardPage })));

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
  onSelectPage?: (page: VkpiPageKey) => void;
  onToggleView?: (targetPage?: VkpiPageKey) => void;
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  onSignOut?: () => Promise<void> | void;
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

// C7-B1:成员工作台只出现在成员日常工作页(不打扰报表/设置等全屏页)
const MEMBER_WORKBENCH_PAGES = new Set<VkpiPageKey>(['channels', 'projects', 'discover', 'links', 'attribution']);

const workstreamLabels: Record<string, string> = {
  recommendation_review: '推荐复核',
  market_watch: '市场跟进',
  evidence_review: '证据复核',
  sync_review: '同步复核',
  repair_review: '修复复核',
  brief_followup: '简报跟进',
};

function WorkspaceTaskFocusBanner({ focus, onDismiss }: { focus: IntelligenceTaskFocusPayload; onDismiss: () => void }) {
  return (
    <section className={`vkpi-task-focus-banner is-${focus.priority}`} aria-live="polite">
      <div>
        <span>来自智能中心 · {workstreamLabels[focus.workstream] || focus.workstream}</span>
        <h2>{focus.taskTitle}</h2>
        <p>{focus.objective}</p>
      </div>
      <div className="vkpi-task-focus-banner__meta">
        <span><b>入口</b>{focus.label}</span>
        <span><b>意图</b>{focus.intent}</span>
        <span><b>来源</b>{focus.sourceLabel}</span>
      </div>
      <div className="vkpi-task-focus-banner__actions">
        <a className="vkpi-button" href="#intelligenceCenter">回智能中心</a>
        <button className="vkpi-button vkpi-button--ghost" type="button" onClick={onDismiss}>关闭</button>
      </div>
    </section>
  );
}

export function WorkspacePage(props: WorkspacePageProps) {
  const [taskFocus, setTaskFocus] = useState<IntelligenceTaskFocusPayload | null>(null);

  useEffect(() => {
    const focus = readTaskBridgeFocus();
    if (focus?.page === props.page) {
      setTaskFocus(focus);
      clearTaskBridgeFocus();
      return;
    }
    setTaskFocus(null);
  }, [props.page]);

  let page = <SettingsPage {...props} />;
  if (props.page === 'agents') page = <AgentsPage {...props} />;
  if (props.page === 'skillStudio') page = <SkillStudioPage {...props} />;
  if (props.page === 'intelligenceCenter') page = <IntelligenceCenterPage {...props} />;
  if (props.page === 'kolPoolV2') {
    page = (
      <KolPoolV2Page
        apiToken={props.apiToken}
        userName={props.userName}
        userRole={props.userRole}
        userAvatar={props.userAvatar}
        onSelectPage={props.onSelectPage}
        onSignOut={props.onSignOut}
      />
    );
  }
  if (props.page === 'discover') page = <DiscoverPage {...props} />;
  if (props.page === 'projects') page = <ProjectsPage {...props} />;
  if (props.page === 'links') page = <LinksPage {...props} />;
  if (props.page === 'attribution') page = <AttributionPage {...props} />;
  if (props.page === 'costs') page = <CostsPage {...props} />;
  if (props.page === 'productBattle' || props.page === 'analytics') page = <ProductBattlePage {...props} />;
  // 数据分析 - 双兼容: 旧 industryData 路由 + 新 dataAnalysis 路由都进 DataAnalysisPage
  if (props.page === 'dataAnalysis' || props.page === 'industryData') page = <DataAnalysisPage {...props} />;
  if (props.page === 'industryBoard') page = <IndustryBoardPage apiToken={props.apiToken} />;
  if (props.page === 'dataExport') page = <DataExportPage apiToken={props.apiToken} />;
  if (props.page === 'dataQuery') page = <DataQueryPage apiToken={props.apiToken} />;
  if (props.page === 'marketTrends') page = <MarketTrendsPage apiToken={props.apiToken} />;
  if (props.page === 'learning') page = <LearningPage apiToken={props.apiToken} />;
  if (props.page === 'capabilities') page = <CapabilitiesPage apiToken={props.apiToken} />;
  if (props.page === 'discovery2') page = <DiscoveryPage apiToken={props.apiToken} />;
  if (props.page === 'channels') page = <MyKolPage {...props} />;
  if (props.page === 'campaigns') page = <CampaignsPage {...props} />;
  if (props.page === 'dataQuality') page = <DataQualityPage {...props} />;
  if (props.page === 'audit') page = <AuditPage {...props} />;
  if (props.page === 'reports') page = <ReportCenterV2Page {...props} />;
  return (
    <Suspense fallback={<WorkspaceLoadingFallback />}>
      {taskFocus ? <WorkspaceTaskFocusBanner focus={taskFocus} onDismiss={() => setTaskFocus(null)} /> : null}
      {props.viewMode === 'employee' && MEMBER_WORKBENCH_PAGES.has(props.page) ? (
        <MemberWorkbench
          data={props.data}
          apiToken={props.apiToken}
          userName={props.userName}
          onSelectPage={props.onSelectPage}
        />
      ) : null}
      {page}
    </Suspense>
  );
}
