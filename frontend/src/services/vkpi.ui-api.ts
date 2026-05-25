import { apiFetch } from "./http";
import {
  buildDashboardAlerts,
  buildDashboardFunnel,
  buildDashboardKpiLedger,
  buildDashboardLeaderboard,
  buildDashboardMetrics,
  buildDashboardPlatformShare,
  buildDashboardProductRoi,
  buildDashboardRevenueTrend,
  buildScopeContext,
  buildWeeklySummary,
  centsToUsd,
  durationLabel,
  emptyEvidence,
  hasAnyDashboardData,
  numberValue,
  platformLabel,
  rangeLabel,
  staffWindow,
  stageValue,
  windowDays,
} from "../domains/dashboard";
import { buildKolOptions, emptyKol } from "../domains/kol";
import { buildStaffMembers } from "../domains/settings";
import {
  buildProductCatalog,
  buildProductCosts,
  buildProductLaunchOptions,
} from "../domains/products";
import {
  buildAttributions,
  buildCosts,
  buildLinks,
} from "../domains/attribution";
export * from "./vkpi";
export * from "./vkpi/alert-api";
export * from "./vkpi/attribution-api";
export * from "./vkpi/audit-api";
export * from "./vkpi/channel-api";
export * from "./vkpi/comment-intelligence-api";
export * from "./vkpi/cost-api";
export * from "./vkpi/data-quality-api";
export * from "./vkpi/evidence-api";
export * from "./vkpi/feedback-api";
export * from "./vkpi/firewall-api";
export * from "./vkpi/industry-api";
export * from "./vkpi/intelligence-api";
export * from "./vkpi/kol-api";
export * from "./vkpi/kolPool-api";
export * from "./vkpi/links-api";
export * from "./vkpi/market-api";
export * from "./vkpi/media-api";
export * from "./vkpi/product-api";
export * from "./vkpi/projects-api";
export * from "./vkpi/repair-api";
export * from "./vkpi/search-api";
export * from "./vkpi/settings-api";
export * from "./vkpi/staff-api";
export * from "./vkpi/sync-api";
export * from "./vkpi/tasks-api";
import type {
  VkpiAttributionRow,
  VkpiCostRow,
  VkpiDashboardData,
  VkpiKolDetail,
  VkpiLinkRow,
  VkpiProjectRow,
} from "../components/vkpi/vkpiTypes";

export interface VkpiDashboardFilters {
  range?: "today" | "7d" | "30d" | "mtd" | "qtd" | "custom";
  startDate?: string;
  endDate?: string;
  scope?: "self" | "team" | "all";
  staffId?: string;
  platform?: string;
  productId?: string;
  includeEstimated?: boolean;
}

type LegacyRepairItem = {
  [key: string]: any;
  acceptance?: string[];
  evidence?: string[];
  fields?: LegacyRepairItem[];
  requiredBeforeWrite?: string[];
  required_before_write?: string[];
  validationCommands?: string[];
  validation_commands?: string[];
};
type LegacyRepairPayload = {
  [key: string]: any;
  proposals?: LegacyRepairItem[];
  rows?: LegacyRepairItem[];
  gates?: LegacyRepairItem[];
  tables?: LegacyRepairItem[];
  endpoints?: LegacyRepairItem[];
  blockedBy?: string[];
  blocked_by?: string[];
  artifacts?: LegacyRepairItem[];
  steps?: LegacyRepairItem[];
  upSql?: LegacyRepairItem[];
  up_sql?: LegacyRepairItem[];
  downSql?: LegacyRepairItem[];
  down_sql?: LegacyRepairItem[];
  reviewCommands?: string[];
  review_commands?: string[];
  events?: LegacyRepairItem[];
  drafts?: LegacyRepairItem[];
  refs?: LegacyRepairItem[];
  fields?: LegacyRepairItem[];
  requiredBeforePersist?: string[];
  required_before_persist?: string[];
  persistencePreview?: LegacyRepairPayload;
  persistence_preview?: LegacyRepairPayload;
};
export type VkpiRepairPersistencePreview = LegacyRepairPayload;
export type VkpiRepairProposalRecord = LegacyRepairPayload;

type Row = Record<string, unknown>;
type OptionalResult<T> = { data: T; failed?: string };

function buildProjects(projects: Row[], links: VkpiLinkRow[], attributions: VkpiAttributionRow[], costs: VkpiCostRow[]): VkpiProjectRow[] {
  const linksByProject = new Map<string, VkpiLinkRow[]>();
  links.forEach((link) => { if (link.projectId) linksByProject.set(link.projectId, [...(linksByProject.get(link.projectId) || []), link]); });
  const revenueByProject = new Map<string, number>();
  attributions.forEach((row) => { if (row.projectId) revenueByProject.set(row.projectId, (revenueByProject.get(row.projectId) || 0) + row.revenue); });
  const costByProject = new Map<string, number>();
  costs.forEach((row) => { if (row.projectId && row.status !== "void") costByProject.set(row.projectId, (costByProject.get(row.projectId) || 0) + row.amount); });
  return projects.map((project) => {
    const id = String(project.id || project.project_uid || "");
    const projectLinks = linksByProject.get(id) || [];
    const clicks = projectLinks.reduce((sum, link) => sum + link.validClicks, 0);
    const gmv = revenueByProject.get(id) || centsToUsd(project.revenue_cents);
    const cost = costByProject.get(id) || centsToUsd(project.cost_cents);
    const startedAt = String(project.started_at || project.first_event_at || project.created_at || "");
    const closedAt = String(project.closed_at || "");
    const stageStartedAt = String(project.current_stage_started_at || project.last_activity_at || project.updated_at || "");
    const views = numberValue(project.total_views || project.views || project.view_count || project.play_count || project.impressions || project.content_views);
    return {
      id,
      kolId: project.kol_id ? String(project.kol_id) : undefined,
      kolName: String(project.kol_name || project.channel_name || `KOL ${project.kol_id || ""}`).trim() || "未知 KOL",
      kolHandle: String(project.handle || project.kol_platform || project.platform || "-"),
      platform: platformLabel(project.kol_platform || project.platform),
      campaign: String(project.project_name || project.project_uid || "未命名项目"),
      stage: stageValue(project.stage),
      latestMessageAt: String(project.last_activity_at || project.updated_at || "-"),
      latestMessageSource: "Manual note",
      views,
      clicks: clicks || null,
      orders: numberValue(project.orders) || null,
      gmv,
      cost,
      roi: cost ? Number((gmv / cost).toFixed(2)) : null,
      ownerId: project.assigned_staff_id ? String(project.assigned_staff_id) : project.created_by_staff_id ? String(project.created_by_staff_id) : undefined,
      ownerName: String(project.staff_name || project.assigned_staff_id || "未分配"),
      productSku: String(project.product_sku || ""),
      productName: String(project.product_name || ""),
      marketplace: String(project.marketplace || ""),
      priority: String(project.priority || ""),
      shopifyLink: String(project.shopify_link || ""),
      createdAt: String(project.created_at || ""),
      startedAt,
      closedAt,
      currentStageStartedAt: stageStartedAt,
      totalDurationLabel: durationLabel(startedAt || project.created_at, closedAt || undefined),
      stageDurationLabel: durationLabel(stageStartedAt),
      stageEventCount: numberValue(project.stage_event_count),
      updatedAt: String(project.updated_at || project.created_at || "-"),
    };
  });
}
function buildKol(projects: VkpiProjectRow[], links: VkpiLinkRow[], messages: Row[]): VkpiKolDetail {
  const first = projects[0];
  if (!first) return emptyKol;
  const link = links.find((row) => row.projectId === first.id) || links[0];
  return { id: first.kolId || first.id, name: first.kolName, handle: first.kolHandle, platform: first.platform, verified: false, subscribersLabel: "-", videosLabel: "-", engagementLabel: "-", country: "", claimOwner: first.ownerName, claimStatus: "进行中项目", recentContent: [], messages: messages.slice(0, 5).map((message, index) => ({ id: String(message.id || index), source: platformLabel(message.source_platform || message.source) === "Email" ? "Email" : "Manual", type: "Note", capturedAt: String(message.created_at || message.occurred_at || "-"), snippet: String(message.title || message.body || message.note || "暂无消息摘要。"), evidenceUrl: String(message.evidence_url || "") || undefined })), shortLink: { slug: link?.slug || "暂无短链", destination: link?.destination || "-", clicks: link?.validClicks || 0, orders: first.orders || 0, gmv: first.gmv, roi: first.roi || 0 }, followUpNote: `项目总耗时 ${first.totalDurationLabel || "-"}，当前阶段已停留 ${first.stageDurationLabel || "-"}。请按流程继续推进。` };
}

function emptyData(filters: VkpiDashboardFilters = {}): VkpiDashboardData {
  return { rangeLabel: rangeLabel(filters), windowDays: windowDays(filters), dataStatus: "empty", dataNotice: "当前周期还没有真实数据。", metrics: buildDashboardMetrics([]), revenueTrend: buildDashboardRevenueTrend([]), funnel: buildDashboardFunnel([]), staffLeaderboard: [], productRoi: [{ product: "暂无项目数据", roi: 0, gmv: 0 }], platformShare: [{ label: "暂无归因", value: 100 }], contentTypePerformance: [{ label: "暂无内容数据", value: 0 }], alerts: [], weeklySummary: "当前还没有生成周报。请在 Shopify、Amazon、短链、成本和项目事件同步后生成。", exportReport: { id: "none", title: "周报尚未生成", generatedAt: "等待数据", status: "Generating" }, projects: [], links: [], attributions: [], unmatchedAttributions: [], costs: [], evidence: emptyEvidence(), staffMembers: [], kpiLedger: [], productCosts: [], productLaunches: [], kolOptions: [], selectedKol: emptyKol, scopes: {} };
}
async function optionalFetch<T>(label: string, path: string, token: string, fallback: T): Promise<OptionalResult<T>> { try { return { data: await apiFetch<T>(path, {}, token) }; } catch { return { data: fallback, failed: label }; } }

export async function fetchVkpiDashboardData(token: string, filters: VkpiDashboardFilters = {}): Promise<VkpiDashboardData> {
  const days = windowDays(filters);
  const windowKey = staffWindow(filters);
  const selfMode = filters.scope === "self";
  const staffQuery = filters.staffId ? `&staff_id=${encodeURIComponent(filters.staffId)}` : "";
  const dashboardPath = selfMode ? `/api/marketing/dashboard/view/employee?window_days=${days}${staffQuery}` : `/api/marketing/dashboard?window_days=${days}${staffQuery}`;
  const staffMembersRequest: Promise<OptionalResult<{ members?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { members: [] } })
    : optionalFetch<{ members?: Row[] }>("员工授权", "/api/admin/staff", token, { members: [] });
  const productCostsRequest: Promise<OptionalResult<{ product_costs?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { product_costs: [] } })
    : optionalFetch<{ product_costs?: Row[] }>("SKU 成本", "/api/marketing/product-costs?limit=200", token, { product_costs: [] });
  const productLaunchesRequest: Promise<OptionalResult<{ launches?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { launches: [] } })
    : optionalFetch<{ launches?: Row[] }>("产品发布", "/api/admin/vkpi/product-analysis/launches?limit=200", token, { launches: [] });
  const [dashboard, trendResult, productPerformanceResult, projectsResult, linksResult, alertsResult, staffKpiResult, attributionResult, unmatchedResult, costsResult, staffMembersResult, kpiLedgerResult, productCostsResult, productLaunchesResult, kolOptionsResult] = await Promise.all([
    apiFetch<Row>(dashboardPath, {}, token),
    optionalFetch<{ rows?: Row[] }>("趋势", `/api/marketing/dashboard/revenue-trend?window_days=${days}${staffQuery}`, token, { rows: [] }),
    optionalFetch<{ rows?: Row[] }>("产品表现", `/api/marketing/dashboard/product-performance?window_days=${days}${staffQuery}&limit=20`, token, { rows: [] }),
    optionalFetch<{ projects?: Row[]; scope?: Row }>("项目", "/api/marketing/projects?limit=100", token, { projects: [] }),
    optionalFetch<{ links?: Row[] }>("短链", "/api/marketing/links?limit=100", token, { links: [] }),
    optionalFetch<{ alerts?: Row[] }>("提醒", "/api/marketing/alerts?status=open&limit=50", token, { alerts: [] }),
    optionalFetch<Row>("员工 KPI", `/api/marketing/staff-kpi?window=${encodeURIComponent(windowKey)}${staffQuery}`, token, { rows: [] }),
    optionalFetch<{ attributions?: Row[] }>("销售归因", `/api/marketing/attribution?limit=200${staffQuery}`, token, { attributions: [] }),
    optionalFetch<{ items?: Row[] }>("未匹配归因", "/api/marketing/attribution/unmatched?limit=100", token, { items: [] }),
    optionalFetch<{ costs?: Row[] }>("成本", `/api/marketing/costs?limit=200${staffQuery}`, token, { costs: [] }),
    staffMembersRequest,
    optionalFetch<{ entries?: Row[] }>("KPI Ledger", `/api/marketing/kpi-ledger?limit=200${staffQuery}`, token, { entries: [] }),
    productCostsRequest,
    productLaunchesRequest,
    optionalFetch<{ kols?: Row[]; scope?: Row }>("红人列表", `/api/marketing/kols?limit=300${staffQuery}`, token, { kols: [] }),
  ]);
  const failedSections = [trendResult, productPerformanceResult, projectsResult, linksResult, alertsResult, staffKpiResult, attributionResult, unmatchedResult, costsResult, staffMembersResult, kpiLedgerResult, productCostsResult, productLaunchesResult, kolOptionsResult].map((item) => item.failed).filter(Boolean) as string[];
  const summary = (dashboard.summary || {}) as Row;
  const dashboardMetrics = Array.isArray(dashboard.metrics) ? dashboard.metrics as Row[] : [];
  const projectRows = (selfMode ? dashboard.projects as Row[] | undefined : projectsResult.data.projects) || [];
  const linkRows = buildLinks((selfMode ? dashboard.links as Row[] | undefined : linksResult.data.links) || []);
  const alertRows = alertsResult.data.alerts || [];
  const staffRows = Array.isArray(staffKpiResult.data.rows) ? staffKpiResult.data.rows as Row[] : [];
  const rawAttributionRows = (selfMode ? dashboard.attribution as Row[] | undefined : attributionResult.data.attributions) || [];
  const attributionRows = buildAttributions(rawAttributionRows);
  const unmatchedRows = buildAttributions(unmatchedResult.data.items || []);
  const costRows = buildCosts(costsResult.data.costs || []);
  const staffMembers = buildStaffMembers(staffMembersResult.data.members || []);
  const kpiLedger = buildDashboardKpiLedger(kpiLedgerResult.data.entries || []);
  const productCosts = buildProductCosts(productCostsResult.data.product_costs || []);
  const productLaunches = buildProductLaunchOptions(productLaunchesResult.data.launches || []);
  const kolOptions = buildKolOptions(kolOptionsResult.data.kols || []);
  const uiProjects = buildProjects(projectRows, linkRows, attributionRows, costRows);
  const hasData = hasAnyDashboardData(summary, projectRows, linkRows, attributionRows, costRows, alertRows, dashboardMetrics);
  const dataStatus = failedSections.length ? "partial" : hasData ? "live" : "empty";
  const dataNotice = failedSections.length ? `部分数据源暂时不可用：${failedSections.join("、")}。当前页面只显示已成功返回的真实数据。` : hasData ? "当前页面来自真实接口数据。" : "当前周期还没有真实数据。";
  return { ...emptyData(filters), windowDays: days, lastSyncedAt: new Date().toISOString(), dataStatus, dataNotice, metrics: buildDashboardMetrics(dashboardMetrics), revenueTrend: buildDashboardRevenueTrend(trendResult.data.rows || []), funnel: buildDashboardFunnel((dashboard.funnel as Row[] | undefined) || (dashboard.by_stage as Row[] | undefined) || []), staffLeaderboard: buildDashboardLeaderboard(staffRows, (dashboard.staff_leaderboard as Row[] | undefined) || []), productRoi: buildDashboardProductRoi((productPerformanceResult.data.rows || (dashboard.roi_by_project as Row[] | undefined) || [])), platformShare: buildDashboardPlatformShare((dashboard.revenue_by_source as Row[] | undefined) || rawAttributionRows), contentTypePerformance: [{ label: "已抓取播放量", value: uiProjects.reduce((sum, row) => sum + row.views, 0) }, { label: "有效点击", value: linkRows.reduce((sum, row) => sum + row.validClicks, 0) }, { label: "已发布内容", value: uiProjects.filter((row) => ["published", "content_published", "measured", "closed"].includes(row.stage)).length }], alerts: buildDashboardAlerts(alertRows), weeklySummary: buildWeeklySummary(summary, staffRows, alertRows), exportReport: { id: `weekly-${new Date().toISOString().slice(0, 10)}`, title: `周报（${rangeLabel(filters)}）`, generatedAt: "由 Viltrox Marketing 接口数据生成", status: "Ready" }, projects: uiProjects, links: linkRows, attributions: attributionRows, unmatchedAttributions: unmatchedRows, costs: costRows, evidence: emptyEvidence(), staffMembers, kpiLedger, productCosts, productLaunches, kolOptions, selectedKol: buildKol(uiProjects, linkRows, alertRows), scopes: { projects: buildScopeContext(projectsResult.data.scope), kols: buildScopeContext(kolOptionsResult.data.scope) } };
}
