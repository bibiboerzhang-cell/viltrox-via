import { METRICS } from "./data/metrics";
import { CURRENT_USER } from "./data/currentUser";
// 地理/地图层级归一化纯逻辑已下沉到 domains/dashboard/geo.ts;此处再导出保持调用方 import 路径不变。
import {
  normalizeMapHierarchy,
  normalizeEventsHierarchy,
  normalizeDealersHierarchy,
  eventCoords,
} from "../../../domains/dashboard/geo";

export { normalizeMapHierarchy, normalizeEventsHierarchy, normalizeDealersHierarchy, eventCoords };

const DASH = "—";
const COLORS = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"];

// 塑形层基础类型:后端 summary 是无类型 JSON,这里用「未知值 + 运行时收窄」表达,
// 而非 any——record()/list()/number() 在运行时把 unknown 收成对象/数组/数字。
type RawValue = unknown;
type RawRecord = Record<string, RawValue>;
type RawList = RawValue[];

// 单个指标卡格(metricData 的产物);scope 三联(all/kol/company)。
interface MetricCell {
  value: number | null;
  trend: string;
  source: string;
  sourceLabel: string | null;
  color: string;
  spark: number[] | null;
  deltaPct: number | null;
  windowDays: number | null;
  basis: RawValue;
  coverage: RawValue;
  waiting: string;
  anomaly: string | null;
}
interface MetricCellOptions {
  source?: string;
  sourceLabel?: string | null;
  trend?: string;
  waiting?: string;
  spark?: number[] | null;
  deltaPct?: number | null;
  windowDays?: number | null;
  basis?: RawValue;
  coverage?: RawValue;
  anomaly?: string | null;
}
// 一个指标在 all/kol/company 三个口径下的卡格。
interface MetricScopes {
  all: MetricCell;
  kol: MetricCell;
  company: MetricCell;
}

function record(value: RawValue): RawRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as RawRecord) : {};
}

function list(value: RawValue): RawList {
  return Array.isArray(value) ? value : [];
}

function number(value: RawValue): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function int(value: RawValue): number | null {
  const parsed = number(value);
  return parsed == null ? null : Math.round(parsed);
}

function hashColor(seed: RawValue) {
  const raw = String(seed || "");
  let hash = 0;
  for (const char of raw) hash = (hash * 31 + char.charCodeAt(0)) % COLORS.length;
  return COLORS[Math.abs(hash)];
}

function compact(value: RawValue) {
  const n = number(value);
  if (n == null) return DASH;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

function percentLabel(value: RawValue, digits = 0) {
  const n = number(value);
  return n == null ? DASH : `${n.toFixed(digits)}%`;
}

function timeLabel(value: RawValue) {
  const raw = String(value || "");
  if (!raw) return "时间待接入";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw.slice(0, 16);
  const diff = Date.now() - date.getTime();
  const minutes = Math.max(0, Math.round(diff / 60000));
  if (minutes < 60) return `${minutes || 1} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function formatDay(value: RawValue) {
  const date = new Date(String(value || Date.now()));
  if (Number.isNaN(date.getTime())) return { day: DASH, weekday: DASH, today: false };
  const today = new Date();
  return {
    day: date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }),
    weekday: date.toLocaleDateString("en-US", { weekday: "short" }),
    today: date.toDateString() === today.toDateString(),
  };
}

function metricData(value: number | null, color: string, options: MetricCellOptions = {}): MetricCell {
  const hasValue = value !== null && value !== undefined;
  const source = options.source || (hasValue ? "real" : "pending");
  return {
    value: hasValue ? value : null,
    trend: hasValue ? options.trend || "真实 API" : options.waiting || "数据待接入",
    source,
    sourceLabel: options.sourceLabel || null,
    color,
    spark: hasValue && Array.isArray(options.spark) ? options.spark : null,
    deltaPct: options.deltaPct ?? null,
    windowDays: options.windowDays ?? null,
    basis: options.basis ?? null,
    coverage: options.coverage ?? null,
    waiting: options.waiting || "数据待接入",
    anomaly: options.anomaly || null,
  };
}

type Scope = "all" | "kol" | "company";

function scopeKey(scope: Scope) {
  return scope === "company" ? "owned" : scope;
}

function scopeLabel(scope: Scope) {
  const key = scopeKey(scope);
  if (key === "owned") return "Owned";
  if (key === "kol") return "KOL";
  return "K + O";
}

function metricSeriesRecord(seriesByScope: RawRecord, scope: Scope, metricId: string): RawRecord | null {
  const direct = record(seriesByScope[scope]);
  let raw = direct[metricId];
  if ((raw === null || raw === undefined) && scope === "company") {
    raw = record(seriesByScope.owned)[metricId];
  }
  return raw === null || raw === undefined ? null : record(raw);
}

function metricSeriesPoints(series: RawRecord): number[] {
  const points: number[] = [];
  for (const rawPoint of list(series.points)) {
    const point = record(rawPoint);
    const parsed = Object.keys(point).length > 0 ? number(point.value) : number(rawPoint);
    if (parsed != null) points.push(parsed);
  }
  return points;
}

function withMetricSeries(cell: MetricCell, seriesByScope: RawRecord, scope: Scope, metricId: string): MetricCell {
  const series = metricSeriesRecord(seriesByScope, scope, metricId);
  if (!series) return cell;
  const points = metricSeriesPoints(series);
  return {
    ...cell,
    spark: points.length > 0 ? points : null,
    deltaPct: number(series.delta_pct),
    windowDays: int(series.window_days),
    basis: series.basis ?? null,
    coverage: series.coverage ?? null,
  };
}

function maturityForScope(dashboard: RawRecord, scope: Scope) {
  const key = scopeKey(scope);
  const contract = record(dashboard.metric_contract);
  const scopes = record(contract.scopes);
  const direct = record(dashboard.metric_maturity_by_scope);
  return record(scopes[key] || direct[key] || scopes.all || direct.all || dashboard.metric_maturity);
}

function maturityLabel(dashboard: RawRecord, scope: Scope) {
  const maturity = maturityForScope(dashboard, scope);
  const days = int(maturity.snapshot_days) ?? 0;
  const required = int(maturity.required_days) ?? 30;
  return String(maturity.maturity_label || `累积中 ${days}/${required}`);
}

function isMaturityReady(dashboard: RawRecord, scope: Scope) {
  const maturity = maturityForScope(dashboard, scope);
  const days = int(maturity.snapshot_days) ?? 0;
  const required = int(maturity.required_days) ?? 30;
  return Boolean(maturity.is_ready) || days >= required;
}

function accumulatingMetricData(value: number | null, color: string, dashboard: RawRecord, scope: Scope, options: MetricCellOptions = {}): MetricCell {
  const label = maturityLabel(dashboard, scope);
  return metricData(value, color, {
    ...options,
    source: "accumulating",
    sourceLabel: label,
    trend: options.trend || `${scopeLabel(scope)} · ${label}`,
    waiting: options.waiting || `${label} · 30d 数据累计中`,
  });
}

function windowMetricData(value: number | null, color: string, dashboard: RawRecord, scope: Scope, options: MetricCellOptions = {}): MetricCell {
  const label = maturityLabel(dashboard, scope);
  if (value !== null && value !== undefined && isMaturityReady(dashboard, scope)) {
    return metricData(value, color, {
      ...options,
      sourceLabel: label,
      trend: options.trend || `${scopeLabel(scope)} · ${label}`,
    });
  }
  return accumulatingMetricData(null, color, dashboard, scope, {
    ...options,
    waiting: options.waiting || `${label} · ${scopeLabel(scope)} 30d 增量累计中`,
  });
}

interface CurrentUserFallback {
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  userEmail?: string;
}

export function normalizeCurrentUser(apiUser: RawValue, fallback: CurrentUserFallback = {}) {
  const user = record(apiUser);
  const name = String(user.name || user.display_name || fallback.userName || CURRENT_USER.name);
  const role = String(user.role || user.staff_role || fallback.userRole || CURRENT_USER.role);
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "V";
  return {
    ...CURRENT_USER,
    id: Number(user.id || user.staff_id || CURRENT_USER.id),
    name,
    role,
    email: String(user.email || fallback.userEmail || ""),  // 2026-07-03:退役 kevin mock,宁空不假
    avatar: initials,
    avatarUrl: String(user.avatar_url || fallback.userAvatar || ""),
    avatarGradient: CURRENT_USER.avatarGradient,
  };
}

export function normalizeAlerts(rawAlerts: RawList = []) {
  const alerts = list(rawAlerts).map((raw, index) => {
    const row = record(raw);
    let metadata = record(row.metadata);
    if (!Object.keys(metadata).length && row.metadata_json) {
      try {
        metadata = record(JSON.parse(String(row.metadata_json || "{}")));
      } catch {
        metadata = {};
      }
    }
    const severity = String(row.severity || row.priority || "info").toLowerCase();
    const isFeedback = row.target_type === "team_feedback" || row.rule_key === "team_feedback.open" || metadata.source === "vkpi_feedback" || Boolean(metadata.feedback_uid);
    const category = isFeedback ? "feedback" : String(row.category || row.alert_type || row.type || "notification").toLowerCase();
    const title = String(row.title || row.name || `提醒 ${index + 1}`);
    const desc = String(row.message || row.summary || row.description || row.body || "等待处理");
    const id = row.id || row.uid || `alert-${index}`;
    const iconColor = isFeedback
      ? "#f59e0b"
      : severity === "high" || severity === "critical"
        ? "#ef4444"
        : severity === "medium" || severity === "warning"
          ? "#f59e0b"
          : "#10b981";
    return {
      id,
      raw: row,
      iconKey: isFeedback || severity === "high" || severity === "critical" || severity === "warning" ? "warning" : category.includes("task") ? "target" : "bell",
      iconColor,
      title,
      desc,
      time: timeLabel(row.due_at || row.created_at || row.updated_at),
      unread: !row.resolved_at && row.status !== "resolved" && row.status !== "closed",
      category,
      severity: severity === "critical" ? "high" : severity === "warning" ? "medium" : severity,
      status: row.resolved_at || row.status === "resolved" ? "done" : "todo",
      priority: severity === "critical" ? "high" : severity === "info" ? "low" : severity === "warning" ? "medium" : severity,
      source: isFeedback ? "vkpi_feedback" : String(row.source || "system"),
    };
  });
  return {
    notifications: alerts.filter((item) => !item.category.includes("reminder") && !item.category.includes("task")),
    reminders: alerts.filter((item) => item.category.includes("reminder") || item.category.includes("task")),
    all: alerts,
  };
}

export function normalizeDashboardMetrics(bundle: RawRecord, kolRows: RawList = []) {
  const dashboard = record(bundle.dashboard);
  const summary = record(dashboard.summary);
  const metricSeriesByScope = record(summary.metric_series_by_scope);
  const evidenceMetrics = record(summary.evidence_metrics);
  const evidenceRoster = record(evidenceMetrics.active_roster_by_scope);
  const evidenceEngagement = record(evidenceMetrics.engagement);
  const evidenceCoverage = record(evidenceMetrics.coverage);
  const evidenceActive30 = record(evidenceMetrics.active_30d_by_scope);
  const rosterDetail = record(evidenceMetrics.roster_detail);
  const officialCount = int(summary.official_account_count);
  const kolCount = kolRows.length || null;
  const rosterAll = int(evidenceRoster.all ?? summary.active_roster) ?? (kolCount != null || officialCount != null ? (kolCount || 0) + (officialCount || 0) : null);
  const rosterKol = int(evidenceRoster.kol) ?? kolCount;
  const rosterCompany = int(evidenceRoster.company) ?? officialCount;
  const totalExposure = number(evidenceMetrics.total_exposure);
  const evidenceRate = number(evidenceEngagement.engagement_rate);
  const engagementPercent = evidenceRate == null ? null : evidenceRate * 100;
  const viewCovered = int(evidenceCoverage.view_covered);
  const coveragePct = number(evidenceCoverage.view_coverage_pct);
  const coveragePercent = coveragePct == null ? null : coveragePct * 100;
  // 2026-07-18 审计修:门面红线禁硬编码「实时」——新鲜度只能来自真实时间戳。
  const summaryFreshness = timeLabel(summary.generated_at || dashboard.generated_at) || DASH;
  const evidenceCoverageText = `${summaryFreshness} · evidence 覆盖 ${percentLabel(coveragePercent, 0)}`;
  const evidenceVideoText = `${summaryFreshness} · 基于 ${viewCovered != null ? viewCovered.toLocaleString() : DASH} 条已抓视频`;
  const active30WindowDays = int(evidenceActive30.window_days) ?? 30;
  const evidenceActive30Text = `${summaryFreshness} · 近 ${active30WindowDays} 天发布/同步`;
  const active30ByScope = record(summary.active_30d_by_scope);
  const exposureByScope = record(summary.exposure_30d_by_scope);
  const engagementByScope = record(summary.engagement_rate_by_scope);
  // 防御性标注(2026-06-12 波3 R2):单条 evidence 占比 >80% 时提示数据被单源污染。
  // 数据问题本身由主控清理;这里只做卡面提示,不改数值。
  const moversTabs = record(rosterDetail.movers_tabs);
  const maxTabValue = (rows: RawValue) => list(rows).reduce((max: number, row) => Math.max(max, number(record(row).value) ?? 0), 0);
  const topEvidenceViews = maxTabValue(moversTabs.by_views);
  const topEvidenceEngagement = maxTabValue(moversTabs.by_engagement);
  const totalEngagement = number(evidenceEngagement.total_engagement);
  const SINGLE_SOURCE_WARNING = "⚠ 单源占比异常";
  const exposureAnomaly = totalExposure != null && totalExposure > 0 && topEvidenceViews / totalExposure > 0.8;
  const engagementAnomaly = totalEngagement != null && totalEngagement > 0 && topEvidenceEngagement / totalEngagement > 0.8;

  const values: Record<string, MetricScopes> = {
    "kol-count": {
      all: metricData(rosterAll, "#a855f7", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: `${summaryFreshness} · evidence active roster` }),
      kol: metricData(rosterKol, "#ec4899", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: `${summaryFreshness} · KOL evidence active` }),
      company: metricData(rosterCompany, "#06b6d4", { source: "owned_matrix", sourceLabel: summaryFreshness, trend: `${summaryFreshness} · 官方矩阵 active` }),
    },
    "active-30d": {
      all: number(evidenceActive30.all) != null ? metricData(number(evidenceActive30.all), "#a855f7", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · active_30d 累计中` }),
      kol: number(evidenceActive30.kol) != null ? metricData(number(evidenceActive30.kol), "#ec4899", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL active_30d 累计中` }),
      company: number(evidenceActive30.company ?? evidenceActive30.owned) != null ? metricData(number(evidenceActive30.company ?? evidenceActive30.owned), "#06b6d4", { source: "owned_matrix", sourceLabel: summaryFreshness, trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.owned ?? active30ByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方 active_30d 累计中` }),
    },
    exposure: {
      all: totalExposure != null ? metricData(totalExposure, "#a855f7", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: evidenceCoverageText, anomaly: exposureAnomaly ? SINGLE_SOURCE_WARNING : null }) : windowMetricData(number(exposureByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · 不使用 lifetime 代替 30d` }),
      // 2026-06-12 波3 R6:KOL 口径不再复用全量 totalExposure(避免「KOL 曝光=全部曝光」假象)
      kol: windowMetricData(number(exposureByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL 30d 曝光累计中(不复用全量口径)` }),
      // 官方矩阵 channel-level 30d 真实增量(summary.exposure_30d_by_scope.owned/company,由 _build_company_window_metrics 写入);
      // 后端给到非 null 即走实时,否则回退到原「累积中」窗口口径。镜像 active-30d.company 的 owned_matrix 模式。
      company: number(exposureByScope.owned ?? exposureByScope.company) != null ? metricData(number(exposureByScope.owned ?? exposureByScope.company), "#06b6d4", { source: "owned_matrix", sourceLabel: summaryFreshness, trend: `${summaryFreshness} · 官方矩阵 30d` }) : windowMetricData(number(exposureByScope.owned ?? exposureByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方 30d 曝光累计中` }),
    },
    engagement: {
      all: engagementPercent != null ? metricData(engagementPercent, "#a855f7", { source: "evidence_metrics", sourceLabel: summaryFreshness, trend: evidenceVideoText, anomaly: engagementAnomaly ? SINGLE_SOURCE_WARNING : null }) : windowMetricData(number(engagementByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · 互动率累计中` }),
      // 2026-06-12 波3 R6:KOL 口径不再复用全量 engagementPercent
      kol: windowMetricData(number(engagementByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL 互动率累计中(不复用全量口径)` }),
      // 官方矩阵最新快照真实互动率(summary.engagement_rate_by_scope.owned/company,百分数,由 _build_company_window_metrics 写入)。
      company: number(engagementByScope.owned ?? engagementByScope.company) != null ? metricData(number(engagementByScope.owned ?? engagementByScope.company), "#06b6d4", { source: "owned_matrix", sourceLabel: summaryFreshness, trend: `${summaryFreshness} · 官方互动率` }) : windowMetricData(number(engagementByScope.owned ?? engagementByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方互动率累计中` }),
    },
    gmv: {
      all: metricData(null, "#fbbf24", { waiting: "待 Shopify 订单接入" }),
      kol: metricData(null, "#fbbf24", { waiting: "待 Shopify 订单 + KOL 归因" }),
      company: metricData(null, "#fbbf24", { waiting: "待 Shopify 订单 webhook" }),
    },
    roi: {
      all: metricData(null, "#fbbf24", { waiting: "待成本与订单接入" }),
      kol: metricData(null, "#fbbf24", { waiting: "待 KOL 成本接入" }),
      company: metricData(null, "#fbbf24", { waiting: "待成本接入" }),
    },
  };

  return METRICS.map((metric) => {
    const scopes = values[metric.id];
    return {
      ...metric,
      data: scopes
        ? {
            all: withMetricSeries(scopes.all, metricSeriesByScope, "all", metric.id),
            kol: withMetricSeries(scopes.kol, metricSeriesByScope, "kol", metric.id),
            company: withMetricSeries(scopes.company, metricSeriesByScope, "company", metric.id),
          }
        : metric.data,
      rosterDetail: metric.id === "kol-count" ? rosterDetail : undefined,
      sub: metric.id === "exposure" && totalExposure != null ? evidenceCoverageText : metric.id === "engagement" && engagementPercent != null ? evidenceVideoText : metric.id === "exposure" ? "30d 增量，不取 lifetime" : metric.sub,
    };
  });
}

function emptyFunnel(projectCount: number | null) {
  return ["发现", "已联系", "已回复", "已合作", "已发货", "已到货", "已发布", "已统计", "已关闭"].map((name, index) => ({
    name,
    label: `${index + 1}.${name}`,
    count: index === 0 ? projectCount || 0 : 0,
  }));
}

export function normalizeCampaigns(rows: RawValue = []) {
  return list(rows).slice(0, 8).map((row, index) => {
    const item = record(row);
    const views = int(item.views);
    const published = int(item.published_content);
    const projectCount = int(item.project_count);
    const healthScore = int(item.health_score ?? item.healthScore ?? item.progress_pct);
    return {
      id: item.project_id || item.id || item.product_sku || `product-${index}`,
      name: String(item.product_name || item.project_name || item.product_sku || "未命名项目"),
      product: String(item.product_sku || item.product_name || ""),
      iconKey: "camera",
      iconColor: COLORS[index % COLORS.length],
      status: String(item.status || "in-progress"),
      statusLabel: String(item.status_label || item.status || "进行中"),
      healthScore: healthScore ?? "待评估",
      healthColor: healthScore == null ? "#64748b" : healthScore >= 85 ? "#10b981" : "#f59e0b",
      lastUpdate: String(item.updated_at || item.last_update || "").slice(0, 10) || "—",
      // 诚实化:owner/startDate 读 API 真值(多候选键),取不到显 — 而非误导性的「待接入」。
      owner: String(item.owner || item.owner_name || item.assigned_staff_name || item.assignee || "") || "—",
      startDate: String(item.start_date || item.started_at || item.created_at || "").slice(0, 10) || "—",
      stats: {
        kolCount: projectCount ?? DASH,
        published: published ?? DASH,
        publishRate: null,
        totalReach: views != null ? compact(views) : DASH,
        shortClicks: int(item.orders) ?? DASH,
        dailyClicks: DASH,
      },
      funnel: emptyFunnel(projectCount),
      bottleneckText: "项目详情接入中",
      kolList: [],
      pendingPublishes: [],
      assets: [],
      newKolSuggestions: [],
      raw: item,
    };
  });
}

function normalizeActiveCampaigns(block: RawValue = {}) {
  const source = record(block);
  return list(source.items).map((row, index) => {
    const item = record(row);
    const healthScore = int(item.health_score ?? item.healthScore);
    const kolCount = int(item.kol_count);
    const published = int(item.published_count);
    const recentVideoCount = int(item.recent_video_count);
    const executionKolCount = int(item.execution_kol_count);
    const totalViews = int(item.total_views);
    const activeSignals = list(item.active_signals).map((signal) => String(signal));
    return {
      id: item.id || item.project_uid || `active-campaign-${index}`,
      name: String(item.name || item.project_name || "未命名项目"),
      product: String(item.product || item.product_name || item.product_sku || ""),
      iconKey: String(item.icon_key || "camera"),
      iconColor: String(item.icon_color || COLORS[index % COLORS.length]),
      status: String(item.status || "active"),
      statusLabel: String(item.status_label || "进行中"),
      healthScore: healthScore ?? "待评估",
      healthColor: healthScore == null ? "#64748b" : healthScore >= 85 ? "#10b981" : healthScore >= 70 ? "#f59e0b" : "#ef4444",
      lastUpdate: "真实 API",
      owner: "项目工作流",
      startDate: "真实项目",
      source: "active_campaigns",
      recentVideoCount: recentVideoCount ?? 0,
      executionKolCount: executionKolCount ?? 0,
      kolCount: kolCount ?? 0,
      publishedCount: published ?? 0,
      reach: totalViews != null ? compact(totalViews) : DASH,
      stats: {
        kolCount: kolCount ?? DASH,
        published: recentVideoCount ?? 0,
        publishRate: null,
        totalReach: totalViews != null ? compact(totalViews) : DASH,
        shortClicks: executionKolCount ?? DASH,
        dailyClicks: DASH,
      },
      funnel: emptyFunnel(kolCount || 0),
      bottleneckText: String(item.bottleneck_text || activeSignals.join(" · ") || "符合当前 active campaign 口径"),
      kolList: [],
      pendingPublishes: [],
      assets: [],
      newKolSuggestions: [],
      raw: item,
    };
  });
}

function normalizeActiveCampaignsMeta(block: RawValue = {}) {
  const source = record(block);
  const hasRealBlock = Object.keys(source).length > 0;
  const criteria = record(source.criteria);
  return {
    isReal: hasRealBlock,
    activeCount: int(source.active_count) ?? 0,
    windowDays: int(source.window_days) ?? 30,
    criteria,
  };
}

function timestampOrEmpty(value: RawValue) {
  const raw = String(value || "");
  const time = raw ? Date.parse(raw) : Number.NEGATIVE_INFINITY;
  return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
}

function normalizeProjectFunnel(stageCounts: RawValue = {}) {
  const counts = record(stageCounts);
  const keys = ["discovery", "contacted", "replied", "agreed", "shipped", "received", "published", "measured", "closed"];
  const labels = ["发现", "已联系", "已回复", "已合作", "已发货", "已到货", "已发布", "已统计", "已关闭"];
  return keys.map((key, index) => ({
    name: labels[index],
    label: `${index + 1}.${labels[index]}`,
    count: int(counts[key]) || 0,
  }));
}

function normalizeStarredCampaigns(rows: RawValue = []) {
  const latestPublishOf = (row: RawValue) => String(record(row).latest_publish_date || record(row).latestEvidencePublishDate || record(row).evidencePublishDate || "");
  return list(rows)
    .slice()
    .sort((a, b) => timestampOrEmpty(latestPublishOf(b)) - timestampOrEmpty(latestPublishOf(a)))
    .map((row, index) => {
      const item = record(row);
      const projectId = item.id || item.project_id || item.projectId || item.project_uid || `starred-project-${index}`;
      const healthScore = int(item.health_score ?? item.healthScore);
      const kolCount = int(item.kol_count ?? item.kolCount);
      const published = int(item.published_count ?? item.publishedCount);
      const totalViews = int(item.total_views ?? item.views);
      const latestPublish = latestPublishOf(item);
      return {
        id: projectId,
        projectId: String(projectId),
        name: String(item.project_name || item.projectName || item.campaign || item.name || "未命名项目"),
        product: String(item.product_name || item.productName || item.product_sku || item.productSku || ""),
        iconKey: "camera",
        iconColor: COLORS[index % COLORS.length],
        status: String(item.stage || "active"),
        statusLabel: String(item.stage_status || item.stage || "进行中"),
        healthScore: healthScore ?? "待评估",
        healthColor: healthScore == null ? "#64748b" : healthScore >= 85 ? "#10b981" : healthScore >= 70 ? "#f59e0b" : "#ef4444",
        lastUpdate: latestPublish ? latestPublish.slice(0, 10) : "活动时间未知",
        owner: String(item.staff_name || item.ownerName || "项目工作流"),
        startDate: String(item.created_at || "").slice(0, 10) || "真实项目",
        source: "starred_projects",
        stats: {
          kolCount: kolCount ?? DASH,
          published: published ?? DASH,
          publishRate: null,
          totalReach: totalViews != null ? compact(totalViews) : DASH,
          shortClicks: int(item.evidence_count ?? item.evidenceCount) ?? DASH,
          dailyClicks: DASH,
        },
        funnel: normalizeProjectFunnel(item.stage_counts || item.stageCounts),
        bottleneckText: String(item.bottleneck || item.current_focus || item.currentFocus || "个人重点项目"),
        kolList: [],
        pendingPublishes: [],
        assets: [],
        newKolSuggestions: [],
        raw: item,
      };
    });
}

// 2026-06-12 波3 R3:recent-content 同批存在 "2026-05-28" 与 "Thu May 28" 两种日期串,
// 直接 slice(0,10) 会把同一天拆成两桶;这里统一归一成 YYYY-MM-DD 再分桶。
function calendarDateKey(value: RawValue) {
  const raw = String(value || "").trim();
  if (!raw) return "unknown";
  const direct = raw.slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(direct)) return direct;
  const withYear = /\d{4}/.test(raw) ? raw : `${raw} ${new Date().getFullYear()}`;
  const parsed = new Date(withYear);
  if (!Number.isNaN(parsed.getTime())) {
    return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
  }
  return direct;
}

// 数据源停更案(2026-06-12):取最近一条内容日期,卡面诚实标注「数据截至 …」。
export function latestCalendarDate(items: RawValue = []) {
  let latest = "";
  for (const raw of list(items)) {
    const item = record(raw);
    const key = calendarDateKey(item.posted_at || item.published_at || item.created_at);
    if (/^\d{4}-\d{2}-\d{2}$/.test(key) && key > latest) latest = key;
  }
  return latest || null;
}

interface CalendarEntry {
  platform: string;
  time: string;
  label: string;
  color: string;
  raw: RawRecord;
}
type CalendarBucket = ReturnType<typeof formatDay> & { items: CalendarEntry[] };

export function normalizeCalendar(items: RawValue = []) {
  const buckets = new Map<string, CalendarBucket>();
  for (const raw of list(items)) {
    const item = record(raw);
    const dateKey = calendarDateKey(item.posted_at || item.published_at || item.created_at);
    const meta = formatDay(dateKey);
    let bucket = buckets.get(dateKey);
    if (!bucket) {
      bucket = { ...meta, items: [] };
      buckets.set(dateKey, bucket);
    }
    bucket.items.push({
      platform: String(item.platform || "").toLowerCase().includes("instagram") ? "IG" : String(item.platform || "").toLowerCase().includes("youtube") ? "YT" : "internal",
      time: String(item.posted_at || item.published_at || "").slice(11, 16) || "--:--",
      label: `${item.account_handle || item.kol_handle || "账号待接入"} · ${item.title || "内容标题待接入"}`,
      color: hashColor(item.platform || item.account_handle),
      raw: item,
    });
  }
  return Array.from(buckets.values()).slice(0, 7);
}

export function normalizeAiInsight(copilotBrief: RawValue = {}, tasks: RawValue = {}, aiTodayHot: RawValue = {}) {
  // 2026-06-15:优先用 AI Today LLM「今日热点」(每早8点生成的拍摄方案+话题);无则回落原 copilot-brief。
  const hot = record(aiTodayHot);
  const hotContent = record(hot.content);
  const attempt = record(hot.latest_attempt || hotContent.latest_attempt);
  const hasAttempt = Boolean(
    attempt.attempted_at || attempt.status || attempt.provider || attempt.reason,
  );
  const latestAttempt = hasAttempt ? {
    attemptedAt: String(attempt.attempted_at || ""),
    attemptedLabel: attempt.attempted_at ? timeLabel(attempt.attempted_at) : "时间未记录",
    status: String(attempt.status || "unknown"),
    provider: String(attempt.provider || "unknown"),
    model: String(attempt.model || ""),
    reason: String(attempt.reason || ""),
    providerStatus: String(attempt.provider_status || ""),
    generationStatus: String(attempt.generation_status || ""),
    providersAttempted: list(attempt.providers_attempted).map((value) => String(value)),
  } : null;
  if (hot.available && hotContent.headline) {
    const freshnessStatus = String(hotContent.freshness_status || hot.freshness_status || "unknown");
    const freshnessLabel = String(hotContent.freshness_label || hot.freshness_label || "");
    const isStale = freshnessStatus === "stale";
    const recommendedVideos = list(hotContent.recommended_videos).map((value) => record(value));
    const sources = list(hotContent.sources).map((value) => record(value));
    return {
      confidence: null,
      updatedLabel: freshnessLabel || (hotContent.generated_at ? timeLabel(hotContent.generated_at) : "时间待接入"),
      freshnessStatus,
      freshnessLabel,
      isStale,
      generatedAt: String(hotContent.generated_at || ""),
      snapshotDate: String(hotContent.snapshot_date || hot.snapshot_date || ""),
      todayDecision: {
        text: String(hotContent.headline || ""),
        amount: "--",
        reason: isStale ? "该建议来自过期快照，需先查看证据再采用" : "基于市场来源与已深析视频生成",
        primaryAction: "查看证据",
        secondaryAction: "稍后处理",
      },
      strengthenLabel: "拍摄方案",
      strengthen: list(hotContent.shooting_plans).slice(0, 3).map((p) => ({ text: String(p), detail: "" })),
      productRecommendationLabel: "产品推荐",
      productRecommendations: list(hotContent.product_recommendations).slice(0, 4).map((x) => String(x)),
      contentRecommendationLabel: "内容打法",
      contentRecommendations: list(hotContent.content_recommendations).slice(0, 4).map((x) => String(x)),
      videoRecommendations: list(hotContent.video_recommendations).slice(0, 4).map((x) => String(x)),
      weaken: [],
      todayContentLabel: "当下热点·赛事",
      todayContent: list(hotContent.hot_topics).slice(0, 3).map((x) => String(x)),
      recommendedVideos,
      sources,
      latestAttempt,
      poweredBy: isStale ? "过期快照 · 库内证据仅供复核" : "每日早 8 点更新 · 来源可回跳",
      raw: hot,
    };
  }
  // 2026-07-16 用户裁决:AI Today 只看外部市场内容 —— 快照不可用时诚实空态,
  // 绝不再回落 copilot-brief/内部任务顶「今日重点决策」位(内部建议归 Action Inbox)。
  // 此前回落造成三重误导:内部产物冒充市场决策、「20 小时前」实为 brief 产物时间、
  // 底部黄字「未保留市场来源」看似快照坏了实为根本没读快照。
  void copilotBrief;
  void tasks;
  const fallbackFreshnessLabel = String(hotContent.freshness_label || hot.freshness_label || "");
  const fallbackFreshnessStatus = String(hotContent.freshness_status || hot.freshness_status || "unknown");
  return {
    confidence: null,
    updatedLabel: fallbackFreshnessLabel || "暂无今日快照",
    todayDecision: {
      text: "",
      amount: "--",
      reason: latestAttempt
        ? "今日外部市场快照未就绪,最近一次生成情况见下方说明"
        : "今日外部市场快照未就绪,等待每日刷新",
      primaryAction: "查看来源",
      secondaryAction: "稍后处理",
    },
    strengthen: [],
    weaken: [],
    todayContent: [],
    recommendedVideos: [],
    productRecommendations: [],
    contentRecommendations: [],
    videoRecommendations: [],
    sources: [],
    latestAttempt,
    freshnessStatus: fallbackFreshnessStatus,
    freshnessLabel: fallbackFreshnessLabel,
    isStale: fallbackFreshnessStatus === "stale",
    generatedAt: String(hotContent.generated_at || hot.generated_at || ""),
    snapshotDate: String(hotContent.snapshot_date || hot.snapshot_date || ""),
    poweredBy: "只展示外部市场内容 · 内部建议见今日该做什么",
    raw: hot,
  };
}

// D3 人话化(2026-07-02):卡面来源行不再堆 evidence 键名(summary.run_id · hot_brands.count),
// 已知技术源名 → 中文人话;键名细节挪去 sourceDetail(卡面 title/tooltip),弹窗仍有原始 sources 可查。
const SIGNAL_SOURCE_HUMAN: Record<string, string> = {
  vkpi_competitor_signals: "市场信号",
  "Market Intelligence v0": "市场情报",
  market_external_signal_smoke_v0: "外部信号",
  google_news: "Google 新闻",
  rss: "RSS 订阅",
};

function humanSignalSource(label: RawValue) {
  const raw = String(label || "").trim();
  if (!raw) return "市场信号";
  if (SIGNAL_SOURCE_HUMAN[raw]) return SIGNAL_SOURCE_HUMAN[raw];
  // provider/model 形态(如 gemini/gemini-2.5-flash)或普通词直接保留,不硬翻。
  return raw;
}

export function normalizeSignals(marketCards: RawValue = {}, competitorRadar: RawValue = null) {
  // 2026-06-15:竞品新品雷达(Gemini+Google 接地)置顶,后接 market-intelligence 信号。
  const radar = record(competitorRadar);
  const radarContent = record(radar.content);
  const radarFreshness = String(radarContent.freshness_status || radar.freshness_status || "unknown");
  const radarFreshnessLabel = String(radarContent.freshness_label || radar.freshness_label || "");
  const radarGlobalSources = list(radarContent.sources);
  const radarItems = (radar.available && Array.isArray(record(radar.content).items))
    ? list(radarContent.items).slice(0, 5).map((it, i) => {
        const d = record(it);
        const isThreat = String(d.impact || "").includes("威胁");
        const brand = String(d.brand || "").trim();
        const sourceRows = (list(d.sources).length ? list(d.sources) : radarGlobalSources)
          .map((source) => {
            const row = record(source);
            const contentOrigin = String(row.content_origin || d.content_origin || "unknown");
            const sourcePlatform = String(row.source_platform || d.source_platform || row.platform || d.platform || "");
            return {
              name: String(row.title || row.provider || row.source_table || "原始来源"),
              url: String(row.url || row.source_url || d.source_url || ""),
              provider: String(row.provider || row.source_table || ""),
              platform: sourcePlatform,
              sourcePlatform,
              content_origin: contentOrigin,
              contentOrigin,
              relationType: String(row.relation_type || "unknown"),
              sourceStatus: String(row.source_status || ""),
              sourceTable: String(row.ledger_table || row.source_table || ""),
              sourceId: row.ledger_id || row.source_id || null,
              published_at: String(row.published_at || d.published_at || ""),
              observedAt: String(row.observed_at || ""),
            };
          });
        return {
          id: `radar-${i}`,
          severity: isThreat ? "high" : "medium",
          title: `${brand || "竞品"}:${String(d.title || "")}`,
          desc: `${String(d.summary || "")}${d.impact ? " · 对我们:" + String(d.impact) : ""}`,
          summary: String(d.summary || ""),
          time: radarFreshnessLabel || timeLabel(radarContent.generated_at),
          freshnessStatus: radarFreshness,
          stale: radarFreshness === "stale",
          generatedAt: String(radarContent.generated_at || ""),
          snapshotDate: String(radarContent.snapshot_date || radar.snapshot_date || ""),
          sources: sourceRows,
          sourceRelation: sourceRows.some((source) => source.relationType === "grounding") ? "grounding" : sourceRows.length ? "brand_context" : "missing",
          impact: d.impact ? [{ level: isThreat ? "风险" : "机会", text: String(d.impact) }] : [],
          // D3:雷达来源本就是人话;品牌做成可点 chip(点击带上品牌进弹窗)。
          sourceLine: `竞品雷达 · ${radarFreshnessLabel || "时间未知"}${sourceRows.some((source) => source.relationType === "grounding") ? ` · ${sourceRows.length} 条原始来源` : sourceRows.length ? ` · ${sourceRows.length} 条关联证据，原始引文未保留` : " · 未保留原始链接"}`,
          sourceDetail: `competitor-radar · snapshot ${String(radarContent.snapshot_date || radar.snapshot_date || "unknown")}`,
          brands: brand ? [brand.toUpperCase()] : [],
          totalMentions: sourceRows.length,
          trendPct: isThreat ? "威胁" : "机会",
          raw: d,
        };
      })
    : [];
  const cards = list(record(marketCards).cards);
  const signalCards = cards.slice(0, 8).map((card, index) => {
    const item = record(card);
    const evidence = list(item.evidence);
    const priority = String(item.priority || "info").toLowerCase();
    // 键名细节(summary.run_id 之类)如实保留在 sourceDetail(tooltip)与 sources(弹窗查看源),不上卡面。
    const evidenceKeys = evidence.map((source) => String(record(source).source || record(source).label || "evidence")).filter(Boolean);
    const sourceHuman = humanSignalSource(item.sourceLabel);
    // 竞品品牌卡(entityType=competitor_brand)的品牌名做成可点 chip;诚实:只用后端已有字段,不造数据。
    const brands = String(item.entityType || "") === "competitor_brand" && String(item.entityId || "").trim()
      ? [String(item.entityId).trim().toUpperCase()]
      : [];
    return {
      id: item.id || `signal-${index}`,
      severity: priority === "high" ? "high" : priority === "medium" ? "medium" : "info",
      title: String(item.title || "市场信号待接入"),
      desc: String(item.summary || "暂无摘要"),
      time: timeLabel(item.freshnessLabel || item.generated_at),
      sources: evidence.length
        ? evidence.map((source) => {
            const row = record(source);
            const contentOrigin = String(row.content_origin || item.content_origin || "unknown");
            const sourcePlatform = String(row.source_platform || row.platform || item.source_platform || "");
            return {
              name: String(row.label || row.source || "evidence"),
              url: String(row.url || row.source_url || ""),
              value: String(row.value || ""),
              sourceTable: String(row.source || row.source_table || ""),
              platform: sourcePlatform,
              sourcePlatform,
              content_origin: contentOrigin,
              contentOrigin,
              published_at: String(row.published_at || item.published_at || ""),
              observedAt: String(row.observed_at || item.observed_at || ""),
            };
          })
        : [{
            name: String(item.sourceLabel || "market-intelligence"),
            url: String(item.source_url || ""),
            platform: String(item.source_platform || ""),
            content_origin: String(item.content_origin || "unknown"),
          }],
      actions: list(item.actions).map((action) => String(record(action).label || record(action).title || "")).filter(Boolean),
      thumbnails: list(item.thumbnails).map((value) => String(value)).filter(Boolean),
      sourceLine: evidence.length ? `${sourceHuman} · 本轮证据 ${evidence.length} 条` : sourceHuman,
      sourceDetail: evidenceKeys.join(" · ") || String(item.sourceLabel || "market-intelligence"),
      brands,
      totalMentions: evidence.length,
      trendPct: item.confidence != null ? `${Math.round(Number(item.confidence) * 100)}%` : "证据可查",
      raw: item,
    };
  });
  return [...radarItems, ...signalCards];
}

export function normalizeTopMovers(kolRows: RawValue = [], fitMovers: RawValue = null) {
  // 2026-06-15 V6 Fit Top 真数据:优先用 fit 历史快照 diff 出的真实 Top Movers(变动方向 +/-Δfit)。
  // 不足两天(warming_up)时 available=false,回落到下方 scored-only 视图(诚实,不编造)。
  const fm = record(fitMovers);
  if (fm.available && Array.isArray(fm.movers) && fm.movers.length) {
    const mode = String(fm.mode || "movers");
    return list(fm.movers).slice(0, 5).map((m, index) => {
      const mr = record(m);
      const delta = number(mr.delta) ?? 0;
      return {
        id: mr.kol_pool_id,
        handle: mr.handle || mr.name || `KOL ${index + 1}`,
        badge: String(mr.handle || mr.name || "K").replace(/^@/, "")[0]?.toUpperCase() || "K",
        badgeColor: COLORS[index % COLORS.length],
        type: "pool",
        note: mr.platform || "真实 KOL Pool",
        // mode=movers:显 ±Δfit(真变动);mode=top_fit:fit 静态无变动,显当前 Fit 分。
        deltaFollower: mode === "top_fit" ? `Fit ${mr.fit_now}` : `${delta >= 0 ? "+" : ""}${Number(delta).toFixed(1)}`,
        deltaReach: mode === "top_fit"
          ? (mr.followers ? compact(mr.followers) : (mr.platform || "真实 KOL Pool"))
          : (mr.fit_now != null ? `Fit ${mr.fit_now}` : "无粉丝信号"),
        raw: mr,
      };
    });
  }
  // 四a:V6 Fit Top 卡只展示「真有契合分」的 KOL(scored-only)。
  // 旧口径 (v6_fit != null || followers != null) 会把只有粉丝、无评分的行也放进来,
  // 它们的 deltaFollower 落「待评估」占满卡面,把 frank_of_all_trades(95)等真分挤掉。
  // 改为仅保留 v6_fit != null,DESC 排序、null 垫底,卡面只见真分不见占位。
  return list(kolRows)
    .map(record)
    .filter((item) => item.v6_fit != null)
    .slice()
    .sort((a, b) => {
      const av = number(a.v6_fit);
      const bv = number(b.v6_fit);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    })
    .slice(0, 5)
    .map((item, index) => ({
      id: item.id,
      handle: item.handle || item.display_name || `KOL ${index + 1}`,
      badge: String(item.handle || item.display_name || "K").replace(/^@/, "")[0]?.toUpperCase() || "K",
      badgeColor: item.avatar_color || COLORS[index % COLORS.length],
      type: item.linked_main_kol_id ? "matrix" : "pool",
      note: item.industry_label || item.country || "真实 KOL Pool",
      deltaFollower: item.v6_fit != null ? `Fit ${item.v6_fit}` : "待评估",
      deltaReach: item.followers != null ? compact(item.followers) : "无粉丝信号",
      raw: item,
    }));
}

// 2026-06-12 C10(波3 R1):消费后端 summary.funnel
// shape = { favorites_total, claimed_total, in_project_total, published_total, by_staff[] }
// 后端块未上线前 isReal=false,卡面显示「漏斗数据待后端」,绝不硬编码假数。
export function normalizeKolFunnel(summary: RawValue = {}) {
  const block = record(record(summary).funnel);
  const stages = [
    { key: "favorites", label: "收藏", count: int(block.favorites_total) },
    { key: "claimed", label: "已认领", count: int(block.claimed_total) },
    { key: "in_project", label: "入项目", count: int(block.in_project_total) },
    { key: "published", label: "已发布", count: int(block.published_total) },
  ];
  const isReal = stages.some((stage) => stage.count != null);
  const byStaff = list(block.by_staff).map((row) => record(row));
  return { isReal, stages, byStaff, raw: block };
}

export function normalizeDashboardSourceHealth(bundle: RawValue = {}) {
  const sources = record(record(bundle)._sources);
  const entries = Object.entries(sources).map(([key, value]) => {
    const source = record(value);
    return {
      key,
      label: String(source.label || key),
      ok: source.ok === true,
    };
  });
  const total = entries.length;
  const ready = entries.filter((entry) => entry.ok).length;
  const failed = entries.filter((entry) => !entry.ok).map((entry) => entry.label);
  // 这两个指标当前在 normalizeDashboardMetrics 中明确保持 null，不能把“接口返回”
  // 误写成“能力已接入”。后续订单归因上线时应连同指标契约一起移除。
  const pendingCapabilities = ["GMV", "ROI"];
  return {
    available: total > 0,
    total,
    ready,
    failed,
    pendingCapabilities,
    degraded: failed.length > 0,
    label: total > 0 ? `${ready}/${total} 可用 · ${pendingCapabilities.length} 待接` : DASH,
    detail: [
      total > 0 ? `Dashboard 数据源 ${ready}/${total} 本次可用` : "Dashboard 数据源状态未知",
      failed.length > 0 ? `异常: ${failed.join("、")}` : "数据源响应正常",
      `待接能力: ${pendingCapabilities.join("、")}`,
    ].join("；"),
  };
}

export function normalizeCockpitDashboard(bundle: RawRecord, kolRows: RawList) {
  const dashboard = record(bundle.dashboard);
  const summary = record(dashboard.summary);
  const activeCampaignsBlock = record(summary.active_campaigns);
  const activeCampaignsMeta = normalizeActiveCampaignsMeta(activeCampaignsBlock);
  const starredCampaigns = normalizeStarredCampaigns(bundle.starredProjects);
  const realCampaigns = activeCampaignsMeta.isReal ? normalizeActiveCampaigns(activeCampaignsBlock) : null;
  // 2026-06-12 波3 R7:无 starred 时回退到后端真实 active_campaigns 块,不再整块丢弃。
  const campaigns = starredCampaigns.length ? starredCampaigns : (realCampaigns || []);
  // 有效的服务端 distribution-pack 已经是完整分母,无论 staff/global 都是地图唯一来源。
  // kolRows 只作 pack 缺失、契约损坏或 is_real=false 时的 fallback,避免同一 KOL 被双重计数。
  return {
    metrics: normalizeDashboardMetrics(bundle, kolRows),
    campaigns,
    campaignsMeta: {
      ...activeCampaignsMeta,
      isReal: true,
      source: starredCampaigns.length ? "starred_projects" : "active_campaigns",
      activeCount: starredCampaigns.length ? starredCampaigns.length : activeCampaignsMeta.activeCount,
      fallbackCount: realCampaigns?.length ?? 0,
    },
    calendarDays: normalizeCalendar(bundle.recentContent),
    calendarMeta: { latestDate: latestCalendarDate(bundle.recentContent) },
    aiInsight: normalizeAiInsight(bundle.copilotBrief, bundle.tasks, bundle.aiTodayHot),
    signals: normalizeSignals(bundle.marketCards, bundle.competitorRadar),
    topMovers: normalizeTopMovers(kolRows, bundle.fitMovers),
    mapHierarchy: normalizeMapHierarchy(bundle.distribution, kolRows),
    kolFunnel: normalizeKolFunnel(summary),
    sourceHealth: normalizeDashboardSourceHealth(bundle),
  };
}
