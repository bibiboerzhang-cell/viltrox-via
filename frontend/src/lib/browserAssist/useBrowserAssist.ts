// browserAssist/useBrowserAssist.ts — 浏览器内本地协助管理器(主线程)。
//
// 员工开着页面时,后台悄悄向服务器领「安全轻活」,交给 Web Worker 在本机算完再交回。
// 用员工已登录的会话(apiFetch 自带 cookie),无需再登录、无需安装、无感。
//
// 安全与克制:
// - 只领 BROWSER_TASK_TYPES(当前仅 comment_clean),视频/LLM 类永不领;
// - 只在页面可见时领活(切走即停,不抢后台带宽/CPU);
// - 串行一次一条 + 空闲长轮询(默认 45s),不打爆服务器;
// - 设备 id 持久化到 localStorage,注册幂等,不刷设备行;
// - 默认关闭(gated),由调用方显式开启(deploy dark:先上线再决定开)。

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../../services/http";
import { BROWSER_TASK_TYPES } from "./executors";
import type { AssistWorkerRequest, AssistWorkerResponse } from "./assist.worker";

const DEVICE_ID_KEY = "vkpi_browser_assist_device";
const IDLE_POLL_MS = 45_000; // 无活时的长轮询间隔(克制,不打爆服务器)
const HEARTBEAT_MS = 50_000;
const BASE = "/api/admin/vkpi/local-workers";

export interface BrowserAssistStatus {
  running: boolean;
  deviceId: string | null;
  tasksDone: number;
  lastTask: string | null;
  lastError: string | null;
}

function persistentDeviceName(): string {
  try {
    let id = localStorage.getItem(DEVICE_ID_KEY);
    if (!id) {
      id = "browser-" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem(DEVICE_ID_KEY, id);
    }
    return id;
  } catch {
    // localStorage 不可用(隐私模式等)→ 退化为会话级临时名,仍可工作只是每次新设备。
    return "browser-ephemeral";
  }
}

/**
 * 挂一次即可(在 cockpit 根)。enabled=false 时完全静默不注册不领活。
 * 返回运行状态供可选展示(如设置页一个小指示)。
 */
export function useBrowserAssist(enabled: boolean): BrowserAssistStatus {
  const [status, setStatus] = useState<BrowserAssistStatus>({
    running: false,
    deviceId: null,
    tasksDone: 0,
    lastTask: null,
    lastError: null,
  });
  const workerRef = useRef<Worker | null>(null);
  const stoppedRef = useRef(false);
  const seqRef = useRef(0);

  // Worker 一次一条:发任务→等这条的回执(按 id 配对),超时/出错走 reject。
  const runInWorker = useCallback((taskType: string, payload: Record<string, unknown>) => {
    return new Promise<AssistWorkerResponse>((resolve, reject) => {
      const worker = workerRef.current;
      if (!worker) {
        reject(new Error("worker not ready"));
        return;
      }
      const id = ++seqRef.current;
      const timer = globalThis.setTimeout(() => {
        worker.removeEventListener("message", onMessage);
        reject(new Error("worker timeout"));
      }, 30_000);
      const onMessage = (event: MessageEvent<AssistWorkerResponse>) => {
        if (event.data?.id !== id) return;
        globalThis.clearTimeout(timer);
        worker.removeEventListener("message", onMessage);
        resolve(event.data);
      };
      worker.addEventListener("message", onMessage);
      const req: AssistWorkerRequest = { id, taskType, payload };
      worker.postMessage(req);
    });
  }, []);

  useEffect(() => {
    if (!enabled) return;
    if (typeof Worker === "undefined") return; // 老浏览器无 Worker → 不启用(不报错)
    stoppedRef.current = false;

    let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
    let loopTimer: ReturnType<typeof setTimeout> | null = null;
    let deviceId: string | null = null;

    const worker = new Worker(new URL("./assist.worker.ts", import.meta.url), { type: "module" });
    workerRef.current = worker;

    const register = async () => {
      const out = await apiFetch<{ device_id?: string }>(`${BASE}/devices/register`, {
        method: "POST",
        body: JSON.stringify({
          device_name: persistentDeviceName(),
          platform: "browser",
          capabilities: { runner: "browser-assist", ua: navigator.userAgent.slice(0, 120) },
        }),
      });
      return String(out?.device_id || "");
    };

    const heartbeat = async () => {
      if (!deviceId || stoppedRef.current) return;
      try {
        await apiFetch(`${BASE}/devices/${deviceId}/heartbeat`, {
          method: "POST",
          body: JSON.stringify({ current_task: null, stats: {} }),
        });
      } catch {
        // 心跳失败无害,下一拍再试;不打断领活。
      }
    };

    // 领一条→算→交回;领到并处理返回 true,无活/不可见返回 false。
    const leaseAndRun = async (): Promise<boolean> => {
      if (stoppedRef.current || document.visibilityState !== "visible") return false;
      const lease = await apiFetch<{
        status?: string;
        lease_id?: number;
        task_type?: string;
        payload?: Record<string, unknown>;
        task_token?: string;
      }>(`${BASE}/lease`, {
        method: "POST",
        body: JSON.stringify({ device_id: deviceId, task_types: BROWSER_TASK_TYPES }),
      });
      if (!lease || lease.status === "no_task" || !lease.lease_id) return false;

      const resp = await runInWorker(lease.task_type || "", lease.payload || {});
      const submitBody = resp.ok
        ? { task_token: lease.task_token, result: resp.result, files_meta: resp.files_meta || [] }
        : { task_token: lease.task_token, result: { task: lease.task_type, error: resp.error }, files_meta: [] };
      await apiFetch(`${BASE}/lease/${lease.lease_id}/submit`, {
        method: "POST",
        body: JSON.stringify(submitBody),
      });
      setStatus((s) => ({
        ...s,
        tasksDone: s.tasksDone + 1,
        lastTask: lease.task_type || null,
        lastError: resp.ok ? null : resp.error || "exec_failed",
      }));
      return true;
    };

    const loop = async () => {
      if (stoppedRef.current) return;
      let worked = false;
      try {
        worked = await leaseAndRun();
      } catch (err) {
        setStatus((s) => ({ ...s, lastError: err instanceof Error ? err.message : String(err) }));
      }
      if (stoppedRef.current) return;
      // 有活立刻再来一条(尽快清空);无活长轮询,克制服务器。
      loopTimer = globalThis.setTimeout(loop, worked ? 500 : IDLE_POLL_MS);
    };

    (async () => {
      try {
        deviceId = await register();
        if (!deviceId || stoppedRef.current) return;
        setStatus((s) => ({ ...s, running: true, deviceId }));
        heartbeatTimer = globalThis.setInterval(heartbeat, HEARTBEAT_MS);
        loop();
      } catch (err) {
        setStatus((s) => ({ ...s, running: false, lastError: err instanceof Error ? err.message : String(err) }));
      }
    })();

    return () => {
      stoppedRef.current = true;
      if (heartbeatTimer) globalThis.clearInterval(heartbeatTimer);
      if (loopTimer) globalThis.clearTimeout(loopTimer);
      worker.terminate();
      workerRef.current = null;
      setStatus((s) => ({ ...s, running: false }));
    };
  }, [enabled, runInWorker]);

  return status;
}
