
const DASH = "--";

function record(value: any) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function list(value: any) {
  return Array.isArray(value) ? value : [];
}

function esc(value: any) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function number(value: any) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compact(value: any) {
  const n = number(value);
  if (n == null) return DASH;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

function formatMetricValue(metric: any, scope = "all") {
  const data = record(record(metric).data?.[scope] || record(metric).data?.all);
  const value = data.value;
  if (value === null || value === undefined || data.source === "pending") return DASH;
  if (metric.format === "compact") return compact(value);
  if (metric.format === "percent") return `${Number(value).toFixed(2)}%`;
  if (metric.format === "money") return `$${compact(value)}`;
  if (metric.format === "multiplier") return `${Number(value).toFixed(2)}x`;
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : String(value);
}

function metricStatus(metric: any, scope = "all") {
  const data = record(record(metric).data?.[scope] || record(metric).data?.all);
  if (data.value === null || data.value === undefined || data.source === "pending") {
    return data.waiting || data.trend || "待接入";
  }
  return data.trend || "真实 API";
}

function getMetric(metrics: any, id: any) {
  return list(metrics).find((item: any) => item.id === id) || {};
}

function dateRange(period: any, language: any) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - (period === "weekly" ? 7 : 30));
  const locale = language === "zh" ? "zh-CN" : "en-US";
  return `${start.toLocaleDateString(locale, { month: "short", day: "numeric" })} - ${end.toLocaleDateString(locale, { month: "short", day: "numeric", year: "numeric" })}`;
}

function periodLabel(period: any, language: any) {
  if (language === "zh") return period === "weekly" ? "过去 7 天" : "过去 30 天";
  return period === "weekly" ? "Last 7 days" : "Last 30 days";
}

function platformRows(kolRows: any) {
  const map = new Map();
  for (const row of list(kolRows)) {
    const key = String(row.platform || "unknown").toLowerCase() || "unknown";
    const prev = map.get(key) || { platform: key, count: 0, followers: 0, followerCount: 0, fitSum: 0, fitCount: 0 };
    prev.count += 1;
    if (number(row.followers) != null) {
      prev.followers += number(row.followers);
      prev.followerCount += 1;
    }
    if (number(row.v6_fit) != null) {
      prev.fitSum += number(row.v6_fit);
      prev.fitCount += 1;
    }
    map.set(key, prev);
  }
  return Array.from(map.values())
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)
    .map((row) => ({
      ...row,
      avgFit: row.fitCount ? Math.round(row.fitSum / row.fitCount) : null,
    }));
}

function countryRows(kolRows: any) {
  const map = new Map();
  for (const row of list(kolRows)) {
    const geo = list(row.geo_distribution)[0];
    const key = String(geo?.country || row.country || "").trim();
    if (!key) continue;
    const prev = map.get(key) || { country: key, count: 0, reach: 0 };
    prev.count += 1;
    prev.reach += number(row.avg_views) || number(row.followers) || 0;
    map.set(key, prev);
  }
  return Array.from(map.values()).sort((a, b) => b.reach - a.reach).slice(0, 8);
}

function selectedMetrics(data: any) {
  const metrics = list(record(record(data).dashboard).metrics);
  return ["kol-count", "active-30d", "exposure", "engagement", "gmv", "roi"].map((id) => getMetric(metrics, id));
}

function mdTable(rows: any) {
  return rows.map((row: any) => `| ${row.map((cell: any) => String(cell ?? DASH).replace(/\n/g, " ")).join(" | ")} |`).join("\n");
}

function runtimeSummary(data: any) {
  const dashboard = record(data.dashboard);
  const kolRows = list(data.kolPoolRows);
  const metrics = list(dashboard.metrics);
  const signals = list(dashboard.signals);
  const campaigns = list(dashboard.campaigns);
  return {
    metrics,
    kolRows,
    signals,
    campaigns,
    calendarDays: list(dashboard.calendarDays),
    aiInsight: record(dashboard.aiInsight),
    topMovers: list(dashboard.topMovers),
    notifications: list(data.notifications),
    reminders: list(data.reminders),
    platformRows: platformRows(kolRows),
    countryRows: countryRows(kolRows),
  };
}

export function generateRuntimeReportMarkdown({ period, language, sections, data }: any) {
  const lang = language || "zh";
  const s = runtimeSummary(data || {});
  const title = lang === "zh" ? `# Viltrox 营销${period === "weekly" ? "周" : "月"}报` : `# Viltrox Marketing ${period === "weekly" ? "Weekly" : "Monthly"} Report`;
  const lines = [
    title,
    "",
    `**${lang === "zh" ? "统计周期" : "Period"}:** ${periodLabel(period, lang)} (${dateRange(period, lang)})`,
    `**${lang === "zh" ? "生成时间" : "Generated"}:** ${new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-US")}`,
    `**${lang === "zh" ? "数据规则" : "Data rule"}:** ${lang === "zh" ? "只展示真实 API 与明确待接入项,不使用 mock 业务数字。" : "Only real API data and explicit pending items are shown. No mock business values."}`,
    "",
  ];

  if (sections.kpiOverview) {
    lines.push("## KPI 总览", "");
    lines.push(mdTable([
      ["指标", "All", "KOL", "公司账号", "状态"],
      ["---", "---", "---", "---", "---"],
      ...selectedMetrics(data).map((metric) => [
        metric.label || "指标",
        formatMetricValue(metric, "all"),
        formatMetricValue(metric, "kol"),
        formatMetricValue(metric, "company"),
        metricStatus(metric, "all"),
      ]),
    ]));
    lines.push("");
  }

  if (sections.kolVsCompany) {
    const kolCount = getMetric(s.metrics, "kol-count");
    const exposure = getMetric(s.metrics, "exposure");
    const engagement = getMetric(s.metrics, "engagement");
    lines.push("## 公司账号 vs KOL", "");
    lines.push(mdTable([
      ["维度", "KOL", "公司账号", "说明"],
      ["---", "---", "---", "---"],
      ["账号数", formatMetricValue(kolCount, "kol"), formatMetricValue(kolCount, "company"), metricStatus(kolCount, "all")],
      ["曝光", formatMetricValue(exposure, "kol"), formatMetricValue(exposure, "company"), metricStatus(exposure, "all")],
      ["互动率", formatMetricValue(engagement, "kol"), formatMetricValue(engagement, "company"), metricStatus(engagement, "all")],
    ]));
    lines.push("");
  }

  if (sections.platformDeepDive) {
    lines.push("## 平台 Deep Dive", "");
    if (s.platformRows.length) {
      lines.push(mdTable([
      ["平台", "KOL 数", "粉丝信号", "平均 V6 Fit"],
      ["---", "---", "---", "---"],
      ...s.platformRows.map((row) => [row.platform, row.count, row.followerCount ? compact(row.followers) : "无粉丝信号", row.avgFit ?? "待评估"]),
      ]));
    } else {
      lines.push("数据不足: KOL 平台字段暂时无信号。");
    }
    lines.push("");
  }

  if (sections.goodNews) {
    lines.push("## 好消息", "");
    if (s.topMovers.length) {
      for (const mover of s.topMovers.slice(0, 5)) {
        lines.push(`- ${mover.handle}: ${mover.deltaFollower || "Fit 待评估"} · ${mover.deltaReach || "Reach 无信号"}`);
      }
    } else {
      lines.push("- 暂无真实 Top Movers 候选。");
    }
    if (s.campaigns.length) {
      lines.push("");
      lines.push("真实项目:");
      for (const campaign of s.campaigns.slice(0, 5)) {
        lines.push(`- ${campaign.name}: ${campaign.stats?.kolCount ?? DASH} KOL · ${campaign.stats?.totalReach ?? DASH} 曝光 · ${campaign.bottleneckText || "详情接入中"}`);
      }
    }
    lines.push("");
  }

  if (sections.badNews) {
    lines.push("## 问题与风险", "");
    const pending = selectedMetrics(data).filter((metric) => metricStatus(metric, "all") !== "真实 API");
    for (const metric of pending) lines.push(`- ${metric.label}: ${metricStatus(metric, "all")}`);
    for (const signal of s.signals.filter((item) => item.severity === "high" || item.severity === "medium").slice(0, 5)) {
      lines.push(`- ${signal.title}: ${signal.desc}`);
    }
    if (!pending.length && !s.signals.length) lines.push("- 暂无真实风险信号。");
    lines.push("");
  }

  if (sections.brandSentiment) {
    lines.push("## 品牌风评分析", "", "评论情绪 / 关键词聚合 endpoint 待接入。当前报告不展示 mock 评论数或情绪百分比。", "");
  }

  if (sections.contentRecs) {
    const ai = s.aiInsight;
    lines.push("## 内容改进建议", "");
    const strengthen = list(ai.strengthen);
    if (strengthen.length) {
      for (const item of strengthen.slice(0, 5)) lines.push(`- ${item.text}${item.detail ? `: ${item.detail}` : ""}`);
    } else {
      lines.push("- 暂无可执行候选。");
    }
    lines.push("");
  }

  if (sections.eventsProgress) {
    lines.push("## 活动进度", "", "events/upcoming endpoint 待接入。当前不展示 prototype 活动日期、地点或预算。", "");
  }

  if (sections.attribution) {
    lines.push("## 归因管线", "", "- GMV: 待 Shopify 订单接入。", "- ROI: 待成本与订单接入。", "");
  }

  if (sections.aiInsights) {
    const ai = s.aiInsight;
    const decision = record(ai.todayDecision);
    lines.push("## 智能判断", "");
    lines.push(`- 判断: ${decision.text || "暂无可执行候选"}`);
    lines.push(`- 证据状态: ${ai.poweredBy || "copilot-brief 无信号"}`);
    lines.push(`- 更新时间: ${ai.updatedLabel || "无信号"}`);
    lines.push("");
  }

  lines.push("---", "由 V-KPI 真实 API 运行态生成。");
  return lines.join("\n");
}

function metricCardsHtml(metrics: any) {
  return selectedMetrics({ dashboard: { metrics } }).map((metric: any) => `
    <div class="kpi-card ${metricStatus(metric, "all") === "真实 API" ? "" : "pending"}">
      <div class="kpi-label">${esc(metric.label || "Metric")}</div>
      <div class="kpi-value">${esc(formatMetricValue(metric, "all"))}</div>
      <div class="kpi-sub">${esc(metricStatus(metric, "all"))}</div>
    </div>
  `).join("");
}

export function generateRuntimeVisualHtml({ period, language, sections, data }: any) {
  const lang = language || "zh";
  const s = runtimeSummary(data || {});
  const title = lang === "zh" ? `Viltrox 营销${period === "weekly" ? "周" : "月"}报` : `Viltrox Marketing ${period === "weekly" ? "Weekly" : "Monthly"} Report`;
  let html = `<div class="header">
    <div class="brand">VILTROX</div>
    <h1>${esc(title)}</h1>
    <div class="meta"><span>${esc(periodLabel(period, lang))}</span><span>${esc(dateRange(period, lang))}</span><span>Real API / Pending only</span></div>
  </div>`;

  if (sections.kpiOverview) {
    html += `<section><h2>KPI 总览</h2><div class="kpi-grid">${metricCardsHtml(s.metrics)}</div></section>`;
  }
  if (sections.kolVsCompany) {
    const rows = [
      ["账号数", formatMetricValue(getMetric(s.metrics, "kol-count"), "kol"), formatMetricValue(getMetric(s.metrics, "kol-count"), "company")],
      ["曝光", formatMetricValue(getMetric(s.metrics, "exposure"), "kol"), formatMetricValue(getMetric(s.metrics, "exposure"), "company")],
      ["互动率", formatMetricValue(getMetric(s.metrics, "engagement"), "kol"), formatMetricValue(getMetric(s.metrics, "engagement"), "company")],
    ];
    html += `<section><h2>公司账号 vs KOL</h2><table><tr><th>维度</th><th>KOL</th><th>公司账号</th></tr>${rows.map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`).join("")}</table></section>`;
  }
  if (sections.platformDeepDive) {
    html += `<section><h2>平台 Deep Dive</h2>`;
    html += s.platformRows.length
      ? `<table><tr><th>平台</th><th>KOL</th><th>粉丝信号</th><th>V6 Fit</th></tr>${s.platformRows.map((row) => `<tr><td>${esc(row.platform)}</td><td>${row.count}</td><td>${esc(row.followerCount ? compact(row.followers) : "无粉丝信号")}</td><td>${esc(row.avgFit ?? "待评估")}</td></tr>`).join("")}</table>`
      : `<div class="empty">数据不足</div>`;
    html += `</section>`;
  }
  if (sections.goodNews) {
    html += `<section><h2>好消息</h2><div class="news-list">`;
    html += s.topMovers.length
      ? s.topMovers.slice(0, 5).map((item) => `<div class="news-item good"><span class="news-tag good">KOL</span><span class="news-text">${esc(item.handle)} · ${esc(item.deltaFollower)} · ${esc(item.deltaReach)}</span></div>`).join("")
      : `<div class="empty">暂无真实 Top Movers 候选</div>`;
    html += `</div></section>`;
  }
  if (sections.badNews) {
    const pending = selectedMetrics({ dashboard: { metrics: s.metrics } }).filter((metric) => metricStatus(metric, "all") !== "真实 API");
    html += `<section><h2>问题与风险</h2><div class="news-list">`;
    html += pending.map((metric) => `<div class="news-item bad"><span class="news-tag bad">待接入</span><span class="news-text">${esc(metric.label)} · ${esc(metricStatus(metric, "all"))}</span></div>`).join("");
    html += s.signals.slice(0, 5).map((signal) => `<div class="news-item bad"><span class="news-tag bad">信号</span><span class="news-text">${esc(signal.title)} · ${esc(signal.desc)}</span></div>`).join("");
    if (!pending.length && !s.signals.length) html += `<div class="empty">暂无真实风险信号</div>`;
    html += `</div></section>`;
  }
  if (sections.brandSentiment) html += `<section><h2>品牌风评分析</h2><div class="callout">评论情绪 / 关键词聚合 endpoint 待接入。</div></section>`;
  if (sections.contentRecs) {
    const actions = list(s.aiInsight.strengthen);
    html += `<section><h2>内容改进建议</h2>${actions.length ? actions.slice(0, 5).map((item) => `<div class="card"><strong>${esc(item.text)}</strong><br>${esc(item.detail || "证据待展开")}</div>`).join("") : `<div class="empty">暂无可执行候选</div>`}</section>`;
  }
  if (sections.eventsProgress) html += `<section><h2>活动进度</h2><div class="callout">events/upcoming endpoint 待接入。当前不展示 prototype 活动日期、地点或预算。</div></section>`;
  if (sections.attribution) html += `<section><h2>归因管线</h2><div class="pipeline-title">GMV / ROI 待 Shopify 与成本数据接入。</div></section>`;
  if (sections.aiInsights) {
    const decision = record(s.aiInsight.todayDecision);
    html += `<section><h2>智能判断</h2><div class="ai-callout"><div class="ai-confidence">${esc(s.aiInsight.poweredBy || "copilot-brief 无信号")}</div><div class="ai-body">${esc(decision.text || "暂无可执行候选")}</div></div></section>`;
  }
  html += `<div class="footer">Generated from V-KPI real runtime data · ${esc(new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-US"))}</div>`;
  return html;
}
