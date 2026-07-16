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
  /** 时间/历史均时推算，不是 Provider 返回的真实完成比例。 */
  progress_estimated?: boolean;
  /** 已运行超过近 7 天同类型均时；此时 progress_pct 必须为 null。 */
  progress_overdue?: boolean;
  progress_label?: string | null;
  eta_seconds?: number | null;
  queue_position?: number | null;
  ahead_count?: number | null;
  provider?: string | null;
  model?: string | null;
  purpose?: string | null;
  task_binding?: string | null;
  fallback_used?: boolean;
  fallback_mode?: "rule_v0" | "provider_fallback" | "safe_fallback" | string | null;
  reason_code?: string | null;
  reason_category?: string | null;
  reason_retryable?: boolean;
  error_category?: string | null;
  next_retry_at?: string | null;
  parent_job_id?: string | number | null;
  phase?: string | null;
  subphase?: string | null;
  attempt_index?: number | null;
  attempt_total?: number | null;
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
  job_type?: string | null;
  provider?: string | null;
  model?: string | null;
  purpose?: string | null;
  task_binding?: string | null;
  fallback_used?: boolean;
  fallback_mode?: "rule_v0" | "provider_fallback" | "safe_fallback" | string | null;
  reason_code?: string | null;
  reason_category?: string | null;
  reason_retryable?: boolean;
  error_category?: string | null;
  parent_job_id?: string | number | null;
  phase?: string | null;
  subphase?: string | null;
  attempt_index?: number | null;
  attempt_total?: number | null;
}

export interface ProgressCenterData {
  status: string;
  generated_at: string | null;
  counts: { running: number; queued: number; active_total: number; recent_total: number };
  running: ProgressTask[];
  queued: ProgressTask[];
  recent_done: ProgressRecentDone[];
  /** Gateway 结果及严格 reservation 的最近状态；不代表绕过 Gateway 的裸调用。 */
  recent_llm: ProgressRecentDone[];
  stage_flow: Array<{ stage: string; label: string }>;
  diagnostics: {
    worker_online: boolean | null;
    llm_visibility?:
      | "gateway_outcomes_plus_strict_reservations"
      | "gateway_outcomes_only_reservation_schema_unavailable"
      | string;
    llm_reservation_schema_available?: boolean;
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
    recent_llm: Array.isArray(res?.recent_llm) ? (res.recent_llm as ProgressRecentDone[]) : [],
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
      llm_visibility: typeof res?.diagnostics?.llm_visibility === "string"
        ? res.diagnostics.llm_visibility
        : undefined,
      llm_reservation_schema_available:
        typeof res?.diagnostics?.llm_reservation_schema_available === "boolean"
          ? res.diagnostics.llm_reservation_schema_available
          : undefined,
    },
  };
}
