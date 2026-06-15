// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Filter, List, MessageCircle as Twitter, Search, X } from "lucide-react";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function SignalsAllModal({ alerts, onClose, onAlertClick }: any) {
  const { t } = useT();
  const [searchQuery, setSearchQuery] = useState("");
  const [sevFilter, setSevFilter] = useState("all");

  const sevColor: any = { high: "#ef4444", medium: "#f59e0b", low: "#10b981", info: "#06b6d4" };
  const sevBg: any    = { high: "rgba(239,68,68,0.06)", medium: "rgba(245,158,11,0.06)", low: "rgba(16,185,129,0.06)", info: "rgba(6,182,212,0.06)" };
  const sevText: any  = { high: "#fca5a5", medium: "#fcd34d", low: "#6ee7b7", info: "#67e8f9" };

  const filtered = alerts.filter((a: any) => {
    if (sevFilter !== "all" && a.severity !== sevFilter) return false;
    if (searchQuery && !a.title.toLowerCase().includes(searchQuery.toLowerCase()) && !a.desc.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    return true;
  });
  
  return e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev: any) => ev.stopPropagation(),
      className: "relative w-full max-w-3xl h-[85vh] flex flex-col rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "shrink-0 px-5 py-3.5 border-b border-white/[0.06] flex items-center gap-3" },
        e(AlertTriangle, { size: 16, className: "text-red-300" }),
        e("h2", { className: "text-sm font-semibold text-white" }, "Signals & Alerts · 全部"),
        e("span", { className: "text-[10px] text-slate-500" }, filtered.length + " / " + alerts.length + " 条"),
        e("button", { onClick: onClose, className: "ml-auto rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
      ),
      // Filter row
      e("div", { className: "shrink-0 px-5 py-3 border-b border-white/[0.04] flex flex-wrap items-center gap-2" },
        // search
        e("div", { className: "flex-1 min-w-[200px] flex items-center gap-2 rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5" },
          e(Search, { size: 12, className: "text-slate-500" }),
          e("input", {
            type: "text",
            value: searchQuery,
            onChange: (ev: any) => setSearchQuery(ev.target.value),
            placeholder: "搜索标题 / 来源 / 关键词",
            className: "flex-1 bg-transparent text-[11px] text-white placeholder:text-slate-500 outline-none"
          })
        ),
        // severity buttons
        ["all", "high", "medium", "low", "info"].map((sev: any) => e("button", {
          key: sev,
          onClick: () => setSevFilter(sev),
          className: "text-[10px] px-2.5 py-1 rounded transition-colors",
          style: sevFilter === sev ? {
            background: sev === "all" ? "rgba(168,85,247,0.25)" : sevColor[sev] + "33",
            color: sev === "all" ? "#fff" : sevText[sev],
            border: "0.5px solid " + (sev === "all" ? "rgba(168,85,247,0.4)" : sevColor[sev] + "55"),
          } : {
            background: "transparent",
            color: "rgba(255,255,255,0.5)",
            border: "0.5px solid rgba(255,255,255,0.1)",
          }
        }, sev === "all" ? "全部" : sev === "high" ? "高" : sev === "medium" ? "中" : sev === "low" ? "低" : "info")),
        // sort
        e("button", { className: "text-[10px] px-2.5 py-1 rounded border border-white/[0.06] text-white/25", disabled: true, title: "时间范围切换待接入" }, t("本周"))
      ),
      // List
      e("div", { className: "flex-1 overflow-y-auto p-5 space-y-2" },
        filtered.length === 0
          ? e("div", { className: "text-center py-12 text-[11px] text-slate-500" }, t("没有匹配的信号"))
          : filtered.map((a: any) => e("div", {
              key: a.id,
              onClick: () => onAlertClick && onAlertClick(a),
              className: "rounded-md p-3 cursor-pointer hover:bg-white/[0.04] transition-colors",
              style: { background: sevBg[a.severity], borderLeft: "2px solid " + sevColor[a.severity] }
            },
              e("div", { className: "flex items-start justify-between gap-2 mb-1" },
                e("div", { className: "text-[12px] font-medium flex-1", style: { color: sevText[a.severity] } }, a.title),
                e("span", { className: "text-[9px] text-slate-500 shrink-0" }, a.time)
              ),
              e("div", { className: "text-[10px] text-slate-400 mb-1.5" }, a.desc),
              e("div", { className: "flex items-center justify-between text-[9px]" },
                e("div", { className: "text-slate-500" },
                  // 防御:某些信号没有 sources(或为字符串源)→ 不再 .map 直接崩页(RouteErrorBoundary)。
                  (Array.isArray(a.sources) ? a.sources : [])
                    .map((s: any) => (typeof s === "string" ? s : s && s.name))
                    .filter(Boolean)
                    .join(" · ")
                ),
                e("div", { className: "flex items-center gap-1.5" },
                  e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-slate-400" }, a.totalMentions + " mentions"),
                  e("span", { className: "text-emerald-400 font-semibold" }, a.trendPct)
                )
              )
            ))
      ),
      // Footer
      e("div", { className: "shrink-0 px-5 py-2.5 border-t border-white/[0.06] flex items-center justify-between" },
        e("div", { className: "text-[10px] text-slate-500" }, "数据源:Google News · Reddit · NewShooter · DPReview · Brand24 · Twitter API"),
        e("div", { className: "text-[10px] text-slate-400" }, "每小时刷新 · 多源聚合")
      )
    )
  );
}
