// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";

const e = React.createElement;

export function TrendDot({ resonance }: { resonance?: number | null }) {
  if (resonance === null || resonance === undefined) return e("span", { className: "text-slate-600" }, "—");
  const color = resonance >= 0.6 ? "#10b981" : resonance >= 0.3 ? "#fbbf24" : "#64748b";
  const label = resonance >= 0.6 ? "高" : resonance >= 0.3 ? "中" : "低";
  return e("div", { className: "flex items-center gap-1" },
    e("span", { className: "h-1.5 w-1.5 rounded-full", style: { background: color } }),
    e("span", { className: "text-[10px] tabular-nums", style: { color } }, (resonance * 100).toFixed(0))
  );
}
