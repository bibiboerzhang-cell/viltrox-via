// browserAssist/assist.worker.ts — 本地协助 Web Worker(独立线程,不卡员工浏览)。
//
// 主线程管理器负责领活/交活(带 session cookie 鉴权);本 Worker 只做纯计算:
// 收 {taskType, payload} → 跑对应执行器 → 回 {ok, result, files_meta} 或 {ok:false, error}。
// 计算放独立线程,几千条评论清洗也不会让员工的页面卡顿。

import { BROWSER_EXECUTORS } from "./executors";

export interface AssistWorkerRequest {
  id: number;
  taskType: string;
  payload: Record<string, unknown>;
}

export interface AssistWorkerResponse {
  id: number;
  ok: boolean;
  result?: unknown;
  files_meta?: unknown[];
  error?: string;
}

self.onmessage = (event: MessageEvent<AssistWorkerRequest>) => {
  const { id, taskType, payload } = event.data || ({} as AssistWorkerRequest);
  const executor = BROWSER_EXECUTORS[taskType];
  if (!executor) {
    const resp: AssistWorkerResponse = { id, ok: false, error: `no browser executor for ${taskType}` };
    self.postMessage(resp);
    return;
  }
  try {
    const { result, files_meta } = executor(payload || {});
    const resp: AssistWorkerResponse = { id, ok: true, result, files_meta };
    self.postMessage(resp);
  } catch (err) {
    const resp: AssistWorkerResponse = {
      id,
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    self.postMessage(resp);
  }
};
