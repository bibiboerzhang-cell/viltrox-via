// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Calendar, MessageCircle as Twitter, X } from "lucide-react";
import { useT } from "../lib/i18n";

const e = React.createElement;

export function ContentCalendarCard({ days, onItemClick, onViewAll }) {
  const { t } = useT();
  const platformIcons = {
    YT: { label: "YT", bg: "#ef4444" },
    IG: { label: "IG", bg: "#ec4899" },
    Twitter: { label: "X",  bg: "#06b6d4" },
    team: { label: "T", bg: "#64748b" },
    internal: { label: "•", bg: "#a855f7" },
  };
  return e(motion.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.16 },
    className: "rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl",
  },
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(Calendar, { size: 14, className: "text-cyan-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "近 7 天内容信号")
      ),
      onViewAll && days.length > 0 && e("button", { onClick: onViewAll, className: "text-[10px] text-slate-400 hover:text-white" }, t("View Full →"))
    ),
    e("div", { className: "space-y-1.5" },
      days.length === 0
        ? e("div", { className: "rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[11px] text-slate-500" }, "暂无真实内容信号")
        : days.map((d) => e("div", {
        key: d.day,
        className: `rounded-md px-2 py-1.5 ${d.today ? "bg-purple-500/[0.08] border border-purple-500/[0.25]" : ""}`
      },
        e("div", { className: "flex items-center gap-2 mb-1" },
          e("span", { className: `text-[9px] font-semibold ${d.today ? "text-purple-300" : "text-slate-500"} w-8` }, d.weekday),
          e("span", { className: `text-[10px] ${d.today ? "text-purple-200" : "text-slate-400"}` }, d.day),
          d.today && e("span", { className: "text-[9px] text-purple-300 ml-1" }, "TODAY"),
          e("span", { className: "ml-auto text-[9px] text-slate-500" }, `${d.items.length} 项`)
        ),
        d.items.length > 0 && e("div", { className: "space-y-1 pl-9" },
          d.items.slice(0, 3).map((item, i) => e("div", {
            key: i,
            onClick: () => onItemClick && onItemClick({ ...item, dayStr: d.day, weekday: d.weekday, label: item.label, kolName: item.label.split(" · ")[0], title: item.label.split(" · ").slice(1).join(" · ") || item.label }),
            className: "flex items-center gap-1.5 text-[10px] cursor-pointer hover:bg-white/[0.02] rounded px-1"
          },
            e("span", { 
              className: "shrink-0 rounded text-[8px] text-white font-bold flex items-center justify-center",
              style: { width: 14, height: 12, background: item.color, fontSize: "8px" }
            }, platformIcons[item.platform]?.label || "·"),
            e("span", { className: "text-slate-500 shrink-0 w-9" }, item.time),
            e("span", { className: "text-slate-300 truncate flex-1" }, item.label)
          ))
        )
      ))
    )
  );
}
