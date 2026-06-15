// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Loader2 } from "lucide-react";

const e = React.createElement;

export function V6FitBar({ score, size = "normal", kind = null }: { score?: number | null; size?: string; kind?: any }) {
  if (score === null || score === undefined) {
    // Kind-aware placeholder: show what's blocking the score.
    if (kind === "new_discovered") return e("span", { className: "text-[10px] text-slate-500 inline-flex items-center gap-1" }, e(Loader2, { size: 9, className: "animate-spin" }), "校验中");
    if (kind === "new_validated")  return e("span", { className: "text-[10px] text-cyan-300/80" }, "待画像");
    if (kind === "existing_low_confidence") return e("span", { className: "text-[10px] text-orange-300/80 inline-flex items-center gap-1" }, e(Loader2, { size: 9, className: "animate-spin" }), "补全中");
    return e("span", { className: "text-slate-600 text-[11px]" }, "—");
  }
  const color = score >= 85 ? "#10b981" : score >= 70 ? "#fbbf24" : score >= 40 ? "#fb923c" : "#94a3b8";
  return e("div", { className: "flex items-center gap-2 min-w-0" },
    e("div", { className: "flex-1 max-w-[44px] h-1 rounded-full bg-white/[0.06] overflow-hidden" },
      e("div", { className: "h-full rounded-full transition-all", style: { width: score + "%", background: color } })
    ),
    e("span", { className: "shrink-0 text-[12px] font-semibold tabular-nums", style: { color } }, score)
  );
}
