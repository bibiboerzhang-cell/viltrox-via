import React, { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { BarChart3, Database, Download, ExternalLink, Loader2, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { loadStoredState, saveStoredState } from "../../lib/storage";

const e = React.createElement;

const SCOPE_OPTIONS = [
  { id: "all", label: "All", desc: "K + O" },
  { id: "kol", label: "KOL", desc: "R-VISIBLE" },
  { id: "company", label: "公司账号", desc: "Owned" },
];

function record(value: any) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function number(value: any) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compact(value: any) {
  const n = number(value);
  if (n == null) return "--";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return Math.round(n).toLocaleString();
}

function formatValue(metric: any, value: any) {
  const n = number(value);
  if (n == null) return "--";
  if (metric.format === "compact") return compact(n);
  if (metric.format === "percent") return `${n.toFixed(2)}%`;
  if (metric.format === "money") return `$${compact(n)}`;
  if (metric.format === "multiplier") return `${n.toFixed(2)}x`;
  return n.toLocaleString();
}

function statusTone(source: any) {
  if (source === "real") return { text: "text-emerald-200", bg: "bg-emerald-500/[0.12]", border: "border-emerald-400/20" };
  if (source === "accumulating") return { text: "text-cyan-200", bg: "bg-cyan-500/[0.10]", border: "border-cyan-400/20" };
  return { text: "text-amber-200", bg: "bg-amber-500/[0.10]", border: "border-amber-400/20" };
}

function sparkPath(points: any, width = 560, height = 128) {
  const values = (Array.isArray(points) ? points : []).map(number).filter((v): v is number => v != null);
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  return values
    .map((value, index) => {
      const x = (index / Math.max(1, values.length - 1)) * width;
      const y = height - ((value - min) / range) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

function pct(value: any, digits = 1) {
  const n = number(value);
  return n == null ? "--" : `${(n * 100).toFixed(digits)}%`;
}

function shortText(value: any, limit = 72) {
  const raw = String(value || "");
  return raw.length > limit ? `${raw.slice(0, limit - 1)}…` : raw;
}

const PARTNERSHIP_LABELS = {
  long_term: { label: "长期合作", desc: "多项目合作 / 多次回片", color: "#10b981" },
  in_production: { label: "生产中", desc: "已寄样，等待内容产出", color: "#f59e0b" },
  one_off: { label: "一次性", desc: "单项目已回片", color: "#3b82f6" },
  pending: { label: "待签", desc: "联系 / 回复 / 确认前", color: "#a855f7" },
};

const COMPANY_GROUP_LABELS = {
  main_brand: { label: "主品牌", desc: "Official / Global 官方主账号", color: "#10b981" },
  product_line: { label: "产品线", desc: "Cine / Flash / Gear 产品矩阵", color: "#f59e0b" },
  regional: { label: "区域", desc: "区域 / 本地化官方账号", color: "#3b82f6" },
};

const MOVER_TABS = [
  { id: "by_views", label: "播放量", metric: "views" },
  { id: "by_activity", label: "最活跃", metric: "evidence" },
  { id: "by_recent", label: "近期爆款", metric: "views" },
  { id: "by_engagement", label: "互动", metric: "engagement" },
];

function metricLabel(tab: any, row: any) {
  const value = number(row?.value);
  if (value == null) return "--";
  if (tab === "by_activity") return `${Math.round(value).toLocaleString()} 条`;
  if (tab === "by_engagement") return `${compact(value)} 互动`;
  return `${compact(value)} 播放`;
}

function csvCell(value: any) {
  const raw = value === null || value === undefined ? "" : String(value);
  return `"${raw.replace(/"/g, '""')}"`;
}

function downloadCsv(filename: any, rows: any) {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const csv = rows.map((row: any) => row.map(csvCell).join(",")).join("\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(url);
}

export function KPIDetailModal({ kpiId, initialScope, metrics = [], onClose, onDrillToKolPool }: any) {
  const { t } = useT();
  const stored = loadStoredState();
  const [timeRange, setTimeRange] = useState(stored.kpiTimeRange || "30d");
  const [scope, setScope] = useState(initialScope || stored.kpiScope || "all");
  const [moverTab, setMoverTab] = useState("by_views");

  useEffect(() => {
    saveStoredState({ kpiTimeRange: timeRange });
  }, [timeRange]);
  useEffect(() => {
    saveStoredState({ kpiScope: scope });
  }, [scope]);

  const metric = useMemo(() => (metrics || []).find((item: any) => item.id === kpiId), [metrics, kpiId]);
  if (!kpiId || !metric) return null;

  const scoped = record(record(metric.data)[scope] || record(metric.data).all);
  const color = scoped.color || metric.color || "#a855f7";
  const Icon = metric.icon || BarChart3;
  const tone = statusTone(scoped.source);
  const hasValue = scoped.value !== null && scoped.value !== undefined;
  const statusLabel = scoped.sourceLabel || scoped.waiting || scoped.trend || "数据待接入";
  const path = sparkPath(scoped.spark);
  const rosterDetail = record(metric.rosterDetail);
  const isRosterDetail = metric.id === "kol-count" && number(rosterDetail.active_roster) != null;

  if (isRosterDetail) {
    const isCompany = scope === "company";
    const company = record(rosterDetail.company);
    const active = isCompany
      ? number(company.active_roster) || 0
      : number(rosterDetail.active_roster) || 0;
    const totalPool = isCompany
      ? number(company.total_pool) || 0
      : number(rosterDetail.total_pool) || 0;
    const companyFollowers = number(company.followers) || 0;
    const companyViews = number(company.total_views) || 0;
    const companyGroups = record(company.groups);
    const composition = record(rosterDetail.composition);
    const tiers = record(rosterDetail.partnership_4tier);
    const tierPct = record(rosterDetail.partnership_4tier_pct);
    const platformRows = isCompany
      ? (Array.isArray(company.by_platform) ? company.by_platform : [])
      : (Array.isArray(rosterDetail.by_platform) ? rosterDetail.by_platform : []);
    const moversTabs = record(rosterDetail.movers_tabs);
    const movers = isCompany
      ? (Array.isArray(company.movers) ? company.movers : [])
      : (Array.isArray(moversTabs[moverTab]) ? moversTabs[moverTab] : []);
    const trend = record(record(rosterDetail.trend)[scope] || record(rosterDetail.trend).all);
    const trendPoints = Array.isArray(trend.points) ? trend.points : [];
    const trendPath = scope === "company" ? sparkPath(trendPoints.map((point: any) => number(record(point).total_views))) : "";
    const maxPlatform = Math.max(1, ...platformRows.map((row: any) => number(record(row).count) || 0));
    const handleExportCsv = () => {
      const headerAndSummary = [
        ["section", "name", "platform", "value", "pct", "title", "date", "url"],
        ["summary", "total_pool", "", totalPool, "", "", "", ""],
        ["summary", "active_roster", "", active, "", "", "", ""],
      ];
      const breakdownRows = isCompany
        ? [
            ["summary", "followers", "", companyFollowers, "", "", "", ""],
            ["summary", "total_views", "", companyViews, "", "", "", ""],
            ...Object.keys(COMPANY_GROUP_LABELS).map((key) => [
              "group",
              (COMPANY_GROUP_LABELS as any)[key].label,
              "",
              number((companyGroups as any)[key]) || 0,
              totalPool > 0 ? pct((number((companyGroups as any)[key]) || 0) / totalPool) : "0.0%",
              "",
              "",
              "",
            ]),
          ]
        : [
            ["composition", "signed", "", number(composition.signed) || 0, "", "", "", ""],
            ["composition", "pending", "", number(composition.pending) || 0, "", "", "", ""],
            ...Object.keys(PARTNERSHIP_LABELS).map((key) => [
              "partnership",
              (PARTNERSHIP_LABELS as any)[key].label,
              "",
              number((tiers as any)[key]) || 0,
              pct((tierPct as any)[key]),
              "",
              "",
              "",
            ]),
          ];
      const rows = [
        ...headerAndSummary,
        ...breakdownRows,
        ...platformRows.map((raw: any) => {
          const row = record(raw);
          return ["platform", row.platform || "", row.platform || "", number(row.count) || 0, pct(row.pct), "", "", ""];
        }),
        ...movers.map((raw: any) => {
          const row = record(raw);
          return [
            isCompany ? "movers:company" : `movers:${moverTab}`,
            row.kol_name || (row.handle ? `@${String(row.handle).replace(/^@/, "")}` : ""),
            row.platform || "",
            number(row.value) || 0,
            "",
            row.title || "",
            row.publish_date ? String(row.publish_date).slice(0, 10) : "",
            row.url || "",
          ];
        }),
      ];
      downloadCsv(`v615-active-roster-${scope}-${moverTab}.csv`, rows);
    };
    const handleDrillToKolPool = () => {
      onClose?.();
      onDrillToKolPool?.();
    };

    return e(motion.div, {
      initial: { opacity: 0 },
      animate: { opacity: 1 },
      exit: { opacity: 0 },
      className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md",
      style: { zIndex: 9999 },
      onClick: onClose,
    },
      e(motion.div, {
        initial: { scale: 0.96, opacity: 0, y: 18 },
        animate: { scale: 1, opacity: 1, y: 0 },
        exit: { scale: 0.96, opacity: 0 },
        onClick: (ev) => ev.stopPropagation(),
        className: "relative max-h-[92vh] w-full max-w-6xl overflow-y-auto rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl",
      },
        e("div", { className: "pointer-events-none absolute inset-0 opacity-60", style: { background: "radial-gradient(circle at 20% -10%, rgba(168,85,247,0.24), transparent 36%), radial-gradient(circle at 90% 0%, rgba(6,182,212,0.16), transparent 35%)" } }),
        e("div", { className: "relative border-b border-white/[0.07] px-5 py-4" },
          e("div", { className: "flex items-start gap-3" },
            e("div", { className: "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-purple-300/20 bg-purple-500/[0.16] text-purple-200" }, e(Icon, { size: 19 })),
            e("div", { className: "min-w-0 flex-1" },
              e("div", { className: "flex flex-wrap items-center gap-2" },
                e("h2", { className: "text-lg font-semibold text-white" }, "Active Roster 详情"),
                e("span", { className: "rounded-md border border-emerald-400/20 bg-emerald-500/[0.12] px-2 py-0.5 text-[10px] text-emerald-200" }, "真实 · evidence + assignment")
              ),
              e("p", { className: "mt-1 text-[11px] text-slate-400" }, "现役名单全景、合作关系、平台分布与真实内容榜单。KOL Trend 未满 30 天时只显示累积状态。")
            ),
            e("button", { onClick: onClose, className: "shrink-0 rounded-lg border border-white/10 bg-white/[0.04] p-2 text-slate-400 hover:bg-white/[0.08] hover:text-white" }, e(X, { size: 15 }))
          ),
          e("div", { className: "mt-3 flex flex-wrap gap-1.5" },
            SCOPE_OPTIONS.map((option) => e("button", {
              key: option.id,
              onClick: () => setScope(option.id),
              className: `rounded-lg border px-3 py-1.5 text-[11px] transition ${scope === option.id ? "border-purple-400/40 bg-purple-500/[0.18] text-white" : "border-white/[0.08] bg-white/[0.02] text-slate-400 hover:text-white"}`,
            }, option.label, e("span", { className: "ml-1 text-[9px] text-slate-500" }, option.desc)))
          )
        ),
        e("div", { className: "relative grid gap-4 p-5 lg:grid-cols-[320px_minmax(0,1fr)]" },
          e("section", { className: "rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
            e("div", { className: "text-[10px] uppercase tracking-[0.18em] text-slate-500" }, isCompany ? "官方账号" : "现役名单"),
            e("div", { className: "mt-2 text-5xl font-light tracking-tight text-white tabular-nums" }, isCompany ? `${totalPool.toLocaleString()}` : totalPool.toLocaleString()),
            e("div", { className: "mt-2 text-sm text-slate-300" }, isCompany ? "官方在役账号" : "精准 active ", isCompany ? e("span", { className: "font-semibold text-emerald-300 tabular-nums" }, `${active.toLocaleString()} 个`) : e("span", { className: "font-semibold text-emerald-300 tabular-nums" }, active.toLocaleString())),
            isCompany
              ? e("div", { className: "mt-4 grid grid-cols-2 gap-2" },
                  e("div", { className: "rounded-lg border border-cyan-400/15 bg-cyan-500/[0.08] p-3" },
                    e("div", { className: "text-[10px] text-cyan-200" }, "粉丝总数"),
                    e("div", { className: "mt-1 text-2xl font-semibold text-white" }, compact(companyFollowers))
                  ),
                  e("div", { className: "rounded-lg border border-purple-400/15 bg-purple-500/[0.08] p-3" },
                    e("div", { className: "text-[10px] text-purple-200" }, "总播放"),
                    e("div", { className: "mt-1 text-2xl font-semibold text-white" }, compact(companyViews))
                  )
                )
              : e("div", { className: "mt-4 grid grid-cols-2 gap-2" },
                  e("div", { className: "rounded-lg border border-emerald-400/15 bg-emerald-500/[0.08] p-3" },
                    e("div", { className: "text-[10px] text-emerald-200" }, "已签约"),
                    e("div", { className: "mt-1 text-2xl font-semibold text-white" }, compact(composition.signed))
                  ),
                  e("div", { className: "rounded-lg border border-purple-400/15 bg-purple-500/[0.08] p-3" },
                    e("div", { className: "text-[10px] text-purple-200" }, "待签"),
                    e("div", { className: "mt-1 text-2xl font-semibold text-white" }, compact(composition.pending))
                  )
                ),
            e("div", { className: "mt-4 rounded-lg border border-white/[0.06] bg-black/20 p-3 text-[10px] leading-relaxed text-slate-400" },
              e("div", { className: "mb-1 flex items-center gap-1.5 text-slate-300" }, e(Database, { size: 11 }), "数据契约"),
              isCompany
                ? e(React.Fragment, null,
                    e("div", null, "accounts: ", e("span", { className: "text-slate-200" }, "vkpi_employee_channels (official active)")),
                    e("div", null, "metrics: ", e("span", { className: "text-slate-200" }, "vkpi_channel_metrics")),
                    e("div", null, "movers: ", e("span", { className: "text-slate-200" }, "vkpi_channel_post_metrics"))
                  )
                : e(React.Fragment, null,
                    e("div", null, "active: ", e("span", { className: "text-slate-200" }, "has_video_evidence / official active")),
                    e("div", null, "partnership: ", e("span", { className: "text-slate-200" }, "vkpi_project_kol_assignments")),
                    e("div", null, "movers: ", e("span", { className: "text-slate-200" }, "vkpi_kol_video_evidence"))
                  )
            )
          ),
          e("section", { className: "rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
            e("div", { className: "mb-3 flex items-center justify-between" },
              e("div", null,
                e("h3", { className: "text-sm font-semibold text-white" }, "Trend"),
                e("p", { className: "text-[10px] text-slate-500" }, scope === "company" ? "官方账号 7 天真实 channel_metrics" : `累积中 ${trend.snapshot_days || 7}/${trend.required_days || 30}`)
              ),
              e("div", { className: "flex rounded-lg border border-white/[0.07] bg-white/[0.02] p-0.5" },
                ["7d", "30d", "90d"].map((range) => e("button", {
                  key: range,
                  onClick: () => setTimeRange(range),
                  disabled: scope !== "company" || range !== "7d",
                  className: `rounded-md px-2 py-1 text-[10px] ${timeRange === range && scope === "company" ? "bg-cyan-500/[0.2] text-white" : "text-slate-500"} disabled:cursor-not-allowed disabled:opacity-50`,
                }, range.toUpperCase()))
              )
            ),
            trendPath
              ? e("svg", { viewBox: "0 0 560 128", className: "h-32 w-full overflow-visible" },
                  e("path", { d: trendPath, fill: "none", stroke: "#06b6d4", strokeWidth: 3, strokeLinecap: "round", strokeLinejoin: "round" }),
                  e("path", { d: `${trendPath} L560,128 L0,128 Z`, fill: "#06b6d4", opacity: 0.12 })
                )
              : e("div", { className: "flex h-32 items-center justify-center rounded-lg border border-dashed border-white/[0.08] text-[11px] text-slate-500" },
                  e(Loader2, { size: 13, className: "mr-2 animate-spin text-cyan-300" }),
                  `真实 snapshot 累积中 ${trend.snapshot_days || 7}/${trend.required_days || 30}`
                )
          )
        ),
        e("div", { className: "relative grid gap-4 px-5 pb-5 lg:grid-cols-[1fr_1fr]" },
          isCompany
            ? e("section", { className: "rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
                e("h3", { className: "text-sm font-semibold text-white" }, "账号分组"),
                e("div", { className: "mt-3 grid grid-cols-3 gap-2" },
                  ["main_brand", "product_line", "regional"].map((key) => {
                    const cfg = (COMPANY_GROUP_LABELS as any)[key];
                    const count = number((companyGroups as any)[key]) || 0;
                    const groupPct = totalPool > 0 ? count / totalPool : 0;
                    return e("div", { key, className: "rounded-lg border border-white/[0.07] bg-black/20 p-3" },
                      e("div", { className: "flex items-center justify-between gap-2" },
                        e("span", { className: "text-[11px] font-medium text-slate-200" }, cfg.label),
                        e("span", { className: "text-[10px] text-slate-500" }, pct(groupPct))
                      ),
                      e("div", { className: "mt-1 text-2xl font-semibold text-white tabular-nums" }, compact(count)),
                      e("div", { className: "mt-1 h-1.5 rounded-full bg-white/[0.06]" }, e("div", { className: "h-full rounded-full", style: { width: pct(groupPct), background: cfg.color } })),
                      e("p", { className: "mt-2 text-[10px] leading-snug text-slate-500" }, cfg.desc)
                    );
                  })
                )
              )
            : e("section", { className: "rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
                e("h3", { className: "text-sm font-semibold text-white" }, "合作关系"),
                e("div", { className: "mt-3 grid grid-cols-2 gap-2" },
                  ["long_term", "in_production", "one_off", "pending"].map((key) => {
                    const cfg = (PARTNERSHIP_LABELS as any)[key];
                    return e("div", { key, className: "rounded-lg border border-white/[0.07] bg-black/20 p-3" },
                      e("div", { className: "flex items-center justify-between gap-2" },
                        e("span", { className: "text-[11px] font-medium text-slate-200" }, cfg.label),
                        e("span", { className: "text-[10px] text-slate-500" }, pct((tierPct as any)[key]))
                      ),
                      e("div", { className: "mt-1 text-2xl font-semibold text-white tabular-nums" }, compact((tiers as any)[key])),
                      e("div", { className: "mt-1 h-1.5 rounded-full bg-white/[0.06]" }, e("div", { className: "h-full rounded-full", style: { width: pct((tierPct as any)[key]), background: cfg.color } })),
                      e("p", { className: "mt-2 text-[10px] leading-snug text-slate-500" }, cfg.desc)
                    );
                  })
                )
              ),
          e("section", { className: "rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
            e("h3", { className: "text-sm font-semibold text-white" }, "按平台"),
            e("div", { className: "mt-3 space-y-2" },
              platformRows.map((raw: any) => {
                const row = record(raw);
                const count = number(row.count) || 0;
                const width = `${Math.max(4, (count / maxPlatform) * 100).toFixed(1)}%`;
                return e("div", { key: row.platform, className: "space-y-1" },
                  e("div", { className: "flex items-center justify-between text-[11px]" },
                    e("span", { className: "capitalize text-slate-300" }, row.platform),
                    e("span", { className: "text-slate-500" }, count.toLocaleString(), " · ", pct(row.pct))
                  ),
                  e("div", { className: "h-2 rounded-full bg-white/[0.06]" }, e("div", { className: "h-full rounded-full bg-cyan-400/80", style: { width } }))
                );
              })
            )
          )
        ),
        e("section", { className: "relative mx-5 mb-5 rounded-xl border border-white/[0.08] bg-white/[0.03] p-4" },
          e("div", { className: "flex flex-wrap items-center justify-between gap-3" },
            e("div", null,
              e("h3", { className: "text-sm font-semibold text-white" }, isCompany ? "官方账号榜单" : "KOL 榜单"),
              e("p", { className: "text-[10px] text-slate-500" }, isCompany ? "官方账号真实帖子,按播放量排序(vkpi_channel_post_metrics)" : "只收 Viltrox 相关视频(深析 final_v1 判定 + 关键词兜底)·用真实播放、内容数、近 30 天爆款和互动替代粉丝涨幅 mock")
            ),
            isCompany
              ? null
              : e("div", { className: "flex rounded-lg border border-white/[0.07] bg-white/[0.02] p-0.5" },
                  MOVER_TABS.map((tab) => e("button", {
                    key: tab.id,
                    onClick: () => setMoverTab(tab.id),
                    className: `rounded-md px-3 py-1.5 text-[11px] ${moverTab === tab.id ? "bg-purple-500/[0.24] text-white" : "text-slate-400 hover:text-white"}`,
                  }, tab.label))
                )
          ),
          e("div", { className: "mt-3 grid gap-2 md:grid-cols-2" },
            movers.slice(0, 10).map((raw: any, index: number) => {
              const row = record(raw);
              return e("div", { key: `${isCompany ? "company" : moverTab}-${row.kol_id || row.handle || index}-${index}`, className: "flex items-center justify-between gap-3 rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2" },
                e("div", { className: "min-w-0" },
                  e("div", { className: "truncate text-[12px] font-medium text-slate-100" }, index + 1, ". ", row.kol_name || (row.handle ? `@${String(row.handle).replace(/^@/, "")}` : (isCompany ? "官方账号" : "Unknown KOL"))),
                  e("div", { className: "truncate text-[10px] text-slate-500" }, row.platform || "unknown", row.title ? ` · ${shortText(row.title, 54)}` : "")
                ),
                e("div", { className: "shrink-0 text-right" },
                  e("div", { className: "text-[12px] font-semibold text-white tabular-nums" }, metricLabel(isCompany ? "by_views" : moverTab, row)),
                  e("div", { className: "text-[9px] text-slate-500" }, row.publish_date ? String(row.publish_date).slice(0, 10) : "无历史涨跌")
                )
              );
            })
          )
        ),
        e("div", { className: "relative flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.07] px-5 py-3" },
          e("div", { className: "text-[10px] text-slate-500" }, "零 mock · 没有 KOL daily snapshot 的趋势保持累积中"),
          e("div", { className: "flex gap-2" },
            e("button", { onClick: handleExportCsv, className: "inline-flex items-center gap-1 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" }, e(Download, { size: 12 }), "导出 CSV"),
            e("button", { onClick: handleDrillToKolPool, className: "inline-flex items-center gap-1 rounded-lg border border-purple-400/20 bg-purple-500/[0.12] px-3 py-1.5 text-[11px] text-purple-100 hover:bg-purple-500/[0.18]" }, e(ExternalLink, { size: 12 }), "钻入 KOL Pool")
          )
        )
      )
    );
  }

  return e(motion.div, {
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.96, opacity: 0, y: 18 },
      animate: { scale: 1, opacity: 1, y: 0 },
      exit: { scale: 0.96, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-3xl overflow-hidden rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl",
    },
      e("div", { className: "pointer-events-none absolute inset-0 opacity-60", style: { background: `radial-gradient(circle at 20% -10%, ${color}33, transparent 38%)` } }),
      e("div", { className: "relative border-b border-white/[0.07] px-5 py-4" },
        e("div", { className: "flex items-start gap-3" },
          e("div", {
            className: "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/[0.06]",
            style: { background: `${color}1f`, color },
          }, e(Icon, { size: 19 })),
          e("div", { className: "min-w-0 flex-1" },
            e("div", { className: "flex flex-wrap items-center gap-2" },
              e("h2", { className: "text-lg font-semibold text-white" }, metric.label || metric.title || kpiId),
              e("span", { className: `rounded-md border px-2 py-0.5 text-[10px] ${tone.bg} ${tone.text} ${tone.border}` }, statusLabel)
            ),
            e("p", { className: "mt-1 text-[11px] text-slate-400" },
              metric.id === "exposure"
                ? "30d 增量指标。snapshot 未成熟时不使用 lifetime 替代。"
                : metric.id === "gmv" || metric.id === "roi"
                  ? "归因链路待 Shopify / 成本数据接入。"
                  : metric.sub || "来自 Dashboard 真实 API / 明确待接入状态。"
            )
          ),
          e("button", {
            onClick: onClose,
            className: "shrink-0 rounded-lg border border-white/10 bg-white/[0.04] p-2 text-slate-400 hover:bg-white/[0.08] hover:text-white",
          }, e(X, { size: 15 }))
        ),
        e("div", { className: "mt-3 flex flex-wrap gap-1.5" },
          SCOPE_OPTIONS.map((option) => e("button", {
            key: option.id,
            onClick: () => setScope(option.id),
            className: `rounded-lg border px-3 py-1.5 text-[11px] transition ${scope === option.id ? "border-purple-400/40 bg-purple-500/[0.18] text-white" : "border-white/[0.08] bg-white/[0.02] text-slate-400 hover:text-white"}`,
          }, option.label, e("span", { className: "ml-1 text-[9px] text-slate-500" }, option.desc)))
        )
      ),
      e("div", { className: "relative grid gap-3 p-5 md:grid-cols-[260px_minmax(0,1fr)]" },
        e("div", { className: "rounded-xl border border-white/[0.07] bg-white/[0.025] p-4" },
          e("div", { className: "mb-2 text-[10px] uppercase tracking-[0.18em] text-slate-500" }, "Current"),
          e("div", { className: "text-4xl font-light tracking-tight text-white tabular-nums" }, formatValue(metric, scoped.value)),
          e("div", { className: "mt-2 text-[11px] text-slate-400" }, hasValue ? scoped.trend || "真实 API" : scoped.waiting || "数据待接入"),
          e("div", { className: "mt-4 rounded-lg border border-white/[0.06] bg-black/20 p-3 text-[10px] leading-relaxed text-slate-400" },
            e("div", { className: "mb-1 flex items-center gap-1.5 text-slate-300" }, e(Database, { size: 11 }), "数据契约"),
            e("div", null, "scope: ", e("span", { className: "text-slate-200" }, scope === "company" ? "owned_matrix" : scope)),
            e("div", null, "window: ", e("span", { className: "text-slate-200" }, scoped.source === "evidence_metrics" ? "evidence metrics" : metric.id === "kol-count" ? "roster snapshot" : "30d snapshot")),
            e("div", null, "source: ", e("span", { className: "text-slate-200" }, scoped.source || "pending"))
          )
        ),
        e("div", { className: "rounded-xl border border-white/[0.07] bg-white/[0.025] p-4" },
          e("div", { className: "mb-3 flex items-center justify-between" },
            e("div", null,
              e("h3", { className: "text-sm font-semibold text-white" }, "Trend"),
              e("p", { className: "text-[10px] text-slate-500" }, path ? "来自真实趋势点" : "趋势需要 snapshot / revenue-trend 成熟后显示")
            ),
            e("div", { className: "flex rounded-lg border border-white/[0.07] bg-white/[0.02] p-0.5" },
              ["7d", "30d", "90d"].map((range) => e("button", {
                key: range,
                onClick: () => setTimeRange(range),
                disabled: !path,
                className: `rounded-md px-2 py-1 text-[10px] ${timeRange === range && path ? "bg-purple-500/[0.2] text-white" : "text-slate-500"} disabled:cursor-not-allowed disabled:opacity-50`,
              }, range.toUpperCase()))
            )
          ),
          path
            ? e("svg", { viewBox: "0 0 560 128", className: "h-32 w-full overflow-visible" },
                e("path", { d: path, fill: "none", stroke: color, strokeWidth: 3, strokeLinecap: "round", strokeLinejoin: "round" }),
                e("path", { d: `${path} L560,128 L0,128 Z`, fill: color, opacity: 0.12 })
              )
            : e("div", { className: "flex h-32 items-center justify-center rounded-lg border border-dashed border-white/[0.08] text-[11px] text-slate-500" },
                scoped.source === "accumulating"
                  ? e(React.Fragment, null, e(Loader2, { size: 13, className: "mr-2 animate-spin text-cyan-300" }), statusLabel)
                  : scoped.waiting || "数据待接入"
              )
        )
      ),
      e("div", { className: "relative flex items-center justify-between border-t border-white/[0.07] px-5 py-3" },
        e("div", { className: "text-[10px] text-slate-500" }, "真实数据详情 · 不展示 mock composition / top contributors"),
        e("button", { onClick: onClose, className: "rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white" }, t("关闭"))
      )
    )
  );
}
