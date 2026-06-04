// @ts-nocheck

import React from "react";
import { Brain, Clock3, FileText, Search, Zap } from "lucide-react";

const e = React.createElement;

const MOCK_ACTIVE_TASKS = [
  { id: "mock-search-1", stage: "search", kind: "URL深抓", target: "juliatrotti", progress: 34 },
  { id: "mock-search-2", stage: "search", kind: "全网发现", target: "35mm 评测", progress: 58 },
  { id: "mock-think-1", stage: "thinking", kind: "video深析", target: "eliinfante", progress: 48 },
  { id: "mock-sum-1", stage: "summarizing", kind: "沉淀分析", target: "deep result", progress: 72 },
];

const MOCK_QUEUED_TASKS = [
  { id: "mock-q-1", kind: "video深析", target: "directedbysean" },
  { id: "mock-q-2", kind: "URL深抓", target: "derrelhoshing" },
  { id: "mock-q-3", kind: "全网发现", target: "低光人像" },
  { id: "mock-q-4", kind: "video深析", target: "editorskeys" },
  { id: "mock-q-5", kind: "URL深抓", target: "jaysoundo" },
  { id: "mock-q-6", kind: "全网发现", target: "85mm 对比" },
  { id: "mock-q-7", kind: "video深析", target: "miklosmayerphoto" },
];

const LANES = [
  {
    key: "search",
    title: "搜索中",
    Icon: Search,
    color: "#5DCAA5",
    titleColor: "#9FE1CB",
    border: "rgba(93,202,165,0.30)",
    bg: "rgba(29,158,117,0.08)",
    showBar: false,
  },
  {
    key: "thinking",
    title: "思考中",
    Icon: Brain,
    color: "#7F77DD",
    titleColor: "#CECBF6",
    border: "rgba(127,119,221,0.35)",
    bg: "rgba(83,74,183,0.10)",
    showBar: true,
  },
  {
    key: "summarizing",
    title: "总结中",
    Icon: FileText,
    color: "#FAC775",
    titleColor: "#FAC775",
    border: "rgba(239,159,39,0.35)",
    bg: "rgba(186,117,23,0.10)",
    showBar: true,
  },
];

function taskLabel(task) {
  return `${task.kind || "任务"} · ${task.target || "未命名"}`;
}

function TaskRow({ task, color, showBar }) {
  const progress = Math.max(6, Math.min(100, Number(task.progress || 0)));
  return e("div", { className: "min-w-0" },
    e("div", { className: "flex items-center gap-1.5 min-w-0" },
      e("span", {
        className: `h-[5px] w-[5px] shrink-0 rounded-full ${showBar ? "animate-pulse" : ""}`,
        style: { background: color }
      }),
      e("span", { className: "truncate text-[11px] leading-4 text-white/70" }, taskLabel(task))
    ),
    showBar && e("div", { className: "ml-[11px] mt-1 h-[3px] overflow-hidden rounded-full bg-white/[0.08]" },
      e("div", {
        className: "h-full rounded-full transition-[width] duration-500",
        style: { width: `${progress}%`, background: color }
      })
    )
  );
}

function TaskLane({ lane, tasks }) {
  return e("section", {
    className: "rounded-lg border px-[9px] py-2",
    style: { borderColor: lane.border, background: lane.bg }
  },
    e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(lane.Icon, { size: 13, style: { color: lane.color } }),
        e("span", {
          className: "truncate text-[11px] font-medium uppercase tracking-[0.04em]",
          style: { color: lane.titleColor }
        }, lane.title)
      ),
      e("span", { className: "text-[11px] font-medium tabular-nums", style: { color: lane.color } }, tasks.length)
    ),
    e("div", { className: "flex flex-col gap-1.5" },
      tasks.length
        ? tasks.map((task) => e(TaskRow, { key: task.id, task, color: lane.color, showBar: lane.showBar }))
        : e("span", { className: "text-[11px] leading-4 text-white/30" }, "—")
    )
  );
}

export function TaskProgressBoard() {
  const lanes = LANES.map((lane) => ({
    ...lane,
    tasks: MOCK_ACTIVE_TASKS.filter((task) => task.stage === lane.key),
  }));
  const visibleQueue = MOCK_QUEUED_TASKS.slice(0, 2);
  const remainingQueue = Math.max(0, MOCK_QUEUED_TASKS.length - visibleQueue.length);

  return e("div", {
    className: "w-full rounded-xl border border-white/10 bg-[#0d1117] px-3 py-3 shadow-[0_18px_44px_rgba(0,0,0,0.28)]"
  },
    e("div", { className: "mb-3.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(Zap, { size: 15, className: "shrink-0 text-[#5DCAA5]" }),
        e("span", { className: "truncate text-[13px] font-medium text-white/90" }, "任务进度")
      ),
      e("span", { className: "shrink-0 text-[11px] text-white/40 tabular-nums" }, `${MOCK_ACTIVE_TASKS.length} 活跃`)
    ),
    e("div", { className: "flex flex-col gap-2.5" },
      lanes.map((lane) => e(TaskLane, { key: lane.key, lane, tasks: lane.tasks }))
    ),
    e("div", { className: "mt-3 border-t border-white/[0.08] pt-2.5" },
      e("div", { className: "flex items-center justify-between gap-2" },
        e("div", { className: "flex min-w-0 items-center gap-1.5" },
          e(Clock3, { size: 12, className: "text-white/35" }),
          e("span", { className: "truncate text-[11px] text-white/45" }, "排队等待")
        ),
        e("span", { className: "text-[11px] font-medium text-white/60 tabular-nums" }, MOCK_QUEUED_TASKS.length)
      ),
      e("div", { className: "mt-1.5 flex flex-col gap-1" },
        visibleQueue.map((task, index) => e("div", { key: task.id, className: "flex min-w-0 items-center gap-1.5" },
          e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `#${index + 1}`),
          e("span", { className: "truncate text-[11px] text-white/60" }, taskLabel(task))
        )),
        remainingQueue > 0 && e("div", { className: "flex min-w-0 items-center gap-1.5 opacity-45" },
          e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `+${remainingQueue}`),
          e("span", { className: "truncate text-[11px] text-white/60" }, "更多任务…")
        )
      )
    )
  );
}
