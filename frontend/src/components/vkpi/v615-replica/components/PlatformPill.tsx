// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { PLATFORMS } from "../data/platforms";

const e = React.createElement;

export function PlatformPill({ platform }: { platform?: any }) {
  const cfg = (PLATFORMS as any)[platform] || { label: platform, short: "?", color: "#64748b" };
  return e("span", {
    className: "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium uppercase tracking-wider",
    style: { background: cfg.color + "22", color: cfg.color }
  }, cfg.short);
}
