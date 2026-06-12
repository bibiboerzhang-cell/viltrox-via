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

function taskEtaText(task) {
  const eta = Number(task?.eta_seconds);
  const ahead = Number(task?.ahead_count);
  if (!Number.isFinite(eta) && !Number.isFinite(ahead)) return "";
  const parts = [];
  if (Number.isFinite(ahead)) parts.push(ahead > 0 ? `前方 ${ahead} 个` : "下一个就是它");
  if (Number.isFinite(eta) && eta > 0) {
    const mins = Math.max(1, Math.round(eta / 60));
    parts.push(`约 ${mins} 分钟`);
  }
  return parts.join(" · ");
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

function taskMyKolPoolId(task) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  const poolId = Number(target.target_id);
  return String(target.target_type || "") === "kol_profile" && Number.isFinite(poolId) && poolId > 0 ? poolId : undefined;
}

// 2026-06-12 波5 R5:target_type=project 的任务(合同润色等同族)可回到项目详情。
// 注意只认 target_type === 'project'(target_id 即 project_id);
// 'project_contract' 的 target_id 是合同 id,无法可靠映射项目,保持禁用(诚实降级)。
function taskProjectId(task) {
  const target = task?.target && typeof task.target === "object" ? task.target : {};
  const projectId = Number(target.target_id);
  return String(target.target_type || "") === "project" && Number.isFinite(projectId) && projectId > 0 ? projectId : undefined;
}

// 从哪发起回哪去(2026-06-12 裁令):账号分析(kol_profile)点开回 MY KOL 并定位该收藏行,
// project 任务回项目详情,其余仍走 KOL Pool 查找会话。
function openTaskOrigin(task) {
  const poolId = taskMyKolPoolId(task);
  if (poolId && typeof window !== "undefined") {
    window.localStorage.setItem("vkpi:pending-mykol-pool-id", String(poolId));
    window.dispatchEvent(new CustomEvent("vkpi:open-mykol-kol", { detail: { kolPoolId: poolId, task } }));
    return;
  }
  const projectId = taskProjectId(task);
  if (projectId && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("vkpi:open-project-task", { detail: { projectId: String(projectId), task } }));
    return;
  }
  openSearchSessionFromTask(task);
}

function taskCanOpen(task) {
  return Boolean(taskMyKolPoolId(task) || taskProjectId(task) || taskSearchSessionId(task));
}

function taskInitiatorChip(task) {
  const initiator = task?.initiator_user_id || task?.initiator_staff_id;
  return initiator ? `用户 ${initiator}` : "";
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
  const canOpen = taskCanOpen(task);
  const retryText = taskRetryText(task);
  return e("button", {
    type: "button",
    onClick: canOpen ? () => openTaskOrigin(task) : undefined,
    disabled: !canOpen,
    className: `block w-full min-w-0 text-left ${canOpen ? "cursor-pointer rounded-md transition-colors hover:bg-white/[0.045]" : "cursor-default"} disabled:cursor-default`,
    title: canOpen ? (taskMyKolPoolId(task) ? "回到 MY KOL 查看该 KOL" : "打开这次查找记录") : "",
  },
    e("div", { className: "flex items-center gap-1.5 min-w-0" },
      e("span", {
        className: `h-[5px] w-[5px] shrink-0 rounded-full ${showBar ? "animate-pulse" : ""}`,
        style: { background: color }
      }),
      e("span", { className: "truncate text-[11px] leading-4 text-white/70" }, taskLabel(task)),
      taskInitiatorChip(task) && e("span", { className: "shrink-0 text-[9px] text-white/30" }, taskInitiatorChip(task))
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
  // 排队区按真实执行序排(后端 queue_position 按 claim 顺序;无该字段的按 created_at 升序垫后)——
  // 此前直接用合并列表(updated_at 倒序)切片,屏上 #1 可能是最后才跑的(2026-06-12 主管裁令)。
  const queuedTasks = useMemo(() => {
    const queued = activeTasks.filter((task) => task?.status === "queued");
    return queued.slice().sort((a, b) => {
      const pa = Number(a?.queue_position); const pb = Number(b?.queue_position);
      if (Number.isFinite(pa) && Number.isFinite(pb)) return pa - pb;
      if (Number.isFinite(pa)) return -1;
      if (Number.isFinite(pb)) return 1;
      return String(a?.created_at || "").localeCompare(String(b?.created_at || ""));
    });
  }, [activeTasks]);
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
  const speedLight = payload?.speed_light || {};
  const lanes = LANES.map((lane) => ({
    ...lane,
    tasks: laneTasks[lane.key] || [],
  }));
  // 主管裁令(2026-06-12):排队要能看清"前面还有谁"——放宽到 5 条(其余以 +N 计数)。
  const visibleQueue = queuedTasks.slice(0, 5);
  const remainingQueue = Math.max(0, queueTotal - visibleQueue.length);
  // 账号分析等队列任务 ~8 秒即完成,若按 session 过滤会"一闪而过"再无痕迹——
  // 即使 payload 暂缺 search_session_id 也保底留在「最近完成」(无 session 时不可点开)。
  const visibleRecent = recentTasks.filter((task) => taskCanOpen(task) || task?.kind === "账号分析").slice(0, 2);
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
            onClick: taskCanOpen(task) ? () => openTaskOrigin(task) : undefined,
            disabled: !taskCanOpen(task),
            className: "flex min-w-0 items-start gap-1.5 text-left disabled:cursor-default"
          },
            e("span", { className: "w-[18px] shrink-0 text-[10px] text-white/40 tabular-nums" }, `#${Number.isFinite(Number(task?.queue_position)) ? Number(task.queue_position) : index + 1}`),
            e("span", { className: "min-w-0 flex-1" },
              e("span", { className: "block truncate text-[11px] text-white/60" },
                taskLabel(task),
                taskInitiatorChip(task) && e("span", { className: "ml-1 text-[9px] text-white/30" }, taskInitiatorChip(task))
              ),
              taskEtaText(task) && e("span", { className: "block truncate text-[10px] text-emerald-300/70" }, taskEtaText(task)),
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
    // 2026-06-12 波5 R7:无 target 的条目不再假装可点 — 按钮禁用、「可打开」标签按实际情况显示
    visibleRecent.length ? e("div", { className: "mt-3 border-t border-white/[0.08] pt-2.5" },
      e("div", { className: "mb-1.5 flex items-center justify-between gap-2" },
        e("span", { className: "text-[11px] text-white/45" }, "最近完成"),
        visibleRecent.some((task) => taskCanOpen(task)) && e("span", { className: "text-[10px] text-[#5DCAA5]/80" }, "可打开")
      ),
      e("div", { className: "flex flex-col gap-1" },
        visibleRecent.map((task) => {
          const canOpen = taskCanOpen(task);
          return e("button", {
            key: `recent-${task.id}`,
            type: "button",
            onClick: canOpen ? () => openTaskOrigin(task) : undefined,
            disabled: !canOpen,
            title: canOpen ? "" : "无可跳转的所属对象",
            className: `flex min-w-0 items-center justify-between gap-2 rounded-md border border-emerald-300/10 bg-emerald-400/[0.045] px-2 py-1 text-left ${canOpen ? "hover:bg-emerald-400/[0.08]" : "cursor-default opacity-60"}`
          },
            e("span", { className: "truncate text-[10.5px] text-white/65" }, taskLabel(task)),
            canOpen && e("span", { className: "shrink-0 text-[10px] text-[#5DCAA5]" }, "打开")
          );
        })
      )
    ) : null
  );
}
