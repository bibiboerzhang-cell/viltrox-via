// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { X } from "lucide-react";
import { CenterModal } from "./CenterModal";
import { useT } from "../../lib/i18n";

const e = React.createElement;

export function FullCalendarModal({ days, onClose, onItemClick }) {
  const { t } = useT();
  return e(CenterModal, { onClose, maxWidth: "2xl" },
    e("div", { className: "px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between" },
      e("div", null,
        e("h2", { className: "text-sm font-semibold text-white" }, t("完整日历")),
        e("div", { className: "text-[10px] text-slate-500 mt-0.5" }, `${days.length} ${t("天")}`)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white" }, e(X, { size: 14 }))
    ),
    e("div", { className: "p-3 max-h-[60vh] overflow-y-auto space-y-2" },
      days.map(day => e("div", { 
        key: day.date,
        className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3"
      },
        e("div", { className: "flex items-center justify-between mb-2" },
          e("div", { className: "flex items-center gap-2" },
            e("span", { className: "text-[11px] font-semibold text-white" }, day.day),
            e("span", { className: "text-[10px] text-slate-500" }, day.date),
            day.isToday && e("span", { className: "text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300" }, "TODAY")
          ),
          e("span", { className: "text-[10px] text-slate-500" }, `${day.items.length} ${t("项")}`)
        ),
        day.items.length === 0
          ? e("div", { className: "text-[10px] text-slate-500 text-center py-2" }, t("没有内容"))
          : e("div", { className: "space-y-1" },
              day.items.map((item, i) => e("button", {
                key: i,
                onClick: () => { onClose(); onItemClick && onItemClick(item); },
                className: "w-full text-left flex items-center gap-2 px-2 py-1.5 rounded hover:bg-white/[0.04]"
              },
                e("span", { 
                  className: "text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded font-medium",
                  style: { 
                    background: (item.platformColor || "#a855f7") + "20",
                    color: item.platformColor || "#a855f7"
                  }
                }, item.platform || "YT"),
                e("span", { className: "text-[10px] text-slate-500" }, item.time),
                e("span", { className: "flex-1 text-[11px] text-white truncate" }, item.title)
              ))
            )
      ))
    )
  );
}
