import React from "react";
import { Activity, Clock3, Database, Search, Sparkles } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import {
  fetchProgressCenter,
  type ProgressCenterData,
  type ProgressTask,
} from "../../../../services/vkpi/progressCenter-api";
import {
  ACTIVE_POLL_INTERVAL_MS,
  BASE_CONFIG_TITLE,
  COST_REFRESH_INTERVAL_MS,
  IDLE_POLL_INTERVAL_MS,
  RECENT_WINDOW_TITLE,
  channelGroupKey,
  classifyFetchError,
  isRecentLlmBlocked,
  isRecentLlmSuccess,
  laneProgress,
  laneStateShortText,
  nonNegativeCount,
  progressStateLabel,
  readStateLabel,
  recentBlockedLabel,
  recentSuccessLabel,
  taskTitle,
  type CostOverview,
  type LlmSystemModelsOverview,
  type LlmTaskReadiness,
  type ReadState,
} from "./DashboardTaskQueueCard.copy";

const LANES = [
  { key: "search", label: "抓取", empty: "暂无抓取任务", Icon: Search, color: "var(--ds-accent)" },
  { key: "thinking", label: "分析", empty: "暂无分析任务", Icon: Sparkles, color: "var(--ds-accent-2)" },
  { key: "summarizing", label: "落库", empty: "暂无落库任务", Icon: Database, color: "var(--ds-good)" },
  { key: "queued", label: "等待中", empty: "暂无排队任务", Icon: Clock3, color: "var(--ds-warn)" },
] as const;

/** 每条链路各自持有状态:一条读失败不得把另外两条也塌成 null。 */
interface Link<T> { state: ReadState; data: T | null }

const LOADING: Link<never> = { state: "loading", data: null };

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

function DashboardTaskQueueCardImpl({ apiToken = "", compact = false }: { apiToken?: string; compact?: boolean }) {
  const [progress, setProgress] = React.useState<Link<ProgressCenterData>>(LOADING);
  const [cost, setCost] = React.useState<Link<CostOverview>>(LOADING);
  const [models, setModels] = React.useState<Link<LlmSystemModelsOverview>>(LOADING);

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
          setProgress({ state: "ready", data: payload });
          nextDelayMs = hasActiveTasks(payload) ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
        } catch (error) {
          if (stopped || controller.signal.aborted) return;
          setProgress({ state: classifyFetchError(error), data: null });
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

  // 成本账本每次要跑 8 个只读聚合，5 分钟一拍即可；页面隐藏期间完全不发，
  // 回到前台时只有真的过期了才补一拍。
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | undefined;
    let lastRunAt = 0;

    const run = () => {
      if (cancelled) return;
      lastRunAt = Date.now();
      controller?.abort();
      const active = new AbortController();
      controller = active;
      const url = `/api/admin/vkpi/ops/cost-ledger?tz_offset_minutes=${-new Date().getTimezoneOffset()}`;
      void apiFetch<CostOverview>(url, { signal: active.signal, timeoutMs: 12000 }, apiToken)
        .then((payload) => {
          if (!cancelled && !active.signal.aborted) setCost({ state: "ready", data: payload });
        })
        .catch((error) => {
          if (!cancelled && !active.signal.aborted) setCost({ state: classifyFetchError(error), data: null });
        });
    };

    const startTimer = () => {
      window.clearInterval(timer);
      timer = window.setInterval(run, COST_REFRESH_INTERVAL_MS);
    };

    const handleVisibility = () => {
      if (isDocumentHidden()) {
        window.clearInterval(timer);
        timer = undefined;
        return;
      }
      if (Date.now() - lastRunAt >= COST_REFRESH_INTERVAL_MS) run();
      startTimer();
    };

    setCost(LOADING);
    run();
    startTimer();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [apiToken]);

  // 基础配置快照只由进程环境与注册表决定，唯一的时间依赖是证据新鲜度（小时/天量级）。
  // 原来的 60 秒轮询零收益，改为每次挂载取一次。
  React.useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    const controller = new AbortController();
    setModels(LOADING);
    void apiFetch<LlmSystemModelsOverview>("/api/admin/system/models", { signal: controller.signal, timeoutMs: 12000 }, apiToken)
      .then((payload) => {
        if (!cancelled && !controller.signal.aborted) setModels({ state: "ready", data: payload });
      })
      .catch((error) => {
        if (!cancelled && !controller.signal.aborted) setModels({ state: classifyFetchError(error), data: null });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiToken]);

  const data = progress.data;
  const progressReady = progress.state === "ready" && data !== null;

  const byStage = React.useMemo(() => {
    const running = data?.running || [];
    return {
      search: running.filter((task) => task.stage === "search"),
      thinking: running.filter((task) => task.stage === "thinking"),
      summarizing: running.filter((task) => task.stage === "summarizing"),
      queued: data?.queued || [],
    } as Record<string, ProgressTask[]>;
  }, [data]);

  const running = data?.running || [];
  const runningTotal = Number(data?.counts.running ?? running.length);
  const queueTotal = Number(data?.counts.queued ?? byStage.queued.length);
  const currentTask = running[0];
  const workerOffline = data?.diagnostics?.worker_online === false;
  const queueBlocked = workerOffline && queueTotal > 0;

  const today = cost.data?.today;
  const todayCalls = Number(today?.apify_calls || 0) + Number(today?.llm_calls || 0);
  const todayUsd = Number(today?.total_usd);
  const budget = cost.data?.budgets?.monthly_total;
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

  const readiness = React.useMemo(() => {
    const overview = models.data;
    const taskReadiness = Object.values(overview?.task_model_readiness || {});
    const readinessByBinding = new Map<string, LlmTaskReadiness>();
    taskReadiness.forEach((row) => {
      const binding = String(row.binding || "").trim();
      if (binding) readinessByBinding.set(binding, row);
    });
    const auditScope = overview?.readiness_audit?.active_scope;
    const activeBindings = Array.from(new Set(
      (Array.isArray(auditScope?.bindings) && auditScope.bindings.length > 0
        ? auditScope.bindings
        : taskReadiness.map((row) => row.binding)
      ).map((binding) => String(binding || "").trim()).filter(Boolean),
    ));
    // 只按服务通道归组计数，键不渲染 —— 卡面不出厂商名。
    const channels = new Map<string, boolean[]>();
    activeBindings.forEach((binding) => {
      const key = channelGroupKey(binding);
      const states = channels.get(key) || [];
      states.push(readinessByBinding.get(binding)?.configured === true);
      channels.set(key, states);
    });
    const channelTotal = channels.size;
    const channelConfigured = Array.from(channels.values()).filter(
      (states) => states.length > 0 && states.every(Boolean),
    ).length;
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
    return {
      activeBindings,
      channelTotal,
      channelConfigured,
      bindingTotal,
      runtimeAuthorized,
      signedReady,
      temporaryAuthorized: Math.max(derivedTemporaryAuthorized, runtimeAuthorized - signedReady, 0),
    };
  }, [models.data]);

  const modelsReady = models.state === "ready" && models.data !== null;
  const channelGateKnown = modelsReady && readiness.activeBindings.length > 0 && readiness.channelTotal > 0;
  const channelGateAllowed = channelGateKnown && readiness.channelConfigured === readiness.channelTotal;
  const modelGateKnown = modelsReady && readiness.bindingTotal > 0;
  const modelGateAllowed = modelGateKnown && readiness.runtimeAuthorized === readiness.bindingTotal;
  const workerGateKnown = typeof data?.diagnostics?.worker_online === "boolean";
  const workerGateAllowed = workerGateKnown && !workerOffline;
  const baseConfigurationFullyChecked = workerGateKnown && channelGateKnown && budgetGateKnown && modelGateKnown;
  const baseConfigurationVerified = baseConfigurationFullyChecked
    && workerGateAllowed
    && channelGateAllowed
    && budgetGateAllowed
    && modelGateAllowed;

  const recentLlm = data?.recent_llm || [];
  const latestSuccess = recentLlm.find(isRecentLlmSuccess);
  const latestBlocked = recentLlm.find(isRecentLlmBlocked);
  const idleStatusLabel = baseConfigurationVerified
    ? "基础配置正常 · 每个任务开跑前再确认"
    : !baseConfigurationFullyChecked
      ? "当前无任务 · 状态待核"
      : !workerGateAllowed
        ? "当前受限 · 后台未运行"
        : !channelGateAllowed
          ? "当前受限 · 服务通道"
          : !budgetGateAllowed
            ? "当前受限 · 预算"
            : "当前受限 · 模型未授权";
  const statusLabel = !progressReady
    ? progressStateLabel(progress.state)
    : runningTotal > 0
      ? `${runningTotal} 处理中${workerOffline ? " · 后台心跳中断" : ""}`
      : queueBlocked
        ? `后台未运行 · ${queueTotal} 等待中`
        : queueTotal > 0
          ? `${queueTotal} 等待中`
          : idleStatusLabel;
  const idleBaseConfigurationBlocked = progressReady
    && runningTotal === 0
    && queueTotal === 0
    && baseConfigurationFullyChecked
    && !baseConfigurationVerified;
  const cardTitle = !progressReady
    ? `${progressStateLabel(progress.state)}；下方四项基础状态与最近结果仍按各自数据链路单独显示`
    : workerOffline
      ? runningTotal > 0
        ? "检测到跑中记录，但后台心跳已过期；请按当前任务与最近更新时间核验是否仍在执行"
        : queueBlocked
          ? "后台当前未运行，排队任务尚未开始"
          : "后台心跳已过期"
      : BASE_CONFIG_TITLE;
  const channelTitle = channelGateKnown
    ? `已配置 ${readiness.channelConfigured} 条，共 ${readiness.channelTotal} 条服务通道；缺一条就可能有任务跑不动`
    : models.state === "forbidden"
      ? "当前账号无权查看服务通道配置状态"
      : `服务通道配置状态${readStateLabel(models.state)}`;
  const modelsUnknownText = modelsReady ? "待核" : readStateLabel(models.state);
  const budgetUnknownText = cost.state === "ready" ? "待核" : readStateLabel(cost.state);
  const budgetState = !budgetGateKnown
    ? budgetUnknownText
    : !budgetGateAllowed
      ? "已阻断"
      : budget?.configured === false
        ? "未配置·放行"
        : "可用";
  const currentRowText = !progressReady
    ? progressStateLabel(progress.state)
    : currentTask
      ? taskTitle(currentTask)
      : queueBlocked
        ? "后台未运行，排队任务尚未开始"
        : "当前没有在跑的任务";

  return (
    <article
      className={`vkpi-dashboard-task-queue ${compact ? "is-compact" : ""}`}
      title={cardTitle}
    >
      <header>
        <div>
          <Activity size={13} />
          <strong>{compact ? "排队" : "任务队列"}</strong>
        </div>
        <span className={progressReady && runningTotal > 0 ? "is-running" : !progressReady || queueBlocked || idleBaseConfigurationBlocked ? "is-blocked" : ""}>{statusLabel}</span>
      </header>
      {compact ? (
        <div
          className="vkpi-dashboard-task-queue__current"
          title={currentTask ? `${taskTitle(currentTask)} · #${currentTask.id}` : currentRowText}
        >
          <span>当前</span>
          <strong>{currentRowText}</strong>
          {currentTask ? <small>#{currentTask.id}</small> : null}
        </div>
      ) : null}
      <div className="vkpi-dashboard-task-queue__runtime" aria-label="基础配置状态">
        <span title={channelTitle}>
          <small>服务通道</small>
          <strong className={channelGateKnown ? channelGateAllowed ? "is-ok" : "is-warn" : ""}>
            {channelGateKnown ? `${readiness.channelConfigured}/${readiness.channelTotal}` : modelsUnknownText}
          </strong>
        </span>
        <span title="月度总预算闸；未配置时仅按全局基础契约默认放行，具体任务的独立预算闸仍可能阻断">
          <small>月总预算</small>
          <strong className={budgetGateKnown ? budgetGateAllowed ? "is-ok" : "is-warn" : ""}>{budgetState}</strong>
        </span>
        <span title="这几项是人工先放行的，还没走完整核验，结果要多留意">
          <small>人工放行</small>
          <strong className={modelGateKnown && readiness.temporaryAuthorized > 0 ? "is-warn" : ""}>
            {modelGateKnown ? `${readiness.temporaryAuthorized}/${readiness.bindingTotal}` : modelsUnknownText}
          </strong>
        </span>
        <span title="已通过完整核验的项数；数字等于总数才算全部就绪。当前未配置核验证据源时会长期为 0，模型仍以人工放行运行">
          <small>正式核验</small>
          <strong className={modelGateKnown && readiness.signedReady === readiness.bindingTotal ? "is-ok" : modelGateKnown ? "is-warn" : ""}>
            {modelGateKnown ? `${readiness.signedReady}/${readiness.bindingTotal}` : modelsUnknownText}
          </strong>
        </span>
      </div>
      <div className="vkpi-dashboard-task-queue__recent" title={RECENT_WINDOW_TITLE}>
        <span>
          <small>最近成功</small>
          <strong>{progressReady ? recentSuccessLabel(latestSuccess) : progressStateLabel(progress.state)}</strong>
        </span>
        <span className={progressReady && latestBlocked ? "is-warn" : ""}>
          <small>最近受阻</small>
          <strong>{progressReady ? recentBlockedLabel(latestBlocked) : progressStateLabel(progress.state)}</strong>
        </span>
      </div>
      <div className="vkpi-dashboard-task-queue__lanes">
        {LANES.map((lane) => {
          const tasks = progressReady ? byStage[lane.key] || [] : [];
          const isQueuedLane = lane.key === "queued";
          const isWaiting = isQueuedLane && tasks.length > 0;
          const isBlocked = isQueuedLane && queueBlocked;
          const progressPct = isQueuedLane ? 0 : laneProgress(tasks);
          const topTask = tasks[0];
          const countText = !progressReady
            ? laneStateShortText(progress.state)
            : tasks.length === 0
              ? "0"
              : isWaiting
                ? String(tasks.length)
                : topTask?.progress_overdue
                  ? "超均时"
                  : progressPct === null
                    ? String(tasks.length)
                    : `${progressPct}%`;
          const laneText = !progressReady
            ? progressStateLabel(progress.state)
            : isBlocked
              ? "等待后台恢复"
              : topTask
                ? taskTitle(topTask)
                : lane.empty;
          return (
            <div
              className={`vkpi-dashboard-task-queue__lane ${tasks.length > 0 ? "is-active" : "is-idle"} ${isBlocked ? "is-blocked" : ""}`}
              key={lane.key}
              title={topTask
                ? `${taskTitle(topTask)} · #${topTask.id}${topTask.progress_overdue ? " · 已超历史均时" : ""}`
                : laneText}
            >
              <lane.Icon size={compact ? 11 : 12} />
              <span className="vkpi-dashboard-task-queue__name">{lane.label}</span>
              {!compact ? <span className="vkpi-dashboard-task-queue__task">{laneText}</span> : null}
              <span className="vkpi-dashboard-task-queue__bar">
                <i
                  className={isWaiting ? "is-waiting" : progressPct === null ? "is-indeterminate" : ""}
                  style={{
                    width: isWaiting ? "10%" : progressPct === null ? "42%" : `${Math.max(tasks.length > 0 ? 6 : 0, Number(progressPct || 0))}%`,
                    background: tasks.length > 0 ? lane.color : undefined,
                  }}
                />
              </span>
              <small>{countText}</small>
            </div>
          );
        })}
      </div>
      <footer>
        {cost.state !== "ready"
          ? <span>{`今日成本${readStateLabel(cost.state)}`}</span>
          : today && Number.isFinite(todayUsd)
            ? <span>今日 {todayCalls.toLocaleString()} 次 · ${todayUsd.toFixed(2)}{compact && budgetPct !== null ? ` · 预算 ${budgetPct}%` : ""}</span>
            : <span>今日暂无成本记录</span>}
        {!compact ? <span>{budgetPct === null ? "预算未配置" : `月预算已用 ${budgetPct}%`}</span> : null}
      </footer>
    </article>
  );
}

// props 是两个原始值，memo 能真正挡住父层(侧栏/看板)重渲染引发的整卡重算。
export const DashboardTaskQueueCard = React.memo(DashboardTaskQueueCardImpl);
