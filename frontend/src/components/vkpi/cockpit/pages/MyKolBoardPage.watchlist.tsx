import React from "react";
import { ChevronDown, ChevronRight, ExternalLink, Users } from "lucide-react";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import { ApiResponseError } from "../../../../services/http";
import {
  getMyKolGroupWatchOverview,
  getMyKolWatchOverview,
  watchDeepText,
  watchDeltaText,
  type VkpiMyKolGroupWatchOverviewResponse,
  type VkpiMyKolWatchOverviewResponse,
  type WatchGroupCard,
  type WatchKolRow,
  type WatchSummary,
} from "../../../../services/vkpi/myKolWatchlist-api";

/* ============ MY KOL · 观察清单(波 C·C3)============
   用户原话:「能不能特别给某些 kol 增加一个观察列表跟进进度」。
   落点:分组 = 既有员工分组(团队浮层里建/改,勾选「共享 KOL 池」即组内 KOL),本模块只读铺
   C5 的 watch-overview 两端点:总览 = 每组一张汇总卡;点卡展开 = 组内逐 KOL 观察行
   (追踪视频数 / 最近快照 / 7 天播放增量 / 深析完成比 / 待处理失败 / 出镜镜头 / 最近活动)。
   诚实空态:端点 404 = 该版本暂无观察清单(整块 PendingCard);无分组 / 组内无 KOL /
   未登记追踪 / 样本不足 全部按后端 reason 如实显示,不编数;缺字段不渲染。
   「把本组未登记视频加入追踪」后端无端点,先只显示未登记数。
   入口:卡头「管理分组」→ 团队浮层(vkpi:open-team-groups);团队浮层组卡「观察清单 →」
   回到本模块并自动展开该组(sessionStorage vkpi:my-kol-watch-group)。
   红线:纯读;颜色全 token;时间走 formatLocal(浏览器时区);零 fit/rule_v0。 */

export const WATCH_GROUP_FOCUS_KEY = "vkpi:my-kol-watch-group";

function errDetail(err: unknown, fallback: string): string {
  const detail = (err as { detail?: unknown; message?: unknown }) || {};
  return String(detail.detail || detail.message || fallback);
}

function isNotFound(err: unknown): boolean {
  return err instanceof ApiResponseError && err.status === 404;
}

function num(value: unknown): number | null {
  const n = Number(value);
  return value != null && Number.isFinite(n) ? n : null;
}

function openTeamGroups(detail?: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent("vkpi:open-team-groups", { detail: detail || {} }));
}

function openKolProfile(kolPoolId: number) {
  try {
    window.sessionStorage.setItem("vkpi:kol-profile-id", String(kolPoolId));
  } catch {
    /* 存不进不阻断,事件管道仍切页 */
  }
  window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
}

function readFocusGroup(): string {
  try {
    const value = window.sessionStorage.getItem(WATCH_GROUP_FOCUS_KEY) || "";
    if (value) window.sessionStorage.removeItem(WATCH_GROUP_FOCUS_KEY);
    return value;
  } catch {
    return "";
  }
}

const STATE_LABEL: Record<string, { label: string; cls: string }> = {
  tracked: { label: "追踪中", cls: "border-good text-good" },
  untracked: { label: "未登记", cls: "border-warn text-warn" },
  no_videos: { label: "无视频", cls: "border-line text-muted" },
};

function Stat({ label, value, tone = "" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <span className="whitespace-nowrap">
      {label} <b className={`font-mono ${tone || "text-ink"}`}>{value}</b>
    </span>
  );
}

/** 汇总行(总览 totals 与组卡 summary 同形)。缺字段不渲染。 */
function SummaryLine({ summary, compact = false }: { summary: WatchSummary | undefined; compact?: boolean }) {
  if (!summary) return null;
  const untracked = num(summary.untracked_count);
  const failures = num(summary.open_failures);
  const deep = summary.deep_analysis;
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${compact ? "text-[10.5px]" : "text-[11px]"} text-ink-2`}>
      {num(summary.kol_count) != null && <Stat label="KOL" value={num(summary.kol_count)} />}
      {num(summary.tracked_videos_total) != null && <Stat label="追踪视频" value={num(summary.tracked_videos_total)} />}
      {untracked != null && <Stat label="未登记" value={untracked} tone={untracked > 0 ? "text-warn" : "text-ink"} />}
      {"views_delta_7d" in summary && (
        <Stat label="7 天播放" value={watchDeltaText(summary.views_delta_7d, summary.views_delta_7d_reason)} tone={summary.views_delta_7d == null ? "text-muted" : "text-ink"} />
      )}
      {deep && <Stat label="深析" value={watchDeepText(deep)} />}
      {failures != null && <Stat label="待处理失败" value={failures} tone={failures > 0 ? "text-crit" : "text-ink"} />}
      {!compact && summary.last_activity_at && <Stat label="最近活动" value={formatLocal(summary.last_activity_at)} tone="text-muted" />}
    </div>
  );
}

function GroupCard({ card, expanded, onToggle }: { card: WatchGroupCard; expanded: boolean; onToggle: () => void }) {
  const summary = card.summary;
  const kolCount = num(summary?.kol_count) ?? (Array.isArray(card.kol_ids) ? card.kol_ids.length : null);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className={`flex min-w-0 flex-col gap-1.5 rounded-[10px] border px-3 py-2.5 text-left transition-colors ${expanded ? "border-accent bg-accent-soft" : "border-line bg-card hover:border-accent"}`}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <Users size={12} className="flex-none text-muted" />
        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-ink" title={card.name || card.group_id}>{card.name || card.group_id}</span>
        <Chevron size={13} className="flex-none text-muted" />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 text-[10px] text-muted">
        {kolCount != null && <span>{kolCount} KOL</span>}
        {num(card.member_count) != null && <span>· {num(card.member_count)} 成员</span>}
        {summary?.last_activity_at && <span>· 最近活动 {formatLocal(summary.last_activity_at)}</span>}
      </div>
      {kolCount === 0 ? (
        <div className="text-[10.5px] text-muted">本组还没有 KOL —— 编辑分组勾选「共享 KOL 池」后出现在这里。</div>
      ) : (
        <SummaryLine summary={summary} compact />
      )}
    </button>
  );
}

function LensChips({ row }: { row: WatchKolRow }) {
  const lenses = Array.isArray(row.lens_families) ? row.lens_families : [];
  if (lenses.length === 0) return <span className="text-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {lenses.slice(0, 3).map((lens, index) => (
        <span key={`${lens.lens_key || index}`} className="rounded-[4px] border border-line px-1 py-px text-[9px] text-ink-2" title={lens.videos != null ? `${lens.videos} 条视频出镜` : undefined}>
          {lens.display_name || lens.lens_key}
        </span>
      ))}
    </span>
  );
}

function GroupDetailTable({ items }: { items: WatchKolRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-[11.5px]">
        <thead>
          <tr className="text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
            <th className="py-1.5 pr-2 font-semibold">KOL</th>
            <th className="py-1.5 pr-2 text-right font-semibold">追踪视频</th>
            <th className="py-1.5 pr-2 font-semibold">最近快照</th>
            <th className="py-1.5 pr-2 text-right font-semibold">7 天播放</th>
            <th className="py-1.5 pr-2 text-right font-semibold">深析</th>
            <th className="py-1.5 pr-2 text-right font-semibold">待处理失败</th>
            <th className="py-1.5 pr-2 font-semibold">出镜镜头</th>
            <th className="py-1.5 font-semibold">最近活动</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => {
            const state = STATE_LABEL[String(row.tracking_state || "")];
            const failures = num(row.open_failures);
            const tracked = num(row.tracked_videos);
            const paused = num(row.tracked_paused);
            const total = num(row.video_evidence_total);
            return (
              <tr key={row.kol_pool_id} className="border-t border-line">
                <td className="max-w-[220px] py-1.5 pr-2">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => openKolProfile(row.kol_pool_id)}
                      className="min-w-0 truncate text-left font-semibold text-ink-2 transition-colors hover:text-accent"
                      title="打开 KOL 档案"
                    >
                      {row.display_name || row.handle || `#${row.kol_pool_id}`}
                    </button>
                    <ExternalLink size={10} className="flex-none text-muted" />
                  </div>
                  <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[9px] text-muted">
                    {row.platform ? <span>{row.platform}</span> : null}
                    {state ? <span className={`rounded-[4px] border px-1 py-px text-[8.5px] font-semibold ${state.cls}`}>{state.label}</span> : null}
                  </div>
                </td>
                <td className="py-1.5 pr-2 text-right font-mono text-ink">
                  {tracked != null ? tracked : "—"}
                  {total != null ? <span className="text-muted"> / {total}</span> : null}
                  {paused ? <div className="text-[9px] text-muted">已暂停 {paused}</div> : null}
                </td>
                <td className="py-1.5 pr-2 font-mono text-[10px] text-muted">{row.last_snapshot_at ? formatLocal(row.last_snapshot_at) : "未实测"}</td>
                <td className={`py-1.5 pr-2 text-right font-mono ${row.views_delta_7d == null ? "text-muted" : "text-ink-2"}`} title={row.views_delta_7d_videos ? `含 ${row.views_delta_7d_videos} 条视频` : undefined}>
                  {watchDeltaText(row.views_delta_7d, row.views_delta_7d_reason)}
                  {row.views_delta_7d_status === "partial" ? <span className="text-[9px] text-muted">(不足整窗)</span> : null}
                </td>
                <td className="py-1.5 pr-2 text-right font-mono text-ink-2" title={row.deep_analysis?.in_progress ? `进行中 ${row.deep_analysis.in_progress}` : undefined}>
                  {watchDeepText(row.deep_analysis)}
                  {row.deep_analysis?.in_progress ? <div className="text-[9px] text-muted">进行中 {row.deep_analysis.in_progress}</div> : null}
                </td>
                <td className={`py-1.5 pr-2 text-right font-mono ${failures ? "text-crit" : "text-muted"}`}>{failures != null ? failures : "—"}</td>
                <td className="py-1.5 pr-2"><LensChips row={row} /></td>
                <td className="py-1.5 font-mono text-[10px] text-muted">{row.last_activity_at ? formatLocal(row.last_activity_at) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-1 text-[10px] text-muted">追踪视频 = 已登记 / 已采集;7 天播放 = 追踪视频实测增量之和(样本不足不编数);深析 = 最近 N 条视频完成 / 范围;时间按浏览器时区。</div>
    </div>
  );
}

type GroupDetailState = { loading: boolean; error: string; data: VkpiMyKolGroupWatchOverviewResponse | null };

export function WatchlistModule({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  const [data, setData] = React.useState<VkpiMyKolWatchOverviewResponse | null>(null);
  const [error, setError] = React.useState("");
  const [unsupported, setUnsupported] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [tick, setTick] = React.useState(0);
  const [expanded, setExpanded] = React.useState<string>(() => readFocusGroup());
  const [details, setDetails] = React.useState<Record<string, GroupDetailState>>({});

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    getMyKolWatchOverview(apiToken)
      .then((res) => {
        if (!alive) return;
        setUnsupported(false);
        setData(res && typeof res === "object" ? res : null);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        if (isNotFound(err)) {
          setUnsupported(true);
          return;
        }
        setError(errDetail(err, "读取失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, tick]);

  // 团队浮层「观察清单 →」回跳:事件到达时本模块可能已挂载,补读 focus 键。
  React.useEffect(() => {
    const focus = () => {
      const target = readFocusGroup();
      if (target) setExpanded(target);
    };
    window.addEventListener("vkpi:open-mykol-kol", focus);
    return () => window.removeEventListener("vkpi:open-mykol-kol", focus);
  }, []);

  // 分组改动(团队浮层增删改)后总览重读。
  React.useEffect(() => {
    const refresh = () => {
      setDetails({});
      setTick((t) => t + 1);
    };
    window.addEventListener("vkpi:staff-groups-changed", refresh);
    return () => window.removeEventListener("vkpi:staff-groups-changed", refresh);
  }, []);

  const loadGroup = React.useCallback(
    (groupId: string) => {
      if (!apiToken || !groupId) return;
      setDetails((current) => ({ ...current, [groupId]: { loading: true, error: "", data: current[groupId]?.data || null } }));
      getMyKolGroupWatchOverview(apiToken, groupId)
        .then((res) => {
          setDetails((current) => ({ ...current, [groupId]: { loading: false, error: "", data: res && typeof res === "object" ? res : null } }));
        })
        .catch((err: unknown) => {
          const message = isNotFound(err) ? "分组不存在或已删除" : errDetail(err, "读取失败");
          setDetails((current) => ({ ...current, [groupId]: { loading: false, error: message, data: null } }));
        });
    },
    [apiToken],
  );

  React.useEffect(() => {
    if (!expanded || !apiToken) return;
    if (details[expanded]?.data || details[expanded]?.loading) return;
    loadGroup(expanded);
    // details 故意不入依赖:只在切换展开组 / 重读时拉,避免回写 state 再触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, apiToken, tick, loadGroup]);

  if (!apiToken) return <>{noToken}</>;
  if (unsupported) {
    return (
      <PendingCard>
        <b>该版本暂无观察清单</b> —— 后端尚未提供分组观察总览端点;接通后本模块自动点亮,不摆假数。
      </PendingCard>
    );
  }
  if (error) return <ErrorCard title="观察清单读取失败" text={error} />;
  if (!data) return <LoadingLine text={loading ? "观察清单读取中…" : "等待读取…"} />;

  const groups = Array.isArray(data.groups) ? data.groups : [];
  const totals = data.totals;
  const schedulerOn = data.scheduler?.enabled;
  const orphanFavorites = num(totals?.favorites_not_in_any_group);
  const detail = expanded ? details[expanded] : undefined;
  const expandedCard = groups.find((card) => card.group_id === expanded);

  const toolbar = (
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-2">
      {num(totals?.group_count) != null && <Stat label="分组" value={num(totals?.group_count)} />}
      <SummaryLine summary={totals} />
      <span className="ml-auto flex items-center gap-1.5">
        <button type="button" onClick={() => openTeamGroups()} className="rounded-full border border-line px-2 py-0.5 text-[9.5px] text-muted transition-colors hover:text-ink" title="打开团队浮层:新建 / 编辑分组并勾选共享 KOL">
          管理分组
        </button>
        <button
          type="button"
          onClick={() => {
            setDetails({});
            setTick((t) => t + 1);
          }}
          className="rounded-full border border-line px-2 py-0.5 text-[9.5px] text-muted transition-colors hover:text-ink"
          title="重新读取(不触发抓取)"
        >
          重读
        </button>
      </span>
    </div>
  );

  if (data.empty_reason === "no_groups" || groups.length === 0) {
    return (
      <div>
        {toolbar}
        <PendingCard>
          <b>还没有分组</b> —— 观察清单按分组跟进:在「团队」里新建分组,勾选「共享 KOL 池」把要重点跟进的 KOL 放进去,这里就会按组显示追踪进度。
          <button type="button" onClick={() => openTeamGroups({ mode: "new" })} className="ml-2 rounded-full border border-accent bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold text-accent">
            新建分组
          </button>
        </PendingCard>
      </div>
    );
  }

  return (
    <div>
      {toolbar}
      {schedulerOn === false ? (
        <div className="mb-2 rounded-[8px] border border-warn bg-warn-soft px-2.5 py-1.5 text-[10.5px] leading-[1.6] text-ink-2">
          <b className="font-semibold text-warn">自动刷新未开启</b> —— 追踪视频只有手动「刷新指标」才会产生新的实测点,7 天增量会停留在旧值。
        </div>
      ) : null}
      {data.empty_reason === "no_kols_in_groups" ? (
        <div className="mb-2">
          <EmptyLine text="分组里还没有 KOL —— 编辑分组勾选「共享 KOL 池」后按组显示进度。" />
        </div>
      ) : null}
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        {groups.map((card) => (
          <GroupCard
            key={card.group_id}
            card={card}
            expanded={expanded === card.group_id}
            onToggle={() => setExpanded((current) => (current === card.group_id ? "" : card.group_id))}
          />
        ))}
      </div>
      {expanded ? (
        <div className="mt-3 rounded-[10px] border border-line bg-card px-3 py-2.5">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="text-[12px] font-semibold text-ink">{expandedCard?.name || detail?.data?.group?.name || expanded}</span>
            {expandedCard?.description ? <span className="truncate text-[10.5px] text-muted" title={expandedCard.description}>{expandedCard.description}</span> : null}
            <button type="button" onClick={() => openTeamGroups({ mode: "edit", groupId: expanded })} className="ml-auto rounded-full border border-line px-2 py-0.5 text-[9.5px] text-muted transition-colors hover:text-ink" title="编辑分组:增减本组 KOL / 成员">
              编辑本组
            </button>
          </div>
          {detail?.error ? (
            <ErrorCard title="分组观察读取失败" text={detail.error} />
          ) : !detail?.data ? (
            <LoadingLine text="组内 KOL 读取中…" />
          ) : !Array.isArray(detail.data.items) || detail.data.items.length === 0 ? (
            <EmptyLine text="本组还没有 KOL —— 编辑分组勾选「共享 KOL 池」后出现在这里。" />
          ) : (
            <>
              {num(detail.data.summary?.untracked_count) ? (
                <div className="mb-1.5 text-[10.5px] text-warn">
                  本组有 {num(detail.data.summary?.untracked_count)} 个 KOL 已采集视频但未登记追踪 —— 打开 KOL 详情「已采集内容」用「追踪已有视频」登记(批量登记入口待后端提供)。
                </div>
              ) : null}
              <GroupDetailTable items={detail.data.items} />
              {detail.data.kols_truncated ? <div className="mt-1 text-[10px] text-muted">本组 KOL 超过上限,只显示前 {detail.data.items.length} 个(配置 {detail.data.kol_ids_configured ?? "?"} 个,已截断如实标注)。</div> : null}
              {detail.data.tracked_truncated ? <div className="mt-1 text-[10px] text-muted">追踪视频数超过统计上限,增量按已读取部分计算(已截断如实标注)。</div> : null}
              {Array.isArray(detail.data.missing_kol_ids) && detail.data.missing_kol_ids.length > 0 ? <div className="mt-1 text-[10px] text-muted">{detail.data.missing_kol_ids.length} 个已配置 KOL 在库里找不到(可能已删除)。</div> : null}
            </>
          )}
        </div>
      ) : null}
      {orphanFavorites ? (
        <div className="mt-2 text-[10.5px] text-muted">你收藏的 KOL 里有 {orphanFavorites} 个不在任何分组 —— 想重点跟进可在「管理分组」里加进去。</div>
      ) : null}
      {data.tracked_truncated && !expanded ? <div className="mt-1 text-[10px] text-muted">追踪视频数超过统计上限,汇总增量按已读取部分计算(已截断如实标注)。</div> : null}
    </div>
  );
}
