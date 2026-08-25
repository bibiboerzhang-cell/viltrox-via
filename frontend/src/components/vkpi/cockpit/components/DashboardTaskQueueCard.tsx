import React from "react";
import { Activity, Clock3, Database, Search, Sparkles } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import {
  fetchProgressCenter,
  type ProgressCenterData,
  type ProgressRecentDone,
  type ProgressTask,
} from "../../../../services/vkpi/progressCenter-api";
import { humanizeLlmReason } from "../llmReasonCopy";

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
      allowed?: boolean;
      hard_stopped?: boolean;
      cap_usd?: number;
      current_spend?: number;
    };
  };
}

interface LlmTaskReadiness {
  binding?: string;
  configured?: boolean;
  production_ready?: boolean;
  runtime_authorization?: {
    allowed_by_model_readiness?: boolean;
    source?: "signed_evidence" | "operator_ack" | "blocked";
    temporary?: boolean;
  };
}

interface LlmSystemModelsOverview {
  task_model_readiness?: Record<string, LlmTaskReadiness>;
  readiness_audit?: {
    active_scope?: {
      binding_count?: number;
      bindings?: string[];
      production_ready_count?: number;
      runtime_authorized_count?: number;
      runtime_blocked_count?: number;
    };
  };
}

const ACTIVE_POLL_INTERVAL_MS = 10_000;
const IDLE_POLL_INTERVAL_MS = 30_000;
const STATUS_REFRESH_INTERVAL_MS = 60_000;
const SUCCESS_LLM_STATUSES = new Set(["success", "done", "completed", "settled"]);
const BLOCKED_LLM_STATUSES = new Set([
  "blocked", "budget_blocked", "cancelled", "failed", "timeout", "triage",
  "parse_failure", "validation_failure", "all_providers_failed", "fleet_breaker_open",
  "provider_exception", "provider_http_error", "provider_blocked", "transport_error",
]);

function hasActiveTasks(payload: ProgressCenterData): boolean {
  return Number(payload.counts?.active_total || 0) > 0
    || Number(payload.counts?.running || 0) > 0
    || Number(payload.counts?.queued || 0) > 0
    || payload.running.length > 0
    || payload.queued.length > 0;
}

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
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

function nonNegativeCount(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
}

function providerLabel(bindingOrProvider: unknown): string {
  const raw = String(bindingOrProvider || "").trim();
  const provider = raw.split(/[/:]/, 1)[0].toLowerCase();
  if (provider === "google" || provider === "gemini") return "Google";
  if (provider === "openai") return "OpenAI";
  if (provider === "anthropic" || provider === "claude") return "Anthropic";
  return provider ? `${provider.slice(0, 1).toUpperCase()}${provider.slice(1)}` : "未知 Provider";
}

function isRecentLlmSuccess(item: ProgressRecentDone): boolean {
  return !item.has_error && SUCCESS_LLM_STATUSES.has(String(item.status || "").toLowerCase());
}

function isRecentLlmBlocked(item: ProgressRecentDone): boolean {
  const status = String(item.status || "").toLowerCase();
  return item.has_error || BLOCKED_LLM_STATUSES.has(status)
    || (Boolean(item.reason_code) && !SUCCESS_LLM_STATUSES.has(status));
}

function recentSuccessLabel(item: ProgressRecentDone | undefined): string {
  if (!item) return "窗口内无成功记录";
  const binding = String(item.task_binding || "");
  const provider = providerLabel(item.provider || (binding.includes("/") ? binding : ""));
  const model = String(item.model || "").trim();
  return model ? `${provider} · ${model}` : provider;
}

function recentBlockedLabel(item: ProgressRecentDone | undefined): string {
  if (!item) return "最近记录未见阻断";
  const reason = item.failure_reason_human
    || item.reason_code
    || item.error_category
    || item.reason_category
    || item.status;
  return humanizeLlmReason(reason, "最近一条 LLM 任务未完成，请查看设置页。").message;
}

export function DashboardTaskQueueCard({ apiToken = "", compact = false }: { apiToken?: string; compact?: boolean }) {
  const [data, setData] = React.useState<ProgressCenterData | null>(null);
  const [cost, setCost] = React.useState<CostOverview | null>(null);
  const [models, setModels] = React.useState<LlmSystemModelsOverview | null>(null);

  React.useEffect(() => {
    if (!apiToken) return;
    let stopped = false;
    let wasHidden = isDocumentHidden();
    let timer: number | undefined;
    let inFlight: { controller: AbortController; promise: Promise<void> } | null = null;

    const clearScheduledPoll = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    };

    const abortActiveRequest = () => {
      const activeRequest = inFlight;
      inFlight = null;
      activeRequest?.controller.abort();
    };

    const scheduleNextPoll = (delayMs: number) => {
      clearScheduledPoll();
      if (stopped || isDocumentHidden()) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        void pollOnce();
      }, delayMs);
    };

    const pollOnce = (): Promise<void> => {
      if (stopped || isDocumentHidden()) return Promise.resolve();
      if (inFlight) return inFlight.promise;

      clearScheduledPoll();
      const controller = new AbortController();
      const activeRequest = { controller, promise: Promise.resolve() };
      inFlight = activeRequest;
      let nextDelayMs = IDLE_POLL_INTERVAL_MS;

      const request = (async () => {
        try {
          const payload = await fetchProgressCenter({ token: apiToken, signal: controller.signal });
          if (stopped || controller.signal.aborted) return;
          setData(payload);
          nextDelayMs = hasActiveTasks(payload) ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
        } catch {
          if (stopped || controller.signal.aborted) return;
          setData(null);
        } finally {
          // hidden/unmount 会先解除当前请求所有权；被中止的旧请求即使延迟结算，
          // 也不能替新一轮再挂一个计时器。
          if (inFlight === activeRequest) {
            inFlight = null;
            scheduleNextPoll(nextDelayMs);
          }
        }
      })();
      activeRequest.promise = request;
      return request;
    };

    const handleVisibility = () => {
      clearScheduledPoll();
      if (isDocumentHidden()) {
        wasHidden = true;
        abortActiveRequest();
        return;
      }
      if (!wasHidden) return;
      wasHidden = false;
      void pollOnce();
    };

    void pollOnce();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopped = true;
      clearScheduledPoll();
      abortActiveRequest();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    const refresh = () => {
      if (cancelled) return;
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      void apiFetch<CostOverview>(`/api/admin/vkpi/ops/cost-ledger?tz_offset_minutes=${-new Date().getTimezoneOffset()}`, { signal, timeoutMs: 12000 }, apiToken)
        .then((payload) => { if (!cancelled && !signal.aborted) setCost(payload); })
        .catch(() => { if (!cancelled && !signal.aborted) setCost(null); });
      void apiFetch<LlmSystemModelsOverview>("/api/admin/system/models", { signal, timeoutMs: 12000 }, apiToken)
        .then((payload) => { if (!cancelled && !signal.aborted) setModels(payload); })
        .catch(() => { if (!cancelled && !signal.aborted) setModels(null); });
    };
    setCost(null);
    setModels(null);
    refresh();
    const refreshTimer = window.setInterval(() => {
      if (!isDocumentHidden()) refresh();
    }, STATUS_REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(refreshTimer);
      controller?.abort();
    };
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
  const today = cost?.today;
  const todayCalls = Number(today?.apify_calls || 0) + Number(today?.llm_calls || 0);
  const todayUsd = Number(today?.total_usd);
  const budget = cost?.budgets?.monthly_total;
  const cap = Number(budget?.cap_usd);
  const spend = Number(budget?.current_spend);
  const budgetPct = budget?.configured && cap > 0 && Number.isFinite(spend)
    ? Math.min(999, Math.round((spend / cap) * 100))
    : null;
  const budgetGateKnown = Boolean(budget) && (
    typeof budget?.allowed === "boolean"
    || typeof budget?.hard_stopped === "boolean"
    || budget?.configured === false
  );
  const budgetGateAllowed = budgetGateKnown
    && budget?.allowed !== false
    && budget?.hard_stopped !== true;

  const taskReadiness = Object.values(models?.task_model_readiness || {});
  const readinessByBinding = new Map<string, LlmTaskReadiness>();
  taskReadiness.forEach((row) => {
    const binding = String(row.binding || "").trim();
    if (binding) readinessByBinding.set(binding, row);
  });
  const auditScope = models?.readiness_audit?.active_scope;
  const activeBindings = Array.from(new Set(
    (Array.isArray(auditScope?.bindings) && auditScope.bindings.length > 0
      ? auditScope.bindings
      : taskReadiness.map((row) => row.binding)
    ).map((binding) => String(binding || "").trim()).filter(Boolean),
  ));
  const providers = new Map<string, boolean[]>();
  activeBindings.forEach((binding) => {
    const provider = providerLabel(binding);
    const states = providers.get(provider) || [];
    states.push(readinessByBinding.get(binding)?.configured === true);
    providers.set(provider, states);
  });
  const providerTotal = providers.size;
  const providerConfigured = Array.from(providers.values()).filter(
    (states) => states.length > 0 && states.every(Boolean),
  ).length;
  const providerGateKnown = models !== null && activeBindings.length > 0 && providerTotal > 0;
  const providerGateAllowed = providerGateKnown && providerConfigured === providerTotal;

  const bindingTotal = nonNegativeCount(auditScope?.binding_count) ?? activeBindings.length;
  const derivedRuntimeAuthorized = activeBindings.filter((binding) => (
    readinessByBinding.get(binding)?.runtime_authorization?.allowed_by_model_readiness === true
  )).length;
  const derivedSignedReady = activeBindings.filter((binding) => (
    readinessByBinding.get(binding)?.production_ready === true
  )).length;
  const derivedTemporaryAuthorized = activeBindings.filter((binding) => (
    readinessByBinding.get(binding)?.runtime_authorization?.source === "operator_ack"
    || readinessByBinding.get(binding)?.runtime_authorization?.temporary === true
  )).length;
  const runtimeAuthorized = nonNegativeCount(auditScope?.runtime_authorized_count) ?? derivedRuntimeAuthorized;
  const signedReady = nonNegativeCount(auditScope?.production_ready_count) ?? derivedSignedReady;
  const temporaryAuthorized = Math.max(derivedTemporaryAuthorized, runtimeAuthorized - signedReady, 0);
  const modelGateKnown = models !== null && bindingTotal > 0;
  const modelGateAllowed = modelGateKnown && runtimeAuthorized === bindingTotal;
  const workerGateKnown = typeof data?.diagnostics?.worker_online === "boolean";
  const workerGateAllowed = workerGateKnown && !workerOffline;
  const baseConfigurationFullyChecked = workerGateKnown && providerGateKnown && budgetGateKnown && modelGateKnown;
  const llmBaseConfigurationVerified = baseConfigurationFullyChecked
    && workerGateAllowed
    && providerGateAllowed
    && budgetGateAllowed
    && modelGateAllowed;

  const recentLlm = data?.recent_llm || [];
  const latestLlmSuccess = recentLlm.find(isRecentLlmSuccess);
  const latestLlmBlocked = recentLlm.find(isRecentLlmBlocked);
  const idleStatusLabel = llmBaseConfigurationVerified
    ? "基础配置已核 · 具体任务待预检"
    : !baseConfigurationFullyChecked
      ? "当前无任务 · 状态待核"
      : !workerGateAllowed
        ? "当前受限 · Worker 离线"
        : !providerGateAllowed
          ? "当前受限 · Provider"
          : !budgetGateAllowed
            ? "当前受限 · 预算闸"
            : "当前受限 · 模型门";
  const statusLabel = runningTotal > 0
    ? `${runningTotal} 处理中${workerOffline ? " · Worker 心跳断开" : ""}`
    : queueBlocked
      ? `Worker 离线 · ${queueTotal} 等待`
      : queueTotal > 0
        ? `${queueTotal} 排队`
        : idleStatusLabel;
  const idleBaseConfigurationBlocked = runningTotal === 0
    && queueTotal === 0
    && baseConfigurationFullyChecked
    && !llmBaseConfigurationVerified;
  const cardTitle = workerOffline
    ? runningTotal > 0
      ? "检测到跑中记录，但 Worker 心跳已过期；请按当前任务与最近更新时间核验是否仍在执行"
      : queueBlocked
        ? "Worker 当前离线，排队任务尚未开始"
        : "Worker 心跳已过期"
    : "基础配置状态只核 Worker 心跳、Provider 配置、月总预算闸与模型运行授权，不代表具体任务已可调用；single_call、任务所选 provider/cost scope、force_offline 与 fleet breaker 仍由每次任务预检决定；最近结果仅覆盖近 2 小时最多 5 条记录";
  const runtimeTitle = providerGateKnown
    ? `${Array.from(providers.keys()).join(" / ")}；配置完整 ${providerConfigured}/${providerTotal}`
    : "当前账号无权读取或尚未取得 Provider 配置状态";
  const budgetState = !budgetGateKnown
    ? "待核"
    : !budgetGateAllowed
      ? "已阻断"
      : budget?.configured === false
        ? "未配置·放行"
        : "可用";

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
        <span className={runningTotal > 0 ? "is-running" : queueBlocked || idleBaseConfigurationBlocked ? "is-blocked" : ""}>{statusLabel}</span>
      </header>
      {compact && currentTask ? (
        <div className="vkpi-dashboard-task-queue__current" title={`${taskTitle(currentTask)} · #${currentTask.id}`}>
          <span>当前</span>
          <strong>{taskTitle(currentTask)}</strong>
          <small>#{currentTask.id}</small>
        </div>
      ) : null}
      <div className="vkpi-dashboard-task-queue__runtime" aria-label="LLM 基础配置状态">
        <span title={runtimeTitle}>
          <small>Provider</small>
          <strong className={providerGateKnown ? providerGateAllowed ? "is-ok" : "is-warn" : ""}>
            {providerGateKnown ? `${providerConfigured}/${providerTotal}` : "待核"}
          </strong>
        </span>
        <span title="月度总预算闸；未配置时仅按全局基础契约默认放行，具体任务的独立预算闸仍可能阻断">
          <small>月总预算</small>
          <strong className={budgetGateKnown ? budgetGateAllowed ? "is-ok" : "is-warn" : ""}>{budgetState}</strong>
        </span>
        <span title="运营临时授权仅代表当前运行放行，不等于签名生产就绪">
          <small>临时授权</small>
          <strong className={modelGateKnown && temporaryAuthorized > 0 ? "is-warn" : ""}>
            {modelGateKnown ? `${temporaryAuthorized}/${bindingTotal}` : "待核"}
          </strong>
        </span>
        <span title="经独立信任根校验的签名生产就绪绑定">
          <small>签名就绪</small>
          <strong className={modelGateKnown && signedReady === bindingTotal ? "is-ok" : modelGateKnown ? "is-warn" : ""}>
            {modelGateKnown ? `${signedReady}/${bindingTotal}` : "待核"}
          </strong>
        </span>
      </div>
      <div
        className="vkpi-dashboard-task-queue__recent"
        title="进度中心近 2 小时最多 5 条 LLM 记录的只读摘要，不代表 24 小时全量统计"
      >
        <span>
          <small>近窗成功</small>
          <strong>{recentSuccessLabel(latestLlmSuccess)}</strong>
        </span>
        <span className={latestLlmBlocked ? "is-warn" : ""}>
          <small>近窗阻断</small>
          <strong>{recentBlockedLabel(latestLlmBlocked)}</strong>
        </span>
      </div>
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
