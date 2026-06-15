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
  RefreshCw,
  Share2,
  Sparkles,
  UserPlus,
  X,
} from "lucide-react";
import {
  approveAction,
  dismissAction,
  listActionInbox,
  snoozeAction,
} from "../../../../services/vkpi/actionInbox-api";

const e = React.createElement;

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

export function ActionInboxPanel({ apiToken = "", limit = 6 }) {
  const [items, setItems] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [scope, setScope] = React.useState("");
  const [available, setAvailable] = React.useState(true);
  // per-id 操作态:'approve' | 'dismiss' | 'snooze' | undefined。
  const [busy, setBusy] = React.useState<Record<string, any>>({});
  const [actionError, setActionError] = React.useState("");
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
    listActionInbox(apiToken, { limit })
      .then((res) => {
        setItems(Array.isArray(res?.items) ? res.items : []);
        setScope(res?.scope || "");
        setAvailable(res?.available !== false);
      })
      .catch((err) => {
        setError(err?.message || "加载失败");
      })
      .finally(() => setLoading(false));
  }, [apiToken, limit]);

  React.useEffect(() => {
    load();
  }, [load]);

  // 乐观移除:成功后本地剔除该行(approve/dismiss/snooze 都让它离开 suggested 列表)。
  const removeItem = React.useCallback((id: any) => {
    setItems((prev) => prev.filter((it: any) => it.id !== id));
  }, []);

  const runAction = React.useCallback(
    (it: any, kind: any) => {
      if (!apiToken || !it || busy[it.id]) return;
      // approve 二次确认:会触发后端执行链 / 烧 LLM 的明示。
      if (kind === "approve" && (it.requires_approval || it.uses_llm)) {
        const warn = it.uses_llm
          ? "审批通过后将执行该动作(可能调用 LLM、产生成本)。确认?"
          : "审批通过后将执行该动作(会写入业务数据)。确认?";
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
            : snoozeAction(apiToken, it.id, 1440);
      call
        .then((res) => {
          if (res && res.ok === false) {
            setActionError(res.reason || "操作未生效");
            return;
          }
          removeItem(it.id);
        })
        .catch((err) => setActionError(err?.message || "操作失败"))
        .finally(() =>
          setBusy((b) => {
            const next = { ...b };
            delete next[it.id];
            return next;
          }),
        );
    },
    [apiToken, busy, removeItem],
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
              "span",
              { className: `shrink-0 rounded border px-1 py-0.5 text-[8px] ${pr.cls}` },
              pr.label,
            ),
          ),
          e("div", { className: "mt-0.5 line-clamp-2 text-[10px] text-slate-400" }, it.detail),
          // 红线提示:需人审 / 烧 LLM 的动作明示(approve 时会二次确认)
          (it.requires_approval || it.uses_llm)
            ? e(
                "div",
                { className: "mt-1 flex items-center gap-1.5 text-[8px] text-slate-500" },
                it.uses_llm && e("span", { className: "text-violet-300/70" }, "LLM"),
                it.requires_approval && e("span", null, "· 需人工审批"),
              )
            : null,
          // 操作区:仅 suggested 状态可操作;approve / snooze / dismiss
          it.status === "suggested"
            ? e(
                "div",
                { className: "mt-1.5 flex items-center gap-1" },
                e(
                  "button",
                  {
                    type: "button",
                    disabled: Boolean(busy[it.id]),
                    onClick: () => runAction(it, "approve"),
                    title: "审批通过(随后可执行)",
                    className:
                      "flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40",
                  },
                  busy[it.id] === "approve"
                    ? e(Loader2, { size: 9, className: "animate-spin" })
                    : e(Check, { size: 9 }),
                  "通过",
                ),
                e(
                  "button",
                  {
                    type: "button",
                    disabled: Boolean(busy[it.id]),
                    onClick: () => runAction(it, "snooze"),
                    title: "稍后再说(默认 24h)",
                    className:
                      "flex items-center gap-1 rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[9px] text-slate-300 transition-colors hover:bg-white/[0.08] disabled:opacity-40",
                  },
                  busy[it.id] === "snooze"
                    ? e(Loader2, { size: 9, className: "animate-spin" })
                    : e(Clock, { size: 9 }),
                  "稍后",
                ),
                e(
                  "button",
                  {
                    type: "button",
                    disabled: Boolean(busy[it.id]),
                    onClick: () => runAction(it, "dismiss"),
                    title: "忽略此建议",
                    className:
                      "flex items-center gap-1 rounded border border-white/10 bg-white/[0.02] px-1.5 py-0.5 text-[9px] text-slate-400 transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:opacity-40",
                  },
                  busy[it.id] === "dismiss"
                    ? e(Loader2, { size: 9, className: "animate-spin" })
                    : e(X, { size: 9 }),
                  "忽略",
                ),
              )
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
        "h-full rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl flex flex-col",
    },
    // header
    e(
      "div",
      { className: "mb-3 flex items-center justify-between" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Sparkles, { size: 14, className: "text-emerald-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "今日建议"),
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
    // footer:数据源 + scope + 人审后执行 诚实标注
    e(
      "div",
      { className: "mt-3 flex items-center justify-between border-t border-white/[0.06] pt-2" },
      e("div", { className: "text-[9px] text-slate-500" }, scopeLabel ? `范围:${scopeLabel}` : "Auto-Ops · 建议"),
      e("div", { className: "text-[9px] text-slate-500" }, "人审后执行 · 进 ledger"),
    ),
  );
}
