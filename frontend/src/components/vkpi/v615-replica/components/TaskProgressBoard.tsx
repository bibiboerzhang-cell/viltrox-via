// @ts-nocheck

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Clock3, FileText, Search, Zap } from "lucide-react";
import { getTaskQueueCompact } from "../../../../services/vkpi/tasks-api";

const e = React.createElement;
const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";

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

function taskRetryText(task) {
  const category = String(task?.error_category || "").trim();
  const retryAt = String(task?.next_retry_at || "").trim();
  if (!category && !retryAt) return "";
  let timeText = "";
  if (retryAt) {
    const parsed = new Date(retryAt);
    if (Number.isFinite(parsed.getTime())) {
      timeText = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
  }
  const categoryLabel = category === "provider_pressure" ? "provider压力" : category;
  if (categoryLabel && timeText) return `${categoryLabel} · 退避至 ${timeText}`;
  if (timeText) return `退避至 ${timeText}`;
  return categoryLabel;
}

function taskSearchSessionId(task) {
  const session = task?.search_session && typeof task.search_session === "object" ? task.search_session : {};
  const raw = session.session_id || session.id || task?.target?.target_id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 && String(task?.target?.target_type || "").includes("search_session")
    ? parsed
    : Number.isFinite(Number(session.session_id || session.id)) && Number(session.session_id || session.id) > 0
      ? Number(session.session_id || session.id)
      : undefined;
}

function openSearchSessionFromTask(task) {
  const sessionId = taskSearchSessionId(task);
  if (!sessionId || typeof window === "undefined") return;
  window.localStorage.setItem(PENDING_SEARCH_SESSION_KEY, String(sessionId));
  window.dispatchEvent(new CustomEvent("vkpi:open-kol-search-session", { detail: { sessionId, task } }));
}

function lightColor(light) {
  const tone = String(light?.tone || "").toLowerCase();
  if (tone === "red") return "#fb7185";
  if (tone === "amber") return "#FAC775";
  return "#5DCAA5";
}

function TaskRow({ task, color, showBar }) {
  const rawProgress = task.progress_pct ?? task.progress;
  const hasProgress = Number.isFinite(Number(rawProgress));
  const progress = Math.max(6, Math.min(100, Number(rawProgress || 0)));
  const canOpen = Boolean(taskSearchSessionId(task));
  const retryText = taskRetryText(task);
  return e("button", {
    type: "button",
    onClick: canOpen ? () => openSearchSessionFromTask(task) : undefined,
    disabled: !canOpen,
    className: `block w-full min-w-0 text-left ${canOpen ? "cursor-pointer rounded-md transition-colors hover:bg-white/[0.045]" : "cursor-default"} disabled:cursor-default`,
    title: canOpen ? "打开这次查找记录" : "",
  },
    e("div", { className: "flex items-center gap-1.5 min-w-0" },
      e("span", {
        className: `h-[5px] w-[5px] shrink-0 rounded-full ${showBar ? "animate-pulse" : ""}`,
        style: { background: color }
      }),
      e("span", { className: "truncate text-[11px] leading-4 text-white/70" }, taskLabel(task))
    ),
    retryText && e("div", { className: "ml-[11px] truncate text-[10px] leading-4 text-white/35" }, retryText),
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
  // 客户端进行中的 LLM 活动(如合同提取):同步请求在途期间由前端派发 vkpi:llm-activity 事件,
  // 请求结束即移除。区别于后端任务队列,纯本地、仅在真在跑时出现。
  const [clientActivities, setClientActivities] = useState([]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const handler = (event) => {
      const detail = (event && event.detail) || {};
      if (!detail.id) return;
      setClientActivities((current) => {
        const rest = current.filter((item) => item.id !== detail.id);
        return detail.active
          ? [...rest, { id: detail.id, label: detail.label || "AI 任务", kind: detail.kind || "AI 任务" }]
          : rest;
      });
    };
    window.addEventListener("vkpi:llm-activity", handler);
    return () => window.removeEventListener("vkpi:llm-activity", handler);
  }, []);

  const refreshQueue = useCallback(async () => {
    if (!apiToken || (typeof document !== "undefined" && document.visibilityState === "hidden")) return;
    setLoading(true);
    try {
      const response = await getTaskQueueCompact(apiToken, { limit: 30, recentMinutes: 5 });
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
  const recentTasks = useMemo(() => asArray(payload?.recent), [payload]);
  const queuedTasks = useMemo(() => activeTasks.filter((task) => task?.status === "queued"), [activeTasks]);
  const laneTasks = useMemo(() => {
    const nonQueued = activeTasks.filter((task) => task?.status !== "queued");
    return {
      search: nonQueued.filter((task) => task?.stage === "search"),
      thinking: nonQueued.filter((task) => task?.stage === "thinking"),
      summarizing: nonQueued.filter((task) => task?.stage === "summarizing"),
    };
  }, [activeTasks]);
  const clientThinkingTasks = useMemo(
    () => clientActivities.map((activity) => ({
      id: `client-${activity.id}`,
      kind: activity.kind || "AI 任务",
      target: { label: activity.label },
      stage: "thinking",
      status: "running",
      progress_pct: null,
    })),
    [clientActivities],
  );
  const activeTotal = (Number(payload?.counts?.active_total ?? activeTasks.length) || 0) + clientThinkingTasks.length;
  const queueTotal = Number(payload?.counts?.queued ?? queuedTasks.length) || 0;
  const speedLight = payload?.speed_light || {};
  const lanes = LANES.map((lane) => ({
    ...lane,
    tasks: lane.key === "thinking"
      ? [...clientThinkingTasks, ...(laneTasks[lane.key] || [])]
      : (laneTasks[lane.key] || []),
  }));
  const visibleQueue = queuedTasks.slice(0, 2);
  const remainingQueue = Math.max(0, queueTotal - visibleQueue.length);
  const visibleRecent = recentTasks.filter((task) => taskSearchSessionId(task)).slice(0, 2);
  const emptyActive = activeTotal === 0 && !loading && !error;

  return e("div", {
    className: "w-full rounded-xl border border-white/10 bg-[#0d1117] px-3 py-3 shadow-[0_18px_44px_rgba(0,0,0,0.28)]"
  },
    e("div", { className: "mb-3.5 flex items-center justify-between gap-2" },
      e("div", { className: "flex min-w-0 items-center gap-1.5" },
        e(Zap, { size: 15, className: "shrink-0 text-[#5DCAA5]" }),
        e("span", { className: "truncate text-[13px] font-medium text-white/90" }, "任务进度")
      ),
      e("div", { className: "flex shrink-0 items-center gap-1.5" },
        e("span", { className: "text-[11px] text-white/40 tabular-nums" },
          loading && !payload ? "连接中" : `${activeTotal} 活跃`
        ),
        e("span", {
          className: "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[9.5px] font-medium tabular-nums",
          style: {
            borderColor: `${lightColor(speedLight)}55`,
            color: lightColor(speedLight),
            background: `${lightColor(speedLight)}14`,
          },
          title: speedLight?.policy || "调度速度灯",
        },
          e("span", { className: "h-1.5 w-1.5 rounded-full", style: { background: lightColor(speedLight) } }),
          speedLight?.level || "L1"
        )
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
          ? visibleQueue.map((task, index) => e("button", {
            key: task.id,
            type: "button",
            onClick: taskSearchSessionId(task) ? () => openSearchSessionFromTask(task) : undefined,
            disabled: !taskSearchSessionId(task),
            className: "flex min-w-0 items-start gap-1.5 text-left disabled:cursor-default"
          },
            e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `#${index + 1}`),
            e("span", { className: "min-w-0 flex-1" },
              e("span", { className: "block truncate text-[11px] text-white/60" }, taskLabel(task)),
              taskRetryText(task) && e("span", { className: "block truncate text-[10px] text-white/30" }, taskRetryText(task))
            )
          ))
          : e("span", { className: "text-[11px] text-white/25" }, "—"),
        remainingQueue > 0 && e("div", { className: "flex min-w-0 items-center gap-1.5 opacity-45" },
          e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `+${remainingQueue}`),
          e("span", { className: "truncate text-[11px] text-white/60" }, "更多任务…")
        )
      )
    ),
    visibleRecent.length ? e("div", { className: "mt-3 border-t border-white/[0.08] pt-2.5" },
      e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
        e("span", { className: "text-[11px] text-white/45" }, "最近完成"),
        e("span", { className: "text-[10px] text-[#5DCAA5]/80" }, "可打开")
      ),
      e("div", { className: "flex flex-col gap-1" },
        visibleRecent.map((task) => e("button", {
          key: `recent-${task.id}`,
          type: "button",
          onClick: () => openSearchSessionFromTask(task),
          className: "flex min-w-0 items-center justify-between gap-2 rounded-md border border-emerald-300/10 bg-emerald-400/[0.045] px-2 py-1 text-left hover:bg-emerald-400/[0.08]"
        },
          e("span", { className: "truncate text-[10.5px] text-white/65" }, taskLabel(task)),
          e("span", { className: "shrink-0 text-[10px] text-[#5DCAA5]" }, "打开")
        ))
      )
    ) : null
  );
}
