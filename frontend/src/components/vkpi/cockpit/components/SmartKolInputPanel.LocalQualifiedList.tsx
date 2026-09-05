import { CheckCircle2, Clock3, Heart, Loader2, ShieldAlert } from "lucide-react";

import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import {
  ACTIVITY_UNKNOWN_VIDEO_LABEL,
  localQualifiedSummary,
  type LocalQualifiedRow,
  type LocalQualifiedSummary,
} from "./SmartKolInputPanel.LocalQualified";
import { languageOriginCounts, languageOriginSummaryLabel } from "./LanguageProvenance";
import { LanguageProvenanceCell } from "./LanguageProvenanceChip";
import { candidateGrowthSummary, candidateRankSummary } from "./SmartKolInputPanel.CandidateEvidence";

const EMPTY_SELECTION: ReadonlySet<number> = new Set<number>();

export function OnlineContentEvidenceNotice({
  count,
  followupStatus,
  target = 30,
}: {
  count: number;
  followupStatus: string;
  target?: number;
}) {
  if (count <= 0) return null;
  const followupLabel = followupStatus === "not_scheduled"
    ? "本轮未安排补抓"
    : followupStatus === "queued" || followupStatus === "running"
      ? "已安排补抓，完成前"
      : "补证状态待确认，当前";
  return (
    <div
      data-testid="online-content-evidence-pending"
      className="flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.06] px-2.5 py-2 text-[11px] leading-[18px] text-amber-100"
    >
      <Clock3 size={11} className="mt-0.5 shrink-0" />
      <span>缺正文/字幕 {count} 人 · {followupLabel} · 不计入联网严格 {target} 人目标</span>
    </div>
  );
}

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
  terminal = false,
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
  terminal?: boolean;
}) {
  const online = lane === "online";
  const laneLabel = online ? "联网净新增" : "本地合格";
  const hasGrowthRows = summary.rows.some((row) => candidateGrowthSummary(row.item).active);
  const selectableIds = summary.rows
    .filter((row) => row.strictQualified && Number(row.item.kol_pool_id) > 0)
    .map((row) => Number(row.item.kol_pool_id));
  // 活跃度未知的人不进「全选」——他们不计入 30 人目标,要一个个主动确认。
  const activityUnknownSelectable = summary.rows
    .filter((row) => row.activityUnknown && !candidateGrowthSummary(row.item).active && Number(row.item.kol_pool_id) > 0).length;
  const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id));
  // 语言口径的分布:全是自报时不显示,免得跟旁边的「活跃度未知」挤成一片。
  //
  // 这一格与语言列表头的两句悬停说明必须与**实际显示出来的档数**对得上。说明少一档,
  // 那一档就会被操作员顺手归进他最熟的那一档 —— 而最熟的那一档正是「他自己填的」。
  // 历轮补过的、以及本轮补的:
  //  1. 「推断只作参考,不改任何合格标准」是假话 —— 推断出来的语言和自报的语言一样
  //     参与语言筛选,真的决定一个人被选中还是被筛掉。改的是**文案**:照实说出来,
  //     同时讲清它只管语言这一条、别的合格标准一格没动。
  //  2. 「未知」不止一种来路 —— 还有一种是照他发的东西试着判断过、但把握不够,
  //     没当结论,于是值不显示(见 LanguageProvenance.ts 的 WITHHELD_NOTE 一档)。
  //     曾经写在这里的「两份记录各执一词」那一档已经随门面仲裁一起删掉了:
  //     现在归属只由服务端裁决说了算,门面不再自己判该信哪一份,那个状态永不出现。
  //  3. 本轮:界面已经会显示第四档「来源不明」(资料里有这个值,但看不出是不是他自己
  //     填的),说明却还只讲三档,而且是**排他式**的写法(「两样都没有就是未知」)——
  //     照那句话读,来源不明的人本该显示成「未知」,与眼前看到的直接打架。四档改成
  //     逐档正面说,不再用「剩下的就是」这种反推句式。
  //  4. 同一轮:有推断值、但他发的文字互相印证不够的那一票,服务端没敢用、不参与筛选,
  //     门面也不许把它算成推断档。说明里给它留了位置,免得操作员看见一个「未知」,
  //     以为我们连试都没试过。
  // 两句说明只转述服务端已经定好的口径,不在这里判定谁是哪一档。
  const languageStat = languageOriginSummaryLabel(languageOriginCounts(summary.rows.map((row) => row.language)));
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
              title="这些人还没有可审计的视频活跃证据，不计入 30 人；增长候选必须补齐严格证据后才能选择。"
              className="inline-flex items-center gap-1 rounded border border-sky-300/20 bg-sky-400/[0.06] px-1.5 py-0.5 text-[10.5px] leading-4 text-sky-100"
            >
              <Clock3 size={9} /> 从没抓到过视频 {summary.activityUnknown}（不计入 {summary.target} 人；增长候选先补证）
            </span>
          ) : null}
          {extraStats.map((label) => (
            <span key={label} className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]">{label}</span>
          ))}
          {languageStat ? (
            <span
              data-testid={`${lane}-language-origin-stat`}
              title="这一栏按「这个语言是谁说的」分四档。「自报」是他在平台资料里自己填的；「推断」是平台资料没填、我们照他自己发的个人简介或作品标题倒推出来的；「来源不明」是资料里确实有这个值、但看不出是不是他自己填的；「未知」有两种来路：一种是我们这里没有他的语言，另一种是照他发的东西试着判断过、但把握不够，没当结论。除「未知」外，前三档的语言值都一样参与语言筛选，会影响一个人被选中还是被筛掉。推断另有一道门槛：只有他发的文字里有多处互相印证才算数；印证不足、我们没敢用的那一票不参与筛选，也不算进上面的「推断」，按「未知」计。语言之外的其他合格标准不受影响。"
              className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10.5px] leading-4 text-[var(--ds-text-meta)]"
            >
              {languageStat}
            </span>
          ) : null}
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
          <table className={`${hasGrowthRows ? "min-w-[1510px]" : "min-w-[1180px]"} w-full border-collapse text-left text-[11px] leading-[18px]`}>
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
                <th className="w-28 px-2 py-2 font-medium">最近内容</th>
                <th className="min-w-36 px-2 py-2 font-medium">市场证据</th>
                <th className="w-28 px-2 py-2 font-medium" title="这一格分四档。「自报」是他在平台资料里自己填的，值正常显示、不带角标；「推断」是平台资料没填、由我们照他发的东西倒推出来的，值旁边带「推断」角标；「来源不明」是资料里有这个值、但看不出是不是他自己填的，值旁边带「来源不明」角标；「未知」有两种来路：一种是我们这里没有他的语言，另一种是照他发的东西试着判断过，但他发的文字互相印证不够、我们把握不够，没当结论。这两种都只显示「未知」，不显示语言值，也不带「推断」角标。">语言</th>
                <th className="w-24 px-2 py-2 font-medium">KOL 类型</th>
                <th className="min-w-52 px-2 py-2 font-medium" title="这里只解释为何被检索命中；是否值得联系由右侧严格证据与增长判断决定。">为何被找到</th>
                {hasGrowthRows ? (
                  <th className="min-w-80 px-2 py-2 font-medium" title="增长候选分按产品适配、市场推进、受众适配、内容执行汇总；严格证据未通过时仍只是候选，缺失维度不按 0 分。">
                    为什么值得联系 / 还缺什么
                  </th>
                ) : null}
                <th className="w-24 px-2 py-2 font-medium">联系方式</th>
                <th className="w-24 px-2 py-2 font-medium">分析</th>
                <th className="w-28 px-2 py-2 font-medium">硬闸</th>
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row) => {
                const poolId = Number(row.item.kol_pool_id) || 0;
                const growth = candidateGrowthSummary(row.item);
                const selectable = (row.strictQualified || (row.activityUnknown && !growth.active)) && poolId > 0;
                const favorited = favoriteIds.has(poolId);
                const favoriteBusy = favoriteBusyIds.has(poolId);
                const favoriteResult = favoriteResults.get(poolId) || "";
                const favoriteError = favoriteErrors.get(poolId) || "";
                const favoriteAllowed = selectable && selectionReady && !selectionDisabled && Boolean(onFavorite);
                const legacyRank = growth.active ? null : candidateRankSummary(row.item);
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
                  <td className="px-2 py-2">
                    <LanguageProvenanceCell provenance={row.language} testId={`${lane}-language-${row.identity}`} />
                  </td>
                  <td className="px-2 py-2" title={row.accountQuality || "账号质量待核验"}>{row.profileType || "待核验"}</td>
                  <td className="max-w-72 truncate px-2 py-2" title={row.whyFit || "待补充匹配依据"}>
                    {row.whyFit || "待补充"}
                  </td>
                  {hasGrowthRows ? (
                    <td className="px-2 py-2" data-testid={`${lane}-growth-${poolId || row.rank}`}>
                      {growth.active ? (
                        <div className="min-w-72 space-y-1">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10.5px] font-medium text-emerald-100">
                            <span>增长候选分 {growth.score == null ? "待补证" : growth.score.toFixed(1).replace(/\.0$/, "")}</span>
                            <span>证据置信度 {growth.evidenceConfidence == null ? "待补证" : `${growth.evidenceConfidence.toFixed(1).replace(/\.0$/, "")}/100`}</span>
                          </div>
                          <div className={growth.strictGatePassed ? "text-[10px] leading-4 text-emerald-200" : "text-[10px] leading-4 text-amber-200"}>
                            {growth.decisionLabel}
                          </div>
                          {growth.whyToFind.length ? (
                            <div className="text-[9.5px] leading-4 text-slate-300" title={growth.whyToFind.join("；")}>
                              为什么找：{growth.whyToFind.slice(0, 2).join("；")}
                            </div>
                          ) : null}
                          <div className="grid grid-cols-2 gap-x-2 text-[9.5px] leading-4 text-slate-400">
                            {growth.dimensions.map((dimension) => (
                              <span key={dimension.key} className={dimension.score == null ? "text-amber-200/90" : ""}>
                                {dimension.label} {dimension.displayValue}
                              </span>
                            ))}
                          </div>
                          {growth.nextAction ? (
                            <div className="text-[9.5px] leading-4 text-amber-200">下一步：{growth.nextAction}</div>
                          ) : null}
                          {growth.disclaimer ? <div className="text-[9px] leading-4 text-slate-500">{growth.disclaimer}</div> : null}
                        </div>
                      ) : legacyRank?.score != null ? (
                        <span className="text-[10px] text-slate-400">{legacyRank.scoreLabel} {legacyRank.score.toFixed(2)}</span>
                      ) : (
                        <span className="text-[10px] text-slate-500">旧结果 · 未投影增长评分</span>
                      )}
                    </td>
                  ) : null}
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
          {online
            ? terminal
              ? "本轮已结束，没有已通过联网严格验收的候选。"
              : "联网严格候选正在进入服务端排名流，首批通过验收后会立即显示。"
            : "本地候选正在进入服务端排名流，首批到达后会立即显示。"}
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
