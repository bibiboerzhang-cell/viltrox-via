// W1→W2 Action Inbox — Dashboard 右侧"今日建议"。自取数据(apiToken)。
// W2 起可操作:approve/dismiss/snooze;execute 仍走后端 validators 双闸(approved +
//   touches_v6_fit=False + budget + entity 存在)。approve→execute 两步,前端只触发,不绕审批。
// 后端已按 scope 过滤(管理层全局 / 成员仅自己 owner 的)。

import React from "react";
import { motion } from "framer-motion";
import {
  Brain,
  CalendarClock,
  Check,
  ClipboardList,
  Clock,
  Eye,
  FileCheck,
  ListChecks,
  Loader2,
  PackageOpen,
  Play,
  RefreshCw,
  Share2,
  Sparkles,
  UserPlus,
  X,
} from "lucide-react";
import {
  approveAction,
  dismissAction,
  executeAction,
  listActionInbox,
  listRecentExecutionLedger,
  snoozeAction,
} from "../../../../services/vkpi/actionInbox-api";

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
};

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
        setItems(merged);
        setScope(sug?.scope || appr?.scope || "");
        setAvailable(sug?.available !== false);
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
    [apiToken, busy, removeItem, setItemStatus, ledgerOpen, loadLedger],
  );

  const headerRight = loading
    ? e(Loader2, { size: 12, className: "animate-spin text-slate-400" })
    : e(
        "button",
        {
          onClick: load,
          title: "刷新建议",
          className: "text-slate-400 transition-colors hover:text-white",
        },
        e(RefreshCw, { size: 12 }),
      );

  // 状态感知操作区:approved → 执行(第二步);suggested 可执行类 → 通过/稍后/忽略;
  // suggested 提醒类(requires_approval=false,无执行器)→ 只「知道了/稍后」(诚实,不给执行钮)。
  const renderActions = (it: any) => {
    const b = busy[it.id];
    const spin = (k: string) =>
      b === k ? e(Loader2, { size: 9, className: "animate-spin" }) : null;

    const snoozeBtn = e(
      "button",
      {
        key: "snooze",
        type: "button",
        disabled: Boolean(b),
        onClick: () => runAction(it, "snooze"),
        title: "稍后再说(默认 24h)",
        className:
          "flex items-center gap-1 rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[9px] text-slate-300 transition-colors hover:bg-white/[0.08] disabled:opacity-40",
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
            "flex items-center gap-1 rounded border border-white/10 bg-white/[0.02] px-1.5 py-0.5 text-[9px] text-slate-400 transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40",
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
      e(Loader2, { size: 18, className: "animate-spin text-slate-500" }),
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
          "rounded-md border border-dashed border-white/[0.08] px-3 py-8 text-center text-[11px] text-slate-500",
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
          "div",
          {
            key: it.id,
            className: "rounded-md border border-white/[0.05] bg-white/[0.015] px-2.5 py-2",
          },
          e(
            "div",
            { className: "flex items-center justify-between gap-2" },
            e(
              "div",
              { className: "flex items-center gap-1.5 min-w-0" },
              e(meta.Icon, { size: 12, className: `${meta.color} shrink-0` }),
              e("span", { className: "truncate text-[11px] font-medium text-white" }, it.title),
            ),
            e(
              "div",
              { className: "flex shrink-0 items-center gap-1" },
              risk ? e("span", { className: `rounded border px-1 py-0.5 text-[8px] ${risk.cls}` }, risk.label) : null,
              e("span", { className: `rounded border px-1 py-0.5 text-[8px] ${pr.cls}` }, pr.label),
            ),
          ),
          e("div", { className: "mt-0.5 line-clamp-2 text-[10px] text-slate-400" }, it.detail),
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
                { className: "mt-1 flex items-center gap-1.5 text-[8px] text-slate-500" },
                it.uses_llm && e("span", { className: "text-violet-300/70" }, "LLM"),
                it.requires_approval && e("span", null, "· 需人工审批"),
              )
            : null,
          // 操作区:suggested → 通过/稍后/忽略(提醒类只「知道了/稍后」);approved → 执行。
          renderActions(it),
        );
      }),
      items.length > COLLAPSED_COUNT
        ? e(
            "button",
            {
              type: "button",
              onClick: () => setExpanded((v: boolean) => !v),
              className:
                "mt-1 w-full rounded-md border border-white/[0.06] py-1 text-[10px] text-slate-400 transition hover:bg-white/[0.04] hover:text-slate-200",
            },
            expanded ? "收起" : `查看全部 ${items.length} →`,
          )
        : null,
    );
  }

  const scopeLabel = scope === "all" ? "公司全局" : scope === "own" ? "仅我负责的" : "";

  return e(
    motion.div,
    {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
      transition: { delay: 0.2 },
      className:
        "rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl flex flex-col",
    },
    // header
    e(
      "div",
      { className: "mb-3 flex items-center justify-between" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Sparkles, { size: 14, className: "text-emerald-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, heading),
        items.length > 0 &&
          e(
            "span",
            { className: "rounded-full bg-white/[0.06] px-1.5 py-0.5 text-[9px] text-slate-300" },
            String(items.length),
          ),
      ),
      headerRight,
    ),
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
          { className: "mt-2 max-h-44 space-y-1 overflow-y-auto rounded border border-white/[0.06] bg-black/20 p-1.5" },
          ledgerLoading
            ? e("div", { className: "py-2 text-center text-[9px] text-slate-500" }, "加载执行台账…")
            : ledgerItems.length === 0
              ? e("div", { className: "py-2 text-center text-[9px] text-slate-500" }, "暂无执行记录")
              : ledgerItems.map((l: any) =>
                  e(
                    "div",
                    {
                      key: `lg-${l.id}`,
                      className: "flex items-center justify-between gap-2 rounded bg-white/[0.02] px-1.5 py-1",
                    },
                    e(
                      "span",
                      { className: "min-w-0 flex-1 truncate text-[9px] text-slate-300" },
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
                              : "text-slate-400"
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
      { className: "mt-3 flex items-center justify-between border-t border-white/[0.06] pt-2" },
      e("div", { className: "text-[9px] text-slate-500" }, scopeLabel ? `范围:${scopeLabel}` : "Auto-Ops · 建议"),
      e(
        "button",
        {
          type: "button",
          onClick: toggleLedger,
          className: "text-[9px] text-slate-500 transition-colors hover:text-slate-300",
          title: "回读执行台账(谁/何时/结果/成本)",
        },
        ledgerOpen ? "收起台账" : "执行台账",
      ),
    ),
  );
}
