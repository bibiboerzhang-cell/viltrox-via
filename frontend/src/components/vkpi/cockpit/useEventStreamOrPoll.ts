import { useEffect, useRef } from "react";
import { prepareSseStream } from "../../../services/sse-api";

// SSE 扩面归一:统一「SSE 优先 + 轮询兜底」的定时器基元。
// ----------------------------------------------------------------------------
// 同页此前有多路各自为政的 setInterval(进度中心 10s / 思考流 10s / …),后端被反复
// 打点。本 hook 把这类「动态刷新」收敛成一个基元:
//   - 有事件流地址且浏览器支持 EventSource → 订阅 SSE,靠事件驱动重拉(近实时、少打点);
//   - 无地址 / 不支持 / 断线 → 无感回退到固定间隔轮询,行为与切换前完全一致。
//
// 可见性节流复用 useWorkflowRunsStream 的同款形态:标签页切后台(visibilitychange →
// hidden)即停轮询,切回前台补一拍再恢复(SSE 连接本身低频推送,保持常连不受可见性影响,
// 与 TaskCenter 的 SSE 口径一致)。
//
// 单卡错不拖垮整页:SSE 构造/连接异常一律 try 包裹 + 静默回退轮询,绝不上抛。
// 时间机制(MEMORY: vkpi-time-mechanism)不在本层处理,交由各消费方按浏览器时区格式化。

function isDocumentHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

const DEFAULT_INTERVAL_MS = 10000;
// 默认监听的 SSE 事件名:命名事件 + 匿名 message。收到任一即触发一次重拉(或 onEvent)。
const DEFAULT_SSE_EVENTS = ["message", "update", "snapshot", "status_update"] as const;

export interface EventStreamOrPollOptions {
  /** 每拍要执行的拉取动作(轮询兜底逐拍调用;SSE 模式下先播种一次 + 每次事件后重拉)。 */
  pollFn: () => void | Promise<void>;
  /** 轮询间隔(毫秒),默认 10000;<=0 回退默认。 */
  interval?: number;
  /**
   * SSE 事件流地址(须已 buildApiUrl)。为空 / undefined → 直接走轮询兜底(骨架就绪、
   * 后端聚合事件流未上线)。后端上线后传入 url 即自动切 SSE,无需改消费方其余代码。
   */
  streamUrl?: string | null;
  /**
   * 用于签发一次性 SSE ticket 的登录 token。长期 token 不会进入 EventSource URL；
   * 不传时签发请求依赖现有 HttpOnly 登录 cookie。
   */
  streamToken?: string | null;
  /** 要监听的 SSE 事件名;缺省用 DEFAULT_SSE_EVENTS。 */
  events?: readonly string[];
  /** 收到 SSE 事件时的自定义处理;不传则默认调用 pollFn 重拉一次。 */
  onEvent?: (ev: MessageEvent) => void;
  /** EventSource withCredentials(与 TaskCenter 同款),默认 true。 */
  withCredentials?: boolean;
  /** 总开关:false(如无 token)时既不订阅也不轮询。默认 true。 */
  enabled?: boolean;
}

/**
 * SSE 优先 + 轮询兜底的动态刷新基元。返回 void(数据/错误状态由 pollFn 内部自持)。
 *
 * 回调用 latest-ref 读取:pollFn / events / onEvent 的 identity 变化不会重订阅 SSE 或
 * 重建定时器,消费方无需为它们额外 memo(仍建议 memo pollFn 以避免误触发)。
 */
export function useEventStreamOrPoll(options: EventStreamOrPollOptions): void {
  const {
    interval,
    streamUrl,
    streamToken,
    withCredentials = true,
    enabled = true,
  } = options;
  const intervalMs = interval && interval > 0 ? interval : DEFAULT_INTERVAL_MS;
  const pollRef = useRef(options.pollFn);
  const eventsRef = useRef(options.events);
  const onEventRef = useRef(options.onEvent);
  pollRef.current = options.pollFn;
  eventsRef.current = options.events;
  onEventRef.current = options.onEvent;

  useEffect(() => {
    if (!enabled) return undefined;

    const runPoll = () => {
      void pollRef.current();
    };

    // ── 轮询兜底(与 useWorkflowRunsStream 同款可见性暂停形态)────────────────
    let intervalId: number | undefined;
    const startPolling = () => {
      if (intervalId || isDocumentHidden()) return;
      runPoll();
      intervalId = window.setInterval(runPoll, intervalMs);
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

    const canSse = Boolean(streamUrl) && typeof EventSource !== "undefined";

    if (canSse) {
      // SSE 优先:订阅成功即事件驱动(仍先播种一次首屏快照);任一错误无感回退轮询。
      let source: EventSource | null = null;
      let fellBack = false;
      let cancelled = false;
      const fallbackToPolling = () => {
        if (fellBack) return;
        fellBack = true;
        document.addEventListener("visibilitychange", handleVisibility);
        startPolling();
      };
      const onSseMessage = (ev: Event) => {
        const handler = onEventRef.current;
        if (handler) handler(ev as MessageEvent);
        else runPoll();
      };
      runPoll(); // 播种首屏,不等 ticket 或第一条事件
      void prepareSseStream(streamUrl as string, streamToken || undefined)
        .then((authorizedUrl) => {
          if (cancelled) return;
          source = new EventSource(authorizedUrl, { withCredentials });
          const names = eventsRef.current && eventsRef.current.length ? eventsRef.current : DEFAULT_SSE_EVENTS;
          names.forEach((name) => source?.addEventListener(name, onSseMessage));
          source.onerror = () => {
            try {
              source?.close();
            } catch {
              /* noop */
            }
            source = null;
            fallbackToPolling();
          };
        })
        .catch(() => {
          if (cancelled) return;
          try {
            source?.close();
          } catch {
            /* noop */
          }
          source = null;
          fallbackToPolling();
        });
      return () => {
        cancelled = true;
        if (source) {
          try {
            source.close();
          } catch {
            /* noop */
          }
        }
        stopPolling();
        document.removeEventListener("visibilitychange", handleVisibility);
      };
    }

    // 纯轮询:立即补一拍 + 固定间隔 + 可见性暂停 + 卸载清理。
    document.addEventListener("visibilitychange", handleVisibility);
    startPolling();
    return () => {
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [enabled, streamUrl, streamToken, intervalMs, withCredentials]);
}
