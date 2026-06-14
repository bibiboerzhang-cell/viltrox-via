// @ts-nocheck
import { METRICS } from "./data/metrics";
import { CURRENT_USER } from "./data/currentUser";
import { getCountryInfo } from "./data/countryInfo";

const DASH = "—";
const COLORS = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"];
const COUNTRY_CENTROIDS = {
  AE: [24.0, 54.0], AT: [47.6, 14.1], AU: [-25.3, 133.8], BE: [50.5, 4.5],
  BR: [-14.2, -51.9], CA: [56.1, -106.3], CH: [46.8, 8.2], CN: [35.9, 104.2],
  DE: [51.2, 10.5], ES: [40.5, -3.7], FR: [46.2, 2.2], GB: [55.4, -3.4],
  HK: [22.3, 114.2], ID: [-0.8, 113.9], IE: [53.4, -8.2], IN: [20.6, 78.9],
  IT: [41.9, 12.6], JP: [36.2, 138.3], KR: [36.5, 127.8], MX: [23.6, -102.5],
  MY: [4.2, 101.9], NL: [52.1, 5.3], NZ: [-40.9, 174.9], PH: [12.9, 121.8],
  RU: [61.5, 105.3], SE: [60.1, 18.6], SG: [1.4, 103.8], TH: [15.9, 101.0],
  TW: [23.7, 121.0], UK: [55.4, -3.4], US: [39.8, -98.6], VN: [14.1, 108.3],
  ZA: [-30.6, 22.9],
};

function record(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function number(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function int(value) {
  const parsed = number(value);
  return parsed == null ? null : Math.round(parsed);
}

function percent(numerator, denominator) {
  const top = number(numerator);
  const bottom = number(denominator);
  if (top == null || bottom == null || bottom <= 0) return null;
  return (top / bottom) * 100;
}

function hashColor(seed) {
  const raw = String(seed || "");
  let hash = 0;
  for (const char of raw) hash = (hash * 31 + char.charCodeAt(0)) % COLORS.length;
  return COLORS[Math.abs(hash)];
}

function compact(value) {
  const n = number(value);
  if (n == null) return DASH;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

function percentLabel(value, digits = 0) {
  const n = number(value);
  return n == null ? DASH : `${n.toFixed(digits)}%`;
}

function jitter(seed, salt, spread = 0.18) {
  const raw = String(seed || "");
  let hash = salt * 997;
  for (const char of raw) hash = (hash * 31 + char.charCodeAt(0)) % 100000;
  return ((hash / 100000) - 0.5) * spread;
}

function countryCentroid(code) {
  const normalized = code === "UK" ? "GB" : code;
  const pair = COUNTRY_CENTROIDS[normalized] || COUNTRY_CENTROIDS[code];
  return pair ? { lat: pair[0], lng: pair[1] } : null;
}

function asCityEntries(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        const row = record(item);
        const name = String(row.name || row.city || row.label || "");
        return name ? [name, row] : null;
      })
      .filter(Boolean);
  }
  return Object.entries(record(value));
}

function normalizeKolPins(rows, cityLat, cityLng) {
  return list(rows).map((raw, index) => {
    const item = record(raw);
    const seed = item.id || item.handle || item.display_name || index;
    const followers = int(item.followers);
    const views = int(item.avg_views);
    const fit = int(item.viltrox_fit_score);
    const source = String(item.location_source || "");
    const topic = String(item.primary_topic || item.content_style || item.platform || "真实 KOL Pool");
    return {
      id: item.id,
      handle: String(item.handle || item.display_name || `KOL ${index + 1}`),
      name: String(item.display_name || item.handle || `KOL ${index + 1}`),
      platform: String(item.platform || "kol-pool"),
      niche: source === "country_scatter" ? `${topic} · 国家级散点` : topic,
      engagement: fit != null ? `Fit ${fit}` : views != null ? `曝光 ${compact(views)}` : followers != null ? `粉丝 ${compact(followers)}` : "待评估",
      followers: followers != null ? compact(followers) : DASH,
      lat: cityLat + jitter(seed, 1),
      lng: cityLng + jitter(seed, 2),
      venues: [],
      raw: item,
    };
  });
}

function timeLabel(value) {
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

function formatDay(value) {
  const date = new Date(String(value || Date.now()));
  if (Number.isNaN(date.getTime())) return { day: DASH, weekday: DASH, today: false };
  const today = new Date();
  return {
    day: date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }),
    weekday: date.toLocaleDateString("en-US", { weekday: "short" }),
    today: date.toDateString() === today.toDateString(),
  };
}

function metricData(value, color, options = {}) {
  const hasValue = value !== null && value !== undefined;
  const source = options.source || (hasValue ? "real" : "pending");
  return {
    value: hasValue ? value : null,
    trend: hasValue ? options.trend || "真实 API" : options.waiting || "数据待接入",
    source,
    sourceLabel: options.sourceLabel || null,
    color,
    spark: hasValue ? options.spark || null : null,
    waiting: options.waiting || "数据待接入",
    anomaly: options.anomaly || null,
  };
}

function scopeKey(scope) {
  return scope === "company" ? "owned" : scope;
}

function scopeLabel(scope) {
  const key = scopeKey(scope);
  if (key === "owned") return "Owned";
  if (key === "kol") return "KOL";
  return "K + O";
}

function maturityForScope(dashboard, scope) {
  const key = scopeKey(scope);
  const contract = record(dashboard.metric_contract);
  const scopes = record(contract.scopes);
  const direct = record(dashboard.metric_maturity_by_scope);
  return record(scopes[key] || direct[key] || scopes.all || direct.all || dashboard.metric_maturity);
}

function maturityLabel(dashboard, scope) {
  const maturity = maturityForScope(dashboard, scope);
  const days = int(maturity.snapshot_days) ?? 0;
  const required = int(maturity.required_days) ?? 30;
  return String(maturity.maturity_label || `累积中 ${days}/${required}`);
}

function isMaturityReady(dashboard, scope) {
  const maturity = maturityForScope(dashboard, scope);
  const days = int(maturity.snapshot_days) ?? 0;
  const required = int(maturity.required_days) ?? 30;
  return Boolean(maturity.is_ready) || days >= required;
}

function accumulatingMetricData(value, color, dashboard, scope, options = {}) {
  const label = maturityLabel(dashboard, scope);
  return metricData(value, color, {
    ...options,
    source: "accumulating",
    sourceLabel: label,
    trend: options.trend || `${scopeLabel(scope)} · ${label}`,
    waiting: options.waiting || `${label} · 30d 数据累计中`,
  });
}

function windowMetricData(value, color, dashboard, scope, options = {}) {
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

export function normalizeCurrentUser(apiUser, fallback = {}) {
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
    email: String(user.email || CURRENT_USER.email),
    avatar: initials,
    avatarUrl: String(user.avatar_url || fallback.userAvatar || ""),
    avatarGradient: CURRENT_USER.avatarGradient,
  };
}

export function normalizeAlerts(rawAlerts = []) {
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

export function normalizeDashboardMetrics(bundle, kolRows = []) {
  const dashboard = record(bundle.dashboard);
  const summary = record(dashboard.summary);
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
  const evidenceCoverageText = `实时 · evidence 覆盖 ${percentLabel(coveragePercent, 0)}`;
  const evidenceVideoText = `实时 · 基于 ${viewCovered != null ? viewCovered.toLocaleString() : DASH} 条已抓视频`;
  const active30WindowDays = int(evidenceActive30.window_days) ?? 30;
  const evidenceActive30Text = `实时 · 近 ${active30WindowDays} 天发布/同步`;
  const active30ByScope = record(summary.active_30d_by_scope);
  const exposureByScope = record(summary.exposure_30d_by_scope);
  const engagementByScope = record(summary.engagement_rate_by_scope);
  // 防御性标注(2026-06-12 波3 R2):单条 evidence 占比 >80% 时提示数据被单源污染。
  // 数据问题本身由主控清理;这里只做卡面提示,不改数值。
  const moversTabs = record(rosterDetail.movers_tabs);
  const maxTabValue = (rows) => list(rows).reduce((max, row) => Math.max(max, number(record(row).value) ?? 0), 0);
  const topEvidenceViews = maxTabValue(moversTabs.by_views);
  const topEvidenceEngagement = maxTabValue(moversTabs.by_engagement);
  const totalEngagement = number(evidenceEngagement.total_engagement);
  const SINGLE_SOURCE_WARNING = "⚠ 单源占比异常";
  const exposureAnomaly = totalExposure != null && totalExposure > 0 && topEvidenceViews / totalExposure > 0.8;
  const engagementAnomaly = totalEngagement != null && totalEngagement > 0 && topEvidenceEngagement / totalEngagement > 0.8;

  const values = {
    "kol-count": {
      all: metricData(rosterAll, "#a855f7", { source: "evidence_metrics", sourceLabel: "实时", trend: "实时 · evidence active roster" }),
      kol: metricData(rosterKol, "#ec4899", { source: "evidence_metrics", sourceLabel: "实时", trend: "实时 · KOL evidence active" }),
      company: metricData(rosterCompany, "#06b6d4", { source: "owned_matrix", sourceLabel: "实时", trend: "实时 · 官方矩阵 active" }),
    },
    "active-30d": {
      all: number(evidenceActive30.all) != null ? metricData(number(evidenceActive30.all), "#a855f7", { source: "evidence_metrics", sourceLabel: "实时", trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · active_30d 累计中` }),
      kol: number(evidenceActive30.kol) != null ? metricData(number(evidenceActive30.kol), "#ec4899", { source: "evidence_metrics", sourceLabel: "实时", trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL active_30d 累计中` }),
      company: number(evidenceActive30.company ?? evidenceActive30.owned) != null ? metricData(number(evidenceActive30.company ?? evidenceActive30.owned), "#06b6d4", { source: "owned_matrix", sourceLabel: "实时", trend: evidenceActive30Text }) : windowMetricData(number(active30ByScope.owned ?? active30ByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方 active_30d 累计中` }),
    },
    exposure: {
      all: totalExposure != null ? metricData(totalExposure, "#a855f7", { source: "evidence_metrics", sourceLabel: "实时", trend: evidenceCoverageText, anomaly: exposureAnomaly ? SINGLE_SOURCE_WARNING : null }) : windowMetricData(number(exposureByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · 不使用 lifetime 代替 30d` }),
      // 2026-06-12 波3 R6:KOL 口径不再复用全量 totalExposure(避免「KOL 曝光=全部曝光」假象)
      kol: windowMetricData(number(exposureByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL 30d 曝光累计中(不复用全量口径)` }),
      // 官方矩阵 channel-level 30d 真实增量(summary.exposure_30d_by_scope.owned/company,由 _build_company_window_metrics 写入);
      // 后端给到非 null 即走实时,否则回退到原「累积中」窗口口径。镜像 active-30d.company 的 owned_matrix 模式。
      company: number(exposureByScope.owned ?? exposureByScope.company) != null ? metricData(number(exposureByScope.owned ?? exposureByScope.company), "#06b6d4", { source: "owned_matrix", sourceLabel: "实时", trend: "实时 · 官方矩阵 30d" }) : windowMetricData(number(exposureByScope.owned ?? exposureByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方 30d 曝光累计中` }),
    },
    engagement: {
      all: engagementPercent != null ? metricData(engagementPercent, "#a855f7", { source: "evidence_metrics", sourceLabel: "实时", trend: evidenceVideoText, anomaly: engagementAnomaly ? SINGLE_SOURCE_WARNING : null }) : windowMetricData(number(engagementByScope.all), "#a855f7", dashboard, "all", { waiting: `${maturityLabel(dashboard, "all")} · 互动率累计中` }),
      // 2026-06-12 波3 R6:KOL 口径不再复用全量 engagementPercent
      kol: windowMetricData(number(engagementByScope.kol), "#ec4899", dashboard, "kol", { waiting: `${maturityLabel(dashboard, "kol")} · KOL 互动率累计中(不复用全量口径)` }),
      // 官方矩阵最新快照真实互动率(summary.engagement_rate_by_scope.owned/company,百分数,由 _build_company_window_metrics 写入)。
      company: number(engagementByScope.owned ?? engagementByScope.company) != null ? metricData(number(engagementByScope.owned ?? engagementByScope.company), "#06b6d4", { source: "owned_matrix", sourceLabel: "实时", trend: "实时 · 官方互动率" }) : windowMetricData(number(engagementByScope.owned ?? engagementByScope.company), "#06b6d4", dashboard, "company", { waiting: `${maturityLabel(dashboard, "company")} · 官方互动率累计中` }),
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

  return METRICS.map((metric) => ({
    ...metric,
    data: values[metric.id] || metric.data,
    rosterDetail: metric.id === "kol-count" ? rosterDetail : undefined,
    sub: metric.id === "exposure" && totalExposure != null ? evidenceCoverageText : metric.id === "engagement" && engagementPercent != null ? evidenceVideoText : metric.id === "exposure" ? "30d 增量，不取 lifetime" : metric.sub,
  }));
}

function emptyFunnel(projectCount) {
  return ["发现", "已联系", "已回复", "已合作", "已发货", "已到货", "已发布", "已统计", "已关闭"].map((name, index) => ({
    name,
    label: `${index + 1}.${name}`,
    count: index === 0 ? projectCount || 0 : 0,
  }));
}

export function normalizeCampaigns(rows = []) {
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
      lastUpdate: "真实 API",
      owner: "待接入",
      startDate: "待接入",
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

function normalizeActiveCampaigns(block = {}) {
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

function normalizeActiveCampaignsMeta(block = {}) {
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

function timestampOrEmpty(value) {
  const raw = String(value || "");
  const time = raw ? Date.parse(raw) : Number.NEGATIVE_INFINITY;
  return Number.isFinite(time) ? time : Number.NEGATIVE_INFINITY;
}

function normalizeProjectFunnel(stageCounts = {}) {
  const counts = record(stageCounts);
  const keys = ["discovery", "contacted", "replied", "agreed", "shipped", "received", "published", "measured", "closed"];
  const labels = ["发现", "已联系", "已回复", "已合作", "已发货", "已到货", "已发布", "已统计", "已关闭"];
  return keys.map((key, index) => ({
    name: labels[index],
    label: `${index + 1}.${labels[index]}`,
    count: int(counts[key]) || 0,
  }));
}

function normalizeStarredCampaigns(rows = []) {
  const latestPublishOf = (row) => String(record(row).latest_publish_date || record(row).latestEvidencePublishDate || record(row).evidencePublishDate || "");
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
function calendarDateKey(value) {
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
export function latestCalendarDate(items = []) {
  let latest = "";
  for (const raw of list(items)) {
    const item = record(raw);
    const key = calendarDateKey(item.posted_at || item.published_at || item.created_at);
    if (/^\d{4}-\d{2}-\d{2}$/.test(key) && key > latest) latest = key;
  }
  return latest || null;
}

export function normalizeCalendar(items = []) {
  const buckets = new Map();
  for (const raw of list(items)) {
    const item = record(raw);
    const dateKey = calendarDateKey(item.posted_at || item.published_at || item.created_at);
    const meta = formatDay(dateKey);
    if (!buckets.has(dateKey)) buckets.set(dateKey, { ...meta, items: [] });
    buckets.get(dateKey).items.push({
      platform: String(item.platform || "").toLowerCase().includes("instagram") ? "IG" : String(item.platform || "").toLowerCase().includes("youtube") ? "YT" : "internal",
      time: String(item.posted_at || item.published_at || "").slice(11, 16) || "--:--",
      label: `${item.account_handle || item.kol_handle || "账号待接入"} · ${item.title || "内容标题待接入"}`,
      color: hashColor(item.platform || item.account_handle),
      raw: item,
    });
  }
  return Array.from(buckets.values()).slice(0, 7);
}

export function normalizeAiInsight(copilotBrief = {}, tasks = {}) {
  const brief = record(copilotBrief);
  const summary = record(brief.summary);
  const items = list(brief.items);
  const actions = list(brief.actions);
  const taskItems = list(record(tasks).tasks);
  const headline = String(brief.headline || summary.headline || "暂无可执行候选");
  return {
    // 2026-06-12 波3 R4:此前 72% 为前端硬编码假置信度;brief 产物不含模型置信度,诚实置 null(卡面不显示 conf)。
    confidence: null,
    updatedLabel: brief.last_run_at ? timeLabel(brief.last_run_at) : "无信号",
    todayDecision: {
      text: headline,
      amount: "--",
      reason: brief.is_real ? "来自 copilot-brief 只读产物" : "当前没有可用真实候选",
      primaryAction: "查看证据",
      secondaryAction: "稍后处理",
    },
    strengthen: (actions.length ? actions : items).slice(0, 3).map((item, index) => ({
      text: String(record(item).title || record(item).label || record(item).action || `建议 ${index + 1}`),
      detail: String(record(item).body || record(item).reason || record(item).summary || ""),
    })),
    weaken: [],
    todayContent: taskItems.slice(0, 3).map((item) => String(record(item).title || record(item).body || "任务待复核")),
    poweredBy: brief.source || "真实 API / 无 LLM 调用",
    raw: brief,
  };
}

export function normalizeSignals(marketCards = {}) {
  const cards = list(record(marketCards).cards);
  return cards.slice(0, 8).map((card, index) => {
    const item = record(card);
    const evidence = list(item.evidence);
    const priority = String(item.priority || "info").toLowerCase();
    return {
      id: item.id || `signal-${index}`,
      severity: priority === "high" ? "high" : priority === "medium" ? "medium" : "info",
      title: String(item.title || "市场信号待接入"),
      desc: String(item.summary || "暂无摘要"),
      time: timeLabel(item.freshnessLabel || item.generated_at),
      sources: evidence.length
        ? evidence.map((source) => ({ name: String(record(source).source || record(source).label || "evidence"), url: record(source).url || "" }))
        : [{ name: String(item.sourceLabel || "market-intelligence"), url: "" }],
      totalMentions: evidence.length,
      trendPct: item.confidence != null ? `${Math.round(Number(item.confidence) * 100)}%` : "证据可查",
      raw: item,
    };
  });
}

export function normalizeTopMovers(kolRows = []) {
  // 四a:V6 Fit Top 卡只展示「真有契合分」的 KOL(scored-only)。
  // 旧口径 (v6_fit != null || followers != null) 会把只有粉丝、无评分的行也放进来,
  // 它们的 deltaFollower 落「待评估」占满卡面,把 frank_of_all_trades(95)等真分挤掉。
  // 改为仅保留 v6_fit != null,DESC 排序、null 垫底,卡面只见真分不见占位。
  return list(kolRows)
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

export function normalizeMapHierarchy(distribution = {}, kolRows = []) {
  const countries = list(record(distribution).countries);
  const hierarchy = {};
  for (const row of countries) {
    const item = record(row);
    const code = String(item.code || item.country_code || item.name || "");
    if (!code) continue;
    const centroid = countryCentroid(code) || { lat: 0, lng: 0 };
    hierarchy[code] = {
      lat: number(item.lat) || number(item.latitude) || centroid.lat,
      lng: number(item.lng) || number(item.longitude) || centroid.lng,
      count: int(item.count) || int(item.kol_count) || 0,
      revenue: item.exposure ? compact(item.exposure) : "",
      cities: {},
    };
    for (const [cityName, cityRaw] of asCityEntries(item.cities)) {
      const cityItem = record(cityRaw);
      const lat = number(cityItem.lat) || number(cityItem.latitude);
      const lng = number(cityItem.lng) || number(cityItem.longitude);
      if (lat == null || lng == null) continue;
      hierarchy[code].cities[cityName] = {
        lat,
        lng,
        count: int(cityItem.count) || int(cityItem.kol_count) || 0,
        revenue: cityItem.exposure ? compact(cityItem.exposure) : "",
        kols: normalizeKolPins(cityItem.sample_kols || cityItem.kols, lat, lng),
        raw: cityItem,
      };
    }
  }

  for (const row of list(kolRows)) {
    const item = record(row);
    const rawCode = String(item.country || list(item.geo_distribution)[0]?.country || "").trim().toUpperCase();
    const countryInfo = getCountryInfo(rawCode);
    const code = countryInfo?.code || rawCode;
    const cityName = String(item.city || item.location_city || item.location || "国家分布").trim();
    const centroid = countryCentroid(code);
    const lat = number(item.lat) || number(item.latitude) || centroid?.lat;
    const lng = number(item.lng) || number(item.longitude) || centroid?.lng;
    if (!code || !cityName || lat == null || lng == null) continue;
    if (!hierarchy[code]) {
      hierarchy[code] = { lat, lng, count: 0, revenue: "", cities: {} };
    }
    const bucket = hierarchy[code].cities[cityName] || {
      lat: lat + jitter(item.id || item.handle, 21, 0.8),
      lng: lng + jitter(item.id || item.handle, 22, 0.8),
      count: 0,
      revenue: "",
      kols: [],
      raw: {},
    };
    bucket.count += 1;
    hierarchy[code].count = Math.max(int(hierarchy[code].count) || 0, Object.values(hierarchy[code].cities || {}).reduce((sum, city) => sum + (int(city.count) || 0), 0) + 1);
    bucket.kols.push(...normalizeKolPins([item], bucket.lat, bucket.lng));
    hierarchy[code].cities[cityName] = bucket;
  }
  return Object.keys(hierarchy).length ? hierarchy : null;
}

// 2026-06-12 C10(波3 R1):消费后端 summary.funnel
// shape = { favorites_total, claimed_total, in_project_total, published_total, by_staff[] }
// 后端块未上线前 isReal=false,卡面显示「漏斗数据待后端」,绝不硬编码假数。
export function normalizeKolFunnel(summary = {}) {
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

export function normalizeV615Dashboard(bundle, kolRows) {
  const dashboard = record(bundle.dashboard);
  const summary = record(dashboard.summary);
  const activeCampaignsBlock = record(summary.active_campaigns);
  const activeCampaignsMeta = normalizeActiveCampaignsMeta(activeCampaignsBlock);
  const starredCampaigns = normalizeStarredCampaigns(bundle.starredProjects);
  const realCampaigns = activeCampaignsMeta.isReal ? normalizeActiveCampaigns(activeCampaignsBlock) : null;
  // 2026-06-12 波3 R7:无 starred 时回退到后端真实 active_campaigns 块,不再整块丢弃。
  const campaigns = starredCampaigns.length ? starredCampaigns : (realCampaigns || []);
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
    aiInsight: normalizeAiInsight(bundle.copilotBrief, bundle.tasks),
    signals: normalizeSignals(bundle.marketCards),
    topMovers: normalizeTopMovers(kolRows),
    mapHierarchy: normalizeMapHierarchy(bundle.distribution, kolRows),
    kolFunnel: normalizeKolFunnel(summary),
  };
}
