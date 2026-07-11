// U1 顶栏全局任务进度中心 API:GET /api/admin/vkpi/progress/center
// 一次请求喂顶栏:跑中(含进度%/阶段/ETA)+ 排队深度 + 最近完成 5 条。纯读。
// token 口径与 globalSearch-api 同源:CockpitTopbar 是纯展示组件不吃 apiToken prop,
// 从 localStorage 直读登录 token(readStoredApiToken 复用,全库仅两处 TOKEN_KEY)。

import { apiFetch } from "../http";
import { readStoredApiToken } from "./globalSearch-api";

export interface ProgressTask {
  id: string;
  source: string | null;
  kind: string | null;
  job_type: string | null;
  label: string | null;
  platform: string | null;
  kol_pool_id: string | null;
  status: string | null;
  stage: string | null;
  stage_label: string | null;
  created_at: string | null;
  updated_at: string | null;
  masked: boolean;
  progress_pct: number | null;
  eta_seconds?: number | null;
  queue_position?: number | null;
  ahead_count?: number | null;
}

export interface ProgressRecentDone {
  id: string;
  source: string | null;
  kind: string | null;
  label: string | null;
  status: string | null;
  finished_at: string | null;
  has_error: boolean;
  masked: boolean;
}

export interface ProgressCenterData {
  status: string;
  generated_at: string | null;
  counts: { running: number; queued: number; active_total: number; recent_total: number };
  running: ProgressTask[];
  queued: ProgressTask[];
  recent_done: ProgressRecentDone[];
  stage_flow: Array<{ stage: string; label: string }>;
  diagnostics: {
    worker_online: boolean | null;
  };
}

/** 顶栏进度中心快照(10s 轮询)。后端字段缺失/非数组一律兜底,渲染永不炸。 */
export async function fetchProgressCenter(
  opts: { token?: string; signal?: AbortSignal } = {},
): Promise<ProgressCenterData> {
  const token = opts.token || readStoredApiToken();
  const res = await apiFetch<Partial<ProgressCenterData>>(
    "/api/admin/vkpi/progress/center",
    { signal: opts.signal, timeoutMs: 10000 },
    token || undefined,
  );
  const counts = (res?.counts || {}) as Partial<ProgressCenterData["counts"]>;
  return {
    status: typeof res?.status === "string" ? res.status : "ready",
    generated_at: typeof res?.generated_at === "string" ? res.generated_at : null,
    counts: {
      running: Number(counts.running) || 0,
      queued: Number(counts.queued) || 0,
      active_total: Number(counts.active_total) || 0,
      recent_total: Number(counts.recent_total) || 0,
    },
    running: Array.isArray(res?.running) ? (res.running as ProgressTask[]) : [],
    queued: Array.isArray(res?.queued) ? (res.queued as ProgressTask[]) : [],
    recent_done: Array.isArray(res?.recent_done) ? (res.recent_done as ProgressRecentDone[]) : [],
    stage_flow: Array.isArray(res?.stage_flow)
      ? (res.stage_flow as Array<{ stage: string; label: string }>)
      : [
          { stage: "queued", label: "队列中" },
          { stage: "search", label: "抓取" },
          { stage: "thinking", label: "分析" },
          { stage: "summarizing", label: "落库" },
        ],
    diagnostics: {
      worker_online: typeof res?.diagnostics?.worker_online === "boolean"
        ? res.diagnostics.worker_online
        : null,
    },
  };
}
