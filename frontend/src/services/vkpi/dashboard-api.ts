import { apiFetch, jsonBody } from "../http";
import type { VkpiDashboardData } from "../../components/vkpi/vkpiTypes";
import type { DashboardAccountKpi, DashboardAccountList, DashboardAccountType } from "../../domains/dashboard/accountPicker";
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
  emptyEvidence,
  hasAnyDashboardData,
  rangeLabel,
  staffWindow,
  windowDays,
} from "../../domains/dashboard";
import {
  buildKolOptions,
  buildSelectedKol,
  emptyKol,
} from "../../domains/kol";
import { buildStaffMembers } from "../../domains/settings";
import { buildDashboardProjects } from "../../domains/projects";
import {
  buildProductCosts,
  buildProductLaunchOptions,
} from "../../domains/products";
import {
  buildAttributions,
  buildCosts,
  buildLinks,
} from "../../domains/attribution";

type Row = Record<string, unknown>;

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

export interface VkpiExportPayload extends VkpiDashboardFilters {
  reportType:
    | "weekly"
    | "monthly"
    | "staff"
    | "project"
    | "product_roi"
    | "finance"
    | "vkpi_kol_pool"
    | "favorites"
    | "project_kols";
  format: "pdf" | "csv" | "xlsx";
  // P0-5 KOL 名单导出:project_kols 需带 projectId(后端读 project_id/projectId)。
  projectId?: string | number;
}

type OptionalResult<T> = { data: T; failed?: string };
const DASHBOARD_SLICE_TIMEOUT_MS = 5000;

function emptyDashboardData(filters: VkpiDashboardFilters = {}): VkpiDashboardData {
  return {
    rangeLabel: rangeLabel(filters),
    windowDays: windowDays(filters),
    dataStatus: "empty",
    dataNotice: "当前周期还没有真实数据。",
    metrics: buildDashboardMetrics([]),
    revenueTrend: buildDashboardRevenueTrend([]),
    funnel: buildDashboardFunnel([]),
    staffLeaderboard: [],
    productRoi: [{ product: "暂无项目数据", roi: 0, gmv: 0 }],
    platformShare: [{ label: "暂无归因", value: 100 }],
    contentTypePerformance: [{ label: "暂无内容数据", value: 0 }],
    alerts: [],
    weeklySummary: "当前还没有生成周报。请在 Shopify、Amazon、短链、成本和项目事件同步后生成。",
    exportReport: { id: "none", title: "周报尚未生成", generatedAt: "等待数据", status: "Generating" },
    projects: [],
    starredProjects: [],
    links: [],
    attributions: [],
    unmatchedAttributions: [],
    costs: [],
    evidence: emptyEvidence(),
    staffMembers: [],
    kpiLedger: [],
    productCosts: [],
    productLaunches: [],
    kolOptions: [],
    selectedKol: emptyKol,
    scopes: {},
  };
}

async function optionalFetch<T>(label: string, path: string, token: string, fallback: T): Promise<OptionalResult<T>> {
  try {
    return { data: await apiFetch<T>(path, { timeoutMs: DASHBOARD_SLICE_TIMEOUT_MS }, token) };
  } catch {
    return { data: fallback, failed: label };
  }
}

export async function fetchVkpiDashboardData(
  token: string,
  filters: VkpiDashboardFilters = {},
): Promise<VkpiDashboardData> {
  const days = windowDays(filters);
  const windowKey = staffWindow(filters);
  const selfMode = filters.scope === "self";
  const staffQuery = filters.staffId ? `&staff_id=${encodeURIComponent(filters.staffId)}` : "";
  const dashboardPath = selfMode
    ? `/api/marketing/dashboard/view/employee?window_days=${days}${staffQuery}`
    : `/api/marketing/dashboard?window_days=${days}${staffQuery}`;
  const staffMembersRequest: Promise<OptionalResult<{ members?: Row[]; staff?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { members: [] } })
    : optionalFetch<{ members?: Row[]; staff?: Row[] }>("员工目录", "/api/admin/vkpi/staff-directory", token, { members: [], staff: [] });
  const productCostsRequest: Promise<OptionalResult<{ product_costs?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { product_costs: [] } })
    : optionalFetch<{ product_costs?: Row[] }>("SKU 成本", "/api/marketing/product-costs?limit=200", token, { product_costs: [] });
  const productLaunchesRequest: Promise<OptionalResult<{ launches?: Row[] }>> = selfMode
    ? Promise.resolve({ data: { launches: [] } })
    : optionalFetch<{ launches?: Row[] }>("产品发布", "/api/admin/vkpi/product-analysis/launches?limit=200", token, { launches: [] });
  const [
    dashboardResult,
    trendResult,
    productPerformanceResult,
    projectsResult,
    linksResult,
    alertsResult,
    staffKpiResult,
    attributionResult,
    unmatchedResult,
    costsResult,
    staffMembersResult,
    kpiLedgerResult,
    productCostsResult,
    productLaunchesResult,
    kolOptionsResult,
  ] = await Promise.all([
    optionalFetch<Row>("主控摘要", dashboardPath, token, {}),
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
  const failedSections = [
    dashboardResult,
    trendResult,
    productPerformanceResult,
    projectsResult,
    linksResult,
    alertsResult,
    staffKpiResult,
    attributionResult,
    unmatchedResult,
    costsResult,
    staffMembersResult,
    kpiLedgerResult,
    productCostsResult,
    productLaunchesResult,
    kolOptionsResult,
  ].map((item) => item.failed).filter(Boolean) as string[];
  const dashboard = dashboardResult.data;
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
  const staffMembers = buildStaffMembers(staffMembersResult.data.staff || staffMembersResult.data.members || []);
  const kpiLedger = buildDashboardKpiLedger(kpiLedgerResult.data.entries || []);
  const productCosts = buildProductCosts(productCostsResult.data.product_costs || []);
  const productLaunches = buildProductLaunchOptions(productLaunchesResult.data.launches || []);
  const kolOptions = buildKolOptions(kolOptionsResult.data.kols || []);
  const uiProjects = buildDashboardProjects(projectRows, linkRows, attributionRows, costRows);
  const hasData = hasAnyDashboardData(summary, projectRows, linkRows, attributionRows, costRows, alertRows, dashboardMetrics);
  const dataStatus = failedSections.length ? "partial" : hasData ? "live" : "empty";
  const dataNotice = failedSections.length
    ? `部分数据源暂时不可用：${failedSections.join("、")}。当前页面只显示已成功返回的真实数据。`
    : hasData
      ? "当前页面来自真实接口数据。"
      : "当前周期还没有真实数据。";
  return {
    ...emptyDashboardData(filters),
    windowDays: days,
    lastSyncedAt: new Date().toISOString(),
    dataStatus,
    dataNotice,
    metrics: buildDashboardMetrics(dashboardMetrics),
    revenueTrend: buildDashboardRevenueTrend(trendResult.data.rows || []),
    funnel: buildDashboardFunnel((dashboard.funnel as Row[] | undefined) || (dashboard.by_stage as Row[] | undefined) || []),
    staffLeaderboard: buildDashboardLeaderboard(staffRows, (dashboard.staff_leaderboard as Row[] | undefined) || []),
    productRoi: buildDashboardProductRoi((productPerformanceResult.data.rows || (dashboard.roi_by_project as Row[] | undefined) || [])),
    platformShare: buildDashboardPlatformShare((dashboard.revenue_by_source as Row[] | undefined) || rawAttributionRows),
    contentTypePerformance: [
      { label: "已抓取播放量", value: uiProjects.reduce((sum, row) => sum + row.views, 0) },
      { label: "有效点击", value: linkRows.reduce((sum, row) => sum + row.validClicks, 0) },
      { label: "已发布内容", value: uiProjects.filter((row) => ["published", "content_published", "measured", "closed"].includes(row.stage)).length },
    ],
    alerts: buildDashboardAlerts(alertRows),
    weeklySummary: buildWeeklySummary(summary, staffRows, alertRows),
    exportReport: {
      id: `weekly-${new Date().toISOString().slice(0, 10)}`,
      title: `周报（${rangeLabel(filters)}）`,
      generatedAt: "由 Viltrox Marketing 接口数据生成",
      status: "Ready",
    },
    projects: uiProjects,
    starredProjects: uiProjects.filter((project) => project.isStarred),
    links: linkRows,
    attributions: attributionRows,
    unmatchedAttributions: unmatchedRows,
    costs: costRows,
    evidence: emptyEvidence(),
    staffMembers,
    kpiLedger,
    productCosts,
    productLaunches,
    kolOptions,
    selectedKol: buildSelectedKol(uiProjects, linkRows, alertRows),
    scopes: {
      projects: buildScopeContext(projectsResult.data.scope),
      kols: buildScopeContext(kolOptionsResult.data.scope),
    },
  };
}

export async function fetchDashboardAccountKpi(
  token: string,
  accountType: DashboardAccountType = "all",
  selectedKolIds: string[] = [],
): Promise<DashboardAccountKpi> {
  return apiFetch<DashboardAccountKpi>(
    "/api/admin/dashboard/kpi",
    {
      method: "POST",
      body: jsonBody({
        account_type: accountType,
        selected_kol_ids: selectedKolIds,
      }),
    },
    token,
  );
}

export async function fetchDashboardAccounts(
  token: string,
  accountType: DashboardAccountType = "all",
  options: { page?: number; pageSize?: number; search?: string } = {},
): Promise<DashboardAccountList> {
  const params = new URLSearchParams({
    account_type: accountType,
    page: String(options.page || 1),
    page_size: String(options.pageSize || 8),
  });
  if (options.search?.trim()) {
    params.set("search", options.search.trim());
  }
  return apiFetch<DashboardAccountList>(`/api/admin/dashboard/kols?${params.toString()}`, {}, token);
}

export interface VkpiAgentInboxItem {
  id: string;
  agent_id: string;
  agent_name: string;
  type: string;
  title: string;
  summary: string;
  status: "active" | "warning" | "idle" | string;
  passed: boolean;
  mode?: string;
  generated_at?: string | null;
  last_run_at?: string | null;
  artifact_name?: string;
  source?: string;
  details?: {
    summary?: Record<string, unknown>;
    next_steps?: string[];
  };
}

export interface VkpiAgentsInboxResponse {
  items?: VkpiAgentInboxItem[];
  total?: number;
  limit?: number;
  agent_id?: string | null;
  ops_dir?: string;
  is_real?: boolean;
  source?: string;
}

export async function getDashboardAgentsInbox(
  token: string,
  params: { limit?: number; agentId?: string } = {},
) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 50));
  if (params.agentId) query.set("agent_id", params.agentId);
  return apiFetch<VkpiAgentsInboxResponse>(
    `/api/admin/vkpi/dashboard/agents/inbox?${query.toString()}`,
    {},
    token,
  );
}

export async function generateWeeklyReport(token: string, filters: VkpiDashboardFilters = {}) {
  return apiFetch<{ reportId?: string; report_id?: string; status: string; downloadUrl?: string; download_url?: string }>(
    "/api/marketing/reports/weekly/generate",
    { method: "POST", body: jsonBody(filters) },
    token,
  );
}

export async function exportVkpiReport(token: string, payload: VkpiExportPayload) {
  return apiFetch<{ exportId?: string; export_id?: string; status: string; downloadUrl?: string; download_url?: string }>(
    `/api/marketing/exports/${payload.format}`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function runKpiRollup(token: string, ledgerDate?: string) {
  return apiFetch<Row>(
    "/api/marketing/rollups/run-now",
    { method: "POST", body: jsonBody({ ledger_date: ledgerDate || undefined }) },
    token,
  );
}

export async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}
