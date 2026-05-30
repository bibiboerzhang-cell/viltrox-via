// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";

const e = React.createElement;

export function SmallCard({ title, right, children, className = "" }) {
  return e("div", {
    className: `rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl hover:border-white/[0.14] ${className}`,
  },
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("h3", { className: "text-sm font-semibold text-white" }, title),
      right ?? e("button", { className: "rounded-md border border-white/[0.08] bg-white/5 px-2 py-1 text-[10px] text-slate-400 hover:text-white" }, "View All")
    ),
    children
  );
}
