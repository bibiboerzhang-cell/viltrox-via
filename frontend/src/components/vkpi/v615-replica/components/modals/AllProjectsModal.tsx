// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { Filter, Target, X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";
import { REMINDER_ICON_MAP } from "../../data/reminderIconMap";

const e = React.createElement;

export function AllProjectsModal({ campaigns, onClose, onProjectClick }) {
  const { t } = useT();
  const [filter, setFilter] = useState("all");
  const filtered = campaigns.filter(c => filter === "all" || c.status === filter);
  return e(CenterModal, { onClose, maxWidth: "2xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("全部项目")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, `${campaigns.length} ${t("项")} · ${t("按状态筛选")}`)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    // Filter tabs
    e("div", { className: "px-5 pt-2 flex gap-3 border-b border-white/[0.04]" },
      [
        { key: "all",      label: t("全部"),   count: campaigns.length },
        { key: "active",   label: t("进行中"), count: campaigns.filter(c => c.status === "active").length },
        { key: "planning", label: t("规划中"), count: campaigns.filter(c => c.status === "planning").length },
        { key: "done",     label: t("已完成"), count: campaigns.filter(c => c.status === "done").length },
      ].map(x => e("button", {
        key: x.key, onClick: () => setFilter(x.key),
        className: "text-[11px] py-1.5 px-0.5 border-b-2 flex items-center gap-1",
        style: { borderColor: filter === x.key ? "#a855f7" : "transparent", color: filter === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
      }, x.label, e("span", { className: "text-[9px] text-slate-500" }, x.count)))
    ),
    e("div", { className: "p-3 max-h-[60vh] overflow-y-auto" },
      filtered.length === 0
        ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, t("没有内容"))
        : e("div", { className: "space-y-2" },
            filtered.map(c => {
              const IconComp = REMINDER_ICON_MAP[c.iconKey] || Target;
              return e("button", {
                key: c.id,
                onClick: () => { onClose(); onProjectClick && onProjectClick(c); },
                className: "w-full text-left rounded-lg border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05] p-3 transition-colors"
              },
                e("div", { className: "flex items-start gap-3" },
                  e("div", { 
                    className: "shrink-0 w-9 h-9 rounded-md flex items-center justify-center",
                    style: { background: c.iconColor + "22" }
                  }, e(IconComp, { size: 14, style: { color: c.iconColor } })),
                  e("div", { className: "flex-1 min-w-0" },
                    e("div", { className: "flex items-center gap-2 mb-0.5" },
                      e("span", { className: "text-[12px] font-medium text-white" }, c.name),
                      e("span", { 
                        className: "text-[9px] px-1.5 py-0.5 rounded uppercase",
                        style: { 
                          background: c.status === "active" ? "rgba(16,185,129,0.15)" : c.status === "planning" ? "rgba(245,158,11,0.15)" : "rgba(100,116,139,0.15)",
                          color: c.status === "active" ? "#34d399" : c.status === "planning" ? "#fbbf24" : "#94a3b8"
                        }
                      }, t(c.statusLabel))
                    ),
                    e("div", { className: "text-[10px] text-slate-400 line-clamp-1" }, c.bottleneckText || ""),
                    e("div", { className: "flex items-center gap-3 mt-1.5 text-[10px] text-slate-500" },
                      c.kolCount != null && e("span", null, "KOL " + c.kolCount),
                      c.publishedCount != null && e("span", null, `${t("已发")} ${c.publishedCount}`),
                      c.reach && e("span", null, `${t("曝光")} ${c.reach}`),
                      c.healthScore != null && e("span", { 
                        className: "ml-auto px-1.5 py-0.5 rounded font-medium",
                        style: { 
                          background: c.healthScore >= 80 ? "rgba(16,185,129,0.15)" : c.healthScore >= 60 ? "rgba(245,158,11,0.15)" : "rgba(239,68,68,0.15)",
                          color: c.healthScore >= 80 ? "#34d399" : c.healthScore >= 60 ? "#fbbf24" : "#f87171"
                        }
                      }, `${t("健康")} ${c.healthScore}`)
                    )
                  )
                )
              );
            })
          )
    )
  );
}
