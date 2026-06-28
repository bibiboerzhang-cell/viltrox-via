// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { ArrowLeft, ChevronRight } from "lucide-react";

const e = React.createElement;

export function Breadcrumb({ levels, onGoBack }: any) {
  if (levels.length === 0) return null;
  return e("div", { className: "flex items-center gap-2 rounded-lg border border-white/[0.08] bg-[#0b1220]/70 px-3 py-1.5 backdrop-blur-xl" },
    e("button", {
      onClick: onGoBack,
      "aria-label": "Go back",
      className: "flex items-center gap-1 rounded-md text-[10px] text-slate-400 hover:text-white"
    },
      e(ArrowLeft, { size: 12 }),
      e("span", null, "Back")
    ),
    e("div", { className: "h-3 w-px bg-white/10" }),
    e("div", { className: "flex items-center gap-1 text-[11px]" },
      e("span", { className: "text-slate-500" }, "World"),
      levels.map((lv: any, i: any) => e(React.Fragment, { key: i },
        e(ChevronRight, { size: 10, className: "text-slate-600" }),
        e("span", { className: i === levels.length - 1 ? "text-white font-medium" : "text-slate-400" }, lv)
      ))
    )
  );
}
