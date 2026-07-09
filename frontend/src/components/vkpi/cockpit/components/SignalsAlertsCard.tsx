// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { m } from "framer-motion";
import { Activity, AlertTriangle } from "lucide-react";
import { useT } from "../lib/i18n";
import { freshnessLabel, nowLocal } from "../../lib/timeLocal";

const e = React.createElement;

// freshnessAt = 数据真实新鲜度(UTC ts);有 → 显示"更新于 X前 · 本地时间"(按观看者浏览器时区),
// 无 → 显示当前本地时间(实时钟)。绝不再硬编码 "Just now · Live" 这种假 live 标。
export function SignalsAlertsCard({ alerts, onAlertClick, onViewAll, freshnessAt }: any) {
  const { t } = useT();
  const sevColor: any = {
    high:   "#ef4444",
    medium: "#f59e0b",
    low:    "#10b981",
    info:   "#06b6d4",
  };
  const sevBg: any = {
    high:   "rgba(239, 68, 68, 0.06)",
    medium: "rgba(245, 158, 11, 0.06)",
    low:    "rgba(16, 185, 129, 0.06)",
    info:   "rgba(6, 182, 212, 0.06)",
  };
  const sevTextColor: any = {
    high:   "#fca5a5",
    medium: "#fcd34d",
    low:    "#6ee7b7",
    info:   "#67e8f9",
  };
  return e(m.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.20 },
    className: "h-full rounded-xl border border-line bg-panel p-4 backdrop-blur-xl flex flex-col",
  },
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(AlertTriangle, { size: 14, className: "text-crit" }),
        e("h3", { className: "text-sm font-semibold text-ink" }, "Signals & Alerts")
      ),
      e("div", { className: "flex items-center gap-1.5 text-[9px] text-muted" },
        e("span", { className: "rounded-full p-1" },
          e(Activity, { size: 10 })
        ),
        e("span", null, freshnessAt ? freshnessLabel(freshnessAt) : nowLocal())
      )
    ),
    e("div", { className: "flex-1 space-y-1.5" },
      alerts.length === 0
        ? e("div", { className: "rounded-md border border-dashed border-line px-3 py-8 text-center text-[11px] text-muted" }, "暂无真实市场信号")
        : alerts.slice(0, 4).map((a: any) => e("div", {
        key: a.id,
        onClick: () => onAlertClick && onAlertClick(a),
        className: "group cursor-pointer rounded-md px-2.5 py-2 transition-colors hover:bg-accent-soft",
        style: { background: sevBg[a.severity], borderLeft: `2px solid ${sevColor[a.severity]}` }
      },
        e("div", { className: "flex items-start justify-between gap-2 mb-1" },
          e("div", { className: "text-[11px] font-medium flex-1 truncate", style: { color: sevTextColor[a.severity] } }, a.title),
          e("span", { className: "text-[9px] text-muted shrink-0" }, a.time)
        ),
        e("div", { className: "text-[10px] text-muted line-clamp-2 mb-1" }, a.desc),
        // D3(2026-07-02):品牌名可点 chip —— 点击在 setSelectedSignal 原逻辑之外带上品牌(不造数据,
        // brands 只来自后端已有 entityId/brand 字段);stopPropagation 避免和整卡点击叠加。
        Array.isArray(a.brands) && a.brands.length > 0 && e("div", { className: "flex flex-wrap items-center gap-1 mb-1" },
          a.brands.slice(0, 4).map((brand: string) => e("button", {
            key: brand,
            type: "button",
            title: `查看 ${brand} 相关信号`,
            onClick: (ev: any) => { ev.stopPropagation(); onAlertClick && onAlertClick({ ...a, brand }); },
            className: "rounded border border-line bg-card px-1.5 py-0.5 text-[9px] font-semibold text-ink-2 hover:bg-accent-soft transition-colors"
          }, brand))
        ),
        // Sources + mentions
        e("div", { className: "flex items-center justify-between text-[9px]" },
          // D3 人话来源行:卡面显 sourceLine(如「市场信号 · 本轮证据 N 条」);evidence 键名细节
          // (summary.run_id · hot_brands.count)挪到 title tooltip,弹窗内仍有完整查看源。
          e("div", {
            className: "flex items-center gap-1 text-muted",
            title: a.sourceDetail || (Array.isArray(a.sources) ? a.sources : []).map((s: any) => (typeof s === "string" ? s : s && s.name)).filter(Boolean).join(" · ")
          },
            // 防御:sources 缺失/非数组时不再 .slice/.length 崩页。
            e("span", null, a.sourceLine || (Array.isArray(a.sources) ? a.sources : []).slice(0, 3).map((s: any) => (typeof s === "string" ? s : s && s.name)).filter(Boolean).join(" · ")),
            !a.sourceLine && (Array.isArray(a.sources) ? a.sources.length : 0) > 3 && e("span", null, ` +${a.sources.length - 3}`)
          ),
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "rounded bg-card px-1 py-0.5 text-muted" }, `${a.totalMentions} mentions`),
            e("span", { className: "text-good font-semibold" }, a.trendPct)
          )
        )
      ))
    ),
    e("div", { className: "mt-3 border-t border-line pt-2 flex items-center justify-between" },
      e("div", { className: "text-[9px] text-muted truncate flex-1" }, alerts.length ? "Sources: market-intelligence/cards/v0" : "Sources: 无信号"),
      e("button", { onClick: onViewAll, className: "ml-2 text-[10px] text-ink-2 hover:text-ink shrink-0" }, "View All →")
    )
  );
}
