// Verbatim from vkpi_v6.15.7_integrated.html


import { Globe2, MapPin, Target } from "lucide-react";

export function AudienceTypeChip({ type }: { type?: any }) {
  if (!type) return <span className="text-slate-600 text-[10px]">—</span>;
  const normalized = String(type).trim().toLowerCase();
  const key = normalized === "global" ? "Global" : normalized === "regional" ? "Regional" : normalized === "local" ? "Local" : null;
  const cfg = ({
    Global:   { icon: Globe2, color: "#06b6d4", label: "Global"   },
    Regional: { icon: MapPin, color: "#fbbf24", label: "Regional" },
    Local:    { icon: Target, color: "#a855f7", label: "Local"    },
  } as any)[key as any] || { icon: Target, color: "#64748b", label: "待评估" };
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ background: cfg.color + "1a", color: cfg.color }}
    >
      <cfg.icon size={9} />
      {cfg.label}
    </span>
  );
}
