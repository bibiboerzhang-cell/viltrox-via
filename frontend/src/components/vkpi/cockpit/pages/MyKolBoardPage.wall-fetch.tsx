import React from "react";

import {
  fetchWallFetchOutcome,
  getWallFetchPlan,
  startWallFetch,
  type WallFetchOutcome,
  type WallFetchPlan,
  type WallFetchResult,
} from "../../../../services/vkpi/myKolWallFetch-api";
import { CHIP, CHIP_OFF } from "./MyKolBoardPage.libdetail";
import { ReceiptLine } from "./MyKolBoardPage.receipt";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

// 内容墙「去查最新内容」入口(内容墙车道独占文件)。
//
// 四条硬规矩,改这个文件前先读:
//  1. **先报价再花钱。** 任何会去平台取数的点击,点之前服务端都已经算出「这次去几个
//     账号、合计几次取数」。确认框里的每个数字都原样来自报价,前端一个数都不自己乘。
//  2. **诚实状态机。** 没派出去就一个字都不说「正在取」;派出去了只说「已安排 N 个、
//     还没回来」。**派完之后必须回读结局**,并且回读有次数上限:读到了就照实说
//     「取回来了 / 没能取到 + 为什么」,读不到就照实说读不到,绝不转永不结束的圈,
//     更不许把「读不到」算成「已完成」(2026-08-24 线上 P0 的病根正是这个:活被拦死,
//     界面却一直显示「已安排,还没结果回来」,操作员对着假状态等了 17 分钟)。
//  3. **不自动触发。** 没有 useEffect 自动派活,没有常驻定时器,页面加载不会花一分钱。
//     刷新墙面是纯读,与取数无关;回读也是纯读,且只在派活之后有限次发生。
//  4. **确认 fail-closed。** 「全部收藏」永远弹框——不依赖服务端返回的
//     ``requires_confirmation``。服务端漏返回该字段时,批量操作绝不能静默直发。
//
// 确认边界:选中单个账号 = 小操作,点按钮本身即确认,不弹框打扰;
// 「全部收藏 KOL」= 操作员没有逐个看过名单,**永远**弹框,哪怕只算出 1 个。

type Phase = "idle" | "quoting" | "confirming" | "dispatching" | "watching" | "settled";

// 回读节奏:第一次立刻读(被拦下的活要马上现形),之后每 8 秒一次,最多 10 次(≈80 秒)。
// 到点就停,并如实说「不再自动查了」。**这是终止条件,不许改成无限轮询。**
const WATCH_INTERVAL_MS = 8000;
const WATCH_MAX_POLLS = 10;

const PLATFORM_LABEL: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
};

const SKIP_LABEL: Record<string, string> = {
  shared_readonly: "同事分享给你的,只能查看",
  recently_fetched: "最近刚取过,这次跳过",
  per_click_cap: "超出单次上限,这次没排上",
  daily_cap: "今天的取数次数已用完",
};

const FAIL_LABEL: Record<string, string> = {
  my_kol_paid_action_write_forbidden: "同事分享给你的,只能查看",
  vkpi_write_permission_required: "你的账号没有发起取数的权限",
  kol_profile_target_drifted: "这个账号的主页地址变了,请先核对",
  profile_url_missing: "缺主页地址,没法去取",
  staff_identity_required: "登录身份异常,请重新登录",
};

// 未映射的机器错误码**不许原样上门面**(红线 5):一律归入这一句。
const UNKNOWN_FAIL_TEXT = "没能开始,原因还没查清";

function errorCode(reason: unknown): string {
  const detail = (reason as { detail?: unknown })?.detail;
  if (detail && typeof detail === "object" && "code" in (detail as Record<string, unknown>)) {
    return String((detail as Record<string, unknown>).code || "");
  }
  return String(detail || (reason as Error)?.message || "");
}

function failText(code: string): string {
  return FAIL_LABEL[code] || UNKNOWN_FAIL_TEXT;
}

function platformName(platform: string): string {
  const key = String(platform || "").toLowerCase();
  return PLATFORM_LABEL[key] || key || "未标平台";
}

/** 报价 → 一句人话。数字全部来自服务端,前端只负责拼句子。 */
export function planSentence(plan: WallFetchPlan): string {
  if (plan.planned_count <= 0) return "这次没有需要去取的账号。";
  const calls = plan.fetch_calls || { total: 0, max_total: 0, by_platform: {} };
  const parts: string[] = [];
  // 计量单位 = 真实的平台取数次数,不是「一个账号算一次」。
  parts.push(
    calls.total > 0
      ? `本次会去 ${plan.planned_count} 个账号取最新内容,合计 ${calls.total} 次取数${
          calls.max_total > calls.total ? `(最多 ${calls.max_total} 次)` : ""
        }`
      : `本次会去 ${plan.planned_count} 个账号取最新内容`,
  );
  const multi = Object.entries(calls.by_platform || {})
    .filter(([, value]) => Number(value?.per_account) > 1)
    .map(([platform, value]) => `${platformName(platform)} 每个账号要取 ${Number(value.per_account)} 次`);
  if (multi.length) parts.push(`${multi.join("、")}(先取账号资料,再取内容列表)`);
  parts.push(`每个账号取最近 ${plan.posts_per_account} 条`);
  if (plan.days === 0) parts.push("「全部时间」指的是取最近这一批,不是把账号的历史内容全翻一遍");
  return `${parts.join(";")}。`;
}

/** 时间范围口径 → 一句人话。**不依赖确认框**:单账号路径不弹框也必须看得见。 */
export function windowSentence(plan: WallFetchPlan | null, days: number): string {
  if (days <= 0) return "「全部时间」指的是取最近这一批内容,不是把历史全翻一遍。";
  const head = `「最近 ${days} 天」的口径:`;
  if (!plan || !plan.planned?.length) {
    return `${head}YouTube / TikTok 由平台按发布时间截取;Instagram 只能取最近内容,平台不认发布时间,取回的内容可能超出这个范围。`;
  }
  const labels = plan.window?.exactness_labels || {};
  const groups = new Map<string, string[]>();
  plan.planned.forEach((item) => {
    const key = String(item.window_exactness || "");
    const bucket = groups.get(key) || [];
    const name = platformName(item.platform);
    if (!bucket.includes(name)) bucket.push(name);
    groups.set(key, bucket);
  });
  const counts = (plan.window?.exactness_counts || {}) as Record<string, number>;
  const parts = Array.from(groups.entries()).map(([key, platforms]) => {
    const count = Number(counts[key] || 0);
    return `${platforms.join(" / ")}${count > 0 ? ` ${count} 个账号` : ""}:${labels[key] || key}`;
  });
  return `${head}${parts.join(";")}。`;
}

/** 本月额度提示 → 一句人话。服务端已经算出来的东西,不许在路上丢掉。 */
export function budgetSentence(plan: WallFetchPlan | null): string {
  if (!plan?.budget?.configured) return "";
  if (plan.budget.hard_stopped) return "本月取数额度已用完,派出去的多半开始不了。";
  if (plan.budget.usage_ratio == null) return "";
  return `本月取数额度已用约 ${Math.round(plan.budget.usage_ratio * 100)}%。`;
}

/** 跳过明细 → 一句人话。零跳过时返回空串,不制造噪音。 */
export function skipSentence(plan: WallFetchPlan): string {
  const parts = Object.entries(plan.skipped_counts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([key, count]) => `${count} 个${SKIP_LABEL[key] || key}`);
  return parts.length ? `另有:${parts.join(";")}。` : "";
}

/** 派活回执 → 一句人话。只陈述已知事实,绝不出现「正在取」。
 *
 * ``pending`` = 还没有回读到任何结局。一旦回读有了结果就必须传 false,否则
 * 「还没有结果回来」会和后半句「已经取回来了」自相矛盾——那也是一种撒谎。
 */
export function receiptSentence(result: WallFetchResult, pending = true): FlowReceipt {
  if (result.status === "nothing_to_fetch") {
    return { text: `没有派出取数:${skipSentence(result.plan) || "这次没有需要去取的账号。"}`, tone: "info" };
  }
  const parts: string[] = [];
  if (result.queued.length) {
    parts.push(`已安排 ${result.queued.length} 个账号去取最新内容${pending ? ",还没有结果回来" : ""}`);
  }
  if (result.already_queued.length) {
    parts.push(`${result.already_queued.length} 个账号已经有一次在排队,本次并入那一次(时间范围以先排队的那次为准)`);
  }
  if (result.failed.length) {
    const reasons = Array.from(new Set(result.failed.map((item) => failText(String(item.reason || "")))));
    parts.push(`${result.failed.length} 个没能开始:${reasons.join("、")}`);
  }
  if (!parts.length) return { text: "服务端没有返回可读的派单结果,请重试。", tone: "error" };
  const skipped = skipSentence(result.plan);
  return {
    text: `${parts.join(";")}。${skipped}`,
    tone: result.queued.length || result.already_queued.length ? "ok" : "error",
  };
}

/** 回读结局 → 一句人话。三种收尾各说各的,**「读不到」永远不算「已取回」**。 */
export function outcomeSentence(outcome: WallFetchOutcome, exhausted: boolean): FlowReceipt {
  const { landed, waiting, stopped, unknown } = outcome.counts;
  const parts: string[] = [];
  if (landed > 0) parts.push(`${landed} 个已经取回来了`);
  if (stopped > 0) {
    const reasons = Array.from(
      new Set(
        outcome.items
          .filter((item) => item.state === "stopped")
          .map((item) => String(item.reason_human || "").trim() || UNKNOWN_FAIL_TEXT),
      ),
    );
    parts.push(`${stopped} 个没能取到:${reasons.join("、")}`);
  }
  if (waiting > 0) parts.push(`${waiting} 个还在等`);
  if (unknown > 0) parts.push(`${unknown} 个这里读不到结果(可能已并入别人先排的那一次)`);
  if (!parts.length) return { text: "", tone: "info" };
  const tail = waiting > 0
    ? (exhausted
      ? ";这里不再自动查了,过一会儿点「看看回来没有」重新读取这面墙。"
      : ";还在等结果回来。")
    : ";点「看看回来没有」重新读取这面墙。";
  const tone: FlowReceipt["tone"] = stopped > 0 ? "error" : landed > 0 && waiting === 0 ? "ok" : "info";
  return { text: `${parts.join(";")}${tail}`, tone };
}

function ConfirmCard({
  plan,
  days,
  busy,
  onCancel,
  onConfirm,
}: {
  plan: WallFetchPlan;
  days: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const budget = budgetSentence(plan);
  return (
    <div role="dialog" aria-label="确认去取最新内容" className="mt-2 rounded-lg border border-warn bg-warn-soft px-3 py-2.5 text-[12px] leading-5 text-ink">
      <div className="font-semibold">去 {plan.scope_label} 取最新内容（{plan.window_label}）</div>
      <div className="mt-1 text-ink-2">{planSentence(plan)}</div>
      <div className="mt-1 text-muted">{windowSentence(plan, days)}</div>
      {skipSentence(plan) ? <div className="mt-1 text-muted">{skipSentence(plan)}</div> : null}
      {budget ? (
        <div className={`mt-1 ${plan.budget.hard_stopped ? "font-semibold text-crit" : "text-muted"}`}>{budget}</div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          disabled={busy || plan.planned_count <= 0}
          className={`${CHIP} border-accent bg-accent-soft text-accent disabled:opacity-50`}
          onClick={onConfirm}
        >
          {busy ? "正在派单…" : `确认去 ${plan.planned_count} 个账号取`}
        </button>
        <button type="button" disabled={busy} className={`${CHIP} ${CHIP_OFF} disabled:opacity-50`} onClick={onCancel}>
          先不取
        </button>
      </div>
    </div>
  );
}

export function WallFetchControl({
  apiToken,
  kolPoolId,
  kolLabel,
  days,
  dayLabel,
  onRefreshWall,
}: {
  apiToken: string;
  kolPoolId: number;
  kolLabel: string;
  days: number;
  dayLabel: string;
  onRefreshWall: () => void;
}) {
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [plan, setPlan] = React.useState<WallFetchPlan | null>(null);
  const [dispatched, setDispatched] = React.useState<WallFetchResult | null>(null);
  const [notice, setNotice] = React.useState<FlowReceipt | null>(null);
  const [outcome, setOutcome] = React.useState<WallFetchOutcome | null>(null);
  const [watch, setWatch] = React.useState<{ jobIds: number[]; polls: number; exhausted: boolean } | null>(null);
  // 切换范围/时间即作废上一次的报价、回执与回读:界面上的数字永远对应当下的选择。
  const epoch = React.useRef(0);
  React.useEffect(() => {
    epoch.current += 1;
    setPhase("idle");
    setPlan(null);
    setDispatched(null);
    setNotice(null);
    setOutcome(null);
    setWatch(null);
  }, [kolPoolId, days]);

  // 派单进行中的同步锁:setPhase 要等下一次渲染才生效,光靠 phase 挡不住
  // 同一 tick 内的第二次点击。花钱的动作必须由 ref 同步锁住。
  const dispatching = React.useRef(false);

  // 有限次回读:第一次立刻读,之后每 WATCH_INTERVAL_MS 一次,满 WATCH_MAX_POLLS 即停。
  // 纯读端点,不写库、不触发任何取数;停下之后由操作员自己点「看看回来没有」。
  React.useEffect(() => {
    if (!watch || watch.exhausted || !apiToken || !watch.jobIds.length) return undefined;
    const myEpoch = epoch.current;
    const controller = new AbortController();
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const next = await fetchWallFetchOutcome(apiToken, { jobIds: watch.jobIds, signal: controller.signal });
          if (cancelled || epoch.current !== myEpoch) return;
          const polls = watch.polls + 1;
          const done = next.counts.waiting <= 0 || polls >= WATCH_MAX_POLLS;
          setOutcome(next);
          setWatch({ jobIds: watch.jobIds, polls, exhausted: done });
          if (done) setPhase("settled");
        } catch {
          if (cancelled || epoch.current !== myEpoch) return;
          // 回读失败不改写派单事实:停下来,让回执停在「还没有结果回来」这句实话上。
          setWatch({ jobIds: watch.jobIds, polls: watch.polls + 1, exhausted: true });
          setPhase("settled");
        }
      })();
    }, watch.polls === 0 ? 0 : WATCH_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [apiToken, watch]);

  const dispatch = React.useCallback(
    async (target: WallFetchPlan, myEpoch: number) => {
      if (dispatching.current) return;
      dispatching.current = true;
      setPhase("dispatching");
      try {
        const result = await startWallFetch(apiToken, {
          days,
          kolPoolId: kolPoolId || undefined,
          planHash: target.plan_hash,
          expectedCount: target.planned_count,
        });
        if (epoch.current !== myEpoch) return;
        setNotice(null);
        setDispatched(result);
        setOutcome(null);
        const jobIds = [...result.queued, ...result.already_queued]
          .map((item) => Number(item.job_id) || 0)
          .filter((jobId) => jobId > 0);
        if (jobIds.length) {
          setWatch({ jobIds, polls: 0, exhausted: false });
          setPhase("watching");
        } else {
          setWatch(null);
          setPhase("settled");
        }
      } catch (error) {
        if (epoch.current !== myEpoch) return;
        const code = errorCode(error);
        setDispatched(null);
        setNotice({
          text: code === "wall_fetch_plan_drifted"
            ? "名单在你确认期间变了(有人改了收藏,或有账号刚被取过),这次没有派出。请再点一次重新看数字。"
            : `没能派出:${failText(code)}。`,
          tone: "error",
        });
        setPhase("idle");
        setPlan(null);
      } finally {
        dispatching.current = false;
      }
    },
    [apiToken, days, kolPoolId],
  );

  const busyRef = React.useRef(false);
  const start = React.useCallback(async () => {
    if (!apiToken || busyRef.current || dispatching.current) return;
    if (phase === "quoting" || phase === "dispatching") return;
    busyRef.current = true;
    const myEpoch = ++epoch.current;
    setPhase("quoting");
    setNotice(null);
    setDispatched(null);
    setOutcome(null);
    setWatch(null);
    try {
      const quote = await getWallFetchPlan(apiToken, { days, kolPoolId: kolPoolId || undefined });
      if (epoch.current !== myEpoch) return;
      setPlan(quote);
      if (quote.planned_count <= 0) {
        setNotice({ text: `没有派出取数:${skipSentence(quote) || "这次没有需要去取的账号。"}`, tone: "info" });
        setPhase("idle");
        return;
      }
      // 单个账号:点按钮本身即确认,不弹框。「全部收藏」永远二次确认——
      // fail-closed:服务端漏了 requires_confirmation 也照样弹,绝不静默直发。
      if (quote.requires_confirmation || !kolPoolId) {
        setPhase("confirming");
        return;
      }
      await dispatch(quote, myEpoch);
    } catch (error) {
      if (epoch.current !== myEpoch) return;
      setNotice({ text: `没能算出这次要取几个账号:${failText(errorCode(error))}。`, tone: "error" });
      setPhase("idle");
    } finally {
      busyRef.current = false;
    }
  }, [apiToken, days, dispatch, kolPoolId, phase]);

  // 回执 = 派单事实 + 回读结局。永远只有一条,且后半段随回读推进,不会停在假状态上。
  const receipt: FlowReceipt | null = React.useMemo(() => {
    if (notice) return notice;
    if (!dispatched) return null;
    if (dispatched.status === "nothing_to_fetch" || !outcome) return receiptSentence(dispatched);
    const follow = outcomeSentence(outcome, Boolean(watch?.exhausted));
    if (!follow.text) return receiptSentence(dispatched);
    // 有结局了就不再说「还没有结果回来」——两句话不能互相打脸。
    return { text: `${receiptSentence(dispatched, false).text}${follow.text}`, tone: follow.tone };
  }, [dispatched, notice, outcome, watch?.exhausted]);

  const hasDispatch = Boolean(dispatched && (dispatched.queued.length || dispatched.already_queued.length));
  const buttonLabel = phase === "quoting"
    ? "正在算这次取几个…"
    : phase === "dispatching"
      ? "正在派单…"
      : kolPoolId
        ? "去查这个账号的最新内容"
        : "去查全部收藏账号的最新内容";
  const budget = budgetSentence(plan);

  return (
    <div data-vkpi-wall-fetch="">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          disabled={!apiToken || phase === "quoting" || phase === "dispatching" || phase === "confirming"}
          className={`${CHIP} border-accent bg-accent-soft text-accent disabled:opacity-50`}
          title={kolPoolId
            ? `去平台查 ${kolLabel} 的最新内容(${dayLabel});这是一次真实取数,点下即执行`
            : `先算出要去几个账号,再由你确认(${dayLabel})`}
          onClick={() => void start()}
        >
          {buttonLabel}
        </button>
        {hasDispatch ? (
          <button type="button" className={`${CHIP} ${CHIP_OFF}`} title="重新读取这面墙(纯读,不会再去取数)" onClick={onRefreshWall}>
            看看回来没有
          </button>
        ) : null}
      </div>
      {/* 口径与额度都不依赖确认框:单账号路径不弹框,也必须看得见这两句。 */}
      <div className="mt-1 text-[11px] leading-4 text-muted">{windowSentence(plan, days)}</div>
      {budget ? (
        <div className={`mt-1 text-[11px] leading-4 ${plan?.budget?.hard_stopped ? "font-semibold text-crit" : "text-muted"}`}>
          {budget}
        </div>
      ) : null}
      {phase === "confirming" && plan ? (
        <ConfirmCard
          plan={plan}
          days={days}
          busy={false}
          onCancel={() => { setPhase("idle"); setPlan(null); }}
          onConfirm={() => { const myEpoch = epoch.current; void dispatch(plan, myEpoch); }}
        />
      ) : null}
      <ReceiptLine msg={receipt} />
    </div>
  );
}
