// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useMemo } from "react";
import { Activity, Check, Loader2, Search, Sparkles, Zap } from "lucide-react";
import { candidateKindGroup } from "../lib/candidateKind";

const e = React.createElement;

export function SearchProgressBar({ items, searchActive }: { items: any[]; searchActive?: boolean }) {
  // Per-kind counts derived from current dataset (in real impl, these come from SSE events).
  const counts = useMemo(() => {
    const c = { existing: 0, new_promoted: 0, new_validated: 0, new_discovered: 0, warming: 0, stale: 0 };
    items.forEach(it => {
      if (candidateKindGroup(it.candidate_kind) === "existing") c.existing += 1;
      if (it.candidate_kind === "new_promoted")  c.new_promoted  += 1;
      if (it.candidate_kind === "new_validated") c.new_validated += 1;
      if (it.candidate_kind === "new_discovered") c.new_discovered += 1;
      if (it.refresh_state === "warming") c.warming += 1;
      if (it.refresh_state === "stale")   c.stale += 1;
    });
    return c;
  }, [items]);

  const top30Eligible = Math.min(30, counts.existing + counts.new_promoted + counts.new_validated);
  const top30Ready    = Math.max(0, top30Eligible - counts.warming - counts.stale);
  const validating    = counts.new_discovered;
  const allReady      = top30Ready === top30Eligible && validating === 0;

  // Single inline status row, low-key. Information density > visual weight.
  const segs = [
    { icon: Check,    color: "#86efac", label: "已有库",        value: counts.existing,                                done: true },
    { icon: Sparkles, color: "#c4b5fd", label: "新发现",        value: counts.new_promoted + counts.new_validated,    done: true },
    { icon: Loader2,  color: "#cbd5e1", label: "校验中",        value: validating,                                     done: validating === 0 },
    { icon: Zap,      color: top30Ready < top30Eligible ? "#fde68a" : "#86efac", label: "Top 30 fast", value: top30Ready + "/" + top30Eligible, done: top30Ready === top30Eligible },
  ];

  return e("div", {
    className: "flex items-center gap-3 px-3 py-1.5 mb-3 rounded-md border border-white/[0.06] bg-white/[0.015] text-[10.5px]",
  },
    e("span", { className: "flex items-center gap-1.5 text-slate-500 uppercase tracking-wider text-[9px] shrink-0" },
      e(Activity, { size: 10, className: "text-purple-400" }),
      "Search v2"
    ),
    e("div", { className: "h-3 w-px bg-white/[0.08]" }),
    segs.map((s, i) => e("span", {
      key: i,
      className: "flex items-center gap-1 shrink-0",
      style: { color: s.color }
    },
      e(s.icon, { size: 10, className: s.done ? "" : "animate-spin" }),
      e("span", { className: "text-slate-400" }, s.label),
      e("span", { className: "tabular-nums font-medium" }, s.value),
      i < segs.length - 1 && e("span", { className: "text-slate-700 ml-2" }, "·"),
    )),
    e("div", { className: "flex-1" }),
    e("span", { className: "text-[9px] text-slate-600 shrink-0" }, "/api/admin/vkpi/kol-pool · 只读")
  );
}
