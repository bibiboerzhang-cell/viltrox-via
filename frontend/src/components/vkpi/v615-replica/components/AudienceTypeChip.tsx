// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { Globe2, MapPin, Target } from "lucide-react";

const e = React.createElement;

export function AudienceTypeChip({ type }: { type?: any }) {
  if (!type) return e("span", { className: "text-slate-600 text-[10px]" }, "—");
  const normalized = String(type).trim().toLowerCase();
  const key = normalized === "global" ? "Global" : normalized === "regional" ? "Regional" : normalized === "local" ? "Local" : null;
  const cfg = ({
    Global:   { icon: Globe2, color: "#06b6d4", label: "Global"   },
    Regional: { icon: MapPin, color: "#fbbf24", label: "Regional" },
    Local:    { icon: Target, color: "#a855f7", label: "Local"    },
  } as any)[key as any] || { icon: Target, color: "#64748b", label: "待评估" };
  return e("span", {
    className: "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium",
    style: { background: cfg.color + "1a", color: cfg.color }
  }, e(cfg.icon, { size: 9 }), cfg.label);
}
