// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function AllMoversModal({ movers, onClose, onMoverClick }: any) {
  const { t } = useT();
  const [sortBy, setSortBy] = useState("reach");
  const sorted = [...movers].sort((a: any, b: any) => {
    if (sortBy === "reach")     return (b.deltaReach || 0) - (a.deltaReach || 0);
    if (sortBy === "er")        return (b.er || 0) - (a.er || 0);
    if (sortBy === "followers") return (b.deltaFollowers || 0) - (a.deltaFollowers || 0);
    return 0;
  });
  return e(CenterModal, { onClose, maxWidth: "2xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("全部 KOL Movers")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, `${movers.length} ${t("项")} · ${t("vs 上周")}`)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "px-5 pt-2 flex gap-3 border-b border-white/[0.04]" },
      [
        { key: "reach",     label: t("曝光") + " ↓" },
        { key: "er",        label: "ER ↓" },
        { key: "followers", label: t("7 天粉丝") + " ↓" },
      ].map(x => e("button", {
        key: x.key, onClick: () => setSortBy(x.key),
        className: "text-[11px] py-1.5 px-0.5 border-b-2",
        style: { borderColor: sortBy === x.key ? "#a855f7" : "transparent", color: sortBy === x.key ? "#fff" : "rgba(255,255,255,0.5)" }
      }, x.label))
    ),
    e("div", { className: "p-3 max-h-[60vh] overflow-y-auto space-y-1.5" },
      sorted.map((m, i) => e("button", {
        key: m.handle,
        onClick: () => { onClose(); onMoverClick && onMoverClick(m); },
        className: "w-full text-left flex items-center gap-3 rounded-md hover:bg-white/[0.04] px-2 py-2 transition-colors"
      },
        e("div", { className: "shrink-0 text-[10px] text-slate-500 w-5 text-center" }, i + 1),
        e("div", {
          className: "shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-[11px] font-bold text-white",
          style: { background: m.color || "#a855f7" }
        }, m.avatar || m.handle.slice(1, 2).toUpperCase()),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "text-[12px] font-medium text-white" }, m.handle),
          e("div", { className: "text-[10px] text-slate-500 truncate" }, m.reason || m.platform || "")
        ),
        e("div", { className: "text-right" },
          m.deltaReach != null && e("div", { className: "text-[11px] font-semibold", style: { color: m.deltaReach > 0 ? "#34d399" : "#f87171" } }, 
            (m.deltaReach > 0 ? "+" : "") + (m.deltaReach > 1000 ? (m.deltaReach/1000).toFixed(1) + "k" : m.deltaReach)),
          m.er != null && e("div", { className: "text-[9px] text-slate-500" }, `ER ${m.er}%`)
        )
      ))
    )
  );
}
