// @ts-nocheck
// W1 Action Inbox(只读)— Dashboard 右侧"今日建议"。自取数据(apiToken),不做执行;
// 执行(approve/dismiss/snooze)留 W2。后端已按 scope 过滤(管理层全局 / 成员仅自己 owner 的)。

import React from "react";
import { motion } from "framer-motion";
import {
  Brain,
  CalendarClock,
  ClipboardList,
  Eye,
  FileCheck,
  ListChecks,
  Loader2,
  PackageOpen,
  RefreshCw,
  Sparkles,
  UserPlus,
} from "lucide-react";
import { listActionInbox } from "../../../../services/vkpi/actionInbox-api";

const e = React.createElement;

// 8 类 → 中文标签 + 图标 + 强调色
const CATEGORY_META = {
  kol_profile: { label: "补全资料", Icon: UserPlus, color: "text-amber-300" },
  deep_missing: { label: "深析待跑", Icon: Brain, color: "text-violet-300" },
  failed_retry: { label: "失败重试", Icon: RefreshCw, color: "text-red-300" },
  project_observation: { label: "开观察窗", Icon: Eye, color: "text-cyan-300" },
  content_candidate: { label: "内容确认", Icon: FileCheck, color: "text-emerald-300" },
  retrospective: { label: "项目复盘", Icon: ClipboardList, color: "text-sky-300" },
  event_followup: { label: "活动收尾", Icon: CalendarClock, color: "text-orange-300" },
  inventory_low: { label: "库存预警", Icon: PackageOpen, color: "text-yellow-300" },
};

const PRIORITY_META = {
  high: { label: "高", cls: "bg-red-500/15 text-red-300 border-red-500/25" },
  medium: { label: "中", cls: "bg-amber-500/15 text-amber-300 border-amber-500/25" },
  low: { label: "低", cls: "bg-slate-500/15 text-slate-300 border-slate-500/25" },
};

export function ActionInboxPanel({ apiToken = "", limit = 6 }) {
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [scope, setScope] = React.useState("");
  const [available, setAvailable] = React.useState(true);

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
      items.slice(0, limit).map((it) => {
        const meta = CATEGORY_META[it.category] || { label: it.category, Icon: ListChecks, color: "text-slate-300" };
        const pr = PRIORITY_META[it.priority] || PRIORITY_META.low;
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
          // 红线提示:需人审 / 烧 LLM 的动作明示(不点不动)
          (it.requires_approval || it.uses_llm)
            ? e(
                "div",
                { className: "mt-1 flex items-center gap-1.5 text-[8px] text-slate-500" },
                it.uses_llm && e("span", { className: "text-violet-300/70" }, "LLM"),
                it.requires_approval && e("span", null, "· 需人工审批"),
              )
            : null,
        );
      }),
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
    // footer:数据源 + scope + dry-run 诚实标注
    e(
      "div",
      { className: "mt-3 flex items-center justify-between border-t border-white/[0.06] pt-2" },
      e("div", { className: "text-[9px] text-slate-500" }, scopeLabel ? `范围:${scopeLabel}` : "Auto-Ops · 建议"),
      e("div", { className: "text-[9px] text-slate-500" }, "dry-run · 仅建议不执行"),
    ),
  );
}
