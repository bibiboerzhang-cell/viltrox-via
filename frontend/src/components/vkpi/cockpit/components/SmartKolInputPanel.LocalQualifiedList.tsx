import { CheckCircle2, Clock3, Heart, Loader2, ShieldAlert } from "lucide-react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import {
  ACTIVITY_UNKNOWN_VIDEO_LABEL,
  localQualifiedSummary,
  type LocalQualifiedRow,
  type LocalQualifiedSummary,
} from "./SmartKolInputPanel.LocalQualified";

const EMPTY_SELECTION: ReadonlySet<number> = new Set<number>();

function compactNumber(value: number | null): string {
  if (value == null) return "待核验";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function dateLabel(value: string): string {
  if (!value) return "待核验";
  // Activity qualification is day-based in UTC. Preserve an explicit ISO calendar date instead
  // of shifting midnight into the previous day in western browser time zones.
  const isoDay = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (isoDay) return `${isoDay[1]}/${isoDay[2]}/${isoDay[3]}`;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 16);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "UTC",
  }).format(parsed);
}

function qualificationTone(row: LocalQualifiedRow): string {
  if (row.qualification === "qualified") return "border-emerald-300/25 bg-emerald-400/[0.10] text-emerald-100";
  if (row.qualification === "rejected") return "border-rose-300/20 bg-rose-400/[0.08] text-rose-100";
  // 「从没抓到过视频」自成一色:既不是绿色的合格,也不是红色的未通过。
  if (row.activityUnknown) return "border-sky-300/25 bg-sky-400/[0.10] text-sky-100";
  return "border-amber-300/20 bg-amber-400/[0.08] text-amber-100";
}

function statusTone(value: string): string {
  if (["可联系", "已完成"].includes(value)) return "text-emerald-200";
  if (value.includes("中")) return "text-cyan-200";
  if (value.includes("失败")) return "text-rose-200";
  return "text-slate-400";
}

export function StrictQualifiedList({
  summary,
  onOpen,
  selectedIds = EMPTY_SELECTION,
  onSelectionChange,
  selectionDisabled = false,
  selectionReady = true,
  favoriteIds = EMPTY_SELECTION,
  favoriteBusyIds = EMPTY_SELECTION,
  favoriteResults = new Map<number, string>(),
  favoriteErrors = new Map<number, string>(),
  favoritesSyncing = false,
  onFavorite,
  lane = "local",
  extraStats = [],
}: {
  summary: LocalQualifiedSummary;
  onOpen?: (item: VkpiKolRecallItem) => void;
  selectedIds?: ReadonlySet<number>;
  onSelectionChange?: (ids: Set<number>) => void;
  selectionDisabled?: boolean;
  selectionReady?: boolean;
  favoriteIds?: ReadonlySet<number>;
  favoriteBusyIds?: ReadonlySet<number>;
  favoriteResults?: ReadonlyMap<number, string>;
  favoriteErrors?: ReadonlyMap<number, string>;
  favoritesSyncing?: boolean;
  onFavorite?: (kolPoolId: number) => void;
  lane?: "local" | "online";
  extraStats?: string[];
}) {
  const online = lane === "online";
  const laneLabel = online ? "联网净新增" : "本地合格";
  const selectableIds = summary.rows
    .filter((row) => row.strictQualified && Number(row.item.kol_pool_id) > 0)
    .map((row) => Number(row.item.kol_pool_id));
  // 活跃度未知的人不进「全选」——他们不计入 30 人目标,要一个个主动确认。
  const activityUnknownSelectable = summary.rows
    .filter((row) => row.activityUnknown && Number(row.item.kol_pool_id) > 0).length;
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id));
  const updateAll = () => {
    if (selectionDisabled || !selectionReady || !onSelectionChange || !selectableIds.length) return;
    const next = new Set(selectedIds);
    selectableIds.forEach((id) => (allSelected ? next.delete(id) : next.add(id)));
    onSelectionChange(next);
  };
  const updateOne = (id: number) => {
    if (selectionDisabled || !selectionReady || !onSelectionChange || id <= 0) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectionChange(next);
  };
  return (
    <div className="space-y-2" data-testid={`${lane}-qualified-list`}>
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-violet-300/20 bg-black/20 px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-violet-100">
            <CheckCircle2 size={12} /> {laneLabel} {summary.qualified}/{summary.target}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">
            {online ? "服务端 accepted" : "服务端返回"} {summary.serverReturned}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">
            {online ? "严格过闸" : "过闸候选"} {summary.serverQualified}
          </span>
          <span className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">
            {online ? "净新增唯一" : "合格唯一"} {summary.uniqueQualified}
          </span>
          {summary.pending > 0 ? (
            <span className="inline-flex items-center gap-1 rounded border border-amber-300/15 bg-amber-400/[0.05] px-1.5 py-0.5 text-[10.5px] leading-4 text-amber-100">
              <Clock3 size={9} /> 待验收 {summary.pending}（不计入）
            </span>
          ) : null}
          {summary.rejected > 0 ? (
            <span className="rounded border border-rose-300/15 bg-rose-400/[0.05] px-1.5 py-0.5 text-[10.5px] leading-4 text-rose-100">
              未通过 {summary.rejected}（不计入）
            </span>
          ) : null}
          {summary.activityUnknown > 0 ? (
            <span
              data-testid={`${lane}-activity-unknown-count`}
              title="这些人其他条件都合格，但我们一次都没抓到过他们的视频，所以还不知道他们最近有没有更新。不计入 30 人，可以单独勾选先收着。"
              className="inline-flex items-center gap-1 rounded border border-sky-300/20 bg-sky-400/[0.06] px-1.5 py-0.5 text-[10.5px] leading-4 text-sky-100"
            >
              <Clock3 size={9} /> 从没抓到过视频 {summary.activityUnknown}（不计入 {summary.target} 人，可单独勾选）
            </span>
          ) : null}
          {extraStats.map((label) => (
            <span key={label} className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">{label}</span>
          ))}
          {selectableIds.length || activityUnknownSelectable ? (
            <span className="rounded border border-emerald-300/15 px-1.5 py-0.5 text-[10.5px] leading-4 text-emerald-100">
              可选 {selectableIds.length + activityUnknownSelectable}
            </span>
          ) : null}
        </div>
        <span className="text-[10.5px] leading-4 text-[var(--ds-text-meta)]">按服务端排名逐条出现 · 不在浏览器重算资格</span>
      </div>

      {summary.shortfall > 0 ? (
        <div className="flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.06] px-2.5 py-2 text-[11px] leading-[18px] text-amber-100">
          <ShieldAlert size={11} className="mt-0.5 shrink-0" />
          <span>
            还缺 {summary.shortfall} 人
            {summary.shortfallReasons.length ? ` · ${summary.shortfallReasons.join("；")}` : ""}
          </span>
        </div>
      ) : (
        <div className="rounded-md border border-emerald-300/20 bg-emerald-400/[0.06] px-2.5 py-2 text-[11px] leading-[18px] text-emerald-100">
          {online ? "联网净新增 30 人硬闸已满足" : "本地 30 人硬闸已满足"}；未知或待核验候选未计入。
        </div>
      )}

      {summary.rows.length ? (
        <div className="overflow-x-auto rounded-lg border border-white/[0.07]">
          <table className="min-w-[1180px] w-full border-collapse text-left text-[11px] leading-[18px]">
            <thead className="bg-white/[0.035] text-[10.5px] text-[var(--ds-text-meta)]">
              <tr>
                <th className="w-14 px-2 py-2 font-medium">
                  <label className="inline-flex items-center gap-1" title={`只选择服务端已通过全部硬闸的${online ? "联网净新增" : "库内"}候选`}>
                    <input
                      type="checkbox"
                      aria-label={`全选${laneLabel} KOL`}
                      checked={allSelected}
                      disabled={selectionDisabled || !selectionReady || !selectableIds.length || !onSelectionChange}
                      onChange={updateAll}
                      className="accent-emerald-500"
                    />
                    全选
                  </label>
                </th>
                <th className="w-10 px-2 py-2 font-medium">排名</th>
                <th className="min-w-36 px-2 py-2 font-medium">KOL</th>
                <th className="w-32 px-2 py-2 font-medium">关注 / MY KOL</th>
                <th className="w-24 px-2 py-2 font-medium">平台</th>
                <th className="w-24 px-2 py-2 font-medium">粉丝</th>
                <th className="w-28 px-2 py-2 font-medium">最新视频</th>
                <th className="min-w-36 px-2 py-2 font-medium">市场证据</th>
                <th className="w-20 px-2 py-2 font-medium">语言</th>
                <th className="w-24 px-2 py-2 font-medium">KOL 类型</th>
                <th className="min-w-52 px-2 py-2 font-medium">为什么匹配</th>
                <th className="w-24 px-2 py-2 font-medium">联系方式</th>
                <th className="w-24 px-2 py-2 font-medium">分析</th>
                <th className="w-28 px-2 py-2 font-medium">硬闸</th>
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row) => {
                const poolId = Number(row.item.kol_pool_id) || 0;
                // 返回给操作员的行必须点得动:活跃度未知的人不计入 30 人目标,
                // 但同样可以勾选入库——看得见却点不动才是最坏的一种。
                const selectable = (row.strictQualified || row.activityUnknown) && poolId > 0;
                const favorited = favoriteIds.has(poolId);
                const favoriteBusy = favoriteBusyIds.has(poolId);
                const favoriteResult = favoriteResults.get(poolId) || "";
                const favoriteError = favoriteErrors.get(poolId) || "";
                const favoriteAllowed = selectable && selectionReady && !selectionDisabled && Boolean(onFavorite);
                return (
                  <tr
                    key={row.identity}
                    className="h-11 border-t border-white/[0.055] bg-black/10 text-slate-200 hover:bg-violet-400/[0.035]"
                  >
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      aria-label={`选择${online ? "联网" : "本地"} KOL ${row.name}`}
                      checked={selectable && selectedIds.has(poolId)}
                      disabled={selectionDisabled || !selectionReady || !selectable || !onSelectionChange}
                      title={
                        !selectable || !selectionReady
                          ? "待验收、未终态或未通过候选不可选择"
                          : row.activityUnknown
                            ? "这个人其他条件都合格，但我们一次都没抓到过他的视频，先收着也可以"
                            : "选择此服务端合格候选"
                      }
                      onChange={() => updateOne(poolId)}
                      className="accent-emerald-500 disabled:cursor-not-allowed disabled:opacity-35"
                    />
                  </td>
                  <td className="px-2 py-2 tabular-nums text-slate-500">{row.rank}</td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => onOpen?.(row.item)}
                      className="max-w-52 truncate text-left font-medium text-slate-100 hover:text-cyan-100"
                      title={row.name}
                    >
                      {row.name}
                    </button>
                  </td>
                  <td className="px-2 py-2">
                    {!row.strictQualified && !row.activityUnknown ? (
                      <span className="text-[10.5px] leading-4 text-[var(--ds-text-meta)]">过闸后可关注</span>
                    ) : poolId <= 0 ? (
                      <span className="text-[10.5px] leading-4 text-amber-200">待入库</span>
                    ) : favorited ? (
                      <span className="inline-flex min-h-8 items-center gap-1 rounded border border-emerald-300/25 bg-emerald-400/[0.08] px-2 py-1 text-[10.5px] leading-4 text-emerald-100">
                        <CheckCircle2 size={9} /> 已关注
                      </span>
                    ) : (
                      <button
                        type="button"
                        aria-label={`关注${online ? "联网" : "本地"} KOL ${row.name}`}
                        disabled={!favoriteAllowed || favoriteBusy}
                        onClick={() => onFavorite?.(poolId)}
                        title={favoriteAllowed ? "关注后进入本人 MY KOL；不会批准项目或自动外联" : selectionDisabled ? "当前结果已过期，重新搜索后可关注" : !selectionReady ? "联网严格名单终态后可关注" : "当前行暂不可关注"}
                        className="inline-flex min-h-9 items-center gap-1 rounded border border-emerald-300/25 bg-emerald-500/[0.10] px-2 py-1 text-[10.5px] leading-4 text-emerald-100 transition-colors hover:bg-emerald-500/[0.18] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300 disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        {favoriteBusy ? <Loader2 size={9} className="animate-spin" /> : <Heart size={9} />}
                        {favoriteBusy ? "关注中" : favoriteError ? "重试" : "关注"}
                      </button>
                    )}
                    {favoriteError ? <div className="mt-1 text-[10.5px] leading-4 text-rose-200">{favoriteError}</div> : null}
                    {!favoriteError && favoriteResult && !favorited ? <div className="mt-1 text-[10.5px] leading-4 text-emerald-200">{favoriteResult}</div> : null}
                    {!favoriteError && !favoriteResult && favoritesSyncing && !favorited ? <div className="mt-1 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">状态同步中</div> : null}
                  </td>
                  <td className="px-2 py-2 uppercase text-slate-400">{row.platform}</td>
                  <td className="px-2 py-2 tabular-nums">{compactNumber(row.followers)}</td>
                  <td
                    className={`px-2 py-2 ${row.activityUnknown ? "text-sky-200" : "tabular-nums"}`}
                    title={row.activityUnknown ? "这个人的视频我们一次都没抓到过，还不知道他最近有没有更新" : undefined}
                  >
                    {row.activityUnknown ? ACTIVITY_UNKNOWN_VIDEO_LABEL : dateLabel(row.latestVideoAt)}
                  </td>
                  <td className="max-w-48 truncate px-2 py-2" title={row.marketEvidence || "待服务端提供市场证据"}>
                    {row.marketEvidence || "待核验"}
                  </td>
                  <td className="px-2 py-2 uppercase text-slate-400">{row.languageEvidence || "待核验"}</td>
                  <td className="px-2 py-2" title={row.accountQuality || "账号质量待核验"}>{row.profileType || "待核验"}</td>
                  <td className="max-w-72 truncate px-2 py-2" title={row.whyFit || "待补充匹配依据"}>
                    {row.whyFit || "待补充"}
                  </td>
                  <td className={`px-2 py-2 ${statusTone(row.contactStatus)}`}>{row.contactStatus}</td>
                  <td className={`px-2 py-2 ${statusTone(row.analysisStatus)}`}>{row.analysisStatus}</td>
                  <td className="px-2 py-2">
                    <span className={`inline-flex rounded border px-2 py-1 text-[10.5px] leading-4 ${qualificationTone(row)}`}>
                      {row.qualificationLabel}
                      {row.activityUnknown
                        ? `（不计入 ${summary.target} 人）`
                        : row.qualification === "pending" ? "（不计数）" : ""}
                    </span>
                  </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] leading-[18px] text-[var(--ds-text-meta)]">
          {online ? "联网严格候选正在进入服务端排名流，首批 accepted 到达后会立即显示。" : "本地候选正在进入服务端排名流，首批到达后会立即显示。"}
        </div>
      )}
    </div>
  );
}

export function LocalQualifiedList({
  result,
  ...props
}: {
  result: VkpiKolRecallResponse;
  onOpen?: (item: VkpiKolRecallItem) => void;
  selectedIds?: ReadonlySet<number>;
  onSelectionChange?: (ids: Set<number>) => void;
  selectionDisabled?: boolean;
  favoriteIds?: ReadonlySet<number>;
  favoriteBusyIds?: ReadonlySet<number>;
  favoriteResults?: ReadonlyMap<number, string>;
  favoriteErrors?: ReadonlyMap<number, string>;
  favoritesSyncing?: boolean;
  onFavorite?: (kolPoolId: number) => void;
}) {
  return <StrictQualifiedList summary={localQualifiedSummary(result)} {...props} />;
}
