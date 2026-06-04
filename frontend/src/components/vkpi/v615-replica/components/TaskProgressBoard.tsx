// @ts-nocheck

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Clock3, FileText, Search, Zap } from "lucide-react";
import { getTaskQueue } from "../../../../services/vkpi/tasks-api";

const e = React.createElement;

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

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function taskTargetText(task) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  return String(
    target.label ||
    target.handle ||
    target.display_name ||
    target.target_id ||
    target.source_url ||
    task?.target_label ||
    ""
  ).trim();
}

function taskLabel(task) {
  return `${task.kind || "任务"} · ${taskTargetText(task) || "未命名"}`;
}

function TaskRow({ task, color, showBar }) {
  const rawProgress = task.progress_pct ?? task.progress;
  const hasProgress = Number.isFinite(Number(rawProgress));
  const progress = Math.max(6, Math.min(100, Number(rawProgress || 0)));
  return e("div", { className: "min-w-0" },
    e("div", { className: "flex items-center gap-1.5 min-w-0" },
      e("span", {
        className: `h-[5px] w-[5px] shrink-0 rounded-full ${showBar ? "animate-pulse" : ""}`,
        style: { background: color }
      }),
      e("span", { className: "truncate text-[11px] leading-4 text-white/70" }, taskLabel(task))
    ),
    showBar && (
      hasProgress
        ? e("div", { className: "ml-[11px] mt-1 h-[3px] overflow-hidden rounded-full bg-white/[0.08]" },
          e("div", {
            className: "h-full rounded-full transition-[width] duration-500",
            style: { width: `${progress}%`, background: color }
          })
        )
        : e("div", { className: "ml-[11px] mt-1 h-[3px] overflow-hidden rounded-full bg-white/[0.08]" },
          e("div", {
            className: "h-full w-1/2 rounded-full opacity-60",
            style: { background: `linear-gradient(90deg, transparent, ${color}, transparent)` }
          })
        )
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

export function TaskProgressBoard({ apiToken = "" }) {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refreshQueue = useCallback(async () => {
    if (!apiToken || (typeof document !== "undefined" && document.visibilityState === "hidden")) return;
    setLoading(true);
    try {
      const response = await getTaskQueue(apiToken, { limit: 50, recentMinutes: 10, includeLlmCalls: true });
      setPayload(response);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "任务进度连接中");
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  useEffect(() => {
    if (!apiToken) {
      setPayload(null);
      setError("缺少 API token");
      return undefined;
    }
    let intervalId;
    const startPolling = () => {
      if (intervalId || (typeof document !== "undefined" && document.visibilityState === "hidden")) return;
      void refreshQueue();
      intervalId = window.setInterval(() => {
        void refreshQueue();
      }, 2500);
    };
    const stopPolling = () => {
      if (intervalId) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") {
        stopPolling();
      } else {
        startPolling();
      }
    };
    startPolling();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [apiToken, refreshQueue]);

  const activeTasks = useMemo(() => asArray(payload?.active), [payload]);
  const queuedTasks = useMemo(() => activeTasks.filter((task) => task?.status === "queued"), [activeTasks]);
  const laneTasks = useMemo(() => {
    const nonQueued = activeTasks.filter((task) => task?.status !== "queued");
    return {
      search: nonQueued.filter((task) => task?.stage === "search"),
      thinking: nonQueued.filter((task) => task?.stage === "thinking"),
      summarizing: nonQueued.filter((task) => task?.stage === "summarizing"),
    };
  }, [activeTasks]);
  const activeTotal = Number(payload?.counts?.active_total ?? activeTasks.length) || 0;
  const queueTotal = Number(payload?.counts?.queued ?? queuedTasks.length) || 0;
  const lanes = LANES.map((lane) => ({
    ...lane,
    tasks: laneTasks[lane.key] || [],
  }));
  const visibleQueue = queuedTasks.slice(0, 2);
  const remainingQueue = Math.max(0, queueTotal - visibleQueue.length);
  const emptyActive = activeTotal === 0 && !loading && !error;

  return e("div", {
    className: "w-full rounded-xl border border-white/10 bg-[#0d1117] px-3 py-3 shadow-[0_18px_44px_rgba(0,0,0,0.28)]"
  },
    e("div", { className: "mb-3.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(Zap, { size: 15, className: "shrink-0 text-[#5DCAA5]" }),
        e("span", { className: "truncate text-[13px] font-medium text-white/90" }, "任务进度")
      ),
      e("span", { className: "shrink-0 text-[11px] text-white/40 tabular-nums" },
        loading && !payload ? "连接中" : `${activeTotal} 活跃`
      )
    ),
    error && e("div", { className: "mb-2 rounded border border-amber-300/15 bg-amber-300/[0.05] px-2 py-1 text-[10px] leading-4 text-amber-100/70" },
      payload ? "任务进度连接中 · 保留上次状态" : error
    ),
    emptyActive && e("div", { className: "mb-2 rounded border border-white/[0.06] bg-white/[0.025] px-2 py-1.5 text-center text-[10.5px] text-white/35" },
      "暂无运行中任务"
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
        e("span", { className: "text-[11px] font-medium text-white/60 tabular-nums" }, queueTotal)
      ),
      e("div", { className: "mt-1.5 flex flex-col gap-1" },
        visibleQueue.length
          ? visibleQueue.map((task, index) => e("div", { key: task.id, className: "flex min-w-0 items-center gap-1.5" },
            e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `#${index + 1}`),
            e("span", { className: "truncate text-[11px] text-white/60" }, taskLabel(task))
          ))
          : e("span", { className: "text-[11px] text-white/25" }, "—"),
        remainingQueue > 0 && e("div", { className: "flex min-w-0 items-center gap-1.5 opacity-45" },
          e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `+${remainingQueue}`),
          e("span", { className: "truncate text-[11px] text-white/60" }, "更多任务…")
        )
      )
    )
  );
}
