import React, { useCallback, useEffect, useMemo, useState } from 'react';
import './VkpiDashboard.css';
import { CommandCenter } from './dashboard/CommandCenter';
import { DashboardPremium } from './pages/DashboardPremium';
import { GlassDemoPage } from './pages/GlassDemoPage';
import { MissionControlV2Page } from './pages/MissionControlV2Page';
import { V615ReplicaApp } from './v615-replica/V615ReplicaApp';
import { MANAGER_NAV_ITEMS } from './layout/vkpiLayoutConstants';
import {
  cleanVkpiPageCandidate,
  getInitialVkpiPage,
  isVkpiPageKey,
  normalizeVkpiPage,
  writeVkpiHash,
} from './layout/vkpiDashboardRouting';
import { VkpiSidebar } from './layout/VkpiSidebar';
import { VkpiTopbar } from './layout/VkpiTopbar';
import { TaskCenter, TaskCenterProvider } from '../tasks/TaskCenter';
import { WorkspacePage } from './pages/WorkspacePage';
import { EvidenceDrawer } from './drawers/EvidenceDrawer';
import { ProjectDetailDrawer } from './drawers/ProjectDetailDrawer';
import { StaffProfileDrawer } from './drawers/StaffProfileDrawer';
import { KolProfileDrawer } from './drawers/KolProfileDrawer';
import { AlertDetailDrawer } from './drawers/AlertDetailDrawer';
import { FeedbackWidget } from './shared/FeedbackWidget';
import { useMetricEvidence } from './hooks/useMetricEvidence';
import { useProjectDetailDrawer } from './hooks/useProjectDetailDrawer';
import { profileToKolDetail, textValue } from './shared/vkpiDataUtils';
import { emptyDashboardData } from './data/emptyDashboardData';
import { getMarketingAlertDetail, resolveMarketingAlert } from '../../domains/dashboard';
import { getKolProfile } from '../../domains/kol';
import { getStaffProfile } from '../../domains/settings';
import type {
  VkpiKolProfile,
  VkpiKolLookupResult,
  VkpiDashboardData,
  VkpiAlertDetail,
  VkpiMetricEvidenceKey,
  VkpiPageKey,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiRangeKey,
  VkpiStaffMember,
  VkpiStaffProfile,
} from './vkpiTypes';

export interface VkpiDashboardProps {
  data?: VkpiDashboardData;
  range?: VkpiRangeKey;
  onRangeChange?: (range: VkpiRangeKey) => void;
  onRefreshData?: () => void;
  isRefreshing?: boolean;
  lastSyncedAt?: string;
  onExportPDF?: () => void;
  onExportCSV?: () => void;
  onGenerateWeeklyReport?: () => void;
  weeklyReportStatus?: {
    state: 'idle' | 'loading' | 'success' | 'error';
    message: string;
    href?: string;
  } | null;
  onDownloadReportPDF?: () => void;
  onOpenProject?: (projectId: string) => void;
  onCopyShortLink?: (slug: string) => void;
  onUploadAvatar?: (file: File) => void;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; email?: string; contactEmail?: string; notes?: string; scanAccount?: boolean; maxPosts?: number; productSku?: string }) => Promise<VkpiKolLookupResult>;
  onScanKolAccount?: (kolId: string, maxPosts?: number) => Promise<Record<string, unknown>>;
  onClaimKol?: (kolId: string) => Promise<void>;
  onUpdateKol?: (kolId: string, payload: { avatarUrl?: string; profileUrl?: string; contactEmail?: string; contactPhone?: string; notes?: string; contactLinks?: Array<{ label?: string; value?: string; url?: string }> }) => Promise<void>;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
  onCreateProject?: (payload: { projectName: string; kolId?: string; productSku?: string; productName?: string; productSkus?: string[]; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; sourceType?: string; note?: string }) => Promise<void>;
  onUpdateProject?: (projectId: string, payload: { projectName?: string; productSku?: string; productName?: string; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; priority?: string; shopifyLink?: string; targetPostDate?: string; dueAt?: string; note?: string }) => Promise<void>;
  onMoveProjectStage?: (projectId: string, toStage: VkpiProjectStage, note?: string, extras?: { trackingNumber?: string; sampleStatus?: string; sourceRefType?: string; sourceRefId?: string }) => Promise<void>;
  onDeleteProject?: (projectId: string, reason?: string) => Promise<void>;
  onAddProjectCost?: (payload: { projectId: string; costType: string; amountUsd: number; note?: string; sourceRef?: string; metadata?: Record<string, unknown> }) => Promise<void>;
  onUpdateCost?: (costId: string, payload: { costType?: string; amountUsd?: number; note?: string; sourceRef?: string }) => Promise<void>;
  onApproveCost?: (costId: string, note?: string) => Promise<void>;
  onVoidCost?: (costId: string, reason?: string) => Promise<void>;
  onAddProjectMessage?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectContent?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onCreateLink?: (payload: { destinationUrl: string; slug?: string; projectId?: string; kolId?: string; platform?: string; productSku?: string; campaignName?: string; utmSource?: string; utmMedium?: string; utmCampaign?: string; utmContent?: string }) => Promise<void>;
  onPauseLink?: (linkId: string) => Promise<void>;
  onArchiveLink?: (linkId: string) => Promise<void>;
  onHealthCheckLink?: (linkId: string) => Promise<void>;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write'; permissions?: Record<string, string>; permissionTemplate?: string }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onRunKpiRollup?: (ledgerDate?: string) => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; currency?: string; active?: boolean; note?: string }) => Promise<void>;
  onCreateAttribution?: (payload: { sourcePlatform: 'shopify' | 'amazon' | 'manual' | 'custom'; sourceRef: string; projectId?: string; linkId?: string; productSku?: string; orderId?: string; revenueUsd: number; commissionUsd?: number; confidence?: string; occurredAt?: string }) => Promise<void>;
  onImportAmazonRows?: (payload: { projectId?: string; amazonTag?: string; asin?: string; marketplace?: string; reportDate?: string; rows: Array<Record<string, unknown>> }) => Promise<void>;
  onUploadAmazonReport?: (payload: { projectId?: string; amazonTag?: string; asin?: string; marketplace?: string; reportDate?: string; file: File }) => Promise<void>;
  apiToken?: string;
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  avatarRequired?: boolean;
  onSignOut?: () => Promise<void> | void;
  viewMode?: 'manager' | 'employee';
  canSwitchView?: boolean;
  onToggleView?: (targetPage?: VkpiPageKey) => void;
}

export function VkpiDashboard({
  data = emptyDashboardData,
  range = '7d',
  onRangeChange,
  onRefreshData,
  isRefreshing = false,
  lastSyncedAt,
  onExportPDF,
  onExportCSV,
  onGenerateWeeklyReport,
  weeklyReportStatus,
  onDownloadReportPDF,
  onOpenProject,
  onCopyShortLink,
  onUploadAvatar,
  onLookupKol,
  onScanKolAccount,
  onClaimKol,
  onUpdateKol,
  onUploadEvidenceFile,
  onCreateProject,
  onUpdateProject,
  onMoveProjectStage,
  onDeleteProject,
  onAddProjectCost,
  onUpdateCost,
  onApproveCost,
  onVoidCost,
  onAddProjectMessage,
  onAddProjectContent,
  onUpsertProjectTerms,
  onAddProjectShipment,
  onCreateLink,
  onPauseLink,
  onArchiveLink,
  onHealthCheckLink,
  onInviteStaff,
  onUpdateStaffPermission,
  onRunKpiRollup,
  onUpsertProductCost,
  onCreateAttribution,
  onImportAmazonRows,
  onUploadAmazonReport,
  apiToken,
  userName = 'Viltrox 成员',
  userRole = '营销运营',
  userAvatar,
  avatarRequired = false,
  onSignOut,
  viewMode = 'manager',
  canSwitchView = false,
  onToggleView,
}: VkpiDashboardProps) {
  const [query, setQuery] = useState('');
  const [selectedProjectId, setSelectedProjectId] = useState(data.projects[0]?.id);
  const [activePage, setActivePage] = useState<VkpiPageKey>(() => getInitialVkpiPage(viewMode));
  const [evidenceMetric, setEvidenceMetric] = useState<VkpiMetricEvidenceKey | null>(null);
  const [evidenceMetricValueId, setEvidenceMetricValueId] = useState<number | null>(null);
  const [kolProfileDrawerProject, setKolProfileDrawerProject] = useState<VkpiProjectRow | null>(null);
  const [kolProfileDrawerProfile, setKolProfileDrawerProfile] = useState<VkpiKolProfile | null>(null);
  const [kolProfileDrawerLoading, setKolProfileDrawerLoading] = useState(false);
  const [kolProfileDrawerError, setKolProfileDrawerError] = useState('');
  const [staffProfileDrawerMember, setStaffProfileDrawerMember] = useState<VkpiStaffMember | null>(null);
  const [staffProfileDrawerProfile, setStaffProfileDrawerProfile] = useState<VkpiStaffProfile | null>(null);
  const [staffProfileDrawerLoading, setStaffProfileDrawerLoading] = useState(false);
  const [staffProfileDrawerError, setStaffProfileDrawerError] = useState('');
  const [resolvedAlertIds, setResolvedAlertIds] = useState<Set<string>>(() => new Set());
  const [alertDetailOpen, setAlertDetailOpen] = useState(false);
  const [alertDetail, setAlertDetail] = useState<VkpiAlertDetail | null>(null);
  const [alertDetailLoading, setAlertDetailLoading] = useState(false);
  const [alertDetailError, setAlertDetailError] = useState('');

  useEffect(() => {
    if (!selectedProjectId && data.projects[0]?.id) {
      setSelectedProjectId(data.projects[0].id);
    }
  }, [data.projects, selectedProjectId]);

  const metricEvidence = useMetricEvidence({
    apiToken,
    metric: evidenceMetric,
    metricValueId: evidenceMetricValueId,
    fallbackEvidence: data.evidence,
  });

  const filteredProjects = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return data.projects;
    return data.projects.filter((project) => {
      return [
        project.kolName,
        project.kolHandle,
        project.platform,
        project.campaign,
        project.ownerName,
        project.latestMessageSource,
      ]
        .join(' ')
        .toLowerCase()
        .includes(normalized);
    });
  }, [data.projects, query]);

  const selectedProject = data.projects.find((project) => project.id === selectedProjectId) ?? data.projects[0];
  const projectDetailDrawer = useProjectDetailDrawer({
    apiToken,
    selectedProject,
    onOpenProject,
    onAddProjectMessage,
    onAddProjectContent,
    onUpsertProjectTerms,
    onAddProjectShipment,
  });
  const evidenceRows = evidenceMetric ? metricEvidence.rows : [];
  const visibleProjects = filteredProjects.slice(0, 5);
  const visibleEnd = filteredProjects.length ? Math.min(5, filteredProjects.length) : 0;
  const visibleMetrics = viewMode === 'employee' ? data.metrics.filter((metric) => metric.key !== 'cost') : data.metrics;
  const visibleAlerts = useMemo(
    () => data.alerts.filter((alert) => !resolvedAlertIds.has(alert.id)),
    [data.alerts, resolvedAlertIds],
  );
  // 成员 UI = 管理层同款全量 UI(用户决策「一模一样」):两种角色共用全量导航,数据按后端 RBAC 过滤。
  const navItems = MANAGER_NAV_ITEMS;
  const selectedKolForPanel = useMemo(
    () => profileToKolDetail(projectDetailDrawer.projectKolProfile, data.selectedKol, selectedProject, data.links),
    [projectDetailDrawer.projectKolProfile, data.selectedKol, selectedProject, data.links],
  );
  const metricByKey = useMemo(() => {
    return new Map(data.metrics.map((metric) => [metric.key, metric]));
  }, [data.metrics]);
  const openMetricEvidence = (metric: VkpiMetricEvidenceKey, metricValueId?: number | null) => {
    setEvidenceMetric(metric);
    setEvidenceMetricValueId(metricValueId || metricByKey.get(metric)?.metricValueId || null);
  };

  const closeKolProfileDrawer = () => {
    setKolProfileDrawerProject(null);
    setKolProfileDrawerProfile(null);
    setKolProfileDrawerError('');
  };

  const closeStaffProfileDrawer = () => {
    setStaffProfileDrawerMember(null);
    setStaffProfileDrawerProfile(null);
    setStaffProfileDrawerError('');
  };

  const closeAlertDetailDrawer = () => {
    setAlertDetailOpen(false);
    setAlertDetail(null);
    setAlertDetailError('');
  };

  const closeWorkspaceDrawers = () => {
    setEvidenceMetric(null);
    setEvidenceMetricValueId(null);
    projectDetailDrawer.closeProjectDetail();
    closeKolProfileDrawer();
    closeStaffProfileDrawer();
    closeAlertDetailDrawer();
  };

  const handleSelectPage = (page: VkpiPageKey) => {
    const nextPage = normalizeVkpiPage(page, viewMode);
    setActivePage(nextPage);
    writeVkpiHash(nextPage);
    closeWorkspaceDrawers();
  };

  useEffect(() => {
    const handleHashChange = () => {
      const nextPage = getInitialVkpiPage(viewMode);
      setActivePage(nextPage);
      writeVkpiHash(nextPage);
    };
    handleHashChange();
    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('popstate', handleHashChange);
    return () => {
      window.removeEventListener('hashchange', handleHashChange);
      window.removeEventListener('popstate', handleHashChange);
    };
  }, [viewMode]);

  useEffect(() => {
    const handleInternalHashLink = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest<HTMLAnchorElement>('a[href^="#"]');
      if (!anchor) return;
      const href = anchor.getAttribute('href') || '';
      const page = cleanVkpiPageCandidate(href);
      if (!page || !isVkpiPageKey(page)) return;
      event.preventDefault();
      handleSelectPage(normalizeVkpiPage(page, viewMode));
    };
    document.addEventListener('click', handleInternalHashLink);
    return () => document.removeEventListener('click', handleInternalHashLink);
  }, [handleSelectPage, viewMode]);

  useEffect(() => {
    closeWorkspaceDrawers();
  }, [activePage]);

  const handleSelectProject = (project: VkpiProjectRow) => {
    setSelectedProjectId(project.id);
    closeKolProfileDrawer();
    closeStaffProfileDrawer();
    projectDetailDrawer.openProjectDetail(project);
  };

  const handleOpenKolProfile = useCallback(async (project: VkpiProjectRow) => {
    setSelectedProjectId(project.id);
    setKolProfileDrawerProject(project);
    setKolProfileDrawerProfile(null);
    setKolProfileDrawerError('');
    if (!apiToken) {
      setKolProfileDrawerError('当前前端没有登录 token，无法读取真实 KOL Profile。');
      return;
    }
    if (!project.kolId) {
      setKolProfileDrawerError('当前项目没有绑定 KOL ID，不能读取完整档案。');
      return;
    }
    setKolProfileDrawerLoading(true);
    try {
      setKolProfileDrawerProfile(await getKolProfile(apiToken, project.kolId));
    } catch (error) {
      setKolProfileDrawerError(error instanceof Error ? error.message : 'KOL Profile 加载失败');
    } finally {
      setKolProfileDrawerLoading(false);
    }
  }, [apiToken]);

  const handleOpenStaffProfile = useCallback(async (staffId: string, fallback?: Partial<VkpiStaffMember>) => {
    const member = data.staffMembers.find((item) => item.id === staffId) || (fallback ? {
      id: staffId,
      name: fallback.name || `成员 ${staffId}`,
      email: fallback.email || '',
      role: fallback.role || '',
      active: fallback.active ?? true,
      avatarUrl: fallback.avatarUrl,
      employeeCode: fallback.employeeCode,
      vkpiPermission: fallback.vkpiPermission || 'read',
    } : null);
    setStaffProfileDrawerMember(member);
    setStaffProfileDrawerProfile(null);
    setStaffProfileDrawerError('');
    if (!apiToken) {
      setStaffProfileDrawerError('当前前端没有登录 token，无法读取成员详情。');
      return;
    }
    if (!staffId) {
      setStaffProfileDrawerError('缺少成员 ID。');
      return;
    }
    setStaffProfileDrawerLoading(true);
    try {
      setStaffProfileDrawerProfile(await getStaffProfile(apiToken, staffId));
    } catch (error) {
      setStaffProfileDrawerError(error instanceof Error ? error.message : '成员详情加载失败');
    } finally {
      setStaffProfileDrawerLoading(false);
    }
  }, [apiToken, data.staffMembers]);

  const handleResolveAlert = useCallback(async (alertId: string) => {
    if (!apiToken || !alertId) return;
    await resolveMarketingAlert(apiToken, alertId);
    setResolvedAlertIds((current) => {
      const next = new Set(current);
      next.add(alertId);
      return next;
    });
    void onRefreshData?.();
  }, [apiToken, onRefreshData]);

  const handleOpenAlert = useCallback(async (alertId: string) => {
    setAlertDetailOpen(true);
    setAlertDetail(null);
    setAlertDetailError('');
    if (!apiToken || !alertId) {
      setAlertDetailError('当前前端没有登录 token，无法读取告警 source rows。');
      return;
    }
    setAlertDetailLoading(true);
    try {
      setAlertDetail(await getMarketingAlertDetail(apiToken, alertId));
    } catch (error) {
      setAlertDetailError(error instanceof Error ? error.message : '告警详情加载失败');
    } finally {
      setAlertDetailLoading(false);
    }
  }, [apiToken]);

  if (activePage === 'glass-demo') {
    return <GlassDemoPage apiToken={apiToken} userName={userName} userRole={userRole} />;
  }

  if (activePage === 'v615Replica') {
    return (
      <TaskCenterProvider apiToken={apiToken}>
        <V615ReplicaApp
          apiToken={apiToken}
          userName={userName}
          userRole={userRole}
          userAvatar={userAvatar}
          data={data}
          viewMode={viewMode}
          onRefreshData={onRefreshData}
          onSelectPage={handleSelectPage}
          onToggleView={onToggleView}
          onLookupKol={onLookupKol}
          onUpsertProjectTerms={onUpsertProjectTerms}
          onUploadEvidenceFile={onUploadEvidenceFile}
          onAddProjectShipment={onAddProjectShipment}
          onMoveProjectStage={onMoveProjectStage}
          onSignOut={onSignOut}
        />
      </TaskCenterProvider>
    );
  }

  return (
    <TaskCenterProvider apiToken={apiToken}>
    <div className="vkpi-app" data-testid="vkpi-dashboard">
      <VkpiSidebar
        navItems={navItems}
        activePage={activePage}
        userName={userName}
        userRole={userRole}
        userAvatar={userAvatar}
        avatarRequired={avatarRequired}
        onSelectPage={handleSelectPage}
        onUploadAvatar={onUploadAvatar}
        onSignOut={onSignOut}
      />

      <div className="vkpi-shell">
        {activePage !== 'missionControlV2' && activePage !== 'kolPoolV2' && activePage !== 'reports' ? (
        <VkpiTopbar
          query={query}
          range={range}
          dataStatus={data.dataStatus}
          dataNotice={data.dataNotice}
          viewMode={viewMode}
          lastSyncedAt={lastSyncedAt || data.lastSyncedAt}
          isRefreshing={isRefreshing}
          canSwitchView={canSwitchView}
          onQueryChange={setQuery}
          onRangeChange={onRangeChange}
          onRefreshData={onRefreshData}
          onToggleView={onToggleView}
          onExportPDF={onExportPDF}
          onExportCSV={onExportCSV}
          onGenerateWeeklyReport={onGenerateWeeklyReport}
          weeklyReportStatus={weeklyReportStatus}
        />
        ) : null}

	        <main className={`vkpi-page vkpi-page--${activePage}`}>
	          {activePage === 'missionControlV2' ? (
	            <MissionControlV2Page
	              apiToken={apiToken}
	              userName={userName}
	              userRole={userRole}
	              userAvatar={userAvatar}
	              onSelectPage={handleSelectPage}
	              onSignOut={onSignOut}
	            />
	          ) : activePage === 'dashboardPremium' ? (
	            <DashboardPremium apiToken={apiToken} userName={userName} userRole={userRole} embedded onSelectPage={handleSelectPage} />
	          ) : activePage === 'command' ? (
	            <CommandCenter
	              data={data}
	              visibleMetrics={visibleMetrics}
	              filteredProjects={filteredProjects}
	              visibleProjects={visibleProjects}
	              visibleEnd={visibleEnd}
	              selectedProject={selectedProject}
	              selectedKolForPanel={selectedKolForPanel}
	              query={query}
	              viewMode={viewMode}
	              onQueryChange={setQuery}
	              onOpenMetricEvidence={openMetricEvidence}
	              onSelectProject={handleSelectProject}
	              onOpenKolProfile={handleOpenKolProfile}
	              onOpenStaffProfile={handleOpenStaffProfile}
	              onCopyShortLink={onCopyShortLink}
	              alerts={visibleAlerts}
	              onResolveAlert={apiToken ? handleResolveAlert : undefined}
	              onOpenAlert={apiToken ? handleOpenAlert : undefined}
	              onDownloadReportPDF={onDownloadReportPDF}
	              onExportPDF={onExportPDF}
	              apiToken={apiToken}
	              onSelectPage={handleSelectPage}
	            />
	          ) : (
            <WorkspacePage
              page={activePage}
              data={data}
              query={query}
              filteredProjects={filteredProjects}
              selectedProject={selectedProject}
              selectedProjectId={selectedProject?.id}
              viewMode={viewMode}
              onSelectProject={handleSelectProject}
              onOpenKolProfile={handleOpenKolProfile}
              onOpenStaffProfile={handleOpenStaffProfile}
              onLookupKol={onLookupKol}
              onScanKolAccount={onScanKolAccount}
              onClaimKol={onClaimKol}
              onUpdateKol={onUpdateKol}
              onUploadEvidenceFile={onUploadEvidenceFile}
              onCreateProject={onCreateProject}
              onUpdateProject={onUpdateProject}
              onMoveProjectStage={onMoveProjectStage}
              onDeleteProject={onDeleteProject}
              onAddProjectCost={onAddProjectCost}
              onUpdateCost={onUpdateCost}
              onApproveCost={onApproveCost}
              onVoidCost={onVoidCost}
              onUpsertProjectTerms={onUpsertProjectTerms}
              onAddProjectShipment={onAddProjectShipment}
              onCreateLink={onCreateLink}
              onPauseLink={onPauseLink}
              onArchiveLink={onArchiveLink}
              onHealthCheckLink={onHealthCheckLink}
              onInviteStaff={onInviteStaff}
              onUpdateStaffPermission={onUpdateStaffPermission}
              onRunKpiRollup={onRunKpiRollup}
              onUpsertProductCost={onUpsertProductCost}
              onCreateAttribution={onCreateAttribution}
              onImportAmazonRows={onImportAmazonRows}
              onUploadAmazonReport={onUploadAmazonReport}
	              onExportPDF={onExportPDF}
	              onExportCSV={onExportCSV}
              onGenerateWeeklyReport={onGenerateWeeklyReport}
              onRefreshData={onRefreshData}
              onOpenEvidence={openMetricEvidence}
              onSelectPage={handleSelectPage}
              onToggleView={onToggleView}
              userName={userName}
              userRole={userRole}
              userAvatar={userAvatar}
              onSignOut={onSignOut}
              apiToken={apiToken}
            />
          )}
        </main>
        {evidenceMetric ? (
          <EvidenceDrawer
            metric={evidenceMetric}
            rows={evidenceRows}
            lineageInfo={metricEvidence.lineageInfo}
            usedFallback={metricEvidence.usedFallback}
            loading={metricEvidence.loading}
            officialViewsMatrix={metricEvidence.officialViewsMatrix}
            onClose={() => {
              setEvidenceMetric(null);
              setEvidenceMetricValueId(null);
            }}
          />
        ) : null}
        {projectDetailDrawer.projectDetailId ? (
          <ProjectDetailDrawer
            detail={projectDetailDrawer.projectDetail}
            apiToken={apiToken}
            kolProfile={projectDetailDrawer.projectKolProfile}
            fallbackProject={data.projects.find((project) => project.id === projectDetailDrawer.projectDetailId)}
            viewMode={viewMode}
            loading={projectDetailDrawer.projectDetailLoading}
            error={projectDetailDrawer.projectDetailError}
            onAddMessage={onAddProjectMessage ? projectDetailDrawer.addMessage : undefined}
            onAddContent={onAddProjectContent ? projectDetailDrawer.addContent : undefined}
            onUpsertTerms={onUpsertProjectTerms ? projectDetailDrawer.upsertTerms : undefined}
            onAddShipment={onAddProjectShipment ? projectDetailDrawer.addShipment : undefined}
            onUploadEvidenceFile={onUploadEvidenceFile}
            onClose={projectDetailDrawer.closeProjectDetail}
          />
        ) : null}
        {kolProfileDrawerProject ? (
          <KolProfileDrawer
            profile={kolProfileDrawerProfile}
            fallbackKol={data.selectedKol}
            fallbackProject={kolProfileDrawerProject}
            viewMode={viewMode}
            loading={kolProfileDrawerLoading}
            error={kolProfileDrawerError}
            onSelectProject={handleSelectProject}
            onClose={closeKolProfileDrawer}
          />
        ) : null}
        {staffProfileDrawerMember ? (
          <StaffProfileDrawer
            member={staffProfileDrawerMember}
            profile={staffProfileDrawerProfile}
            loading={staffProfileDrawerLoading}
            error={staffProfileDrawerError}
            onSelectProject={handleSelectProject}
            onClose={closeStaffProfileDrawer}
          />
        ) : null}
        {alertDetailOpen ? (
          <AlertDetailDrawer
            detail={alertDetail}
            loading={alertDetailLoading}
            error={alertDetailError}
            onClose={() => {
              closeAlertDetailDrawer();
            }}
          />
        ) : null}
        {activePage !== 'missionControlV2' && activePage !== 'kolPoolV2' && activePage !== 'reports' && activePage !== 'dashboardPremium' && activePage !== 'command' ? (
          <FeedbackWidget apiToken={apiToken} activePage={activePage} userName={userName} />
        ) : null}
        <TaskCenter />
      </div>
    </div>
    </TaskCenterProvider>
  );
}

export default VkpiDashboard;
