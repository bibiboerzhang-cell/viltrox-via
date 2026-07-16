import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  type AsyncTask,
  buildTaskEventStreamUrl,
  cancelTask as cancelAsyncTask,
  getTaskRealtimeStatus,
  listTasks,
  retryTask as retryAsyncTask,
  TERMINAL_STATUSES,
} from '../../domains/tasks';
import { prepareSseStream } from '../../services/sse-api';
import './TaskCenter.css';

const TASK_TYPE_LABELS: Record<string, string> = {
  vkpi_official_channel_sync: '官号同步',
  vkpi_official_weekly_deep_refresh: '官号深度刷新',
  vkpi_video_cache: '视频缓存',
  vkpi_apify_kol_scrape: '红人抓取',
  vkpi_product_recommendation_run: '推荐计算',
  vkpi_ai_analysis: 'AI 分析',
};

const terminalStatusSet = new Set<string>(TERMINAL_STATUSES);
const failedStatusSet = new Set<string>(['failed', 'cancelled', 'timeout', 'prefilter_rejected']);
const callbackFailedStatusSet = new Set<string>(['failed', 'timeout', 'prefilter_rejected']);
const completedStatusSet = new Set<string>(['done', 'partial_done']);

interface WatcherCallbacks {
  onDone?: (task: AsyncTask) => void;
  onFailed?: (task: AsyncTask) => void;
  onCancelled?: (task: AsyncTask) => void;
  onTerminal?: (task: AsyncTask) => void;
}

interface TaskCenterAPI {
  tasks: AsyncTask[];
  activeTasks: AsyncTask[];
  completedTasks: AsyncTask[];
  failedTasks: AsyncTask[];
  cancelTask: (taskId: string) => Promise<void>;
  retryTask: (taskId: string) => Promise<string>;
  waitForTask: (taskId: string, callbacks: WatcherCallbacks) => () => void;
}

const TaskCenterContext = createContext<TaskCenterAPI | null>(null);

function isTerminalTask(task: AsyncTask) {
  return terminalStatusSet.has(task.status);
}

function notifyWatcher(task: AsyncTask, callbacks: WatcherCallbacks) {
  if (completedStatusSet.has(task.status)) callbacks.onDone?.(task);
  if (callbackFailedStatusSet.has(task.status)) callbacks.onFailed?.(task);
  if (task.status === 'cancelled') callbacks.onCancelled?.(task);
  callbacks.onTerminal?.(task);
}

function taskLabel(task: AsyncTask) {
  return TASK_TYPE_LABELS[task.task_type] || task.task_type || '后台任务';
}

function taskTime(value?: string) {
  if (!value) return '刚刚';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '刚刚';
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function resultSummary(task: AsyncTask) {
  const result = task.result_json || task.result || {};
  const keys = ['summary', 'message', 'cached_url', 'skip_reason', 'count', 'total'];
  for (const key of keys) {
    const value = result[key];
    if (value != null && value !== '') return String(value);
  }
  return task.progress_text || '已完成';
}

function progressValue(task: AsyncTask) {
  const value = Number(task.progress_pct ?? 0);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}

export function TaskCenterProvider({ apiToken, children }: { apiToken?: string; children: React.ReactNode }) {
  const [tasks, setTasks] = useState<AsyncTask[]>([]);
  const [intervalMs, setIntervalMs] = useState(() => (typeof document !== 'undefined' && document.hidden ? 30000 : 3000));
  const [realtimeReady, setRealtimeReady] = useState(false);
  const watchersRef = useRef<Map<string, Set<WatcherCallbacks>>>(new Map());
  const previousTasksRef = useRef<Map<string, AsyncTask>>(new Map());
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());
  const connectingTaskIdsRef = useRef<Set<string>>(new Set());
  const activeTaskIdsRef = useRef<Set<string>>(new Set());
  const apiTokenRef = useRef(apiToken);
  const inFlightRef = useRef(false);

  const activeTasks = useMemo(() => tasks.filter((task) => !isTerminalTask(task)), [tasks]);
  apiTokenRef.current = apiToken;
  activeTaskIdsRef.current = new Set(activeTasks.map((task) => task.task_id).filter(Boolean));
  const completedTasks = useMemo(() => tasks.filter((task) => completedStatusSet.has(task.status)), [tasks]);
  const failedTasks = useMemo(() => tasks.filter((task) => failedStatusSet.has(task.status)), [tasks]);

  const triggerWatchers = useCallback((task: AsyncTask) => {
    const watchers = watchersRef.current.get(task.task_id);
    if (!watchers?.size) return;
    watchers.forEach((callbacks) => {
      notifyWatcher(task, callbacks);
    });
    watchersRef.current.delete(task.task_id);
  }, []);

  const refreshTasks = useCallback(async () => {
    if (!apiToken || inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const nextTasks = await listTasks(apiToken);
      const previousTasks = previousTasksRef.current;
      nextTasks.forEach((task) => {
        const previous = previousTasks.get(task.task_id);
        if (isTerminalTask(task) && previous?.status !== task.status) {
          triggerWatchers(task);
        }
      });
      previousTasksRef.current = new Map(nextTasks.map((task) => [task.task_id, task]));
      setTasks(nextTasks);
    } finally {
      inFlightRef.current = false;
    }
  }, [apiToken, triggerWatchers]);

  const closeEventSource = useCallback((taskId: string) => {
    const source = eventSourcesRef.current.get(taskId);
    if (!source) return;
    source.close();
    eventSourcesRef.current.delete(taskId);
  }, []);

  useEffect(() => {
    const onVisibilityChange = () => {
      setIntervalMs(document.hidden ? 30000 : 3000);
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    onVisibilityChange();
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, []);

  useEffect(() => {
    if (!apiToken) {
      setRealtimeReady(false);
      return undefined;
    }
    let cancelled = false;
    getTaskRealtimeStatus(apiToken)
      .then((status) => {
        if (cancelled) return;
        const pollingFallbackRequired = Boolean(status.polling_fallback_required);
        setRealtimeReady(Boolean(
          status.sse_available
          && status.job_queue_present
          && status.task_event_subscription_available
          && !pollingFallbackRequired,
        ));
      })
      .catch(() => {
        if (!cancelled) setRealtimeReady(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  useEffect(() => {
    if (!apiToken) {
      setTasks([]);
      previousTasksRef.current = new Map();
      eventSourcesRef.current.forEach((source) => source.close());
      eventSourcesRef.current.clear();
      return undefined;
    }
    void refreshTasks();
    const timer = window.setInterval(() => {
      void refreshTasks();
    }, intervalMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [apiToken, intervalMs, refreshTasks]);

  useEffect(() => {
    if (!realtimeReady || typeof EventSource === 'undefined') {
      eventSourcesRef.current.forEach((source) => source.close());
      eventSourcesRef.current.clear();
      return;
    }
    const activeIds = new Set(activeTasks.map((task) => task.task_id).filter(Boolean));
    eventSourcesRef.current.forEach((source, taskId) => {
      if (!activeIds.has(taskId)) {
        source.close();
        eventSourcesRef.current.delete(taskId);
      }
    });
    activeIds.forEach((taskId) => {
      if (eventSourcesRef.current.has(taskId) || connectingTaskIdsRef.current.has(taskId)) return;
      const streamUrl = buildTaskEventStreamUrl(taskId);
      const issuedForToken = apiTokenRef.current;
      connectingTaskIdsRef.current.add(taskId);
      void prepareSseStream(streamUrl, issuedForToken)
        .then((authorizedUrl) => {
          if (
            !activeTaskIdsRef.current.has(taskId)
            || apiTokenRef.current !== issuedForToken
            || eventSourcesRef.current.has(taskId)
          ) return;
          const source = new EventSource(authorizedUrl, { withCredentials: true });
          const refresh = () => {
            void refreshTasks();
          };
          const refreshAndClose = () => {
            void refreshTasks();
            closeEventSource(taskId);
          };
          source.addEventListener('status_update', refresh);
          source.addEventListener('result_ready', refreshAndClose);
          source.addEventListener('failed', refreshAndClose);
          source.addEventListener('final_result', refreshAndClose);
          source.onerror = () => {
            closeEventSource(taskId);
          };
          eventSourcesRef.current.set(taskId, source);
        })
        .catch(() => {
          // Existing task polling remains active as the transparent fallback.
        })
        .finally(() => {
          connectingTaskIdsRef.current.delete(taskId);
        });
    });
  }, [activeTasks, apiToken, closeEventSource, realtimeReady, refreshTasks]);

  useEffect(() => {
    return () => {
      watchersRef.current.clear();
      activeTaskIdsRef.current.clear();
      connectingTaskIdsRef.current.clear();
      eventSourcesRef.current.forEach((source) => source.close());
      eventSourcesRef.current.clear();
    };
  }, []);

  const cancelTask = useCallback(async (taskId: string) => {
    if (!apiToken) return;
    await cancelAsyncTask(apiToken, taskId);
    await refreshTasks();
  }, [apiToken, refreshTasks]);

  const retryTask = useCallback(async (taskId: string) => {
    if (!apiToken) return '';
    const response = await retryAsyncTask(apiToken, taskId);
    await refreshTasks();
    return response.task_id;
  }, [apiToken, refreshTasks]);

  const waitForTask = useCallback((taskId: string, callbacks: WatcherCallbacks) => {
    const currentTask = tasks.find((task) => task.task_id === taskId);
    if (currentTask && isTerminalTask(currentTask)) {
      window.setTimeout(() => notifyWatcher(currentTask, callbacks), 0);
      return () => undefined;
    }
    const watchers = watchersRef.current.get(taskId) || new Set<WatcherCallbacks>();
    watchers.add(callbacks);
    watchersRef.current.set(taskId, watchers);
    return () => {
      const currentWatchers = watchersRef.current.get(taskId);
      if (!currentWatchers) return;
      currentWatchers.delete(callbacks);
      if (!currentWatchers.size) watchersRef.current.delete(taskId);
    };
  }, [tasks]);

  const value = useMemo<TaskCenterAPI>(() => ({
    tasks,
    activeTasks,
    completedTasks,
    failedTasks,
    cancelTask,
    retryTask,
    waitForTask,
  }), [activeTasks, cancelTask, completedTasks, failedTasks, retryTask, tasks, waitForTask]);

  return <TaskCenterContext.Provider value={value}>{children}</TaskCenterContext.Provider>;
}

export function useTaskCenter() {
  const context = useContext(TaskCenterContext);
  if (!context) throw new Error('useTaskCenter must be used inside TaskCenterProvider');
  return context;
}

function TaskRow({ task, kind, onCancel, onRetry }: { task: AsyncTask; kind: 'active' | 'done' | 'failed'; onCancel: (taskId: string) => void; onRetry: (taskId: string) => void }) {
  const progress = progressValue(task);
  return (
    <article className={`task-center-card task-center-card--${kind}`}>
      <div className="task-center-card__header">
        <div>
          <strong>{taskLabel(task)}</strong>
          <span>{task.progress_text || task.status}</span>
        </div>
        <time>{taskTime(task.finished_at || task.started_at || task.created_at)}</time>
      </div>
      {kind === 'active' ? (
        <div className="task-center-progress" aria-label={`进度 ${progress}%`}>
          <span style={{ width: `${progress}%` }} />
        </div>
      ) : null}
      {kind === 'done' ? <p>{resultSummary(task)}</p> : null}
      {kind === 'failed' ? <p>{task.error || resultSummary(task) || '任务未完成'}</p> : null}
      <div className="task-center-card__actions">
        {kind === 'active' ? <button type="button" onClick={() => onCancel(task.task_id)}>取消</button> : null}
        {kind === 'failed' ? <button type="button" onClick={() => onRetry(task.task_id)}>重试</button> : null}
      </div>
    </article>
  );
}

export function TaskCenter() {
  const { activeTasks, completedTasks, failedTasks, cancelTask, retryTask } = useTaskCenter();
  const [open, setOpen] = useState(false);
  const [completedOpen, setCompletedOpen] = useState(false);
  const [failedOpen, setFailedOpen] = useState(true);
  const badge = activeTasks.length > 9 ? '9+' : String(activeTasks.length);

  const handleCancel = (taskId: string) => {
    void cancelTask(taskId);
  };

  const handleRetry = (taskId: string) => {
    void retryTask(taskId);
  };

  return (
    <div className="task-center">
      <button className="task-center-bell" type="button" aria-label="任务中心" onClick={() => setOpen((value) => !value)}>
        <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
          <path d="M18 16v-5a6 6 0 0 0-12 0v5l-2 2h16l-2-2Z" />
          <path d="M10 20a2 2 0 0 0 4 0" />
        </svg>
        {activeTasks.length ? <span className="task-center-badge">{badge}</span> : null}
      </button>
      {open ? (
        <section className="task-center-panel" aria-label="任务中心面板">
          <header className="task-center-panel__header">
            <div>
              <span>任务中心</span>
              <strong>{activeTasks.length ? `${activeTasks.length} 个进行中` : '暂无进行中任务'}</strong>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="关闭任务中心">×</button>
          </header>
          <div className="task-center-section">
            <h3>进行中</h3>
            {activeTasks.length ? activeTasks.map((task) => <TaskRow key={task.task_id} task={task} kind="active" onCancel={handleCancel} onRetry={handleRetry} />) : <p className="task-center-empty">没有正在执行的后台任务。</p>}
          </div>
          <div className="task-center-section task-center-section--failed">
            <button className="task-center-section__toggle" type="button" onClick={() => setFailedOpen((value) => !value)}>
              <span>失败</span>
              <strong>{failedTasks.length}</strong>
            </button>
            {failedOpen ? (
              failedTasks.length ? failedTasks.map((task) => <TaskRow key={task.task_id} task={task} kind="failed" onCancel={handleCancel} onRetry={handleRetry} />) : <p className="task-center-empty">暂无失败任务。</p>
            ) : null}
          </div>
          <div className="task-center-section">
            <button className="task-center-section__toggle" type="button" onClick={() => setCompletedOpen((value) => !value)}>
              <span>已完成</span>
              <strong>{completedTasks.length}</strong>
            </button>
            {completedOpen ? (
              completedTasks.length ? completedTasks.map((task) => <TaskRow key={task.task_id} task={task} kind="done" onCancel={handleCancel} onRetry={handleRetry} />) : <p className="task-center-empty">最近一小时没有完成任务。</p>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
