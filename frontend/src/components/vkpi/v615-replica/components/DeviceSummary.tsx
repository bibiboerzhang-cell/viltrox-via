// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { AlertTriangle, Camera } from "lucide-react";

const e = React.createElement;

export function DeviceSummary({ devices }: { devices?: any }) {
  if (!devices || !devices.camera_body) return e("span", { className: "text-slate-600 text-[10px]" }, "—");
  const hasViltrox = devices.has_viltrox;
  const hasCompetitor = devices.competitor_brands && devices.competitor_brands.length > 0;
  return e("div", { className: "flex items-center gap-1.5" },
    e(Camera, { size: 11, className: "text-slate-400 shrink-0" }),
    e("span", { className: "text-[10px] text-slate-300 truncate max-w-[80px]" }, devices.camera_body),
    hasViltrox && e("span", {
      className: "shrink-0 px-1 rounded text-[9px] font-medium",
      style: { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" },
      title: "已使用 Viltrox 镜头"
    }, "V"),
    hasCompetitor && e("span", {
      className: "shrink-0 inline-flex items-center justify-center px-1 rounded text-[9px] font-medium",
      style: { background: "rgba(248,113,113,0.15)", color: "#f87171" },
      title: "在用友商:" + devices.competitor_brands.join(", ")
    }, e(AlertTriangle, { size: 9 }))
  );
}
