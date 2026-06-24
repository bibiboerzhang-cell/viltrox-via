import React from "react";

// A3 数据来源可信标记:ready/partial/missing/mock/stale/awaiting → 统一角标。
// 让用户一眼分清真实/部分/待接入/过期,绝不把 mock 当真数据。纯展示组件。

type Status = "ready" | "partial" | "missing" | "mock" | "stale" | "awaiting" | "awaiting_data" | string;

const MAP: Record<string, { label: string; cls: string }> = {
  ready: { label: "真实", cls: "text-emerald-300 border-emerald-500/30 bg-emerald-500/[0.10]" },
  partial: { label: "部分", cls: "text-sky-300 border-sky-500/30 bg-sky-500/[0.10]" },
  missing: { label: "待接入", cls: "text-slate-400 border-white/15 bg-white/[0.04]" },
  awaiting: { label: "待数据", cls: "text-amber-300 border-amber-500/30 bg-amber-500/[0.10]" },
  awaiting_data: { label: "待数据", cls: "text-amber-300 border-amber-500/30 bg-amber-500/[0.10]" },
  mock: { label: "样例", cls: "text-fuchsia-300 border-fuchsia-500/30 bg-fuchsia-500/[0.10]" },
  stale: { label: "过期", cls: "text-orange-300 border-orange-500/30 bg-orange-500/[0.10]" },
};

export function DataStatusBadge({ status, updatedAt, title }: { status: Status; updatedAt?: string; title?: string }) {
  const key = String(status || "").toLowerCase();
  const m = MAP[key] || { label: key || "—", cls: "text-slate-400 border-white/15 bg-white/[0.04]" };
  const tip = [title, updatedAt ? `更新于 ${updatedAt}` : ""].filter(Boolean).join(" · ");
  return (
    <span
      title={tip || undefined}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-medium leading-none ${m.cls}`}
    >
      {m.label}
    </span>
  );
}
