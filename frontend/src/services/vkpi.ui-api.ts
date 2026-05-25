import { apiFetch } from "./http";
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
  VkpiAlertItem,
  VkpiAttributionRow,
  VkpiContactLink,
  VkpiCostRow,
  VkpiDashboardData,
  VkpiDeltaDirection,
  VkpiKolDetail,
  VkpiKolOption,
  VkpiKpiLedgerEntry,
  VkpiLinkRow,
  VkpiMetricCard,
  VkpiPlatform,
  VkpiProductCatalogItem,
  VkpiProductCostRow,
  VkpiProductLaunchOption,
  VkpiProductRoiItem,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiScopeContext,
  VkpiShareItem,
  VkpiStaffMember,
  VkpiTrendPoint,
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

const emptyKol: VkpiKolDetail = {
  id: "none",
  name: "未选择红人",
  handle: "-",
  platform: "Other",
  verified: false,
  subscribersLabel: "0",
  videosLabel: "0",
  engagementLabel: "0%",
  country: "",
  claimOwner: "未分配",
  claimStatus: "暂无项目",
  recentContent: [],
  messages: [],
  shortLink: { slug: "暂无短链", destination: "-", clicks: 0, orders: 0, gmv: 0, roi: 0 },
  followUpNote: "请选择或创建项目，以查看红人详情、消息记录、短链、归因和备注。",
};

function numberValue(value: unknown): number {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}
function parseJsonValue(value: unknown): unknown {
  if (Array.isArray(value) || (value && typeof value === "object")) return value;
  const text = String(value || "").trim();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
function objectValue(value: unknown, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : fallback;
}
function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
function centsToUsd(value: unknown): number { return numberValue(value) / 100; }
function money(value: number): string { return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value); }
function percent(value: number): string { return `${(value * 100).toFixed(1)}%`; }
function compact(value: number): string { return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value); }
function parseContactLinks(value: unknown): VkpiContactLink[] {
  const source = Array.isArray(value)
    ? value
    : (() => {
      const text = String(value || "").trim();
      if (!text) return [];
      try {
        const parsed = JSON.parse(text);
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    })();
  const links: VkpiContactLink[] = [];
  source.forEach((item) => {
    if (typeof item === "string") {
      const text = item.trim();
      if (text) links.push({ label: text.includes("@") && !text.startsWith("http") ? "Email" : "链接", value: text, url: text.startsWith("http") || text.startsWith("mailto:") ? text : undefined });
      return;
    }
    if (!item || typeof item !== "object") return;
    const row = item as Record<string, unknown>;
    const url = String(row.url || row.href || "").trim();
    const valueText = String(row.value || row.label || url || "").trim();
    if (!valueText && !url) return;
    links.push({
      label: String(row.label || (url.includes("mailto:") ? "Email" : "链接")).trim() || "链接",
      value: valueText || url,
      url: url || undefined,
    });
  });
  return links;
}
function dateValue(value: unknown): Date | null {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
function durationLabel(startValue: unknown, endValue?: unknown): string {
  const start = dateValue(startValue);
  if (!start) return "-";
  const end = dateValue(endValue) || new Date();
  const diffMs = Math.max(0, end.getTime() - start.getTime());
  const hours = Math.floor(diffMs / 36e5);
  if (hours < 1) return "刚开始";
  if (hours < 24) return `${hours} 小时`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天`;
  return `${Math.floor(days / 30)} 个月 ${days % 30} 天`;
}
function rangeLabel(filters: VkpiDashboardFilters): string {
  if (filters.startDate && filters.endDate) return `${filters.startDate} - ${filters.endDate}`;
  if (filters.range === "today") return "今天";
  if (filters.range === "30d") return "近 30 天";
  if (filters.range === "mtd") return "本月至今";
  if (filters.range === "qtd") return "本季度至今";
  return "近 7 天";
}
function windowDays(filters: VkpiDashboardFilters): number {
  if (filters.startDate && filters.endDate) {
    const start = dateValue(filters.startDate);
    const end = dateValue(filters.endDate);
    if (start && end) {
      return Math.max(1, Math.min(180, Math.ceil((end.getTime() - start.getTime()) / 86400000) + 1));
    }
  }
  const now = new Date();
  if (filters.range === "today") return 1;
  if (filters.range === "30d") return 30;
  if (filters.range === "mtd") return now.getDate();
  if (filters.range === "qtd") {
    const quarterStartMonth = Math.floor(now.getMonth() / 3) * 3;
    const quarterStart = new Date(now.getFullYear(), quarterStartMonth, 1);
    return Math.max(1, Math.min(180, Math.ceil((now.getTime() - quarterStart.getTime()) / 86400000) + 1));
  }
  return 7;
}
function staffWindow(filters: VkpiDashboardFilters): string {
  if (filters.range === "today") return "today";
  if (filters.range === "30d") return "30d";
  if (filters.range === "mtd" || filters.range === "qtd") return "month";
  return "7d";
}
function platformLabel(value: unknown): VkpiPlatform {
  const raw = String(value || "").trim().toLowerCase();
  if (raw.includes("youtube") || raw === "yt") return "YouTube";
  if (raw.includes("instagram") || raw === "ig") return "Instagram";
  if (raw.includes("tiktok") || raw === "tt") return "TikTok";
  if (raw.includes("bilibili") || raw === "bili") return "Bilibili";
  if (raw.includes("xhs") || raw.includes("xiaohongshu")) return "XHS";
  if (raw.includes("facebook") || raw === "fb") return "Facebook";
  if (raw.includes("reddit")) return "Reddit";
  if (raw === "x" || raw.includes("twitter")) return "X";
  if (raw.includes("threads")) return "Threads";
  if (raw.includes("twitch")) return "Twitch";
  if (raw.includes("pinterest")) return "Pinterest";
  if (raw.includes("vimeo")) return "Vimeo";
  if (raw.includes("discord")) return "Discord";
  if (raw.includes("website") || raw.includes("blog") || raw.includes("site")) return "Website";
  if (raw.includes("weibo")) return "Weibo";
  if (raw.includes("douyin") || raw.includes("抖音")) return "Douyin";
  if (raw.includes("zhihu") || raw.includes("知乎")) return "Zhihu";
  if (raw.includes("linkedin")) return "LinkedIn";
  if (raw.includes("telegram")) return "Telegram";
  if (raw.includes("newsletter")) return "Newsletter";
  if (raw.includes("forum") || raw.includes("community")) return "Forum";
  if (raw.includes("email")) return "Email";
  return "Other";
}
function platformDisplayLabel(value: unknown): string {
  const labels: Record<string, string> = { YouTube: "YouTube", Instagram: "Instagram", TikTok: "TikTok", Bilibili: "Bilibili", XHS: "小红书", Facebook: "Facebook", Reddit: "Reddit", X: "X", Threads: "Threads", Twitch: "Twitch", Pinterest: "Pinterest", Vimeo: "Vimeo", Discord: "Discord", Website: "官网 / 博客", Weibo: "微博", Douyin: "抖音", Zhihu: "知乎", LinkedIn: "LinkedIn", Telegram: "Telegram", Newsletter: "Newsletter", Forum: "论坛", Email: "邮件", Other: "其他" };
  const key = String(value || "Other");
  return labels[key] || key;
}
function stageValue(value: unknown): VkpiProjectStage {
  const raw = String(value || "discovery").trim().toLowerCase();
  if (raw === "negotiating" || raw === "sample_preparing" || raw === "content_due") return "in_discussion";
  if (raw === "posted" || raw === "content_published") return "content_published";
  if (["invited", "discovery", "contacted", "replied", "agreed", "shipped", "received", "published", "measured", "closed", "stalled", "lost", "released", "cancelled"].includes(raw)) return raw as VkpiProjectStage;
  return "discovery";
}
function delta(direction: VkpiDeltaDirection = "flat", label = "接口数据"): Pick<VkpiMetricCard, "deltaDirection" | "deltaLabel"> { return { deltaDirection: direction, deltaLabel: label }; }

function metricMap(rows: Row[]): Map<string, Row> {
  const entries: Array<[string, Row]> = [];
  rows.forEach((row) => {
    const key = String(row.metric_key || row.key || "");
    if (key) entries.push([key, row]);
  });
  return new Map(entries);
}

function metricMeta(metrics: Map<string, Row>, key: string): Pick<VkpiMetricCard, "metricValueId" | "sourceCount" | "drilldownUrl" | "unit" | "calculation"> {
  const row = metrics.get(key);
  if (!row) return {};
  const metricValueId = numberValue(row.metric_value_id || row.metricValueId);
  return {
    metricValueId: metricValueId || undefined,
    sourceCount: numberValue(row.source_count),
    drilldownUrl: String(row.drilldown_url || row.drilldownUrl || ""),
    unit: String(row.unit || ""),
    calculation: (row.calculation && typeof row.calculation === "object" ? row.calculation : undefined) as Record<string, unknown> | undefined,
  };
}

function metricNumber(metrics: Map<string, Row>, key: string): number {
  const row = metrics.get(key);
  return row ? numberValue(row.value_numeric ?? row.value ?? 0) : 0;
}

function metricSourceLabel(metrics: Map<string, Row>, key: string): Pick<VkpiMetricCard, "deltaDirection" | "deltaLabel"> {
  const row = metrics.get(key);
  if (!row) return delta("flat", "未生成快照");
  return delta("flat", `来源 ${numberValue(row.source_count)} 条`);
}

function buildMetrics(rawMetrics: Row[] = []): VkpiMetricCard[] {
  const lineage = metricMap(rawMetrics);
  const views = metricNumber(lineage, "views");
  const sales = centsToUsd(metricNumber(lineage, "gmv"));
  const cost = centsToUsd(metricNumber(lineage, "cost"));
  const newKol = metricNumber(lineage, "new_kol");
  const published = metricNumber(lineage, "published_content");
  const activeProjects = metricNumber(lineage, "active_projects");
  return [
    { key: "views", label: "播放量", value: compact(views), ...metricSourceLabel(lineage, "views"), ...metricMeta(lineage, "views") },
    { key: "cost", label: "成本", value: money(cost), ...metricSourceLabel(lineage, "cost"), ...metricMeta(lineage, "cost") },
    { key: "gmv", label: "本周销售额", value: money(sales), ...metricSourceLabel(lineage, "gmv"), ...metricMeta(lineage, "gmv") },
    { key: "new_kol", label: "新增 KOL", value: compact(newKol), ...metricSourceLabel(lineage, "new_kol"), ...metricMeta(lineage, "new_kol") },
    { key: "published_content", label: "已发布内容", value: compact(published), ...metricSourceLabel(lineage, "published_content"), ...metricMeta(lineage, "published_content") },
    { key: "active_projects", label: "进行中项目", value: compact(activeProjects), ...metricSourceLabel(lineage, "active_projects"), ...metricMeta(lineage, "active_projects") },
  ];
}
function buildTrend(rows: Row[]): VkpiTrendPoint[] {
  if (rows.length) {
    return rows.map((row) => {
      const views = numberValue(row.views || row.total_views || row.play_count || row.impressions);
      const sales = centsToUsd(row.sales_cents || row.gmv_cents || row.revenue_cents);
      const cost = centsToUsd(row.cost_cents);
      return {
        label: String(row.date || row.day || "").slice(5).replace("-", "/") || "-",
        gmv: views,
        netContribution: sales,
        views,
        sales,
        cost,
      };
    });
  }
  return [];
}
function stageLabel(stage: string): string {
  const labels: Record<string, string> = { discovery: "发现", claimed: "已认领", contacted: "已联系", replied: "已回复", agreed: "已合作", shipped: "已发货", received: "已到货", published: "已发布", measured: "已统计", closed: "已关闭" };
  return labels[stage] || stage || "未知";
}
function buildFunnel(raw: Row[]): VkpiDashboardData["funnel"] {
  const priority = ["claimed", "contacted", "replied", "agreed", "shipped", "received", "published", "measured"];
  const byStage = new Map(raw.map((row) => [String(row.stage || row.to_stage || "").toLowerCase(), numberValue(row.count)]));
  const max = Math.max(1, ...priority.map((stage) => byStage.get(stage) || 0));
  return priority.map((stage) => { const value = byStage.get(stage) || 0; return { label: stageLabel(stage), value, rateLabel: percent(value / max) }; });
}
function buildLeaderboard(staffRows: Row[], fallbackRows: Row[]): VkpiDashboardData["staffLeaderboard"] {
  const source = staffRows.length ? staffRows : fallbackRows;
  return source.slice(0, 8).map((row, index) => ({ staffId: row.staff_id || row.id ? String(row.staff_id || row.id) : undefined, name: String(row.staff_name || row.name || row.email || `员工 ${row.staff_id || index + 1}`), gmv: centsToUsd(row.gmv_cents || row.revenue_cents), avatar: String(row.avatar_url || ""), isTop: index === 0 }));
}
function buildProductRoi(rows: Row[]): VkpiProductRoiItem[] {
  const mapped = rows.slice(0, 8).map((row) => {
    const revenue = numberValue(row.sales_cents || row.revenue_cents || row.gmv_cents);
    const cost = numberValue(row.cost_cents);
    return {
      product: String(row.product_name || row.product_sku || row.project_name || "产品"),
      roi: cost ? Number((revenue / cost).toFixed(2)) : 0,
      gmv: centsToUsd(revenue),
      sales: centsToUsd(revenue),
      cost: centsToUsd(cost),
      views: numberValue(row.views || row.total_views || row.play_count || row.impressions),
    };
  });
  return mapped.length ? mapped : [{ product: "暂无项目数据", roi: 0, gmv: 0 }];
}
function buildPlatformShare(rows: Row[]): VkpiShareItem[] {
  const totals = new Map<string, number>();
  rows.forEach((row) => { const key = platformLabel(row.source_platform || row.platform); totals.set(key, (totals.get(key) || 0) + numberValue(row.revenue_cents)); });
  const total = Array.from(totals.values()).reduce((sum, value) => sum + value, 0);
  if (!total) return [{ label: "暂无归因", value: 100 }];
  return Array.from(totals.entries()).map(([label, value]) => ({ label: platformDisplayLabel(label), value: Number(((value / total) * 100).toFixed(1)) }));
}
function buildAlerts(rows: Row[]): VkpiAlertItem[] {
  return rows.slice(0, 6).map((row) => {
    const severityRaw = String(row.severity || "info").toLowerCase();
    const severity = severityRaw === "critical" || severityRaw === "high" ? "danger" : severityRaw === "warning" ? "warning" : "info";
    const metadata = parseJsonObject(row.metadata_json);
    const ruleKey = String(row.rule_key || "");
    const triageGroup = ruleKey.startsWith("comment_intelligence")
      ? "comment_intelligence"
      : String(row.target_type || "").includes("project")
        ? "workflow"
        : "system";
    return {
      id: String(row.id || row.alert_key || `${row.rule_key || "alert"}-${row.created_at || ""}`),
      alertKey: String(row.alert_key || ""),
      label: String(row.title || row.rule_key || "提醒"),
      count: numberValue(metadata.flagged_comments || 1) || 1,
      severity: severity as VkpiAlertItem["severity"],
      description: String(row.body || row.target_type || ""),
      ruleKey,
      targetType: String(row.target_type || ""),
      targetId: row.target_id ? String(row.target_id) : undefined,
      createdAt: String(row.created_at || row.updated_at || ""),
      platform: String(metadata.platform || row.platform || ""),
      triageGroup,
      negativeCount: numberValue(metadata.negative_count),
      criticalCount: numberValue(metadata.critical_count),
      hostileCount: numberValue(metadata.hostile_count),
      flaggedComments: numberValue(metadata.flagged_comments),
      windowDays: numberValue(metadata.window_days),
    };
  });
}
function parseJsonObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}
function buildLinks(rows: Row[]): VkpiLinkRow[] {
  return rows.map((row) => ({ id: String(row.id || row.link_uid || row.slug || ""), slug: String(row.slug || ""), destination: String(row.destination_url || ""), platform: platformLabel(row.platform), projectId: row.project_id ? String(row.project_id) : undefined, projectName: String(row.project_name || ""), kolName: String(row.kol_name || ""), ownerName: String(row.staff_name || row.staff_id || ""), clicks: numberValue(row.click_count), validClicks: numberValue(row.valid_click_count || row.click_count), botClicks: numberValue(row.bot_click_count), orders: numberValue(row.order_count || row.orders), gmv: centsToUsd(row.revenue_cents || row.gmv_cents), status: String(row.status || "unknown"), healthStatus: String(row.health_status || "unknown"), updatedAt: String(row.updated_at || row.created_at || "-") })).filter((row) => row.id || row.slug);
}
function buildAttributions(rows: Row[]): VkpiAttributionRow[] {
  return rows.map((row) => ({ id: String(row.id || row.source_ref || ""), source: String(row.source_platform || row.source || "manual"), sourceRef: String(row.source_ref || ""), projectId: row.project_id ? String(row.project_id) : undefined, linkId: row.link_id ? String(row.link_id) : undefined, kolId: row.kol_id ? String(row.kol_id) : undefined, staffId: row.staff_id ? String(row.staff_id) : undefined, productSku: String(row.product_sku || ""), orderId: row.order_id ? String(row.order_id) : undefined, revenue: centsToUsd(row.revenue_cents), commission: centsToUsd(row.commission_cents), confidence: String(row.confidence || ""), occurredAt: String(row.occurred_at || row.imported_at || row.created_at || "-") })).filter((row) => row.id || row.sourceRef);
}
function buildCosts(rows: Row[]): VkpiCostRow[] {
  return rows.map((row) => ({ id: String(row.id || `${row.project_id || ""}-${row.incurred_at || ""}`), projectId: row.project_id ? String(row.project_id) : undefined, kolId: row.kol_id ? String(row.kol_id) : undefined, staffId: row.staff_id ? String(row.staff_id) : undefined, costType: String(row.cost_type || "other"), amount: centsToUsd(row.amount_cents), currency: String(row.currency || "USD"), status: String(row.status || "actual"), incurredAt: String(row.incurred_at || row.created_at || "-"), sourceRef: String(row.source_ref || ""), note: String(row.note || ""), projectName: String(row.project_name || ""), productSku: String(row.product_sku || ""), kolName: String(row.kol_name || ""), staffName: String(row.staff_name || row.staff_id || ""), approvedByStaffId: row.approved_by_staff_id ? String(row.approved_by_staff_id) : undefined, approvedAt: String(row.approved_at || ""), voidedByStaffId: row.voided_by_staff_id ? String(row.voided_by_staff_id) : undefined, voidedAt: String(row.voided_at || ""), updatedAt: String(row.updated_at || row.created_at || "") })).filter((row) => row.id);
}

function parsePermissions(row: Row): Record<string, unknown> {
  const raw = row.permissions;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  const rawJson = String(row.permissions_json || "").trim();
  if (!rawJson) return {};
  try {
    const parsed = JSON.parse(rawJson);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function buildStaffMembers(rows: Row[]): VkpiStaffMember[] {
  return rows.map((row) => {
    const permissions = parsePermissions(row);
    return {
      id: String(row.id || ""),
      userId: row.user_id ? String(row.user_id) : undefined,
      name: String(row.user_name || row.name || row.email || row.user_email || "未命名员工"),
      email: String(row.user_email || row.email || ""),
      role: String(row.role || "readonly"),
      active: Number(row.active ?? 1) !== 0,
      avatarUrl: String(row.avatar_url || ""),
      employeeCode: String(row.user_handle || row.employee_code || ""),
      vkpiPermission: String(permissions.vkpi || row.vkpi_permission || "none"),
      permissions: Object.fromEntries(Object.entries(permissions).map(([key, value]) => [key, String(value)])),
      verificationStatus: String(row.verification_status || ""),
      deliveryMethod: String(row.delivery_method || ""),
      inviteTokenActive: Boolean(row.invite_token_active),
      lastActiveAt: String(row.last_active_at || row.last_login || ""),
      invitedAt: String(row.invited_at || ""),
      acceptedAt: String(row.accepted_at || ""),
    };
  }).filter((row) => row.id || row.email);
}

function buildKpiLedger(rows: Row[]): VkpiKpiLedgerEntry[] {
  return rows.map((row) => ({
    id: String(row.id || `${row.ledger_date || ""}-${row.source_ref || ""}`),
    ledgerDate: String(row.ledger_date || ""),
    staffId: row.staff_id ? String(row.staff_id) : undefined,
    staffName: String(row.staff_name || ""),
    employeeCode: String(row.employee_code || ""),
    kolId: row.kol_id ? String(row.kol_id) : undefined,
    kolName: String(row.kol_name || ""),
    projectId: row.project_id ? String(row.project_id) : undefined,
    projectName: String(row.project_name || ""),
    productSku: String(row.product_sku || ""),
    metricKey: String(row.metric_key || ""),
    metricLabel: String(row.metric_label || row.metric_key || ""),
    metricValue: numberValue(row.metric_value),
    sourceType: String(row.source_type || ""),
    sourceRef: String(row.source_ref || ""),
    confidence: String(row.confidence || ""),
    createdAt: String(row.created_at || ""),
  })).filter((row) => row.id);
}

function buildProductCosts(rows: Row[]): VkpiProductCostRow[] {
  return rows.map((row) => ({
    id: String(row.id || row.product_sku || ""),
    productSku: String(row.product_sku || ""),
    productName: String(row.product_name || ""),
    unitCost: centsToUsd(row.unit_cost_cents),
    currency: String(row.currency || "USD"),
    active: row.active === true || Number(row.active ?? 1) !== 0,
    note: String(row.note || ""),
    updatedAt: String(row.updated_at || row.created_at || ""),
  })).filter((row) => row.productSku);
}

function buildProductCatalog(rows: Row[]): VkpiProductCatalogItem[] {
  return rows.map((row) => ({
    sku: String(row.sku || row.product_sku || ""),
    categoryMain: String(row.category_main || ""),
    categoryDetail: String(row.category_detail || ""),
    modelName: String(row.model_name || row.product_name || ""),
    marketingName: String(row.marketing_name || ""),
    priceUsd: row.price_usd === null || row.price_usd === undefined || row.price_usd === "" ? null : numberValue(row.price_usd),
    status: String(row.status || ""),
    description: String(row.description || ""),
    sourceFile: String(row.source_file || ""),
    series: String(row.series || ""),
    mount: String(row.mount || ""),
    productUrl: String(row.product_url || ""),
    specs: objectValue(parseJsonValue(row.specs_json), {}),
    fitTags: arrayValue(parseJsonValue(row.fit_tags_json)).map(String),
    sourceUrl: String(row.source_url || ""),
    sourceCheckedAt: String(row.source_checked_at || ""),
    sourceConfidence: numberValue(row.source_confidence),
  })).filter((row) => row.sku);
}

function buildProductLaunchOptions(rows: Row[]): VkpiProductLaunchOption[] {
  return rows.map((row) => ({
    id: String(row.id || row.launch_uid || row.product_sku || ""),
    productSku: String(row.product_sku || row.sku || ""),
    productName: String(row.product_name || row.name || row.product_sku || ""),
    launchName: String(row.name || row.launch_name || row.product_name || row.product_sku || ""),
    status: String(row.status || ""),
    category: String(row.category || ""),
    updatedAt: String(row.updated_at || row.created_at || ""),
  })).filter((row) => row.productSku || row.productName || row.launchName);
}

function buildKolOptions(rows: Row[]): VkpiKolOption[] {
  return rows.map((row) => {
    const name = String(row.media_name || row.owner_name || row.channel_name || row.handle || `KOL ${row.id || ""}`).trim();
    const handle = String(row.channel_name || row.handle || row.channel_url || "").trim();
    const followerCount = numberValue(row.snapshot_follower_count || row.follower_count);
    const contentCount = numberValue(row.snapshot_content_count || row.content_count);
    return {
      id: String(row.id || ""),
      name: name || "未命名 KOL",
      handle: handle ? (handle.startsWith("@") ? handle : `@${handle}`) : "-",
      platform: platformLabel(row.platform),
      avatar: String(row.avatar_url || ""),
      profileUrl: String(row.profile_url || row.channel_url || ""),
      contactEmail: String(row.contact_email || ""),
      contactPhone: String(row.contact_phone || ""),
      contactLinks: parseContactLinks(row.contact_links_json),
      followerLabel: compact(followerCount),
      contentCountLabel: compact(contentCount),
      activeClaimId: row.active_claim_id ? String(row.active_claim_id) : undefined,
      claimStaffId: row.claim_staff_id ? String(row.claim_staff_id) : undefined,
      claimOwner: String(row.claim_staff_name || row.claim_staff_email || row.assigned_staff_id || ""),
      scanStatus: String(row.snapshot_scan_status || row.contact_status || ""),
    };
  }).filter((row) => row.id);
}

function buildScopeContext(row: Row | undefined): VkpiScopeContext | undefined {
  if (!row || typeof row !== "object") return undefined;
  const scopeMode = String(row.scope_mode || "");
  if (!scopeMode) return undefined;
  const idString = (value: unknown): string | undefined => {
    const next = numberValue(value);
    return next ? String(next) : undefined;
  };
  return {
    actorStaffId: idString(row.actor_staff_id),
    requestedStaffId: idString(row.requested_staff_id),
    effectiveStaffId: idString(row.effective_staff_id),
    canViewAll: Boolean(row.can_view_all),
    scopeMode,
    role: String(row.role || ""),
    isOwner: Boolean(row.is_owner),
    domain: String(row.domain || ""),
  };
}

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
function emptyEvidence(): VkpiDashboardData["evidence"] {
  return { gmv: [], cost: [], roi: [], net_contribution: [], views: [], active_projects: [], published_content: [], valid_clicks: [], alerts: [], new_kol: [] };
}

function emptyData(filters: VkpiDashboardFilters = {}): VkpiDashboardData {
  return { rangeLabel: rangeLabel(filters), windowDays: windowDays(filters), dataStatus: "empty", dataNotice: "当前周期还没有真实数据。", metrics: buildMetrics([]), revenueTrend: buildTrend([]), funnel: buildFunnel([]), staffLeaderboard: [], productRoi: [{ product: "暂无项目数据", roi: 0, gmv: 0 }], platformShare: [{ label: "暂无归因", value: 100 }], contentTypePerformance: [{ label: "暂无内容数据", value: 0 }], alerts: [], weeklySummary: "当前还没有生成周报。请在 Shopify、Amazon、短链、成本和项目事件同步后生成。", exportReport: { id: "none", title: "周报尚未生成", generatedAt: "等待数据", status: "Generating" }, projects: [], links: [], attributions: [], unmatchedAttributions: [], costs: [], evidence: emptyEvidence(), staffMembers: [], kpiLedger: [], productCosts: [], productLaunches: [], kolOptions: [], selectedKol: emptyKol, scopes: {} };
}
async function optionalFetch<T>(label: string, path: string, token: string, fallback: T): Promise<OptionalResult<T>> { try { return { data: await apiFetch<T>(path, {}, token) }; } catch { return { data: fallback, failed: label }; } }
function hasAnyDashboardData(summary: Row, projects: Row[], links: VkpiLinkRow[], attributions: VkpiAttributionRow[], costs: VkpiCostRow[], alerts: Row[], rawMetrics: Row[]): boolean {
  const hasMetricSources = rawMetrics.some((row) => numberValue(row.source_count) > 0 || numberValue(row.value_numeric ?? row.value) > 0);
  return Boolean(hasMetricSources || numberValue(summary.revenue_cents || summary.all_gmv_including_company_cents) || numberValue(summary.cost_cents) || numberValue(summary.total_views || summary.views || summary.view_count || summary.play_count || summary.impressions) || projects.length || links.length || attributions.length || costs.length || alerts.length);
}

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
  const kpiLedger = buildKpiLedger(kpiLedgerResult.data.entries || []);
  const productCosts = buildProductCosts(productCostsResult.data.product_costs || []);
  const productLaunches = buildProductLaunchOptions(productLaunchesResult.data.launches || []);
  const kolOptions = buildKolOptions(kolOptionsResult.data.kols || []);
  const uiProjects = buildProjects(projectRows, linkRows, attributionRows, costRows);
  const hasData = hasAnyDashboardData(summary, projectRows, linkRows, attributionRows, costRows, alertRows, dashboardMetrics);
  const dataStatus = failedSections.length ? "partial" : hasData ? "live" : "empty";
  const dataNotice = failedSections.length ? `部分数据源暂时不可用：${failedSections.join("、")}。当前页面只显示已成功返回的真实数据。` : hasData ? "当前页面来自真实接口数据。" : "当前周期还没有真实数据。";
  return { ...emptyData(filters), windowDays: days, lastSyncedAt: new Date().toISOString(), dataStatus, dataNotice, metrics: buildMetrics(dashboardMetrics), revenueTrend: buildTrend(trendResult.data.rows || []), funnel: buildFunnel((dashboard.funnel as Row[] | undefined) || (dashboard.by_stage as Row[] | undefined) || []), staffLeaderboard: buildLeaderboard(staffRows, (dashboard.staff_leaderboard as Row[] | undefined) || []), productRoi: buildProductRoi((productPerformanceResult.data.rows || (dashboard.roi_by_project as Row[] | undefined) || [])), platformShare: buildPlatformShare((dashboard.revenue_by_source as Row[] | undefined) || rawAttributionRows), contentTypePerformance: [{ label: "已抓取播放量", value: uiProjects.reduce((sum, row) => sum + row.views, 0) }, { label: "有效点击", value: linkRows.reduce((sum, row) => sum + row.validClicks, 0) }, { label: "已发布内容", value: uiProjects.filter((row) => ["published", "content_published", "measured", "closed"].includes(row.stage)).length }], alerts: buildAlerts(alertRows), weeklySummary: buildWeeklySummary(summary, staffRows, alertRows), exportReport: { id: `weekly-${new Date().toISOString().slice(0, 10)}`, title: `周报（${rangeLabel(filters)}）`, generatedAt: "由 Viltrox Marketing 接口数据生成", status: "Ready" }, projects: uiProjects, links: linkRows, attributions: attributionRows, unmatchedAttributions: unmatchedRows, costs: costRows, evidence: emptyEvidence(), staffMembers, kpiLedger, productCosts, productLaunches, kolOptions, selectedKol: buildKol(uiProjects, linkRows, alertRows), scopes: { projects: buildScopeContext(projectsResult.data.scope), kols: buildScopeContext(kolOptionsResult.data.scope) } };
}
function buildWeeklySummary(summary: Row, staffRows: Row[], alerts: Row[]): string {
  const sales = centsToUsd(summary.revenue_cents || summary.all_gmv_including_company_cents);
  const cost = centsToUsd(summary.cost_cents);
  const views = numberValue(summary.total_views || summary.views || summary.view_count || summary.play_count || summary.impressions);
  return `当前周期确认销售额为 ${money(sales)}，成本为 ${money(cost)}（镜头成本发货自动计入，员工只登记快递和推广费），已抓取播放量为 ${compact(views)}。当前范围内共有 ${staffRows.length} 名员工数据，还有 ${alerts.length} 条未处理提醒，需要在周报审批前完成复核。`;
}
