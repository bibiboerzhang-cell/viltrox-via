// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Flame } from "lucide-react";

const e = React.createElement;

export function TrendPulseBar() {
  return e("div", { className: "rounded-xl border border-white/[0.07] bg-white/[0.018] p-3 flex items-center gap-3 backdrop-blur-xl" },
    e("div", { className: "flex items-center gap-2 shrink-0" },
      e("div", { className: "rounded-md p-1.5", style: { background: "rgba(239,68,68,0.18)" } }, e(Flame, { size: 12, className: "text-rose-400" })),
      e("div", null,
        e("div", { className: "text-[11px] font-semibold text-white" }, "本周市场审美"),
        e("div", { className: "text-[9px] text-slate-500" }, "trend-pulse endpoint 待接入")
      )
    ),
    e("div", { className: "flex-1 flex flex-wrap gap-1.5" },
      ["数据待接入", "不展示 mock hashtag", "等待后端 trend-pulse"].map((t, i) => e("span", {
        key: i,
        className: "px-2 py-0.5 rounded-md text-[10px] font-medium border",
        style: { 
          background: "rgba(255,255,255,0.04)",
          borderColor: "rgba(255,255,255,0.1)",
          color: "#e2e8f0"
        }
      }, t))
    )
  );
}
