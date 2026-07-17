import React from "react";
import { Activity, Clock3, Database, Search, Sparkles } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { fetchProgressCenter, type ProgressCenterData, type ProgressTask } from "../../../../services/vkpi/progressCenter-api";

const LANES = [
  { key: "search", label: "抓取", Icon: Search, color: "var(--ds-accent)" },
  { key: "thinking", label: "分析", Icon: Sparkles, color: "var(--ds-accent-2)" },
  { key: "summarizing", label: "落库", Icon: Database, color: "var(--ds-good)" },
  { key: "queued", label: "排队", Icon: Clock3, color: "var(--ds-warn)" },
] as const;

interface CostOverview {
  today?: {
    apify_usd?: number;
    apify_calls?: number;
    llm_usd?: number;
    llm_calls?: number;
    total_usd?: number;
  };
  budgets?: {
    monthly_total?: {
      configured?: boolean;
      cap_usd?: number;
      current_spend?: number;
    };
  };
}

function taskTitle(task: ProgressTask | undefined) {
  if (!task) return "空闲 · 等待入队";
  const kind = String(task.kind || task.job_type || "").trim();
  const label = String(task.label || "").trim();
  if (kind && label && kind !== label) return `${kind} · ${label}`;
  return label || kind || `任务 ${task.id}`;
}

function laneProgress(tasks: ProgressTask[]) {
  // Number(null) === 0.  进度端点用 null 表示“已超历史均时/无法可靠估算”，
  // 不能把它误画成 0% 后再由最小宽度伪装成 6%。
  const values = tasks
    .filter((task) => task.progress_pct !== null && task.progress_pct !== undefined)
    .map((task) => Number(task.progress_pct))
    .filter(Number.isFinite);
  if (values.length === 0) return tasks.length > 0 ? null : 0;
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length);
}

export function DashboardTaskQueueCard({ apiToken = "", compact = false }: { apiToken?: string; compact?: boolean }) {
  const [data, setData] = React.useState<ProgressCenterData | null>(null);
  const [cost, setCost] = React.useState<CostOverview | null>(null);

  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    let timer = 0;
    const load = () => {
      void fetchProgressCenter({ token: apiToken })
        .then((payload) => { if (!cancelled) setData(payload); })
        .catch(() => { if (!cancelled) setData(null); });
    };
    load();
    timer = window.setInterval(load, 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<CostOverview>(`/api/admin/vkpi/ops/cost-ledger?tz_offset_minutes=${-new Date().getTimezoneOffset()}`, { timeoutMs: 12000 }, apiToken)
      .then((payload) => { if (!cancelled) setCost(payload); })
      .catch(() => { if (!cancelled) setCost(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  const running = data?.running || [];
  const queued = data?.queued || [];
  const byStage: Record<string, ProgressTask[]> = {
    search: running.filter((task) => task.stage === "search"),
    thinking: running.filter((task) => task.stage === "thinking"),
    summarizing: running.filter((task) => task.stage === "summarizing"),
    queued,
  };
  const runningTotal = Number(data?.counts.running ?? running.length);
  const queueTotal = Number(data?.counts.queued ?? queued.length);
  const currentTask = running[0];
  const workerOffline = data?.diagnostics?.worker_online === false;
  const queueBlocked = workerOffline && queueTotal > 0;
  const statusLabel = runningTotal > 0
    ? `${runningTotal} 处理中${workerOffline ? " · Worker 心跳断开" : ""}`
    : queueBlocked
      ? `Worker 离线 · ${queueTotal} 等待`
      : queueTotal > 0
        ? `${queueTotal} 排队`
        : "空闲";
  const cardTitle = workerOffline
    ? runningTotal > 0
      ? "检测到跑中记录，但 Worker 心跳已过期；请按当前任务与最近更新时间核验是否仍在执行"
      : queueBlocked
        ? "Worker 当前离线，排队任务尚未开始"
        : "Worker 心跳已过期"
    : "真实任务阶段与成本账本";
  const today = cost?.today;
  const todayCalls = Number(today?.apify_calls || 0) + Number(today?.llm_calls || 0);
  const todayUsd = Number(today?.total_usd);
  const budget = cost?.budgets?.monthly_total;
  const cap = Number(budget?.cap_usd);
  const spend = Number(budget?.current_spend);
  const budgetPct = budget?.configured && cap > 0 && Number.isFinite(spend)
    ? Math.min(999, Math.round((spend / cap) * 100))
    : null;

  return (
    <article
      className={`vkpi-dashboard-task-queue ${compact ? "is-compact" : ""}`}
      title={cardTitle}
    >
      <header>
        <div>
          <Activity size={13} />
          <strong>{compact ? "LLM 队列" : "LLM 任务队列"}</strong>
        </div>
        <span className={runningTotal > 0 ? "is-running" : queueBlocked ? "is-blocked" : ""}>{statusLabel}</span>
      </header>
      {compact && currentTask ? (
        <div className="vkpi-dashboard-task-queue__current" title={`${taskTitle(currentTask)} · #${currentTask.id}`}>
          <span>当前</span>
          <strong>{taskTitle(currentTask)}</strong>
          <small>#{currentTask.id}</small>
        </div>
      ) : null}
      <div className="vkpi-dashboard-task-queue__lanes">
        {LANES.map((lane) => {
          const tasks = byStage[lane.key] || [];
          const isQueuedLane = lane.key === "queued";
          const isWaiting = isQueuedLane && tasks.length > 0;
          const isBlocked = isQueuedLane && queueBlocked;
          const progress = isQueuedLane ? 0 : laneProgress(tasks);
          const topTask = tasks[0];
          const progressText = tasks.length === 0
            ? "--"
            : isWaiting
              ? String(tasks.length)
              : topTask?.progress_overdue
                ? "超均时"
                : progress === null
                  ? String(tasks.length)
                  : `${progress}%`;
          return (
            <div
              className={`vkpi-dashboard-task-queue__lane ${tasks.length > 0 ? "is-active" : "is-idle"} ${isBlocked ? "is-blocked" : ""}`}
              key={lane.key}
              title={topTask ? `${taskTitle(topTask)} · #${topTask.id}${topTask.progress_overdue ? " · 已超历史均时" : ""}` : undefined}
            >
              <lane.Icon size={compact ? 11 : 12} />
              <span className="vkpi-dashboard-task-queue__name">{lane.label}</span>
              {!compact ? <span className="vkpi-dashboard-task-queue__task">{isBlocked ? "等待 Worker 上线" : taskTitle(topTask)}</span> : null}
              <span className="vkpi-dashboard-task-queue__bar">
                <i
                  className={isWaiting ? "is-waiting" : progress === null ? "is-indeterminate" : ""}
                  style={{
                    width: isWaiting ? "10%" : progress === null ? "42%" : `${Math.max(tasks.length > 0 ? 6 : 0, Number(progress || 0))}%`,
                    background: tasks.length > 0 ? lane.color : undefined,
                  }}
                />
              </span>
              <small>{progressText}</small>
            </div>
          );
        })}
      </div>
      <footer>
        {today && Number.isFinite(todayUsd)
          ? <span>今日 {todayCalls.toLocaleString()} 次 · ${todayUsd.toFixed(2)}{compact && budgetPct !== null ? ` · 预算 ${budgetPct}%` : ""}</span>
          : <span>成本账本不可见</span>}
        {!compact ? <span>{budgetPct === null ? "预算未配置" : `月预算已用 ${budgetPct}%`}</span> : null}
      </footer>
    </article>
  );
}
