import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  getTaskQueueCompact,
  type TaskQueueItem,
  type TaskQueueResponse,
} from "../../../services/vkpi/tasks-api";

// 10C 状态同源:后台任务事实源(workflow_runs → task_queue_view 投影,经 /task-queue/compact
// 暴露为 active/recent/queued)轮询 hook。固定间隔拉取 + 页面隐藏时暂停 + 卸载清理,
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
 * 轻量轮询 hook:固定间隔拉取后台任务进度(workflow_runs 统一事实源的 compact 投影)。
 *
 * - 可见性节流:页面切到后台(visibilitychange → hidden)即停轮询,切回前台立即补一拍再恢复。
 * - 卸载清理:清掉 interval + 移除 visibilitychange 监听,且用 mounted 闸防卸载后 setState。
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

  // mounted 闸:异步返回时若组件已卸载则丢弃结果,杜绝 "setState on unmounted" 警告。
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    if (!apiToken || isDocumentHidden()) return;
    setLoading(true);
    try {
      const response = await getTaskQueueCompact(apiToken, { limit, recentMinutes });
      if (!mountedRef.current) return;
      setPayload(response);
      setError("");
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof Error ? err.message : "任务进度连接中");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [apiToken, limit, recentMinutes]);

  useEffect(() => {
    mountedRef.current = true;
    if (!apiToken) {
      setPayload(null);
      setError("缺少 API token");
      return () => {
        mountedRef.current = false;
      };
    }

    let intervalId: number | undefined;
    const startPolling = () => {
      if (intervalId || isDocumentHidden()) return;
      void refresh();
      intervalId = window.setInterval(() => {
        void refresh();
      }, intervalMs);
    };
    const stopPolling = () => {
      if (intervalId) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const handleVisibility = () => {
      if (isDocumentHidden()) stopPolling();
      else startPolling();
    };

    startPolling();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      mountedRef.current = false;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [apiToken, intervalMs, refresh]);

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
