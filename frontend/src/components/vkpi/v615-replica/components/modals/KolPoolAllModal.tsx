import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { KPAvatar } from "../KPAvatar";
import { CenterModal } from "./CenterModal";

type Row = Record<string, any>;

function norm(value: unknown): string {
  return String(value ?? "").toLowerCase().trim();
}

function matchesSearch(item: Row, query: string): boolean {
  if (!query) return true;
  const haystack = [
    item.handle,
    item.display_name,
    item.platform,
    item.bio,
    item.country,
    item.industry_label,
    item.audience_type,
    item.geo_tier,
    item.candidate_kind,
    item.devices?.camera_body,
    ...(Array.isArray(item.devices?.lenses) ? item.devices.lenses.map((lens: Row) => lens?.model || "") : []),
  ].join(" ").toLowerCase();
  return haystack.includes(query);
}

function matchesGroup(item: Row, group: string): boolean {
  if (!group) return true;
  if (group === "analyzed") return Boolean(item.has_video_analysis || item.video_analysis_ready_count || item.llm_deep_analysis_count || item.llm_v6_fit);
  if (group === "youtube") return norm(item.platform).includes("youtube") || norm(item.platform_code) === "yt";
  if (group === "instagram") return norm(item.platform).includes("instagram") || norm(item.platform_code) === "ig";
  if (group === "tiktok") return norm(item.platform).includes("tiktok") || norm(item.platform_code) === "tt";
  return true;
}

function countGroup(items: Row[], group: string): number {
  return items.filter((item) => matchesGroup(item, group)).length;
}

function compactNumber(value: unknown): string {
  const next = Number(value);
  if (!Number.isFinite(next) || next <= 0) return "--";
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 10_000) return `${Math.round(next / 1_000)}K`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

function scoreLabel(value: unknown): string {
  const next = Number(value);
  return Number.isFinite(next) && next > 0 ? String(Math.round(next)) : "--";
}

export function KolPoolAllModal({
  items = [],
  myList,
  selectedItemId,
  onClose,
  onRowClick,
}: {
  items: Row[];
  myList?: Set<unknown>;
  selectedItemId?: unknown;
  onClose: () => void;
  onRowClick: (item: Row) => void;
}) {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const normalizedQuery = norm(query);
  const filtered = useMemo(
    () => items.filter((item) => matchesGroup(item, group) && matchesSearch(item, normalizedQuery)),
    [items, group, normalizedQuery],
  );
  const visibleItems = filtered.slice(0, 160);
  const chips = [
    { key: "", label: "全部", count: items.length },
    { key: "youtube", label: "YouTube", count: countGroup(items, "youtube") },
    { key: "instagram", label: "Instagram", count: countGroup(items, "instagram") },
    { key: "tiktok", label: "TikTok", count: countGroup(items, "tiktok") },
    { key: "analyzed", label: "已分析", count: countGroup(items, "analyzed") },
  ];

  return (
    <CenterModal onClose={onClose} maxWidth="6xl">
      <div className="shrink-0 border-b border-white/[0.06] bg-[#0b1324] px-5 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-white">全部 KOL</h2>
            <div className="mt-0.5 text-[10px] text-slate-500">
              {items.length} 个 KOL · 可搜索、筛选、点行打开现有详情抽屉
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:bg-white/[0.08] hover:text-white"
            aria-label="关闭"
          >
            <X size={14} />
          </button>
        </div>
        <div className="mt-3 grid gap-2 xl:grid-cols-[minmax(0,1fr)_auto]">
          <label className="flex min-h-[36px] items-center gap-2 rounded-md border border-white/[0.08] bg-black/20 px-2.5">
            <Search size={13} className="shrink-0 text-slate-500" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索 handle、平台、国家、设备、关键词..."
              className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-200 outline-none placeholder:text-slate-600"
            />
          </label>
          <div className="flex flex-wrap gap-1.5">
            {chips.map((chip) => (
              <button
                key={chip.key || "all"}
                type="button"
                onClick={() => setGroup(chip.key)}
                className={`min-h-[32px] rounded-md border px-2 text-[10.5px] transition-colors ${
                  group === chip.key
                    ? "border-cyan-300/30 bg-cyan-400/[0.12] text-cyan-100"
                    : "border-white/[0.08] bg-white/[0.025] text-slate-400 hover:bg-white/[0.05] hover:text-slate-200"
                }`}
              >
                {chip.label} <span className="text-slate-500">{chip.count}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="mb-2 flex shrink-0 items-center justify-between px-1 text-[10.5px] text-slate-500">
          <span>显示 {filtered.length} / {items.length}</span>
          <span>点行打开详情；分析动作在详情抽屉内继续使用现有按钮与任务看板</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-white/[0.08] bg-white/[0.025]">
          {visibleItems.map((item) => {
            const selected = selectedItemId === item.id;
            const handle = item.handle || item.display_name || `KOL #${item.id || "--"}`;
            const platform = item.platform || item.platform_code || "unknown";
            return (
              <div
                key={`${item.id || handle}-${platform}`}
                role="button"
                tabIndex={0}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  onRowClick(item);
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  event.stopPropagation();
                  onRowClick(item);
                }}
                className={`grid w-full grid-cols-[minmax(0,1.6fr)_110px_90px_100px] items-center gap-4 border-b border-white/[0.04] px-3 py-2.5 text-left transition-colors last:border-b-0 hover:bg-white/[0.04] ${
                  selected ? "bg-cyan-400/[0.08]" : ""
                }`}
              >
                <span className="flex min-w-0 items-center gap-2.5">
                  <KPAvatar name={item.display_name || item.handle} color={item.avatar_color} size={30} />
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] font-medium text-white">{handle}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-slate-500">
                      {platform} · {item.candidate_kind || item.profile_type || "真实 KOL Pool"}
                    </span>
                  </span>
                </span>
                <span className="text-right text-[11px] text-slate-300">
                  <span className="block tabular-nums text-white">{compactNumber(item.followers)}</span>
                  <span className="text-[9.5px] text-slate-600">粉丝</span>
                </span>
                <span className="text-right text-[11px] text-slate-300">
                  <span className="block tabular-nums text-white">{scoreLabel(item.v6_fit)}</span>
                  <span className="text-[9.5px] text-slate-600">V6 Fit</span>
                </span>
                <span className="text-right text-[10.5px] text-slate-500">
                  打开详情
                </span>
              </div>
            );
          })}
          {!visibleItems.length ? (
            <div className="px-3 py-8 text-center text-[11px] text-slate-500">没有匹配的 KOL</div>
          ) : null}
        </div>
        {filtered.length > visibleItems.length ? (
          <div className="mt-2 px-1 text-[10px] text-slate-600">已显示前 {visibleItems.length} 条，请继续搜索缩小范围。</div>
        ) : null}
      </div>
    </CenterModal>
  );
}
