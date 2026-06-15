// @ts-nocheck
// W2 项目履约时间线(只读)— 建→选→寄→签→观察→发布→复盘。
// 数据源:GET /api/admin/vkpi/projects/{id}/timeline(后端 policy.require_project_read 收口)。
// 后端返回 stages[] 已带 {key,label_zh,index,reached,at,counts};本组件按 index 有序渲染。
// 降级:无 apiToken 或拉取失败时,渲染本地 canonical 阶段骨架(全部 reached=false),不臆造数据。
// 红线:纯只读,不触发任何写;与 viltrox_fit_score 无关。

import React from "react";
import { motion } from "framer-motion";
import {
  AlertCircle,
  Check,
  Loader2,
  Route,
} from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

// 与后端 stage_canonical.CANONICAL_STAGES / _LABELS_ZH 一一对齐(降级骨架用)。
const CANONICAL_STAGES = [
  { key: "discovered", label_zh: "发现" },
  { key: "contacted", label_zh: "已联系" },
  { key: "agreed", label_zh: "已同意" },
  { key: "shipped", label_zh: "已寄样" },
  { key: "delivered", label_zh: "已签收" },
  { key: "observation_pending", label_zh: "观察中" },
  { key: "content_detected", label_zh: "疑似发布" },
  { key: "content_published", label_zh: "已发布" },
  { key: "retrospective_ready", label_zh: "待复盘" },
  { key: "retrospective_done", label_zh: "复盘完成" },
  { key: "closed", label_zh: "已关闭" },
];

function fmtTs(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${d.getFullYear()}-${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return String(ts);
  }
}

// counts 是 {label:count} 自由对象;过滤掉空/0,渲染为小徽标。
function countsBadges(counts) {
  if (!counts || typeof counts !== "object") return [];
  return Object.entries(counts)
    .filter(([, v]) => typeof v === "number" && v > 0)
    .map(([k, v]) => ({ k, v }));
}

export function ProjectTimeline({ apiToken = "", projectId = "" }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const pid = String(projectId || "").trim();

  const load = React.useCallback(() => {
    if (!apiToken || !pid) {
      // 无 token / 无 projectId:走降级骨架,不报错。
      setData(null);
      setError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    apiFetch(
      `/api/admin/vkpi/projects/${encodeURIComponent(pid)}/timeline`,
      { cache: "no-store" },
      apiToken,
    )
      .then((res) => setData(res || null))
      .catch((err) => setError(err?.message || "加载失败"))
      .finally(() => setLoading(false));
  }, [apiToken, pid]);

  React.useEffect(() => {
    load();
  }, [load]);

  // 阶段列表:优先用后端 stages;否则降级到本地 canonical 骨架(reached 全 false)。
  const stages =
    data && Array.isArray(data.stages) && data.stages.length > 0
      ? [...data.stages].sort((a, b) => (a.index ?? 0) - (b.index ?? 0))
      : CANONICAL_STAGES.map((s, i) => ({
          key: s.key,
          label_zh: s.label_zh,
          index: i,
          reached: false,
          at: null,
          counts: {},
        }));

  const currentStage = data?.current_stage || "";
  const degraded = !apiToken || !pid;

  let banner;
  if (loading) {
    banner = e(
      "div",
      { className: "flex items-center gap-2 text-[10.5px] text-slate-300" },
      e(Loader2, { size: 12, className: "animate-spin text-slate-400" }),
      "加载履约时间线…",
    );
  } else if (error) {
    banner = e(
      "div",
      { className: "flex items-center gap-2 text-[10.5px] text-amber-300/80" },
      e(AlertCircle, { size: 12, className: "shrink-0" }),
      `时间线源异常 · ${error}`,
    );
  } else if (degraded) {
    banner = e(
      "div",
      { className: "flex items-center gap-2 text-[10.5px] text-slate-400" },
      e(Route, { size: 12, className: "text-slate-400 shrink-0" }),
      "履约阶段骨架(未接 token / 项目 ID,仅展示阶段顺序)",
    );
  } else {
    banner = e(
      "div",
      { className: "flex items-center gap-2 text-[10.5px] text-slate-300" },
      e(Route, { size: 12, className: "text-emerald-300 shrink-0" }),
      `履约时间线 · 当前阶段:${currentStage || "—"}`,
    );
  }

  // 履约摘要徽标(寄样/观察窗/内容/复盘),仅在有后端数据时展示。
  const summaryChips = [];
  if (data) {
    const ship = data.shipments || {};
    if (typeof ship.delivered_count === "number") {
      summaryChips.push({
        label: `已签收 ${ship.delivered_count}`,
        cls: "text-cyan-300 border-cyan-500/30 bg-cyan-500/10",
      });
    }
    const retro = data.retrospective || {};
    if (retro.active) {
      summaryChips.push({
        label: "复盘进行中",
        cls: "text-sky-300 border-sky-500/30 bg-sky-500/10",
      });
    } else if (retro.last) {
      summaryChips.push({
        label: "已有复盘",
        cls: "text-slate-300 border-white/15 bg-white/[0.04]",
      });
    }
  }

  return e(
    motion.div,
    {
      initial: { opacity: 0, y: 6 },
      animate: { opacity: 1, y: 0 },
      transition: { duration: 0.25 },
      className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3 space-y-3",
      "aria-label": "项目履约时间线",
    },
    // 顶部 banner + 摘要徽标
    e(
      "div",
      { className: "flex items-center justify-between gap-2 flex-wrap" },
      banner,
      summaryChips.length > 0
        ? e(
            "div",
            { className: "flex items-center gap-1.5 flex-wrap" },
            summaryChips.map((c, i) =>
              e(
                "span",
                {
                  key: i,
                  className: `text-[9.5px] px-1.5 py-0.5 rounded border ${c.cls}`,
                },
                c.label,
              ),
            ),
          )
        : null,
    ),
    // 阶段时间线(竖向,reached 高亮)
    e(
      "div",
      { className: "relative space-y-1.5" },
      e("div", { className: "absolute left-[9px] top-2 bottom-2 w-px bg-white/[0.06]" }),
      stages.map((st) => {
        const reached = Boolean(st.reached);
        const isCurrent = currentStage && st.key === currentStage;
        const badges = countsBadges(st.counts);
        return e(
          "div",
          { key: st.key, className: "relative flex items-start gap-2.5 pl-0.5" },
          // 节点圆点
          e(
            "div",
            {
              className:
                "w-[19px] h-[19px] rounded-full flex items-center justify-center shrink-0 z-10 border",
              style: {
                background: reached ? "#10b981" : "#1a1a1f",
                borderColor: reached
                  ? "#10b981"
                  : isCurrent
                    ? "#38bdf8"
                    : "rgba(255,255,255,0.12)",
                boxShadow: "0 0 0 3px #0a0a0d",
              },
            },
            reached
              ? e(Check, { size: 10, className: "text-white" })
              : e("span", {
                  className: "w-1.5 h-1.5 rounded-full",
                  style: { background: isCurrent ? "#38bdf8" : "#475569" },
                }),
          ),
          // 阶段内容
          e(
            "div",
            {
              className: `flex-1 rounded-md border px-2.5 py-1.5 ${
                reached
                  ? "border-emerald-500/20 bg-emerald-500/[0.04]"
                  : isCurrent
                    ? "border-sky-500/25 bg-sky-500/[0.04]"
                    : "border-white/[0.04] bg-white/[0.008]"
              }`,
            },
            e(
              "div",
              { className: "flex items-center justify-between gap-2 flex-wrap" },
              e(
                "div",
                { className: "flex items-center gap-1.5 min-w-0" },
                e(
                  "span",
                  {
                    className: `text-[11px] font-medium ${
                      reached ? "text-white" : isCurrent ? "text-sky-200" : "text-slate-400"
                    }`,
                  },
                  st.label_zh || st.key,
                ),
                isCurrent
                  ? e(
                      "span",
                      {
                        className:
                          "text-[8.5px] px-1 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/25",
                      },
                      "当前",
                    )
                  : null,
              ),
              st.at
                ? e(
                    "span",
                    { className: "text-[9.5px] text-slate-500 tabular-nums font-mono shrink-0" },
                    fmtTs(st.at),
                  )
                : null,
            ),
            badges.length > 0
              ? e(
                  "div",
                  { className: "mt-1 flex items-center gap-1 flex-wrap" },
                  badges.map((b) =>
                    e(
                      "span",
                      {
                        key: b.k,
                        className:
                          "text-[8.5px] px-1 py-0.5 rounded bg-white/[0.04] text-slate-300 border border-white/[0.06]",
                      },
                      `${b.k} ${b.v}`,
                    ),
                  ),
                )
              : null,
          ),
        );
      }),
    ),
  );
}

export default ProjectTimeline;
