// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";

const e = React.createElement;

export function KPAvatar({ name, color, size = 32 }) {
  const initial = (name || "?").trim()[0]?.toUpperCase() || "?";
  return e("div", {
    className: "shrink-0 rounded-full flex items-center justify-center font-bold text-white",
    style: { width: size, height: size, fontSize: size * 0.4, background: color || "#a855f7" }
  }, initial);
}
