import { apiFetch, buildApiUrl, jsonBody } from "./http";
export * from "./vkpi";
import type {
  VkpiAlertItem,
  VkpiAlertDetail,
  VkpiAuditOverview,
  VkpiAttributionRow,
  VkpiContactLink,
  VkpiCostRow,
  VkpiCostDetail,
  VkpiDashboardData,
  VkpiDataQualityResponse,
  VkpiDeltaDirection,
  VkpiKolDetail,
  VkpiKolOption,
  VkpiKolProfile,
  VkpiKolLookupResult,
  VkpiKpiLedgerEntry,
  VkpiLinkDetail,
  VkpiLinkRow,
  VkpiMetricCard,
  VkpiPlatform,
  VkpiProductCatalogItem,
  VkpiProductCostRow,
  VkpiProductLaunchOption,
  VkpiProjectDetail,
  VkpiProductRoiItem,
  VkpiProjectRow,
  VkpiProjectStage,
  VkpiScopeContext,
  VkpiShareItem,
  VkpiStaffProfile,
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

export interface VkpiExportPayload extends VkpiDashboardFilters {
  reportType: "weekly" | "monthly" | "staff" | "project" | "product_roi" | "finance";
  format: "pdf" | "csv" | "xlsx";
}

export interface VkpiKolLookupPayload {
  platform: string;
  handleOrUrl: string;
  createIfMissing?: boolean;
  email?: string;
  country?: string;
  followerCount?: number;
  avgViews?: number;
  contactEmail?: string;
  notes?: string;
  scanAccount?: boolean;
  maxPosts?: number;
  productSku?: string;
}

export interface VkpiKolManualUpdatePayload {
  avatarUrl?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  notes?: string;
  contactLinks?: Array<{ label?: string; value?: string; url?: string }>;
}

export interface VkpiKolAssessmentResponse {
  kol_id?: number;
  score?: number;
  grade?: string;
  method?: string;
  dimensions?: Record<string, { score?: number; source?: string; reason?: string; status?: string }>;
  risk_flags?: Row[];
  recommended_action?: string;
  source_tables?: string[];
}

export interface VkpiKolProductFitResponse {
  kol_id?: number;
  items?: Array<{
    launch_id?: number | null;
    product_sku?: string;
    product_name?: string;
    launch_name?: string;
    score?: number;
    method?: string;
    status?: string;
    reasons?: string[];
    evidence?: string[];
  }>;
}

export interface VkpiKolContactsResponse {
  kol_id?: number;
  contacts?: Array<{
    id?: string;
    contact_type?: string;
    contact_value?: string;
    layer?: number;
    source?: string;
    confidence?: number;
    evidence?: string;
    verified?: boolean;
    status?: string;
  }>;
  summary?: Row;
}

export interface VkpiAddKolContactPayload {
  contactType: string;
  contactValue: string;
  evidence?: string;
  layer?: number;
  source?: string;
}

export interface VkpiNaturalKolSearchResponse {
  query?: string;
  parsed?: Row;
  items?: Row[];
  method?: string;
  degraded?: boolean;
  notes?: string[];
}

export interface VkpiCreateProjectPayload {
  projectName: string;
  kolId?: string;
  productSku?: string;
  productName?: string;
  productSkus?: string[];
  products?: Array<{ productSku: string; productName?: string }>;
  platform?: string;
  marketplace?: string;
  note?: string;
}

export interface VkpiUpdateProjectPayload {
  projectName?: string;
  productSku?: string;
  productName?: string;
  products?: Array<{ productSku: string; productName?: string }>;
  platform?: string;
  marketplace?: string;
  priority?: string;
  shopifyLink?: string;
  targetPostDate?: string;
  dueAt?: string;
  note?: string;
}

export interface VkpiStagePayload {
  toStage: VkpiProjectStage;
  note?: string;
  trackingNumber?: string;
  sampleStatus?: string;
  sourceRefType?: string;
  sourceRefId?: string;
}

export interface VkpiCostPayload {
  projectId: string;
  costType: string;
  amountUsd: number;
  note?: string;
  sourceRef?: string;
}

export interface VkpiCreateLinkPayload {
  destinationUrl: string;
  slug?: string;
  projectId?: string;
  kolId?: string;
  platform?: string;
  productSku?: string;
  campaignName?: string;
  utmSource?: string;
  utmMedium?: string;
  utmCampaign?: string;
  utmContent?: string;
}

export interface VkpiInviteStaffPayload {
  email: string;
  name?: string;
  role: string;
  vkpiPermission: "none" | "read" | "write";
}

export type VkpiPermissionLevel = "none" | "read" | "write" | "admin";

export interface VkpiStaffInviteCapabilities {
  email_available?: boolean;
  external_emails_allowed?: boolean;
  allowed_domains?: string[];
  token_ttl_hours?: number;
  manual_activation_link_available?: boolean;
  delivery_methods?: string[];
  site_url_configured?: boolean;
}

export interface VkpiStaffActivationLinkResponse {
  staff_id?: number;
  user_id?: number;
  email?: string;
  full_name?: string;
  role?: string;
  activation_url?: string;
  token_hint?: string;
  expires_at?: string;
  expires_in_hours?: number;
  delivery_method?: string;
}

export interface VkpiStaffPasswordResetLinkResponse {
  ok?: boolean;
  staff_id?: number;
  user_id?: number;
  email?: string;
  reset_url?: string;
  token_hint?: string;
  expires_at?: string;
  expires_in_hours?: number;
  email_sent?: boolean;
  delivery_method?: string;
}

export type AsyncTaskStatus =
  | "queued"
  | "running"
  | "processing"
  | "retrying"
  | "done"
  | "failed"
  | "cancelled"
  | "timeout"
  | "partial_done"
  | "prefilter_rejected";

export interface AsyncTask {
  task_id: string;
  task_type: string;
  status: AsyncTaskStatus;
  progress_pct?: number;
  progress_text?: string;
  result_json?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

export const TERMINAL_STATUSES = [
  "done",
  "failed",
  "cancelled",
  "timeout",
  "partial_done",
  "prefilter_rejected",
] as const;

export interface VkpiAttributionPayload {
  sourcePlatform: "shopify" | "amazon" | "manual" | "custom";
  sourceRef: string;
  projectId?: string;
  linkId?: string;
  productSku?: string;
  orderId?: string;
  revenueUsd: number;
  commissionUsd?: number;
  confidence?: string;
  occurredAt?: string;
}

export interface VkpiAmazonImportPayload {
  projectId?: string;
  amazonTag?: string;
  asin?: string;
  marketplace?: string;
  reportDate?: string;
  rows: Array<Record<string, unknown>>;
}

export interface VkpiProductCostPayload {
  productSku: string;
  productName?: string;
  unitCostUsd: number;
  currency?: string;
  active?: boolean;
  note?: string;
}

export interface VkpiCommentIntelligenceOverview {
  days: number;
  health: "ok" | "degraded" | "attention" | string;
  runs: {
    total: number;
    by_status?: Record<string, number>;
    success_rate?: number | null;
    recent?: Row[];
    recent_failures?: Row[];
  };
  coverage: {
    comments_total: number;
    comments_with_sentiment: number;
    comments_with_pillar: number;
    sentiment_coverage?: number | null;
    comment_pillar_coverage?: number | null;
    pending_sentiment: number;
    pending_comment_pillar_links: number;
    posts_total: number;
    posts_with_primary_pillar: number;
    post_pillar_coverage?: number | null;
  };
  distributions?: {
    sentiment?: Array<{ label?: string; count?: number }>;
    emotion?: Array<{ label?: string; count?: number }>;
    brand_attitude?: Array<{ label?: string; count?: number }>;
    pillars?: Array<{
      pillar_key?: string;
      display_name?: string;
      layer?: number;
      count?: number;
      primary_count?: number;
    }>;
  };
}

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

export async function lookupKol(token: string, payload: VkpiKolLookupPayload): Promise<VkpiKolLookupResult> {
  return apiFetch<VkpiKolLookupResult>("/api/marketing/kols/lookup", { method: "POST", body: jsonBody({ platform: payload.platform, handle_or_url: payload.handleOrUrl, url: payload.handleOrUrl, create_if_missing: Boolean(payload.createIfMissing), email: payload.email, contact_email: payload.contactEmail || payload.email, country: payload.country, follower_count: payload.followerCount, avg_views: payload.avgViews, notes: payload.notes, scan_account: Boolean(payload.scanAccount), max_posts: payload.maxPosts || 24, product_sku: payload.productSku }) }, token);
}
export async function updateMarketingKol(token: string, kolId: string, payload: VkpiKolManualUpdatePayload) {
  return apiFetch<{ kol?: Row }>(`/api/marketing/kols/${encodeURIComponent(kolId)}`, { method: "PATCH", body: jsonBody({ avatar_url: payload.avatarUrl, profile_url: payload.profileUrl, contact_email: payload.contactEmail, contact_phone: payload.contactPhone, notes: payload.notes, contact_links: payload.contactLinks }) }, token);
}
export async function listMarketingKols(token: string, params: { search?: string; platform?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.search) query.set("search", params.search);
  if (params.platform) query.set("platform", params.platform);
  return apiFetch<{ kols?: Row[]; scope?: Row }>(`/api/marketing/kols?${query.toString()}`, {}, token);
}
export async function searchMarketingKolsNatural(token: string, payload: { query: string; platform?: string; limit?: number }) {
  return apiFetch<VkpiNaturalKolSearchResponse>("/api/marketing/kol/search/natural", {
    method: "POST",
    body: jsonBody({
      query: payload.query,
      platform: payload.platform,
      limit: payload.limit || 100,
    }),
  }, token);
}
export async function scanKolAccount(token: string, kolId: string, maxPosts = 24) {
  const scan = await apiFetch<Record<string, unknown>>(`/api/marketing/kols/${encodeURIComponent(kolId)}/scan-account`, {
    method: "POST",
    body: jsonBody({ max_posts: maxPosts }),
    timeoutMs: 180000,
  }, token);
  if (numberValue(scan.content_count) > 0) {
    const analysis = await apiFetch<Record<string, unknown>>(`/api/marketing/kols/${encodeURIComponent(kolId)}/analyze-account`, {
      method: "POST",
      body: jsonBody({}),
      timeoutMs: 120000,
    }, token);
    return { scan, analysis };
  }
  return { scan };
}

export async function analyzeDataAnalysisPostUrl(
  token: string,
  payload: { url: string; platform?: string; creatorHandle?: string },
) {
  return apiFetch<Record<string, unknown>>("/api/admin/kol/tools/analyze-url", {
    method: "POST",
    body: jsonBody({
      url: payload.url,
      platform: payload.platform || "",
      creator_handle: payload.creatorHandle || "",
    }),
    timeoutMs: 300000,
  }, token);
}

export async function claimKol(token: string, kolId: string, expiresDays = 14) { return apiFetch<Record<string, unknown>>(`/api/marketing/kols/${encodeURIComponent(kolId)}/claim`, { method: "POST", body: jsonBody({ expires_days: expiresDays }) }, token); }
export async function releaseKolClaim(token: string, claimId: string, reason = "employee_unfollow") { return apiFetch<Record<string, unknown>>(`/api/marketing/claims/${encodeURIComponent(claimId)}/release`, { method: "POST", body: jsonBody({ reason }) }, token); }
export async function createProject(token: string, payload: VkpiCreateProjectPayload) {
  return apiFetch<Record<string, unknown>>("/api/marketing/projects", {
    method: "POST",
    body: jsonBody({
      project_name: payload.projectName,
      kol_id: payload.kolId ? Number(payload.kolId) : undefined,
      product_sku: payload.productSku,
      product_name: payload.productName,
      product_skus: payload.productSkus,
      products: payload.products,
      platform: payload.platform,
      marketplace: payload.marketplace,
      note: payload.note,
    }),
  }, token);
}
export async function updateProject(token: string, projectId: string, payload: VkpiUpdateProjectPayload) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    body: jsonBody({
      project_name: payload.projectName,
      product_sku: payload.productSku,
      product_name: payload.productName,
      products: payload.products,
      platform: payload.platform,
      marketplace: payload.marketplace,
      priority: payload.priority,
      shopify_link: payload.shopifyLink,
      target_post_date: payload.targetPostDate,
      due_at: payload.dueAt,
      note: payload.note,
    }),
  }, token);
}
export async function getProjectDetail(token: string, projectId: string) { return apiFetch<VkpiProjectDetail>(`/api/marketing/projects/${encodeURIComponent(projectId)}`, {}, token); }
export async function getKolProfile(token: string, kolId: string) { return apiFetch<VkpiKolProfile>(`/api/marketing/kols/${encodeURIComponent(kolId)}/profile`, {}, token); }
export async function getKolAssessment(token: string, kolId: string) {
  return apiFetch<VkpiKolAssessmentResponse>(`/api/marketing/kols/${encodeURIComponent(kolId)}/assessment`, {}, token);
}
export async function getKolProductFit(token: string, kolId: string, limit = 5) {
  return apiFetch<VkpiKolProductFitResponse>(`/api/marketing/kols/${encodeURIComponent(kolId)}/product-fit?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function listKolContacts(token: string, kolId: string, includeWrong = false) {
  return apiFetch<VkpiKolContactsResponse>(`/api/marketing/kols/${encodeURIComponent(kolId)}/contacts?include_wrong=${includeWrong ? "true" : "false"}`, {}, token);
}
export async function addKolContact(token: string, kolId: string, payload: VkpiAddKolContactPayload) {
  return apiFetch<VkpiKolContactsResponse>(`/api/marketing/kols/${encodeURIComponent(kolId)}/contacts`, {
    method: "POST",
    body: jsonBody({
      contact_type: payload.contactType,
      contact_value: payload.contactValue,
      evidence: payload.evidence,
      layer: payload.layer,
      source: payload.source,
    }),
  }, token);
}
export async function getKolPosts(token: string, kolId: string, params: { limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 100),
    offset: String(params.offset || 0),
  });
  return apiFetch<{ items?: Row[]; page?: Row }>(`/api/marketing/kols/${encodeURIComponent(kolId)}/posts?${query.toString()}`, {}, token);
}
export async function getKolComments(token: string, kolId: string, params: { limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 100),
    offset: String(params.offset || 0),
  });
  return apiFetch<{ items?: Row[]; page?: Row }>(`/api/marketing/kols/${encodeURIComponent(kolId)}/comments?${query.toString()}`, {}, token);
}
export async function getStaffProfile(token: string, staffId: string, window = "month") { return apiFetch<VkpiStaffProfile>(`/api/marketing/staff/${encodeURIComponent(staffId)}/profile?window=${encodeURIComponent(window)}&limit=120`, {}, token); }
export async function transitionProjectStage(token: string, projectId: string, payload: VkpiStagePayload) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}/stage`, { method: "POST", body: jsonBody({ to_stage: payload.toStage, note: payload.note, tracking_number: payload.trackingNumber, sample_status: payload.sampleStatus, source_ref_type: payload.sourceRefType, source_ref_id: payload.sourceRefId }) }, token); }
export async function deleteProject(token: string, projectId: string, reason = "前端删除项目") { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}`, { method: "DELETE", body: jsonBody({ reason }) }, token); }
export async function addProjectCost(token: string, payload: VkpiCostPayload) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(payload.projectId)}/costs`, { method: "POST", body: jsonBody({ cost_type: payload.costType, amount_usd: payload.amountUsd, note: payload.note, source_ref: payload.sourceRef }) }, token); }
export async function getMarketingCostDetail(token: string, costId: string) { return apiFetch<VkpiCostDetail>(`/api/marketing/costs/${encodeURIComponent(costId)}`, {}, token); }
export async function updateMarketingCost(token: string, costId: string, payload: Partial<VkpiCostPayload>) { return apiFetch<Record<string, unknown>>(`/api/marketing/costs/${encodeURIComponent(costId)}`, { method: "PATCH", body: jsonBody({ cost_type: payload.costType, amount_usd: payload.amountUsd, note: payload.note, source_ref: payload.sourceRef }) }, token); }
export async function approveMarketingCost(token: string, costId: string, note?: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/costs/${encodeURIComponent(costId)}/approve`, { method: "POST", body: jsonBody({ note }) }, token); }
export async function voidMarketingCost(token: string, costId: string, reason?: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/costs/${encodeURIComponent(costId)}/void`, { method: "POST", body: jsonBody({ reason }) }, token); }
export async function addProjectMessage(token: string, projectId: string, payload: Record<string, unknown>) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}/messages`, { method: "POST", body: jsonBody(payload) }, token); }
export async function addProjectContent(token: string, projectId: string, payload: Record<string, unknown>) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}/content`, { method: "POST", body: jsonBody(payload) }, token); }
export async function upsertProjectTerms(token: string, projectId: string, payload: Record<string, unknown>) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}/terms`, { method: "POST", body: jsonBody(payload) }, token); }
export async function addProjectShipment(token: string, projectId: string, payload: Record<string, unknown>) { return apiFetch<Record<string, unknown>>(`/api/marketing/projects/${encodeURIComponent(projectId)}/shipments`, { method: "POST", body: jsonBody(payload) }, token); }
export async function listMarketingMessages(token: string, params: { projectId?: string; kolId?: string; staffId?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.kolId) query.set("kol_id", params.kolId);
  if (params.staffId) query.set("staff_id", params.staffId);
  return apiFetch<{ messages?: Row[]; count?: number }>(`/api/marketing/messages?${query.toString()}`, {}, token);
}
export async function createMarketingMessage(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/messages", { method: "POST", body: jsonBody(payload) }, token);
}
export async function getMarketingMessage(token: string, messageId: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/messages/${encodeURIComponent(messageId)}`, {}, token);
}
export async function addMarketingMessageAttachment(token: string, messageId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/messages/${encodeURIComponent(messageId)}/attachments`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function uploadMarketingEvidenceFile(token: string, file: File, payload: { entityType?: string; entityId?: string; purpose?: string } = {}) {
  const form = new FormData();
  form.append("file", file);
  form.append("entity_type", payload.entityType || "manual");
  form.append("entity_id", payload.entityId || "");
  form.append("purpose", payload.purpose || "note");
  return apiFetch<Record<string, unknown>>("/api/marketing/evidence/uploads", { method: "POST", body: form }, token);
}
export async function listMarketingContent(token: string, params: { projectId?: string; kolId?: string; staffId?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.kolId) query.set("kol_id", params.kolId);
  if (params.staffId) query.set("staff_id", params.staffId);
  return apiFetch<{ content?: Row[]; count?: number }>(`/api/marketing/content?${query.toString()}`, {}, token);
}
export async function createMarketingContent(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/content", { method: "POST", body: jsonBody(payload) }, token);
}
export async function getMarketingContent(token: string, postId: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/content/${encodeURIComponent(postId)}`, {}, token);
}
export async function addMarketingContentAsset(token: string, postId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/content/${encodeURIComponent(postId)}/assets`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function listMarketingTerms(token: string, params: { projectId?: string; kolId?: string; staffId?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.kolId) query.set("kol_id", params.kolId);
  if (params.staffId) query.set("staff_id", params.staffId);
  return apiFetch<{ terms?: Row[]; count?: number }>(`/api/marketing/terms?${query.toString()}`, {}, token);
}
export async function upsertMarketingTerms(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/terms", { method: "POST", body: jsonBody(payload) }, token);
}
export async function getMarketingTerms(token: string, termsId: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/terms/${encodeURIComponent(termsId)}`, {}, token);
}
export async function addMarketingDeliverable(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/deliverables", { method: "POST", body: jsonBody(payload) }, token);
}
export async function updateMarketingDeliverable(token: string, deliverableId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/deliverables/${encodeURIComponent(deliverableId)}`, { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function listMarketingShipments(token: string, params: { projectId?: string; kolId?: string; staffId?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.kolId) query.set("kol_id", params.kolId);
  if (params.staffId) query.set("staff_id", params.staffId);
  return apiFetch<{ shipments?: Row[]; count?: number }>(`/api/marketing/shipments?${query.toString()}`, {}, token);
}
export async function createMarketingShipment(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/shipments", { method: "POST", body: jsonBody(payload) }, token);
}
export async function getMarketingShipment(token: string, shipmentId: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/shipments/${encodeURIComponent(shipmentId)}`, {}, token);
}
export async function updateMarketingShipment(token: string, shipmentId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/shipments/${encodeURIComponent(shipmentId)}`, { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function receiveMarketingShipment(token: string, shipmentId: string, payload: Record<string, unknown> = {}) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/shipments/${encodeURIComponent(shipmentId)}/receive`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function listMarketingSamples(token: string, params: { projectId?: string; kolId?: string; staffId?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.projectId) query.set("project_id", params.projectId);
  if (params.kolId) query.set("kol_id", params.kolId);
  if (params.staffId) query.set("staff_id", params.staffId);
  return apiFetch<{ samples?: Row[]; count?: number }>(`/api/marketing/samples?${query.toString()}`, {}, token);
}
export async function upsertProductCost(token: string, payload: VkpiProductCostPayload) { return apiFetch<Record<string, unknown>>("/api/marketing/product-costs", { method: "POST", body: jsonBody({ product_sku: payload.productSku, product_name: payload.productName, unit_cost_usd: payload.unitCostUsd, currency: payload.currency || "USD", active: payload.active ?? true, note: payload.note }) }, token); }
export async function listProductCatalog(token: string, params: { categories?: string[]; status?: string; query?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 300) });
  if (params.categories?.length) query.set("categories", params.categories.join(","));
  if (params.status) query.set("status", params.status);
  if (params.query) query.set("query", params.query);
  const response = await apiFetch<{ products?: Row[]; summary?: Row[]; count?: number }>(`/api/marketing/product-catalog?${query.toString()}`, {}, token);
  return {
    products: buildProductCatalog(response.products || []),
    summary: response.summary || [],
    count: Number(response.count ?? 0),
  };
}
export async function createMarketingLink(token: string, payload: VkpiCreateLinkPayload) { return apiFetch<Record<string, unknown>>("/api/marketing/links", { method: "POST", body: jsonBody({ destination_url: payload.destinationUrl, slug: payload.slug, project_id: payload.projectId ? Number(payload.projectId) : undefined, kol_id: payload.kolId ? Number(payload.kolId) : undefined, platform: payload.platform, product_sku: payload.productSku, campaign_name: payload.campaignName, utm_source: payload.utmSource, utm_medium: payload.utmMedium, utm_campaign: payload.utmCampaign, utm_content: payload.utmContent }) }, token); }
export async function getMarketingLinkDetail(token: string, linkId: string) { return apiFetch<VkpiLinkDetail>(`/api/marketing/links/${encodeURIComponent(linkId)}`, {}, token); }
export async function getMarketingLinkClicks(token: string, linkId: string, limit = 100) { return apiFetch<Record<string, unknown>>(`/api/marketing/links/${encodeURIComponent(linkId)}/clicks?limit=${encodeURIComponent(String(limit))}`, {}, token); }
export async function getMarketingLinkOrders(token: string, linkId: string, limit = 100) { return apiFetch<Record<string, unknown>>(`/api/marketing/links/${encodeURIComponent(linkId)}/orders?limit=${encodeURIComponent(String(limit))}`, {}, token); }
export async function pauseMarketingLink(token: string, linkId: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/links/${encodeURIComponent(linkId)}/pause`, { method: "POST", body: jsonBody({}) }, token); }
export async function archiveMarketingLink(token: string, linkId: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/links/${encodeURIComponent(linkId)}/archive`, { method: "POST", body: jsonBody({}) }, token); }
export async function healthCheckMarketingLink(token: string, linkId: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/links/${encodeURIComponent(linkId)}/health-check`, { method: "POST", body: jsonBody({}) }, token); }
export async function getStaffInviteCapabilities(token: string) {
  return apiFetch<VkpiStaffInviteCapabilities>("/api/admin/staff/invite/capabilities", {}, token);
}
export async function inviteMarketingStaff(token: string, payload: VkpiInviteStaffPayload) { return apiFetch<Record<string, unknown>>("/api/admin/staff/invite", { method: "POST", body: jsonBody({ email: payload.email, name: payload.name, full_name: payload.name, role: payload.role, permissions: { vkpi: payload.vkpiPermission } }) }, token); }
export async function createStaffActivationLink(token: string, payload: VkpiInviteStaffPayload) {
  return apiFetch<VkpiStaffActivationLinkResponse>("/api/admin/staff/invite/activation-link", {
    method: "POST",
    body: jsonBody({
      email: payload.email,
      name: payload.name,
      full_name: payload.name,
      role: payload.role,
      permissions: { vkpi: payload.vkpiPermission },
    }),
  }, token);
}
export async function createExistingStaffActivationLink(token: string, staffId: string) {
  return apiFetch<VkpiStaffActivationLinkResponse>(`/api/admin/staff/${encodeURIComponent(staffId)}/activation-link`, {
    method: "POST",
    body: jsonBody({}),
  }, token);
}
export async function acceptStaffInvite(inviteToken: string, password: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/staff/accept-invite", {
    method: "POST",
    body: jsonBody({ invite_token: inviteToken, password }),
  });
}
export async function updateStaffMarketingPermission(token: string, staffId: string, permission: "none" | "read" | "write") { return apiFetch<Record<string, unknown>>(`/api/admin/staff/${encodeURIComponent(staffId)}/permissions`, { method: "POST", body: jsonBody({ permissions: { vkpi: permission } }) }, token); }
export async function updateStaffPermissions(token: string, staffId: string, permissions: Record<string, VkpiPermissionLevel | string>) {
  return apiFetch<Record<string, unknown>>(`/api/admin/staff/${encodeURIComponent(staffId)}/permissions`, {
    method: "POST",
    body: jsonBody({ permissions }),
  }, token);
}
export async function createStaffPasswordResetLink(token: string, staffId: string) {
  return apiFetch<VkpiStaffPasswordResetLinkResponse>(`/api/admin/staff/${encodeURIComponent(staffId)}/reset-password-link`, {
    method: "POST",
    body: jsonBody({}),
  }, token);
}
export async function getRbacStatus(token: string, includeStaff = false) {
  const suffix = includeStaff ? "?include_staff=true" : "";
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/access/rbac-status${suffix}`, {}, token);
}
export async function listProviderStatuses(token: string) {
  return apiFetch<{ providers?: Row[]; full_key_readable?: boolean }>("/api/marketing/settings/providers", {}, token);
}
export async function probeProviderStatus(token: string, provider: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/settings/providers/${encodeURIComponent(provider)}/probe`, { method: "POST", body: jsonBody({}) }, token);
}
export async function runKpiRollup(token: string, ledgerDate?: string) { return apiFetch<Record<string, unknown>>("/api/marketing/rollups/run-now", { method: "POST", body: jsonBody({ ledger_date: ledgerDate || undefined }) }, token); }
export async function createSalesAttribution(token: string, payload: VkpiAttributionPayload) { return apiFetch<Record<string, unknown>>("/api/marketing/attribution", { method: "POST", body: jsonBody({ source_platform: payload.sourcePlatform, source_ref: payload.sourceRef, project_id: payload.projectId ? Number(payload.projectId) : undefined, link_id: payload.linkId ? Number(payload.linkId) : undefined, product_sku: payload.productSku, order_id: payload.orderId ? Number(payload.orderId) : undefined, revenue_usd: payload.revenueUsd, commission_usd: payload.commissionUsd, confidence: payload.confidence || "confirmed", occurred_at: payload.occurredAt }) }, token); }
export async function getShopifyOrderEvidence(token: string, orderRef: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/shopify/orders/${encodeURIComponent(orderRef)}`, {}, token); }
export async function runShopifySync(token: string, payload: Record<string, unknown> = {}) { return apiFetch<Record<string, unknown>>("/api/marketing/shopify/sync", { method: "POST", body: jsonBody(payload) }, token); }
export async function runShopifyBackfill(token: string, payload: Record<string, unknown> = {}) { return apiFetch<Record<string, unknown>>("/api/marketing/shopify/backfill", { method: "POST", body: jsonBody(payload) }, token); }
export async function getMarketingAlertDetail(token: string, alertId: string) { return apiFetch<VkpiAlertDetail>(`/api/marketing/alerts/${encodeURIComponent(alertId)}`, {}, token); }
export async function resolveMarketingAlert(token: string, alertId: string) { return apiFetch<Record<string, unknown>>(`/api/marketing/alerts/${encodeURIComponent(alertId)}/resolve`, { method: "POST", body: jsonBody({}) }, token); }
export async function importAmazonAttributionRows(token: string, payload: VkpiAmazonImportPayload) { return apiFetch<Record<string, unknown>>("/api/marketing/attribution/amazon/import", { method: "POST", body: jsonBody({ project_id: payload.projectId ? Number(payload.projectId) : undefined, amazon_tag: payload.amazonTag, asin: payload.asin, marketplace: payload.marketplace || "US", report_date: payload.reportDate, rows: payload.rows }) }, token); }
export async function listAmazonAttributions(token: string, options: { staffId?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.staffId) params.set("staff_id", options.staffId);
  return apiFetch<{ attributions?: Row[] }>(`/api/marketing/attribution/amazon?${params.toString()}`, {}, token);
}
export async function getAmazonAttributionSummary(token: string, options: { staffId?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.staffId) params.set("staff_id", options.staffId);
  return apiFetch<{ items?: Row[]; totals?: Row }>(`/api/marketing/attribution/amazon/summary?${params.toString()}`, {}, token);
}
export async function uploadAmazonAttributionReport(token: string, payload: Omit<VkpiAmazonImportPayload, "rows"> & { file: File }) {
  const form = new FormData();
  form.set("file", payload.file);
  if (payload.projectId) form.set("project_id", payload.projectId);
  if (payload.amazonTag) form.set("amazon_tag", payload.amazonTag);
  if (payload.asin) form.set("asin", payload.asin);
  form.set("marketplace", payload.marketplace || "US");
  if (payload.reportDate) form.set("report_date", payload.reportDate);
  return apiFetch<Record<string, unknown>>("/api/marketing/attribution/amazon/upload", { method: "POST", body: form }, token);
}
export async function getDataQuality(token: string, limit = 100) { return apiFetch<VkpiDataQualityResponse>(`/api/marketing/data-quality?limit=${encodeURIComponent(String(limit))}`, {}, token); }
export type VkpiDataQualityAction = "resolve" | "ignore" | "assign" | "rerun" | "evidence" | "reopen";
export async function actOnDataQualityIssue(token: string, issueId: string, action: VkpiDataQualityAction, reason?: string, metadata?: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/data-quality/${encodeURIComponent(issueId)}/${action}`, { method: "POST", body: jsonBody({ reason, metadata }) }, token);
}
export async function getAuditOverview(token: string, options: { limit?: number; eventCategory?: string; staffId?: string; days?: number } = {}): Promise<VkpiAuditOverview> {
  const params = new URLSearchParams({
    limit: String(options.limit || 100),
    days: String(options.days || 7),
  });
  if (options.eventCategory) params.set("event_category", options.eventCategory);
  if (options.staffId) params.set("staff_id", options.staffId);
  const response = await apiFetch<Row>(`/api/admin/vkpi/audit/overview?${params.toString()}`, {}, token);
  const summary = (response.summary || {}) as Row;
  const events = Array.isArray(response.events) ? response.events as Row[] : [];
  return {
    summary: {
      days: numberValue(summary.days || options.days || 7),
      sensitiveAccessCount: numberValue(summary.sensitive_access_count),
      exportCount: numberValue(summary.export_count),
      settingsChangeCount: numberValue(summary.settings_change_count),
      attributionAdjustmentCount: numberValue(summary.attribution_adjustment_count),
      businessEventCount: numberValue(summary.business_event_count),
      eventCount: numberValue(summary.event_count || events.length),
      byCategory: (Array.isArray(summary.by_category) ? summary.by_category as Row[] : []).map((row) => ({ label: String(row.label || ""), count: numberValue(row.count) })),
      byAction: (Array.isArray(summary.by_action) ? summary.by_action as Row[] : []).map((row) => ({ label: String(row.label || ""), count: numberValue(row.count) })),
    },
    events: events.map((row) => ({
      id: String(row.id || `${row.event_category || "event"}-${row.event_id || ""}`),
      eventId: row.event_id as string | number | undefined,
      eventCategory: String(row.event_category || "business"),
      action: String(row.action || ""),
      staffId: row.staff_id ? String(row.staff_id) : undefined,
      staffName: String(row.staff_name || row.staff_email || row.staff_id || "-"),
      staffEmail: String(row.staff_email || ""),
      targetType: String(row.target_type || ""),
      targetId: String(row.target_id || ""),
      detail: String(row.detail || ""),
      ip: String(row.ip || ""),
      userAgent: String(row.user_agent || ""),
      occurredAt: String(row.occurred_at || ""),
      metadata: (row.metadata && typeof row.metadata === "object" ? row.metadata : {}) as Record<string, unknown>,
    })),
  };
}
export async function generateWeeklyReport(token: string, filters: VkpiDashboardFilters = {}) { return apiFetch<{ reportId?: string; report_id?: string; status: string; downloadUrl?: string; download_url?: string }>("/api/marketing/reports/weekly/generate", { method: "POST", body: jsonBody(filters) }, token); }
export async function exportVkpiReport(token: string, payload: VkpiExportPayload) { return apiFetch<{ exportId?: string; export_id?: string; status: string; downloadUrl?: string; download_url?: string }>(`/api/marketing/exports/${payload.format}`, { method: "POST", body: jsonBody(payload) }, token); }
export async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export async function runProductCompare(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/analytics/compare", { method: "POST", body: jsonBody(payload), timeoutMs: 180000 }, token);
}
export async function runProductMonitor(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/analytics/monitor", { method: "POST", body: jsonBody(payload), timeoutMs: 180000 }, token);
}
export async function listAnalyticsProducts(token: string) {
  return apiFetch<{ products?: Row[] }>("/api/admin/vkpi/analytics/products?limit=100", {}, token);
}
export async function upsertAnalyticsProduct(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/analytics/products", { method: "POST", body: jsonBody(payload) }, token);
}
export async function listOutreachSuggestions(token: string, status = "new") {
  return apiFetch<{ suggestions?: Row[] }>(`/api/admin/vkpi/analytics/suggestions?status=${encodeURIComponent(status)}&limit=100`, {}, token);
}
export async function listDailyOutreachDigest(token: string, staffId?: string) {
  const params = new URLSearchParams({ limit: "100" });
  if (staffId) params.set("staff_id", staffId);
  return apiFetch<{ digest?: Row | null; items?: Row[]; digest_date?: string }>(`/api/admin/vkpi/analytics/daily-digest?${params.toString()}`, {}, token);
}
export async function getDailyOutreachDigestStatus(token: string, productSku = "") {
  const params = new URLSearchParams({ limit: "100" });
  if (productSku.trim()) params.set("product_sku", productSku.trim());
  return apiFetch<Row>(`/api/admin/vkpi/analytics/daily-digest/status?${params.toString()}`, {}, token);
}
export async function generateDailyOutreachDigest(token: string, productSku = "") {
  const body: Record<string, unknown> = { limit: 100 };
  if (productSku.trim()) body.product_sku = productSku.trim();
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/analytics/daily-digest/generate", { method: "POST", body: jsonBody(body) }, token);
}
export async function claimOutreachSuggestion(token: string, suggestionId: string) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/claim`, { method: "POST", body: jsonBody({}) }, token);
}
export async function createProjectFromOutreachSuggestion(token: string, suggestionId: string, payload: Record<string, unknown> = {}) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/create-project`, { method: "POST", body: jsonBody({ auto_create_link: true, ...payload }) }, token);
}
export async function dismissOutreachSuggestion(token: string, suggestionId: string, reason = "") {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/dismiss`, { method: "POST", body: jsonBody({ reason }) }, token);
}

export async function listProductLaunches(token: string, options: { status?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.status) params.set("status", options.status);
  return apiFetch<{ launches?: Row[] }>(`/api/admin/vkpi/product-analysis/launches?${params.toString()}`, {}, token);
}
export async function createProductLaunch(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/product-analysis/launches", { method: "POST", body: jsonBody(payload) }, token);
}
export async function listProductKolPool(token: string, options: { platform?: string; query?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.platform) params.set("platform", options.platform);
  if (options.query) params.set("query", options.query);
  return apiFetch<{ items?: Row[]; rows?: Row[] }>(`/api/admin/vkpi/product-analysis/kol-pool?${params.toString()}`, {}, token);
}
export async function importProductKolPool(token: string, payload: { platform?: string; source_type?: string; source_ref?: string; items: Row[] }) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/product-analysis/kol-pool/import", { method: "POST", body: jsonBody(payload) }, token);
}
export async function getKolPoolSummary(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/kol-pool/summary", {}, token);
}
export async function runProductRecommendations(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/product-analysis/recommendations/run", { method: "POST", body: jsonBody(payload), timeoutMs: 120000 }, token);
}
export async function listProductRecommendations(token: string, options: { launchId?: string; runId?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.launchId) params.set("launch_id", options.launchId);
  if (options.runId) params.set("run_id", options.runId);
  return apiFetch<{ recommendations?: Row[] }>(`/api/admin/vkpi/product-analysis/recommendations?${params.toString()}`, {}, token);
}
export async function listProductRecommendationRuns(token: string, options: { strategyVersion?: string; status?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.strategyVersion) params.set("strategy_version", options.strategyVersion);
  if (options.status) params.set("status", options.status);
  return apiFetch<{ runs?: Row[] }>(`/api/admin/vkpi/product-analysis/recommendation-runs?${params.toString()}`, {}, token);
}
export async function getProductRecommendationOutcomeSummary(token: string, options: { launchId?: string; runId?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  if (options.launchId) params.set("launch_id", options.launchId);
  if (options.runId) params.set("run_id", options.runId);
  return apiFetch<{ totals?: Row; conversion?: Row; by_status?: Row[]; by_platform?: Row[]; source_rows?: Row[]; source_count?: number }>(`/api/admin/vkpi/product-analysis/outcomes/summary?${params.toString()}`, {}, token);
}
export async function getProductRecommendationEvidence(token: string, recommendationId: string) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/product-analysis/recommendations/${encodeURIComponent(recommendationId)}/evidence`, {}, token);
}
export async function productRecommendationAction(token: string, recommendationId: string, action: "shortlist" | "reject" | "feedback" | "claim" | "create_project", payload: Row = {}) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/product-analysis/recommendations/${encodeURIComponent(recommendationId)}/${encodeURIComponent(action)}`, { method: "POST", body: jsonBody(payload) }, token);
}

export async function getAiBudgetStatus(token: string) {
  return apiFetch<{ budgets?: Row[]; summary?: Row }>("/api/admin/vkpi/budgets", {}, token);
}
export async function updateAiBudgetScope(token: string, scope: string, payload: Row) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/budgets/${encodeURIComponent(scope)}/update`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function getAiBudgetUsageByProvider(token: string, options: { limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  return apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/budgets/usage-by-provider?${params.toString()}`, {}, token);
}
export async function getAiBudgetUsageByCron(token: string, options: { limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  return apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/budgets/usage-by-cron?${params.toString()}`, {}, token);
}

export async function listIndustryProjects(token: string, options: { activeOnly?: boolean; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100), active_only: String(options.activeOnly ?? true) });
  return apiFetch<{ projects?: Row[] }>(`/api/admin/vkpi/industry-data/projects?${params.toString()}`, {}, token);
}
export async function createIndustryProject(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/industry-data/projects", { method: "POST", body: jsonBody(payload) }, token);
}
export async function listIndustryAccounts(token: string, projectId: string, limit = 300) {
  return apiFetch<{ accounts?: Row[] }>(`/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/accounts?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function addIndustryAccount(token: string, projectId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/accounts`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function importIndustryApifyHistory(token: string, projectId: string, payload: { source_type?: string; source_ref?: string; items: Row[] }) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/apify/import`, { method: "POST", body: jsonBody(payload), timeoutMs: 120000 }, token);
}
export async function getIndustryAccount(token: string, accountId: string, limit = 500) {
  return apiFetch<{ account?: Row; snapshots?: Row[]; posts?: Row[] }>(`/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function refreshIndustryAccount(token: string, accountId: string) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}/refresh`, { method: "POST", body: jsonBody({}), timeoutMs: 120000 }, token);
}
export async function updateIndustryAccount(token: string, accountId: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}`, { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function getIndustryCrossPlatform(token: string, projectId: string) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/cross-platform`, {}, token);
}
export async function listIndustryPosts(token: string, projectId: string, limit = 500) {
  return apiFetch<{ posts?: Row[] }>(`/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/posts?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function getContentBrainStatus(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/industry-data/content-brain/status", {}, token);
}
export async function listContentBrainPosts(token: string, options: { status?: string; platform?: string; query?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.status) params.set("status", options.status);
  if (options.platform) params.set("platform", options.platform);
  if (options.query) params.set("query", options.query);
  return apiFetch<{ posts?: Row[]; count?: number; schema_ready?: boolean }>(`/api/admin/vkpi/industry-data/content-brain/posts?${params.toString()}`, {}, token);
}
export async function getCompetitorBrainStatus(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/industry-data/competitor-brain/status", {}, token);
}
export async function listCompetitorBrainSignals(token: string, options: { reviewStatus?: string; brand?: string; signalType?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.reviewStatus) params.set("review_status", options.reviewStatus);
  if (options.brand) params.set("brand", options.brand);
  if (options.signalType) params.set("signal_type", options.signalType);
  return apiFetch<{ signals?: Row[]; count?: number; schema_ready?: boolean }>(`/api/admin/vkpi/industry-data/competitor-brain/signals?${params.toString()}`, {}, token);
}
export async function reviewCompetitorBrainSignal(token: string, signalId: string | number, payload: { action: string; note?: string }) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/industry-data/competitor-brain/signals/${encodeURIComponent(String(signalId))}/review`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function searchVkpi(token: string, query: string, limit = 20) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return apiFetch<{ items?: Row[]; total?: number; provider_calls?: boolean; write_db?: boolean; tokens?: string[] }>(`/api/admin/vkpi/search?${params.toString()}`, {}, token);
}

export async function getCommentIntelligenceOverview(
  token: string,
  options: { days?: number; recentLimit?: number } = {},
) {
  const params = new URLSearchParams({
    days: String(options.days || 7),
    recent_limit: String(options.recentLimit || 8),
  });
  return apiFetch<VkpiCommentIntelligenceOverview>(`/api/admin/vkpi/comment-intelligence/overview?${params.toString()}`, {}, token);
}

export async function processRecentCommentIntelligence(
  token: string,
  options: {
    platform?: string;
    days?: number;
    limit?: number;
    collectComments?: boolean;
    analyzeSentiment?: boolean;
    classifyPillar?: boolean;
    forceReprocess?: boolean;
  } = {},
) {
  const params = new URLSearchParams({
    days: String(options.days || 7),
    limit: String(options.limit || 10),
    collect_comments: String(options.collectComments ?? false),
    analyze_sentiment: String(options.analyzeSentiment ?? true),
    classify_pillar: String(options.classifyPillar ?? true),
    force_reprocess: String(options.forceReprocess ?? false),
  });
  if (options.platform) params.set("platform", options.platform);
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/comment-intelligence/process-recent?${params.toString()}`, {
    method: "POST",
    body: jsonBody({}),
    timeoutMs: 180000,
  }, token);
}

export async function retryCommentIntelligenceRun(token: string, runId: string | number) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/comment-intelligence/runs/${encodeURIComponent(String(runId))}/retry`, {
    method: "POST",
    body: jsonBody({}),
    timeoutMs: 180000,
  }, token);
}
export async function getOperatingReviewStatus(token: string, limit = 25) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/operating-review/status?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function getRecommendationFeedbackBacklog(token: string, limit = 25, runUid = "") {
  const params = new URLSearchParams({ limit: String(limit) });
  if (runUid) params.set("run_uid", runUid);
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/learning/recommendation-feedback-backlog?${params.toString()}`, {}, token);
}
export async function getMemoryFeedbackBacklog(token: string, limit = 25, entityType = "kol") {
  const params = new URLSearchParams({ limit: String(limit), entity_type: entityType });
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/learning/memory-feedback-backlog?${params.toString()}`, {}, token);
}
export async function createMemoryFeedback(token: string, payload: Row) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/memory/feedback", { method: "POST", body: jsonBody(payload) }, token);
}

export async function listFeatureFlags(token: string) {
  return apiFetch<{ flags?: Row[] }>("/api/admin/vkpi/settings/feature-flags", {}, token);
}
export async function updateFeatureFlags(token: string, flags: Row[]) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/settings/feature-flags", { method: "PATCH", body: jsonBody({ flags }) }, token);
}
export async function listPlatformCrawlSettings(token: string) {
  return apiFetch<{ platforms?: Row[] }>("/api/admin/vkpi/settings/platform-crawl", {}, token);
}
export async function updatePlatformCrawlSettings(token: string, platforms: Row[]) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/settings/platform-crawl", { method: "PATCH", body: jsonBody({ platforms }) }, token);
}
export async function listBudgetSettings(token: string) {
  return apiFetch<{ budgets?: Row[] }>("/api/admin/vkpi/settings/budgets", {}, token);
}
export async function updateBudgetSettings(token: string, budgets: Row[]) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/settings/budgets", { method: "PATCH", body: jsonBody({ budgets }) }, token);
}
export async function getControlStatus(token: string) {
  return apiFetch<Record<string, unknown>>("/api/admin/vkpi/settings/control-status", {}, token);
}
export async function getCommentAlertSettings(token: string) {
  return apiFetch<{ settings?: Row }>("/api/admin/vkpi/settings/comment-alerts", {}, token);
}
export async function updateCommentAlertSettings(token: string, payload: Row) {
  return apiFetch<{ settings?: Row }>("/api/admin/vkpi/settings/comment-alerts", { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function runVkpiAutomation(token: string, job: string, payload: Row = {}) {
  return apiFetch<Record<string, unknown>>(`/api/admin/vkpi/cron/${encodeURIComponent(job)}/run`, { method: "POST", body: jsonBody(payload), timeoutMs: 120000 }, token);
}

export async function getUserPreferences(token: string, staffId?: string) {
  const suffix = staffId ? `?staff_id=${encodeURIComponent(staffId)}` : "";
  return apiFetch<{ preference?: Row; full_scope?: boolean }>(`/api/admin/vkpi/settings/preferences${suffix}`, {}, token);
}
export async function updateUserPreferences(token: string, payload: Row) {
  return apiFetch<{ preference?: Row; full_scope?: boolean }>("/api/admin/vkpi/settings/preferences", { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function listUserPreferences(token: string, limit = 200) {
  return apiFetch<{ preferences?: Row[]; full_scope?: boolean }>(`/api/admin/vkpi/settings/preferences/list?limit=${encodeURIComponent(String(limit))}`, {}, token);
}
export async function getNotificationSettings(token: string, staffId?: string) {
  const suffix = staffId ? `?staff_id=${encodeURIComponent(staffId)}` : "";
  return apiFetch<{ notification_settings?: Row; full_scope?: boolean }>(`/api/admin/vkpi/settings/notifications${suffix}`, {}, token);
}
export async function updateNotificationSettings(token: string, payload: Row) {
  return apiFetch<{ notification_settings?: Row; full_scope?: boolean }>("/api/admin/vkpi/settings/notifications", { method: "PATCH", body: jsonBody(payload) }, token);
}
export async function listNotificationSettings(token: string, limit = 200) {
  return apiFetch<{ notification_settings?: Row[]; full_scope?: boolean }>(`/api/admin/vkpi/settings/notifications/list?limit=${encodeURIComponent(String(limit))}`, {}, token);
}

export async function listEmployeeChannels(token: string, viewAsStaffId?: string) {
  const suffix = viewAsStaffId ? `?view_as_staff_id=${encodeURIComponent(viewAsStaffId)}` : "";
  return apiFetch<{ channels?: Row[] }>(`/api/marketing/channels${suffix}`, {}, token);
}
export async function getOfficialChannelMatrix(token: string, filters: { limit?: number; viewAsStaffId?: string } = {}) {
  const qs = new URLSearchParams();
  qs.set("limit", String(filters.limit ?? 20));
  if (filters.viewAsStaffId) qs.set("view_as_staff_id", filters.viewAsStaffId);
  return apiFetch<{ platforms?: Row[]; account_count?: number; post_count?: number; total_views?: number }>(
    `/api/marketing/channels/official-matrix?${qs.toString()}`,
    {},
    token,
  );
}
export async function getOfficialChannelGapReport(token: string, filters: { limit?: number; viewAsStaffId?: string } = {}) {
  const qs = new URLSearchParams();
  qs.set("limit", String(filters.limit ?? 50));
  if (filters.viewAsStaffId) qs.set("view_as_staff_id", filters.viewAsStaffId);
  return apiFetch<{ summary?: Row; accounts?: Row[]; platforms?: Row[] }>(
    `/api/marketing/channels/official-gap-report?${qs.toString()}`,
    {},
    token,
  );
}
export async function getOfficialChannelPosts(
  token: string,
  channelId: number | string,
  filters: { page?: number; limit?: number; sort?: string; direction?: string; window?: string } = {},
) {
  const qs = new URLSearchParams();
  qs.set("page", String(filters.page ?? 1));
  qs.set("limit", String(filters.limit ?? 10));
  qs.set("sort", filters.sort || "latest");
  qs.set("direction", filters.direction || "desc");
  qs.set("window", filters.window || "all");
  return apiFetch<{ account?: Row; posts?: Row[]; pagination?: Row; sort?: string; source?: string }>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/posts?${qs.toString()}`,
    {},
    token,
  );
}
export async function getChannelPostComments(
  token: string,
  channelId: number | string,
  filters: { postId?: string; url?: string; limit?: number } = {},
) {
  const qs = new URLSearchParams();
  qs.set("post_id", filters.postId || "");
  if (filters.url) qs.set("url", filters.url);
  qs.set("limit", String(filters.limit ?? 50));
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/post-comments?${qs.toString()}`,
    {},
    token,
  );
}
export async function collectChannelPostComments(
  token: string,
  channelId: number | string,
  payload: { postId?: string; url?: string; limit?: number } = {},
) {
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/post-comments/collect`,
    {
      method: "POST",
      body: jsonBody({
        post_id: payload.postId || "",
        url: payload.url || "",
        limit: payload.limit ?? 100,
      }),
    },
    token,
  );
}
export async function getRedditChannelAssessment(token: string, channelId: number | string) {
  return apiFetch<Row>(
    `/api/marketing/channels/${encodeURIComponent(String(channelId))}/reddit-assessment`,
    {},
    token,
  );
}
export async function bindEmployeeChannel(token: string, payload: Record<string, unknown>, viewAsStaffId?: string) {
  const suffix = viewAsStaffId ? `?view_as_staff_id=${encodeURIComponent(viewAsStaffId)}` : "";
  return apiFetch<Record<string, unknown>>(`/api/marketing/channels${suffix}`, { method: "POST", body: jsonBody(payload) }, token);
}
export async function syncEmployeeChannel(token: string, channelId: string) {
  const response = await apiFetch<Record<string, unknown>>(`/api/marketing/channels/${encodeURIComponent(channelId)}/sync-now`, { method: "POST", body: jsonBody({}) }, token);
  if (response.task_id && !response.message) {
    return { ...response, message: "同步任务已加入队列。" };
  }
  return response;
}

const DEFAULT_TASK_STATUSES: AsyncTaskStatus[] = [
  "queued",
  "running",
  "processing",
  "retrying",
  ...TERMINAL_STATUSES,
];

const ONE_HOUR_MS = 60 * 60 * 1000;

function normalizeAsyncTask(raw: Record<string, unknown>): AsyncTask | null {
  const taskId = String(raw.task_id || "");
  if (!taskId) return null;
  const result = (raw.result_json || raw.result || {}) as Record<string, unknown>;
  return {
    task_id: taskId,
    task_type: String(raw.task_type || raw.job_type || ""),
    status: String(raw.status || "queued") as AsyncTaskStatus,
    progress_pct: typeof raw.progress_pct === "number" ? raw.progress_pct : Number(raw.progress_pct || 0),
    progress_text: String(raw.progress_text || ""),
    result_json: result,
    result,
    error: String(raw.error || raw.error_message || ""),
    created_at: String(raw.created_at || ""),
    started_at: raw.started_at ? String(raw.started_at) : undefined,
    finished_at: raw.finished_at ? String(raw.finished_at) : undefined,
  };
}

function isRecentTask(task: AsyncTask): boolean {
  if (!TERMINAL_STATUSES.includes(task.status as (typeof TERMINAL_STATUSES)[number])) return true;
  if (!task.finished_at) return false;
  const finishedAt = new Date(task.finished_at).getTime();
  return Number.isFinite(finishedAt) && Date.now() - finishedAt < ONE_HOUR_MS;
}

export async function listTasks(token: string, filters: { status?: AsyncTaskStatus[] } = {}) {
  const statuses = filters.status?.length ? filters.status : DEFAULT_TASK_STATUSES;
  const uniqueStatuses = Array.from(new Set(statuses));
  const responses = await Promise.all(
    uniqueStatuses.map((status) =>
      apiFetch<{ tasks?: Record<string, unknown>[]; items?: Record<string, unknown>[] }>(
        `/api/marketing/tasks?status=${encodeURIComponent(status)}`,
        {},
        token,
      ),
    ),
  );
  const tasksById = new Map<string, AsyncTask>();
  responses.forEach((response) => {
    const rows = response.tasks || response.items || [];
    rows.forEach((row) => {
      const task = normalizeAsyncTask(row);
      if (task && isRecentTask(task)) tasksById.set(task.task_id, task);
    });
  });
  return Array.from(tasksById.values()).sort((a, b) => {
    const aTime = new Date(a.created_at || a.finished_at || 0).getTime();
    const bTime = new Date(b.created_at || b.finished_at || 0).getTime();
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
  });
}

export async function getTaskRealtimeStatus(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/tasks/realtime-status", {}, token);
}

export function buildTaskEventStreamUrl(taskId: string) {
  return buildApiUrl(`/api/audit/stream/${encodeURIComponent(taskId)}`);
}

export async function cancelTask(token: string, taskId: string) {
  await apiFetch<Record<string, unknown>>(
    `/api/marketing/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function retryTask(token: string, taskId: string) {
  const response = await apiFetch<Record<string, unknown>>(
    `/api/marketing/tasks/${encodeURIComponent(taskId)}/retry`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
  return { task_id: String(response.task_id || "") };
}

export async function lookupCachedVideoUrl(token: string, platform: string, videoId: string) {
  const qs = new URLSearchParams();
  qs.set("platform", platform);
  qs.set("video_id", videoId);
  try {
    const response = await apiFetch<{ hit?: boolean; cached_url?: string; cachedUrl?: string }>(
      `/api/marketing/media/video-cache/lookup?${qs.toString()}`,
      {},
      token,
    );
    return response.hit ? String(response.cached_url || response.cachedUrl || "") : "";
  } catch {
    return "";
  }
}
export async function listTeamChannels(token: string) {
  return apiFetch<{ rows?: Row[] }>("/api/marketing/channels/team-overview", {}, token);
}

export async function listCampaigns(token: string) {
  return apiFetch<{ campaigns?: Row[] }>("/api/marketing/campaigns?limit=100", {}, token);
}
export async function createCampaign(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/campaigns", { method: "POST", body: jsonBody(payload) }, token);
}
export async function addCampaignProject(token: string, campaignId: string, projectId: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/campaigns/${encodeURIComponent(campaignId)}/projects`, { method: "POST", body: jsonBody({ project_id: Number(projectId) }) }, token);
}
export async function listBudgetPools(token: string) {
  return apiFetch<{ budget_pools?: Row[] }>("/api/marketing/budget-pools?limit=100", {}, token);
}
export async function createBudgetPool(token: string, payload: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>("/api/marketing/budget-pools", { method: "POST", body: jsonBody(payload) }, token);
}
export async function initiateOffboarding(token: string, staffId: string, newOwnerStaffId?: string) {
  return apiFetch<Record<string, unknown>>(`/api/marketing/staff/${encodeURIComponent(staffId)}/offboard/initiate`, { method: "POST", body: jsonBody({ new_owner_staff_id: newOwnerStaffId ? Number(newOwnerStaffId) : undefined }) }, token);
}

export interface VkpiTeamFeedbackPayload {
  feedbackType?: string;
  severity?: string;
  pagePath?: string;
  title: string;
  detail?: string;
  metadata?: Record<string, unknown>;
}

export async function submitTeamFeedback(token: string, payload: VkpiTeamFeedbackPayload) {
  return apiFetch<{ feedback?: Row; ok?: boolean }>("/api/admin/vkpi/feedback", { method: "POST", body: jsonBody(payload) }, token);
}

export async function listTeamFeedback(token: string, status = "", limit = 100) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  return apiFetch<{ feedback?: Row[]; count?: number }>(`/api/admin/vkpi/feedback?${params.toString()}`, {}, token);
}

export async function updateTeamFeedbackStatus(token: string, uid: string, status: string) {
  return apiFetch<{ feedback?: Row; ok?: boolean }>(
    `/api/admin/vkpi/feedback/${encodeURIComponent(uid)}`,
    { method: "PATCH", body: jsonBody({ status }) },
    token,
  );
}
