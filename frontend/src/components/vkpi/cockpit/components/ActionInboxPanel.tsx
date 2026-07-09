// W1→W2 Action Inbox — Dashboard 右侧"今日建议"。自取数据(apiToken)。
// W2 起可操作:approve/dismiss/snooze;execute 仍走后端 validators 双闸(approved +
//   touches_v6_fit=False + budget + entity 存在)。approve→execute 两步,前端只触发,不绕审批。
// 后端已按 scope 过滤(管理层全局 / 成员仅自己 owner 的)。

import React from "react";
import { m } from "framer-motion";
import { useRowFlash } from "./ui/rowFlash";
import {
  Brain,
  CalendarClock,
  Check,
  ClipboardList,
  Clock,
  Eye,
  FileCheck,
  Gavel,
  ListChecks,
  Loader2,
  PackageOpen,
  Play,
  RefreshCw,
  Share2,
  Sparkles,
  Target,
  UserPlus,
  X,
} from "lucide-react";
// 闭环波 L4:gtm_verdict 条目内嵌裁决一屏(去裁决 → 展开 VerdictPanel)。
import { VerdictPanel } from "./VerdictPanel";
// GTM-Loop:gtm_bet「标记已执行」直打 mark-done 端点(并行车道在建,前端按合约先行)。
import { apiFetch } from "../../../../services/http";
import {
  approveAction,
  dismissAction,
  executeAction,
  listActionInbox,
  listRecentExecutionLedger,
  snoozeAction,
} from "../../../../services/vkpi/actionInbox-api";
import type { ActionInboxTodaySummary } from "../../../../services/vkpi/actionInbox-api";

const e = React.createElement;

// execute outcome 的 reason 码 → 友好中文(后端 executors/validators 的诚实回因)。
const EXEC_REASON: Record<string, string> = {
  not_approved: "未审批",
  reminder_only_no_executor: "提醒类 · 无需执行,可忽略",
  no_executor: "暂无执行器",
  unknown_category: "未知动作类别",
  entity_missing: "关联实体已不存在",
  budget_hard_stop: "超出单次预算上限",
  touches_v6_fit_violation: "命中 fit 红线,已拦截",
  validation_failed: "执行前校验未通过",
  deep_missing_no_profile_url: "缺主页 URL,无法深析",
  kol_profile_no_profile_url: "缺主页 URL,无法补全",
  failed_retry_not_in_failed_state: "任务非失败态,无需重试",
  content_candidate_no_post_id: "缺内容贴 ID",
};

// 9 类 → 中文标签 + 图标 + 强调色
const CATEGORY_META = {
  kol_profile: { label: "补全资料", Icon: UserPlus, color: "text-amber-300" },
  deep_missing: { label: "深析待跑", Icon: Brain, color: "text-violet-300" },
  failed_retry: { label: "失败重试", Icon: RefreshCw, color: "text-red-300" },
  project_observation: { label: "开观察窗", Icon: Eye, color: "text-cyan-300" },
  content_candidate: { label: "内容确认", Icon: FileCheck, color: "text-emerald-300" },
  retrospective: { label: "项目复盘", Icon: ClipboardList, color: "text-sky-300" },
  event_followup: { label: "活动收尾", Icon: CalendarClock, color: "text-orange-300" },
  inventory_low: { label: "库存预警", Icon: PackageOpen, color: "text-yellow-300" },
  // W4 produce,meta 在此补全:项目共享给你(sky 色)。
  project_shared_to_you: { label: "项目共享", Icon: Share2, color: "text-sky-300" },
  // 闭环波 L4:到期强制裁决任务(L2 produce)。不可跳过,内嵌裁决一屏。
  gtm_verdict: { label: "裁决对答案", Icon: Gavel, color: "text-fuchsia-300" },
  // GTM-Loop L1:GTM Plan materialize 落库的押注(无自动执行体,人做业务动作后标记已执行)。
  gtm_bet: { label: "GTM押注", Icon: Target, color: "text-sky-300" },
};

// 闭环波 L4:gtm_verdict 条目定位结果账本行,id 口径与 L2 decide 端点对齐:
// 有 gtm_outcome_id 直查 outcome;否则用 bet 的 action_inbox id(payload.verdict_for_inbox_id,
// entity_id 兜底 —— L2 的裁决任务 entity_type='action_inbox'、entity_id=bet 行 id)。
function gtmVerdictIdsOf(it: any): { id: number; idType: "inbox" | "outcome" } {
  const p = it && typeof it.payload_json === "object" && it.payload_json ? it.payload_json : {};
  const outcomeId = Number((p as any).gtm_outcome_id ?? (p as any).outcome_id ?? 0) || 0;
  if (outcomeId) return { id: outcomeId, idType: "outcome" };
  const betId =
    Number(
      (p as any).verdict_for_inbox_id ??
        (String(it?.entity_type || "").toLowerCase() === "action_inbox" ? it?.entity_id : 0),
    ) || 0;
  return { id: betId, idType: "inbox" };
}

// 车道B row-flash:条目行外壳 —— status 变化(如 通过 → approved)soft pulse 一次
// (首帧不闪;reduced-motion 在 hook 内降级为静态)。纯展示包装,零业务逻辑。
const InboxRowShell = React.memo(function InboxRowShell({
  status,
  className,
  children,
}: {
  status: string;
  className: string;
  children?: React.ReactNode;
}) {
  const flashRef = useRowFlash<HTMLDivElement>(status);
  return e("div", { ref: flashRef, className }, children);
});

const PRIORITY_META = {
  high: { label: "高", cls: "bg-red-500/15 text-red-300 border-red-500/25" },
  medium: { label: "中", cls: "bg-amber-500/15 text-amber-300 border-amber-500/25" },
  low: { label: "低", cls: "bg-slate-500/15 text-slate-300 border-slate-500/25" },
};

// 路线0:风险等级徽标(执行该动作的风险,独立于优先级)。
const RISK_META: Record<string, { label: string; cls: string }> = {
  high: { label: "风险高", cls: "bg-rose-500/15 text-rose-300 border-rose-500/25" },
  medium: { label: "风险中", cls: "bg-orange-500/15 text-orange-300 border-orange-500/25" },
  low: { label: "风险低", cls: "bg-emerald-500/12 text-emerald-300/80 border-emerald-500/20" },
};

export function ActionInboxPanel({
  apiToken = "",
  limit = 6,
  heading = "今日建议",
}: {
  apiToken?: string;
  limit?: number;
  heading?: string;
}) {
  const [items, setItems] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [scope, setScope] = React.useState("");
  const [available, setAvailable] = React.useState(true);
  // 灌水可见化:当天已执行 / 已批准计数(来自 inbox 响应的 today_summary,执行后增长)。
  const [todaySummary, setTodaySummary] = React.useState<ActionInboxTodaySummary | null>(null);
  // per-id 操作态:'approve' | 'dismiss' | 'snooze' | 'execute' | undefined。
  const [busy, setBusy] = React.useState<Record<string, any>>({});
  const [actionError, setActionError] = React.useState("");
  // R7 执行验收:成功绿色提示 + 折叠的「执行台账」(回读 vkpi_action_execution_ledger)。
  const [okNote, setOkNote] = React.useState("");
  const [ledgerOpen, setLedgerOpen] = React.useState(false);
  const [ledgerItems, setLedgerItems] = React.useState<any[]>([]);
  const [ledgerLoading, setLedgerLoading] = React.useState(false);
  // 默认只显 3 条(避免顶掉下方 KOL 漏斗 / Active Campaigns);可展开看全部(抓取仍 limit 条)。
  const [expanded, setExpanded] = React.useState(false);
  const COLLAPSED_COUNT = 3;
  // 闭环波 L4:gtm_verdict 条目「去裁决」展开态(per-id;内嵌 VerdictPanel)。
  const [verdictOpen, setVerdictOpen] = React.useState<Record<string, boolean>>({});

  const load = React.useCallback(() => {
    if (!apiToken) {
      setLoading(false);
      setError("未登录 / 无 token");
      return;
    }
    setLoading(true);
    setError("");
    // suggested = 待人审;approved = 已审批待执行(让「执行」第二步跨刷新仍可见——
    // approved 态后端不会重新生成,只在此显式拉取)。两者按 id 去重合并。
    Promise.all([
      listActionInbox(apiToken, { limit }),
      listActionInbox(apiToken, { limit, status: "approved" }),
    ])
      .then(([sug, appr]) => {
        const seen = new Set<any>();
        const merged: any[] = [];
        for (const it of [
          ...(Array.isArray(sug?.items) ? sug.items : []),
          ...(Array.isArray(appr?.items) ? appr.items : []),
        ]) {
          if (it && !seen.has(it.id)) {
            seen.add(it.id);
            merged.push(it);
          }
        }
        // 闭环波 L4:到期裁决任务置顶(规格第四章「裁决任务置顶,不可跳过」;稳定排序,其余顺序不动)。
        merged.sort(
          (a: any, b: any) =>
            (b?.category === "gtm_verdict" ? 1 : 0) - (a?.category === "gtm_verdict" ? 1 : 0),
        );
        setItems(merged);
        setScope(sug?.scope || appr?.scope || "");
        setAvailable(sug?.available !== false);
        // 当天闭环计数:两个响应同源(scope 一致),取任一存在的即可。
        setTodaySummary(sug?.today_summary ?? appr?.today_summary ?? null);
      })
      .catch((err) => {
        setError(err?.message || "加载失败");
      })
      .finally(() => setLoading(false));
  }, [apiToken, limit]);

  React.useEffect(() => {
    load();
  }, [load]);

  // 乐观移除:成功后本地剔除该行(dismiss/snooze/execute 成功都让它离开列表)。
  const removeItem = React.useCallback((id: any) => {
    setItems((prev) => prev.filter((it: any) => it.id !== id));
  }, []);

  // 本地置态:approve 后不移除,改 status=approved 让「执行」第二步钮露出(两步分明)。
  const setItemStatus = React.useCallback((id: any, status: string) => {
    setItems((prev) => prev.map((it: any) => (it.id === id ? { ...it, status } : it)));
  }, []);

  // 灌水可见化:操作成功后乐观自增当天计数(下次 load 会被后端真值校正)。
  const bumpToday = React.useCallback((key: keyof ActionInboxTodaySummary) => {
    setTodaySummary((prev) => {
      const base = prev ?? { today_executed_count: 0, today_approved_count: 0 };
      return { ...base, [key]: (base[key] || 0) + 1 };
    });
  }, []);

  // R7:回读最近执行台账(只读 vkpi_action_execution_ledger)。
  const loadLedger = React.useCallback(() => {
    if (!apiToken) return;
    setLedgerLoading(true);
    listRecentExecutionLedger(apiToken, 20)
      .then((res) => setLedgerItems(Array.isArray(res?.items) ? res.items : []))
      .catch(() => setLedgerItems([]))
      .finally(() => setLedgerLoading(false));
  }, [apiToken]);

  const toggleLedger = React.useCallback(() => {
    setLedgerOpen((open) => {
      const next = !open;
      if (next) loadLedger();
      return next;
    });
  }, [loadLedger]);

  const runAction = React.useCallback(
    (it: any, kind: any) => {
      if (!apiToken || !it || busy[it.id]) return;
      // 二次确认搬到 execute(真跑这步):approve 只是人审标记,不在此弹窗。
      if (kind === "execute") {
        const warn = it.uses_llm
          ? "执行该动作可能调用 LLM 并产生成本。确认执行?"
          : "执行该动作会写入业务数据。确认执行?";
        // eslint-disable-next-line no-alert
        if (typeof window !== "undefined" && !window.confirm(warn)) return;
      }
      setBusy((b) => ({ ...b, [it.id]: kind }));
      setActionError("");
      const call =
        kind === "approve"
          ? approveAction(apiToken, it.id)
          : kind === "dismiss"
            ? dismissAction(apiToken, it.id)
            : kind === "snooze"
              ? snoozeAction(apiToken, it.id, 1440)
              : executeAction(apiToken, it.id);
      call
        .then((res: any) => {
          if (kind === "execute") {
            const outcome = res?.outcome;
            if (outcome === "success") {
              const cat = res?.category || it.category || "";
              const label = (CATEGORY_META as any)[cat]?.label || cat;
              const lid = res?.ledger_id ? ` · 台账#${res.ledger_id}` : "";
              // 路线0 验收回执:执行后立即反馈 几个 job / 写几行 / 是否花钱。
              const ck: any = res?.detail?.result_checklist;
              const ckStr = ck
                ? ` · job ${ck.jobs_created ?? 0}/写 ${ck.rows_written ?? 0}行${ck.cost_spent_cents ? `/花 $${(Number(ck.cost_spent_cents) / 100).toFixed(2)}` : "/未花钱"}`
                : "";
              setOkNote(`已执行 · ${label}${ckStr}${lid}`);
              setActionError("");
              removeItem(it.id);
              bumpToday("today_executed_count"); // 当天「已执行」+1(即时增长)
              if (ledgerOpen) loadLedger();
              return;
            }
            // failed → 终态,移除并报因;skipped → 保留(仍 approved,可重试)并报因。
            const why = EXEC_REASON[res?.reason] || res?.reason || "执行未生效";
            setActionError(why);
            if (outcome === "failed") removeItem(it.id);
            return;
          }
          if (res && res.ok === false) {
            setActionError(res.reason || "操作未生效");
            return;
          }
          if (kind === "approve") {
            // 人审通过 → 本地转 approved,露出「执行」按钮(第二步)。
            setItemStatus(it.id, "approved");
            bumpToday("today_approved_count"); // 当天「已批准」+1(即时增长)
          } else {
            // snooze / dismiss → 离开列表。
            removeItem(it.id);
          }
        })
        .catch((err: any) => setActionError(err?.message || "操作失败"))
        .finally(() =>
          setBusy((b) => {
            const next = { ...b };
            delete next[it.id];
            return next;
          }),
        );
    },
    [apiToken, busy, removeItem, setItemStatus, bumpToday, ledgerOpen, loadLedger],
  );

  // GTM-Loop:gtm_bet 无自动执行器 —— approved 后人在线下做完业务动作,在此「标记已执行」。
  // POST /api/admin/vkpi/actions/{id}/mark-done(端点并行车道在建,前端按此合约先行);
  // 成功后刷新列表让后端真值校正(status → executed,离开 suggested/approved 视图)。
  const markBetDone = React.useCallback(
    (it: any) => {
      if (!apiToken || !it || busy[it.id]) return;
      setBusy((b) => ({ ...b, [it.id]: "markdone" }));
      setActionError("");
      apiFetch<{ ok?: boolean; reason?: string }>(
        `/api/admin/vkpi/actions/${it.id}/mark-done`,
        { method: "POST", cache: "no-store" },
        apiToken,
      )
        .then((res) => {
          if (res && res.ok === false) {
            setActionError(res.reason || "标记未生效");
            return;
          }
          setOkNote(`已标记执行 · ${it.title || "GTM押注"}`);
          removeItem(it.id);
          bumpToday("today_executed_count"); // 当天「已执行」+1(即时增长)
          load(); // 成功后刷新,后端真值校正
        })
        .catch((err: any) => setActionError(err?.message || "标记失败"))
        .finally(() =>
          setBusy((b) => {
            const next = { ...b };
            delete next[it.id];
            return next;
          }),
        );
    },
    [apiToken, busy, removeItem, bumpToday, load],
  );

  const headerRight = loading
    ? e(Loader2, { size: 12, className: "animate-spin text-muted" })
    : e(
        "button",
        {
          onClick: load,
          title: "刷新建议",
          className: "text-muted transition-colors hover:text-ink",
        },
        e(RefreshCw, { size: 12 }),
      );

  // 状态感知操作区:approved → 执行(第二步);suggested 可执行类 → 通过/稍后/忽略;
  // suggested 提醒类(requires_approval=false,无执行器)→ 只「知道了/稍后」(诚实,不给执行钮)。
  const renderActions = (it: any) => {
    const b = busy[it.id];
    const spin = (k: string) =>
      b === k ? e(Loader2, { size: 9, className: "animate-spin" }) : null;

    // 闭环波 L4:裁决任务不可跳过 —— 不给忽略/稍后,只给「去裁决」(展开内嵌裁决一屏)。
    if (it.category === "gtm_verdict") {
      const open = Boolean(verdictOpen[it.id]);
      return e(
        "div",
        { className: "mt-1.5 flex items-center gap-1.5" },
        e(
          "button",
          {
            key: "verdict",
            type: "button",
            onClick: () =>
              setVerdictOpen((prev: Record<string, boolean>) => ({ ...prev, [it.id]: !prev[it.id] })),
            title: "展开裁决一屏:当时预期 vs 三窗实际,一键 decision + lesson",
            className:
              "flex items-center gap-1 rounded border border-fuchsia-500/30 bg-fuchsia-500/10 px-1.5 py-0.5 text-[9px] text-fuchsia-300 transition-colors hover:bg-fuchsia-500/20",
          },
          e(Gavel, { size: 9 }),
          open ? "收起裁决" : "去裁决",
        ),
        e("span", { key: "tag", className: "text-[8px] text-muted" }, "不可跳过 · 人工裁决"),
      );
    }

    // GTM-Loop:gtm_bet 已审批 → 「标记已执行」(人做完业务动作后回来点;无自动执行体,
    // 不给通用「执行」钮免得打到无执行器的 execute)。其余 status(suggested 等)走通用流。
    if (it.category === "gtm_bet" && it.status === "approved") {
      return e(
        "div",
        { className: "mt-1.5 flex items-center gap-1.5" },
        e(
          "button",
          {
            key: "markdone",
            type: "button",
            disabled: Boolean(b),
            onClick: () => markBetDone(it),
            title: "业务动作已在线下完成 → 标记执行(复盘日三窗对答案)",
            className:
              "flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40",
          },
          spin("markdone") || e(Check, { size: 9 }),
          "标记已执行",
        ),
        e("span", { key: "tag", className: "text-[8px] text-emerald-300/70" }, "已审批 · 人工执行"),
      );
    }

    const snoozeBtn = e(
      "button",
      {
        key: "snooze",
        type: "button",
        disabled: Boolean(b),
        onClick: () => runAction(it, "snooze"),
        title: "稍后再说(默认 24h)",
        className:
          "flex items-center gap-1 rounded border border-line bg-panel px-1.5 py-0.5 text-[9px] text-ink-2 transition-colors hover:bg-accent-soft disabled:opacity-40",
      },
      spin("snooze") || e(Clock, { size: 9 }),
      "稍后",
    );
    const dismissBtn = (label: string) =>
      e(
        "button",
        {
          key: "dismiss",
          type: "button",
          disabled: Boolean(b),
          onClick: () => runAction(it, "dismiss"),
          title: "忽略此建议",
          className:
            "flex items-center gap-1 rounded border border-line bg-panel px-1.5 py-0.5 text-[9px] text-muted transition-colors hover:bg-crit-soft hover:text-crit disabled:opacity-40",
        },
        spin("dismiss") || e(X, { size: 9 }),
        label,
      );

    // 已审批 → 第二步「执行」(approved 态后端不放行 dismiss,故只暴露执行)。
    if (it.status === "approved") {
      return e(
        "div",
        { className: "mt-1.5 flex items-center gap-1.5" },
        e(
          "button",
          {
            key: "execute",
            type: "button",
            disabled: Boolean(b),
            onClick: () => runAction(it, "execute"),
            title: "执行该动作(写入业务数据 / 可能调用 LLM,进 ledger)",
            className:
              "flex items-center gap-1 rounded border border-sky-500/30 bg-sky-500/10 px-1.5 py-0.5 text-[9px] text-sky-300 transition-colors hover:bg-sky-500/20 disabled:opacity-40",
          },
          spin("execute") || e(Play, { size: 9 }),
          "执行",
        ),
        e("span", { key: "tag", className: "text-[8px] text-emerald-300/70" }, "已审批"),
      );
    }

    if (it.status !== "suggested") return null;

    // 提醒类:无执行器,只能确认/延后(不误导给执行钮)。
    if (!it.requires_approval) {
      return e(
        "div",
        { className: "mt-1.5 flex items-center gap-1" },
        dismissBtn("知道了"),
        snoozeBtn,
      );
    }

    // 可执行类:通过(人审第一步)/ 稍后 / 忽略。
    return e(
      "div",
      { className: "mt-1.5 flex items-center gap-1" },
      e(
        "button",
        {
          key: "approve",
          type: "button",
          disabled: Boolean(b),
          onClick: () => runAction(it, "approve"),
          title: "审批通过(随后点「执行」才真跑)",
          className:
            "flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40",
        },
        spin("approve") || e(Check, { size: 9 }),
        "通过",
      ),
      snoozeBtn,
      dismissBtn("忽略"),
    );
  };

  let body;
  if (loading && items.length === 0) {
    body = e(
      "div",
      { className: "flex-1 flex items-center justify-center py-8" },
      e(Loader2, { size: 18, className: "animate-spin text-muted" }),
    );
  } else if (error || !available) {
    body = e(
      "div",
      { className: "flex-1 flex items-center justify-center" },
      e(
        "div",
        {
          className:
            "rounded-md border border-dashed border-red-500/20 px-3 py-8 text-center text-[11px] text-red-300/70",
        },
        available ? `建议源异常 · ${error}` : "建议系统待启用(迁移 141)",
      ),
    );
  } else if (items.length === 0) {
    body = e(
      "div",
      {
        className:
          "rounded-md border border-dashed border-line px-3 py-8 text-center text-[11px] text-muted",
      },
      "暂无待办建议 · 一切已跟进",
    );
  } else {
    body = e(
      "div",
      { className: "flex-1 space-y-1.5 overflow-hidden" },
      items.slice(0, expanded ? items.length : COLLAPSED_COUNT).map((it: any) => {
        const meta = (CATEGORY_META as any)[it.category] || { label: it.category, Icon: ListChecks, color: "text-slate-300" };
        const pr = (PRIORITY_META as any)[it.priority] || PRIORITY_META.low;
        const risk = (RISK_META as any)[it.risk_level] || null;
        const ck = (it.result_checklist_json && typeof it.result_checklist_json === "object") ? it.result_checklist_json : null;
        const costCents = Number(it.estimated_cost_cents || 0) || 0;
        return e(
          InboxRowShell,
          {
            key: it.id,
            status: String(it.status || ""),
            className: "rounded-md border border-line bg-panel px-2.5 py-2",
          },
          e(
            "div",
            { className: "flex items-center justify-between gap-2" },
            e(
              "div",
              { className: "flex items-center gap-1.5 min-w-0" },
              e(meta.Icon, { size: 12, className: `${meta.color} shrink-0` }),
              e("span", { className: "truncate text-[11px] font-medium text-ink" }, it.title),
            ),
            e(
              "div",
              { className: "flex shrink-0 items-center gap-1" },
              risk ? e("span", { className: `rounded border px-1 py-0.5 text-[8px] ${risk.cls}` }, risk.label) : null,
              e("span", { className: `rounded border px-1 py-0.5 text-[8px] ${pr.cls}` }, pr.label),
            ),
          ),
          e("div", { className: "mt-0.5 line-clamp-2 text-[10px] text-muted" }, it.detail),
          // 路线0 决策四件套:收益 + 成本(为什么=reason 已在 detail 上方语义里;此处补收益/成本)
          (it.expected_gain || costCents > 0)
            ? e(
                "div",
                { className: "mt-1 flex items-center gap-2 text-[9px]" },
                it.expected_gain ? e("span", { className: "truncate text-emerald-300/80" }, `收益:${it.expected_gain}`) : null,
                costCents > 0 ? e("span", { className: "shrink-0 text-amber-300/70" }, `约 $${(costCents / 100).toFixed(2)}`) : null,
              )
            : null,
          // S1 验证计划:批准前可见"会这样验证成功"(suggested/approved 态显示首条)
          (it.status !== "executed" && Array.isArray(it.verification_plan_json) && it.verification_plan_json.length)
            ? e(
                "div",
                { className: "mt-1 flex items-start gap-1 text-[9px] text-sky-300/70" },
                e("span", { className: "shrink-0" }, "验证:"),
                e("span", { className: "line-clamp-1" }, it.verification_plan_json.join(" · ")),
              )
            : null,
          // 路线0+S1 验收回执:已执行项展示标准化结果(job/写几行/是否花钱)+ 真 before/after delta
          (it.status === "executed" && ck)
            ? e(
                "div",
                { className: "mt-1 rounded border border-emerald-500/15 bg-emerald-500/[0.05] px-1.5 py-1 text-[9px] text-emerald-300/85" },
                `验收:${ck.outcome || "success"} · job ${ck.jobs_created ?? 0} · 写 ${ck.rows_written ?? 0} 行` +
                  (ck.cost_spent_cents ? ` · 花 $${(Number(ck.cost_spent_cents) / 100).toFixed(2)}` : " · 未花钱") +
                  (ck.failed_reason ? ` · ${ck.failed_reason}` : "") +
                  (Array.isArray(ck.before_after) && ck.before_after.length
                    ? " · " + ck.before_after.map((ba: any) => `${ba.table} ${ba.before}→${ba.after}(${ba.delta >= 0 ? "+" : ""}${ba.delta})`).join(" ")
                    : ""),
              )
            : null,
          // 红线提示:需人审 / 烧 LLM 的动作明示(approve 时会二次确认)
          (it.requires_approval || it.uses_llm)
            ? e(
                "div",
                { className: "mt-1 flex items-center gap-1.5 text-[8px] text-muted" },
                it.uses_llm && e("span", { className: "text-violet-300/70" }, "LLM"),
                it.requires_approval && e("span", null, "· 需人工审批"),
              )
            : null,
          // 操作区:suggested → 通过/稍后/忽略(提醒类只「知道了/稍后」);approved → 执行。
          renderActions(it),
          // 闭环波 L4:gtm_verdict 展开 → 内嵌裁决一屏(裁决成功后移出列表 + 绿条回执)。
          it.category === "gtm_verdict" && verdictOpen[it.id]
            ? e(VerdictPanel, {
                key: `verdict-${it.id}`,
                apiToken,
                verdictId: gtmVerdictIdsOf(it).id,
                idType: gtmVerdictIdsOf(it).idType,
                fallback: it.payload_json && typeof it.payload_json === "object" ? it.payload_json : null,
                onDecided: (decision: string) => {
                  setOkNote(`已裁决 · ${it.title} → ${decision}`);
                  removeItem(it.id);
                },
              })
            : null,
        );
      }),
      items.length > COLLAPSED_COUNT
        ? e(
            "button",
            {
              type: "button",
              onClick: () => setExpanded((v: boolean) => !v),
              className:
                "mt-1 w-full rounded-md border border-line py-1 text-[10px] text-muted transition hover:bg-accent-soft hover:text-ink-2",
            },
            expanded ? "收起" : `查看全部 ${items.length} →`,
          )
        : null,
    );
  }

  const scopeLabel = scope === "all" ? "公司全局" : scope === "own" ? "仅我负责的" : "";

  // 灌水可见化:面板顶部一行小统计「今日已执行 N 条 / 已批准 M 条」(闭环在转的即时反馈)。
  const todayStrip =
    todaySummary && available
      ? e(
          "div",
          {
            className:
              "mb-2 flex items-center gap-3 rounded-md border border-emerald-500/15 bg-emerald-500/[0.04] px-2.5 py-1.5 text-[10px]",
          },
          e(
            "span",
            { className: "flex items-center gap-1 text-emerald-300/90" },
            e(Check, { size: 11, className: "shrink-0" }),
            "今日已执行 ",
            e("span", { className: "font-semibold text-emerald-200" }, String(todaySummary.today_executed_count)),
            " 条",
          ),
          e("span", { className: "text-muted" }, "/"),
          e(
            "span",
            { className: "flex items-center gap-1 text-sky-300/90" },
            "已批准 ",
            e("span", { className: "font-semibold text-sky-200" }, String(todaySummary.today_approved_count)),
            " 条",
          ),
        )
      : null;

  return e(
    m.div,
    {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
      transition: { delay: 0.2 },
      className:
        "rounded-xl border border-line bg-panel p-4 backdrop-blur-xl flex flex-col",
    },
    // header
    e(
      "div",
      { className: "mb-3 flex items-center justify-between" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Sparkles, { size: 14, className: "text-emerald-300" }),
        e("h3", { className: "text-sm font-semibold text-ink" }, heading),
        items.length > 0 &&
          e(
            "span",
            { className: "rounded-full bg-card px-1.5 py-0.5 text-[9px] text-ink-2" },
            String(items.length),
          ),
      ),
      headerRight,
    ),
    // 灌水可见化:今日已执行 / 已批准计数(执行后即时增长)
    todayStrip,
    body,
    // R7 执行成功绿色提示
    okNote
      ? e(
          "div",
          {
            className:
              "mt-2 rounded border border-emerald-500/25 bg-emerald-500/[0.07] px-2 py-1 text-[9px] text-emerald-300/90",
          },
          okNote,
        )
      : null,
    // 操作错误提示(approve/dismiss/snooze 失败或被后端拒绝)
    actionError
      ? e(
          "div",
          {
            className:
              "mt-2 rounded border border-red-500/20 bg-red-500/[0.06] px-2 py-1 text-[9px] text-red-300/80",
          },
          `操作未生效 · ${actionError}`,
        )
      : null,
    // R7 执行台账(折叠;回读 vkpi_action_execution_ledger,before/after 验收)
    ledgerOpen
      ? e(
          "div",
          { className: "mt-2 max-h-44 space-y-1 overflow-y-auto rounded border border-line bg-black/20 p-1.5" },
          ledgerLoading
            ? e("div", { className: "py-2 text-center text-[9px] text-muted" }, "加载执行台账…")
            : ledgerItems.length === 0
              ? e("div", { className: "py-2 text-center text-[9px] text-muted" }, "暂无执行记录")
              : ledgerItems.map((l: any) =>
                  e(
                    "div",
                    {
                      key: `lg-${l.id}`,
                      className: "flex items-center justify-between gap-2 rounded bg-panel px-1.5 py-1",
                    },
                    e(
                      "span",
                      { className: "min-w-0 flex-1 truncate text-[9px] text-ink-2" },
                      `${(CATEGORY_META as any)[l.category]?.label || l.category || "run"} · ${l.mode}`,
                    ),
                    e(
                      "span",
                      {
                        className: `shrink-0 rounded px-1 text-[8px] ${
                          l.outcome === "success"
                            ? "text-emerald-300"
                            : l.outcome === "failed"
                              ? "text-red-300"
                              : "text-muted"
                        }`,
                      },
                      l.outcome,
                    ),
                  ),
                ),
        )
      : null,
    // footer:数据源 + scope + 人审后执行 诚实标注 + 执行台账开关
    e(
      "div",
      { className: "mt-3 flex items-center justify-between border-t border-line pt-2" },
      e("div", { className: "text-[9px] text-muted" }, scopeLabel ? `范围:${scopeLabel}` : "Auto-Ops · 建议"),
      e(
        "button",
        {
          type: "button",
          onClick: toggleLedger,
          className: "text-[9px] text-muted transition-colors hover:text-ink-2",
          title: "回读执行台账(谁/何时/结果/成本)",
        },
        ledgerOpen ? "收起台账" : "执行台账",
      ),
    ),
  );
}
