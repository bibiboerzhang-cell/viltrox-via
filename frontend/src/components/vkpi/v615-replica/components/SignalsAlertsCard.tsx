// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Activity, AlertTriangle } from "lucide-react";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function SignalsAlertsCard({ alerts, onAlertClick, onViewAll }) {
  const { t } = useT();
  const sevColor = {
    high:   "#ef4444",
    medium: "#f59e0b",
    low:    "#10b981",
    info:   "#06b6d4",
  };
  const sevBg = {
    high:   "rgba(239, 68, 68, 0.06)",
    medium: "rgba(245, 158, 11, 0.06)",
    low:    "rgba(16, 185, 129, 0.06)",
    info:   "rgba(6, 182, 212, 0.06)",
  };
  const sevTextColor = {
    high:   "#fca5a5",
    medium: "#fcd34d",
    low:    "#6ee7b7",
    info:   "#67e8f9",
  };
  return e(motion.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.20 },
    className: "h-full rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl flex flex-col",
  },
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(AlertTriangle, { size: 14, className: "text-red-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "Signals & Alerts")
      ),
      e("div", { className: "flex items-center gap-1.5 text-[9px] text-slate-400" },
        e("button", { className: "rounded-full p-1 hover:bg-white/[0.05]" },
          e(Activity, { size: 10 })
        ),
        e("span", null, "Just now · Live")
      )
    ),
    e("div", { className: "flex-1 space-y-1.5" },
      alerts.length === 0
        ? e("div", { className: "rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[11px] text-slate-500" }, "暂无真实市场信号")
        : alerts.slice(0, 4).map((a) => e("div", {
        key: a.id,
        onClick: () => onAlertClick && onAlertClick(a),
        className: "group cursor-pointer rounded-md px-2.5 py-2 transition-colors hover:bg-white/[0.04]",
        style: { background: sevBg[a.severity], borderLeft: `2px solid ${sevColor[a.severity]}` }
      },
        e("div", { className: "flex items-start justify-between gap-2 mb-1" },
          e("div", { className: "text-[11px] font-medium flex-1 truncate", style: { color: sevTextColor[a.severity] } }, a.title),
          e("span", { className: "text-[9px] text-slate-500 shrink-0" }, a.time)
        ),
        e("div", { className: "text-[10px] text-slate-400 line-clamp-2 mb-1" }, a.desc),
        // Sources + mentions
        e("div", { className: "flex items-center justify-between text-[9px]" },
          e("div", { className: "flex items-center gap-1 text-slate-500" },
            e("span", null, a.sources.slice(0, 3).map(s => s.name).join(" · ")),
            a.sources.length > 3 && e("span", null, ` +${a.sources.length - 3}`)
          ),
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "rounded bg-white/[0.05] px-1 py-0.5 text-slate-400" }, `${a.totalMentions} mentions`),
            e("span", { className: "text-emerald-400 font-semibold" }, a.trendPct)
          )
        )
      ))
    ),
    e("div", { className: "mt-3 border-t border-white/[0.06] pt-2 flex items-center justify-between" },
      e("div", { className: "text-[9px] text-slate-500 truncate flex-1" }, alerts.length ? "Sources: market-intelligence/cards/v0" : "Sources: 无信号"),
      e("button", { onClick: onViewAll, className: "ml-2 text-[10px] text-slate-300 hover:text-white shrink-0" }, "View All →")
    )
  );
}
