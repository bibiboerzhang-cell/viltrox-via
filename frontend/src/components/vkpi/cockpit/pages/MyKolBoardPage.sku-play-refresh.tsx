import React from "react";
import { RefreshCw } from "lucide-react";
import { formatLocal, relativeFromNow } from "../../lib/timeLocal";
import {
  fetchSkuPlayRefreshPlan,
  runSkuPlayRefresh,
  skuPlayRefreshBlockText,
  skuPlayRefreshFailText,
  skuPlayRefreshSkipText,
  skuPlayRefreshState,
  type SkuPlayGroup,
  type SkuPlayItem,
  type SkuPlayRefreshLimits,
  type SkuPlayRefreshPlan,
  type SkuPlayRefreshResult,
} from "../../../../services/vkpi/myKolSkuPlay-api";
import {
  isTaskActive,
  taskChip,
  TASK_CHIP_TONE_CLASS,
  type VkpiTaskState,
} from "../../../../services/vkpi/myKolVideoTasks";

/* ====== MY KOL · 单品播放数据「逐条重新实测」====================================
   一句话事实:重新实测是**异步**的 —— 点一下只是把这条视频排进取数队列,
   读数要等后台取回来才变。所以本件从头到尾只说「已排队 / 还在路上 / 已完成 /
   没成功」,绝不显示「正在实测」式的假同步进度,也绝不用定时器假装完成。

   花钱前先算账(2026-08-25 补的服务端硬闸):点任何取数入口,先走 GET 报价
   (纯读、零花费),把服务端算出来的「这次真的会取几次数」摆出来;确认后才 POST
   派活,并把报价指纹与条数一起回传——服务端重算比对,对不上一条都不派。
   单次上限 / 每日上限 / 冷却三道闸**全部在服务端判**,口径与内容墙侧同源,
   本件只负责如实显示;绕开界面也拿不到无上限的批量取数。

   收工判据必须分清成功与失败(同日修的另一条):「不在路上了」**不等于**「成功了」。
   任务被拦下 / 失败时活跃计数同样归零,只看它就会宣布「全部回来」——那是谎报。
   本件把被盯的行分成 已取回 / 没成功 / 还没确认 三档,分别说人话;失败原因用服务端
   给的中文一句,拿不到就归统一兜底句,绝不把机器码原样打到门面上。
   时间一律 timeLocal(浏览器时区)。门面禁内部词。 */

/** 有限次退避重读节拍(毫秒);到顶即停,交回手动重试。 */
export const REFRESH_POLL_STEPS_MS = [3000, 6000, 12000, 24000, 48000];

export type RefreshReceiptTone = "info" | "warn" | "error" | "done";

export interface RefreshReceipt {
  tone: RefreshReceiptTone;
  text: string;
}

/** 被盯的行读回来之后的分档:还在路上 / 已取回 / 没成功 / 还没确认。 */
interface WatchTally {
  active: number;
  done: number;
  failed: number;
  unclear: number;
  reasons: string[];
}

function detailCode(err: unknown): string {
  const source = (err as { detail?: unknown; message?: unknown }) || {};
  const detail = source.detail;
  if (detail && typeof detail === "object") {
    return String((detail as { code?: unknown }).code || "").trim();
  }
  return String(detail || source.message || "").trim();
}

/** 失败原因一律人话:服务端给了中文就用它,没给就归兜底句(不回显机器码)。 */
function failureText(state: VkpiTaskState): string {
  const human = String(state.failure_reason_human || "").trim();
  return human || "原因暂时说不清,可以稍后再试";
}

function reasonPhrase(list: string[]): string {
  const seen = new Set(list.filter((text) => text));
  return Array.from(seen).slice(0, 3).join("、") || "原因暂时说不清";
}

/** 报价里被闸挡下的部分:每一档都如实说,一条都不静默消失。 */
function skipPhrases(plan: SkuPlayRefreshPlan): string[] {
  const counts = plan.skipped_counts || {};
  const limits = plan.limits;
  return Object.keys(counts)
    .filter((key) => Number(counts[key]) > 0)
    .map((key) => `${Number(counts[key])} 条${skuPlayRefreshSkipText(key, limits)}`);
}

/** 派活回执:派出去几条、几条并入已有、几条没派成 + 被闸挡下的明细。 */
function dispatchReceipt(result: SkuPlayRefreshResult, plan: SkuPlayRefreshPlan): RefreshReceipt {
  const queued = (result.queued || []).length;
  const already = (result.already_queued || []).length;
  const failed = result.failed || [];
  const parts: string[] = [];
  if (queued > 0) parts.push(`已排队 ${queued} 条`);
  if (already > 0) parts.push(`${already} 条本来就在队列里`);
  if (failed.length > 0) {
    parts.push(`${failed.length} 条没能提交(${reasonPhrase(failed.map((item) => skuPlayRefreshFailText(item.reason)))})`);
  }
  parts.push(...skipPhrases(result.plan || plan));
  const tone: RefreshReceiptTone = failed.length > 0 ? "error" : queued + already > 0 ? "info" : "warn";
  const tail = queued + already > 0 ? " —— 取数在后台进行,结果回来后本页自动更新这几行。" : "";
  return { tone, text: `${parts.join(" · ") || "这次没有可取数的行"}${tail}` };
}

/** 报价就把这次拦光了:不派活,但要说清为什么(绝不停在「已安排」不动)。 */
function blockedByPlanReceipt(plan: SkuPlayRefreshPlan): RefreshReceipt {
  const parts = skipPhrases(plan);
  return {
    tone: "warn",
    text: parts.length > 0 ? `这次一条都没去取:${parts.join(" · ")}。` : "这次没有可取数的行。",
  };
}

/** 收工三态:全部成功 / 部分成功 / 全部没成功 —— 把没成功的算进「已完成」是红线。 */
function settleReceipt(tally: WatchTally): RefreshReceipt {
  const total = tally.done + tally.failed + tally.unclear;
  const reasons = reasonPhrase(tally.reasons);
  if (tally.failed === 0 && tally.unclear === 0) {
    return { tone: "done", text: `这批 ${total} 条已经全部取回,上面的读数与实测时间已按最新结果更新。` };
  }
  if (tally.done === 0 && tally.unclear === 0) {
    return {
      tone: "error",
      text: `这批 ${total} 条都没能取到数(${reasons})。读数还是上一次的,可以稍后再试一次。`,
    };
  }
  const parts: string[] = [];
  if (tally.done > 0) parts.push(`${tally.done} 条已取回并更新了读数`);
  if (tally.failed > 0) parts.push(`${tally.failed} 条没成功(${reasons})`);
  if (tally.unclear > 0) parts.push(`${tally.unclear} 条这次没等到结果,可以再看一次`);
  return { tone: tally.done > 0 ? "warn" : "error", text: `${parts.join(";")}。` };
}

/** 单品播放逐行重新实测的全部状态机(报价 / 派活 / 防连点 / 有限次重读)。 */
export function useSkuPlayRefresh({
  apiToken,
  groups,
  loadedTick,
  nextTick,
}: {
  apiToken: string;
  groups: SkuPlayGroup[];
  /** 父级最近一次「读完成」对应的 tick;没等到它就下结论 = 拿旧数据说话。 */
  loadedTick: number;
  nextTick: () => number;
}) {
  const [busy, setBusy] = React.useState<Set<number>>(() => new Set());
  const [receipt, setReceipt] = React.useState<RefreshReceipt | null>(null);
  const [pollStep, setPollStep] = React.useState(-1);
  const [awaitingTick, setAwaitingTick] = React.useState(-1);
  const [watched, setWatched] = React.useState<Set<number>>(() => new Set());
  const timerRef = React.useRef<number | null>(null);

  const clearTimer = React.useCallback(() => {
    if (timerRef.current == null) return;
    window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  React.useEffect(() => clearTimer, [clearTimer]);

  const markBusy = React.useCallback((ids: number[], on: boolean) => {
    setBusy((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
      return next;
    });
  }, []);

  /** 报价:纯读、零花费,可安全反复调用。 */
  const loadPlan = React.useCallback(
    (skuCode: string, evidenceId?: number) => fetchSkuPlayRefreshPlan(apiToken, skuCode, evidenceId),
    [apiToken],
  );

  /** 派活:只按服务端报价派,指纹与条数一起回传。 */
  const runPlan = React.useCallback(async (plan: SkuPlayRefreshPlan) => {
    const ids = (plan.planned || []).map((item) => Number(item.evidence_id) || 0).filter((id) => id > 0);
    if (!apiToken || !plan.plan_hash || ids.length === 0) {
      setReceipt(blockedByPlanReceipt(plan));
      return;
    }
    markBusy(ids, true);
    setReceipt(null);
    try {
      const result = await runSkuPlayRefresh(apiToken, {
        sku_code: String(plan.sku_code || ""),
        ...(plan.evidence_id ? { evidence_id: Number(plan.evidence_id) } : {}),
        plan_hash: String(plan.plan_hash),
        expected_count: Number(plan.planned_count) || 0,
      });
      const landed = [...(result.queued || []), ...(result.already_queued || [])]
        .map((item) => Number(item.evidence_id) || 0)
        .filter((id) => id > 0);
      if (landed.length > 0) {
        setWatched((prev) => new Set([...prev, ...landed]));
        setPollStep(0);
      }
      setReceipt(dispatchReceipt(result, plan));
    } catch (err) {
      setReceipt({ tone: "error", text: `没能提交这次取数:${skuPlayRefreshFailText(detailCode(err))}` });
    } finally {
      markBusy(ids, false);
    }
  }, [apiToken, markBusy]);

  /** 行内一键:先报价(单条不弹确认框),再按报价派活;被闸挡下就如实说。 */
  const refreshRow = React.useCallback(async (skuCode: string, item: SkuPlayItem) => {
    const id = Number(item.evidence_id) || 0;
    if (!apiToken || id <= 0 || busy.has(id)) return;
    markBusy([id], true);
    setReceipt(null);
    let plan: SkuPlayRefreshPlan | null = null;
    try {
      plan = await loadPlan(skuCode, id);
    } catch (err) {
      setReceipt({ tone: "error", text: `没能算出这次要取几条:${skuPlayRefreshFailText(detailCode(err))}` });
    } finally {
      markBusy([id], false);
    }
    if (!plan) return;
    await runPlan(plan);
  }, [apiToken, busy, loadPlan, markBusy, runPlan]);

  // 有限次退避重读:只重新读取聚合,不发起任何新的取数。
  React.useEffect(() => {
    clearTimer();
    if (pollStep < 0 || pollStep >= REFRESH_POLL_STEPS_MS.length) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setAwaitingTick(nextTick());
      setPollStep((step) => step + 1);
    }, REFRESH_POLL_STEPS_MS[pollStep]);
    return clearTimer;
  }, [clearTimer, nextTick, pollStep]);

  // 读回来后按四档清点:还在路上 / 已取回 / 没成功 / 还没确认。
  // 「不在路上」**不等于**「成功」——被拦下和失败必须单独数出来。
  const tally = React.useMemo<WatchTally>(() => {
    const result: WatchTally = { active: 0, done: 0, failed: 0, unclear: 0, reasons: [] };
    if (watched.size === 0) return result;
    let seen = 0;
    groups.forEach((group) => (group.items || []).forEach((item) => {
      const id = Number(item.evidence_id) || 0;
      if (!watched.has(id)) return;
      seen += 1;
      const state = skuPlayRefreshState(item);
      if (isTaskActive(state)) result.active += 1;
      else if (state.status === "blocked" || state.status === "failed") {
        result.failed += 1;
        result.reasons.push(failureText(state));
      } else if (state.status === "ready") result.done += 1;
      else result.unclear += 1;
    }));
    // 被盯的行从本页消失(换了范围 / 被移出追踪)也不能算成功。
    result.unclear += Math.max(0, watched.size - seen);
    return result;
  }, [groups, watched]);

  const inFlight = tally.active;

  // 只有等到「派单之后那一次读」真的落地,才允许下任何结论。
  const settled = awaitingTick >= 0 && loadedTick === awaitingTick;

  React.useEffect(() => {
    if (pollStep <= 0 || watched.size === 0 || !settled || tally.active > 0) return;
    setPollStep(-1);
    setAwaitingTick(-1);
    setWatched(new Set());
    setReceipt(settleReceipt(tally));
  }, [pollStep, settled, tally, watched]);

  const exhausted = pollStep >= REFRESH_POLL_STEPS_MS.length && inFlight > 0;

  const retryRead = React.useCallback(() => {
    setAwaitingTick(nextTick());
    setPollStep(0);
  }, [nextTick]);

  return {
    busy,
    receipt,
    loadPlan,
    runPlan,
    refreshRow,
    inFlight,
    exhausted,
    polling: pollStep >= 0 && pollStep < REFRESH_POLL_STEPS_MS.length,
    retryRead,
  };
}

/** 行内「重新实测」按钮 + 该行任务态(禁用理由一律说真话)。 */
export function RowRefreshCell({
  item,
  busy,
  onRefresh,
}: {
  item: SkuPlayItem;
  busy: boolean;
  onRefresh: (item: SkuPlayItem) => void;
}) {
  const state = skuPlayRefreshState(item);
  const chip = taskChip("metric", state);
  const active = isTaskActive(state);
  const allowed = item.can_refresh !== false;
  const blockedText = allowed ? "" : skuPlayRefreshBlockText(item.refresh_forbidden_reason);
  const measuredAbs = item.measured_at ? formatLocal(item.measured_at) : "";
  const measuredRel = item.measured_at ? relativeFromNow(item.measured_at) : "";
  const freshHint = item.recently_measured && measuredRel
    ? `上次实测 ${measuredRel};刚测过不久的这一次会被跳过`
    : "向平台重新取一次这条视频的播放数据;取数在后台进行,不会立刻出数";
  const title = !allowed
    ? blockedText
    : busy
      ? "正在提交这一行的实测请求"
      : active
        ? `${chip.label} —— 已经在队列里,不用重复点`
        : `${freshHint}${measuredAbs ? `(上次实测 ${measuredAbs},按浏览器时区)` : ""}`;
  return (
    <div className="flex items-center justify-end gap-1.5" data-vkpi-sku-play-refresh={item.evidence_id}>
      {active || state.status === "blocked" || state.status === "failed" ? (
        <span
          className={`inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-px text-[9px] font-bold leading-4 ${TASK_CHIP_TONE_CLASS[chip.tone]}`}
          title={chip.title}
          role="status"
          data-vkpi-sku-play-refresh-status={state.status}
        >
          {active ? <span aria-hidden="true" className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" /> : null}
          {chip.label}
        </span>
      ) : null}
      <button
        type="button"
        disabled={!allowed || busy || active}
        onClick={() => onRefresh(item)}
        title={title}
        aria-label={`重新实测 #${item.evidence_id}`}
        className={`inline-flex min-h-7 items-center gap-1 rounded-full border px-2 py-0.5 text-[9.5px] font-semibold transition-colors ${
          item.recently_measured && allowed && !busy && !active
            ? "border-warn text-warn"
            : "border-line text-muted hover:text-ink"
        } disabled:cursor-not-allowed disabled:opacity-45`}
      >
        <RefreshCw size={10} className={busy ? "animate-spin" : undefined} />
        <span>{busy ? "提交中…" : active ? "已排队" : "重新实测"}</span>
      </button>
    </div>
  );
}

/** 上限一律显示服务端算出来的数字,前端不写死。 */
function LimitLine({ limits }: { limits?: SkuPlayRefreshLimits }) {
  if (!limits) return null;
  return (
    <span className="w-full text-[9px] opacity-80">
      上限由后台把关:一次最多 {limits.per_click} 条 · 今天还可取 {limits.daily_left} 条(每天上限 {limits.daily} 条)
      · {limits.cooldown_hours} 小时内刚测过的会跳过。
    </span>
  );
}

/** 单品行的批量入口:先算账(纯读)再二次确认,确认前一次取数都不会发生。 */
export function GroupRefreshControl({
  group,
  disabled,
  loadPlan,
  onConfirm,
}: {
  group: SkuPlayGroup;
  disabled: boolean;
  loadPlan: (skuCode: string, evidenceId?: number) => Promise<SkuPlayRefreshPlan>;
  onConfirm: (plan: SkuPlayRefreshPlan) => void;
}) {
  const [plan, setPlan] = React.useState<SkuPlayRefreshPlan | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const items = React.useMemo(() => (group.items || []).filter((item) => item.can_refresh !== false), [group.items]);
  const total = (group.items || []).length;

  const ask = React.useCallback(async (event: React.MouseEvent) => {
    event.stopPropagation();
    setLoading(true);
    setError("");
    try {
      setPlan(await loadPlan(String(group.sku_code || "")));
    } catch (err) {
      setError(skuPlayRefreshFailText(detailCode(err)));
    } finally {
      setLoading(false);
    }
  }, [group.sku_code, loadPlan]);

  if (items.length === 0) {
    return total > 0 ? (
      <span className="text-[9.5px] text-muted" title="这些红人是同事分享给你的,只能查看">仅可查看</span>
    ) : null;
  }
  if (!plan) {
    return (
      <span className="inline-flex items-center gap-1.5">
        {error ? <span className="text-[9.5px] text-crit">{error}</span> : null}
        <button
          type="button"
          disabled={disabled || loading}
          onClick={ask}
          title="先算一下这次会对几条视频重新取数(只算账,不取数)"
          className="inline-flex min-h-7 items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[9.5px] font-semibold text-muted transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-45"
        >
          <RefreshCw size={10} className={loading ? "animate-spin" : undefined} />
          <span>{loading ? "正在算这次要取几条…" : "重新实测这个单品"}</span>
        </button>
      </span>
    );
  }

  const planned = Number(plan.planned_count) || 0;
  const calls = Number(plan.fetch_calls_total) || planned;
  const skips = skipPhrases(plan);
  return (
    <span
      className="inline-flex flex-wrap items-center gap-1.5 rounded-[6px] border border-warn bg-warn-soft px-2 py-1 text-[9.5px] text-warn"
      role="alertdialog"
      aria-label="确认重新实测"
      data-vkpi-sku-play-group-confirm={group.sku_code}
      onClick={(event) => event.stopPropagation()}
    >
      <span>
        {planned > 0 ? (
          <>本次会对 <b>{planned}</b> 条视频重新取数,一共向平台取 <b>{calls}</b> 次。</>
        ) : (
          <>本次一条都不会去取。</>
        )}
        {skips.length > 0 ? `另有:${skips.join(";")}。` : ""}
      </span>
      <LimitLine limits={plan.limits} />
      {planned > 0 ? (
        <button
          type="button"
          onClick={(event) => { event.stopPropagation(); setPlan(null); onConfirm(plan); }}
          className="min-h-6 rounded-full border border-warn px-2 py-0.5 font-semibold"
        >
          确认取 {calls} 次
        </button>
      ) : null}
      <button
        type="button"
        onClick={(event) => { event.stopPropagation(); setPlan(null); }}
        className="min-h-6 rounded-full border border-line px-2 py-0.5 font-semibold text-muted"
      >
        {planned > 0 ? "取消" : "知道了"}
      </button>
    </span>
  );
}

const RECEIPT_CLASS: Record<RefreshReceiptTone, string> = {
  info: "border-accent text-accent",
  warn: "border-warn text-warn",
  error: "border-crit text-crit",
  done: "border-good text-good",
};

/** 派单回执 + 还在路上的条数;没派活时一个字都不说。 */
export function RefreshReceiptLine({
  receipt,
  inFlight,
  exhausted,
  onRetryRead,
}: {
  receipt: RefreshReceipt | null;
  inFlight: number;
  exhausted: boolean;
  onRetryRead: () => void;
}) {
  if (!receipt && inFlight === 0) return null;
  const tone = receipt?.tone || "info";
  return (
    <div
      role="status"
      aria-live="polite"
      data-vkpi-sku-play-refresh-receipt={tone}
      className={`mb-2 flex flex-wrap items-center gap-2 rounded-[8px] border px-2.5 py-1.5 text-[10.5px] ${RECEIPT_CLASS[tone]}`}
    >
      {receipt ? <span>{receipt.text}</span> : null}
      {inFlight > 0 ? (
        <span>
          还有 <b>{inFlight}</b> 条没回来
          {exhausted ? " —— 本页已停止自动查看,可手动再看一次。" : " —— 本页会自动再看几次。"}
        </span>
      ) : null}
      {inFlight > 0 ? (
        <button
          type="button"
          onClick={onRetryRead}
          title="只重新读取本页数据,不会发起新的取数"
          className="ml-auto min-h-7 rounded-full border border-current px-2 py-0.5 text-[9.5px] font-semibold"
        >
          看看回来没有
        </button>
      ) : null}
    </div>
  );
}
