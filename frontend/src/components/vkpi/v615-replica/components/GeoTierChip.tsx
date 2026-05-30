// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { X } from "lucide-react";

const e = React.createElement;

export function GeoTierChip({ tier }) {
  if (!tier) return null;
  const cfg = {
    A: { color: "#10b981", label: "A" },
    B: { color: "#fbbf24", label: "B" },
    C: { color: "#94a3b8", label: "C" },
    D: { color: "#64748b", label: "D" },
    X: { color: "#f87171", label: "CN" },  // 中国剥离
  }[tier] || { color: "#64748b", label: "?" };
  return e("span", {
    className: "inline-flex items-center px-1 rounded text-[10px] font-bold",
    style: { background: cfg.color + "1a", color: cfg.color, minWidth: 18, justifyContent: "center" }
  }, cfg.label);
}
