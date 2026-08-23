import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getTaskQueueCompact,
  type TaskQueueItem,
  type TaskQueueResponse,
} from "../../../services/vkpi/tasks-api";

// 10C 状态同源:后台任务事实源(workflow_runs → task_queue_view 投影,经 /task-queue/compact
// 暴露为 active/recent/queued)轮询 hook。请求结算后再计时 + 页面隐藏时暂停 + 卸载清理,
// 让 TaskProgressBoard 等消费方在后台任务推进/完成时自动反映,无需手动刷新。
//
// 时间机制(MEMORY: vkpi-time-mechanism):后端时间字段已是 UTC ISO 串,展示层各组件
// 按浏览器时区格式化即可;本 hook 不硬编码 "刚刚 / Just now / 实时" 等相对时间,
// 只透传后端 created_at / updated_at / next_retry_at 原值给消费方自行本地化。

const DEFAULT_POLL_INTERVAL_MS = 5000;
const DEFAULT_LIMIT = 30;
const DEFAULT_RECENT_MINUTES = 5;

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function asItems(value: TaskQueueItem[] | undefined): TaskQueueItem[] {
  return Array.isArray(value) ? value : [];
}

export interface WorkflowRunsStreamOptions {
  /** 轮询间隔(毫秒),默认 5000;<=0 时回退默认值。 */
  intervalMs?: number;
  limit?: number;
  recentMinutes?: number;
}

export interface WorkflowRunsStream {
  /** 后端原始投影 payload(active/recent/counts/speed_light 等),供既有消费方直接读取。 */
  payload: TaskQueueResponse | null;
  /** active + recent 扁平化后的任务(运行 hook)列表,按来源拼接、去重后返回。 */
  runs: TaskQueueItem[];
  active: TaskQueueItem[];
  recent: TaskQueueItem[];
  loading: boolean;
  error: string;
  /** 手动触发一次拉取(重试成功后立即刷新等场景);页面隐藏 / 无 token 时静默跳过。 */
  refresh: () => Promise<void>;
}

/**
 * 轻量轮询 hook:顺序拉取后台任务进度(workflow_runs 统一事实源的 compact 投影)。
 *
 * - 可见性节流:页面切到后台(visibilitychange → hidden)即停轮询,切回前台立即补一拍再恢复。
 * - 卸载清理:清掉 timeout、取消请求并移除 visibilitychange 监听。
 * - 无 apiToken 时不发请求(置空 payload),避免 401 噪音。
 */
export function useWorkflowRunsStream(
  apiToken: string,
  options: WorkflowRunsStreamOptions = {},
): WorkflowRunsStream {
  const intervalMs = options.intervalMs && options.intervalMs > 0 ? options.intervalMs : DEFAULT_POLL_INTERVAL_MS;
  const limit = options.limit ?? DEFAULT_LIMIT;
  const recentMinutes = options.recentMinutes ?? DEFAULT_RECENT_MINUTES;

  const [payload, setPayload] = useState<TaskQueueResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refreshRunnerRef = useRef<() => Promise<void>>(async () => {});
  const previousTokenRef = useRef(apiToken);
  // C9(优化波 B):后台轮询不击穿消费方——
  //   · 指纹跳过:投影内容未变就不 setPayload(引用不换 → 消费方零重渲染);
  //   · loading 只在「尚无快照」时翻动(首拍/换 token),后台刷新不再每 5s 翻 true→false 两次重渲染。
  const payloadFingerprintRef = useRef("");
  const hasPayloadRef = useRef(false);
  // setError("") 在 error 已空时也会让 React 多走一轮渲染(bailout 不保证零渲染),用 ref 把它挡在外面。
  const errorRef = useRef("");
  const setErrorIfChanged = (next: string) => {
    if (errorRef.current === next) return;
    errorRef.current = next;
    setError(next);
  };
  const refresh = useCallback(() => refreshRunnerRef.current(), []);

  useEffect(() => {
    let stopped = false;
    let timeoutId: number | undefined;
    let inFlight: { controller: AbortController; promise: Promise<void> } | null = null;

    const clearScheduledPoll = () => {
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
        timeoutId = undefined;
      }
    };

    const abortActiveRequest = () => {
      const activeRequest = inFlight;
      inFlight = null;
      activeRequest?.controller.abort(new DOMException("Request paused", "AbortError"));
      if (!stopped && !hasPayloadRef.current) setLoading(false);
    };

    const requestOnce = (): Promise<void> => {
      if (stopped || !apiToken || isDocumentHidden()) return Promise.resolve();
      if (inFlight) return inFlight.promise;

      const requestController = new AbortController();
      const activeRequest = {
        controller: requestController,
        promise: Promise.resolve(),
      };
      const markedLoading = !hasPayloadRef.current;
      if (markedLoading) setLoading(true);

      const request = (async () => {
        try {
          const response = await getTaskQueueCompact(
            apiToken,
            { limit, recentMinutes },
            { signal: requestController.signal },
          );
          if (stopped || requestController.signal.aborted) return;
          let fingerprint = "";
          try {
            fingerprint = JSON.stringify(response);
          } catch {
            fingerprint = "";
          }
          if (!fingerprint || fingerprint !== payloadFingerprintRef.current) {
            payloadFingerprintRef.current = fingerprint;
            hasPayloadRef.current = true;
            setPayload(response);
          }
          setErrorIfChanged("");
        } catch (err) {
          if (stopped || requestController.signal.aborted) return;
          setErrorIfChanged(err instanceof Error ? err.message : "任务进度连接中");
        } finally {
          if (inFlight === activeRequest) {
            inFlight = null;
            if (!stopped && markedLoading) setLoading(false);
          }
        }
      })();
      activeRequest.promise = request;
      inFlight = activeRequest;
      return request;
    };

    const scheduleNextPoll = () => {
      clearScheduledPoll();
      if (stopped || !apiToken || isDocumentHidden()) return;
      timeoutId = window.setTimeout(() => {
        timeoutId = undefined;
        void pollCycle();
      }, intervalMs);
    };

    const pollCycle = async () => {
      await requestOnce();
      scheduleNextPoll();
    };

    refreshRunnerRef.current = requestOnce;

    if (previousTokenRef.current !== apiToken) {
      previousTokenRef.current = apiToken;
      payloadFingerprintRef.current = "";
      hasPayloadRef.current = false;
      setPayload(null);
      setErrorIfChanged("");
    }

    if (!apiToken) {
      payloadFingerprintRef.current = "";
      hasPayloadRef.current = false;
      setPayload(null);
      setLoading(false);
      setErrorIfChanged("缺少 API token");
      return () => {
        stopped = true;
        refreshRunnerRef.current = async () => {};
      };
    }

    const handleVisibility = () => {
      clearScheduledPoll();
      if (isDocumentHidden()) {
        abortActiveRequest();
        return;
      }
      void pollCycle();
    };

    void pollCycle();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      stopped = true;
      clearScheduledPoll();
      abortActiveRequest();
      refreshRunnerRef.current = async () => {};
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [apiToken, intervalMs, limit, recentMinutes]);

  const active = useMemo(() => asItems(payload?.active), [payload]);
  const recent = useMemo(() => asItems(payload?.recent), [payload]);
  const runs = useMemo(() => {
    const merged = [...active, ...recent];
    const seen = new Set<string>();
    const deduped: TaskQueueItem[] = [];
    for (const item of merged) {
      const key = String(item?.id ?? "");
      if (key && seen.has(key)) continue;
      if (key) seen.add(key);
      deduped.push(item);
    }
    return deduped;
  }, [active, recent]);

  return { payload, runs, active, recent, loading, error, refresh };
}
