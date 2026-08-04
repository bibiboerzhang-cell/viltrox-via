import { apiFetch, buildApiUrl, jsonBody } from "../http";

type Row = Record<string, unknown>;

export type AsyncTaskStatus =
  | "queued"
  | "running"
  | "processing"
  | "retrying"
  | "done"
  | "failed"
  | "cancelled"
  | "timeout"
  | "partial_done"
  | "prefilter_rejected";

export interface AsyncTask {
  task_id: string;
  task_type: string;
  status: AsyncTaskStatus;
  progress_pct?: number;
  progress_text?: string;
  result_json?: Row;
  result?: Row;
  error?: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
}

export interface TaskQueueItem {
  id: string;
  source: string;
  kind: string;
  job_type?: string;
  target?: Row;
  status: string;
  raw_status?: string;
  stage: "queued" | "search" | "thinking" | "summarizing" | string;
  stage_label?: string;
  created_at?: string;
  updated_at?: string;
  progress_pct?: number;
  error?: string;
  error_category?: string;
  next_retry_at?: string;
}

export interface TaskQueueResponse {
  status: "ready" | string;
  source?: string;
  query?: Row;
  counts?: {
    active_total?: number;
    recent_total?: number;
    queued?: number;
    running?: number;
    active_by_status?: Record<string, number>;
    recent_by_status?: Record<string, number>;
    active_by_stage?: Record<string, number>;
  };
  active?: TaskQueueItem[];
  recent?: TaskQueueItem[];
  speed_light?: Row;
  polling?: Row;
  diagnostics?: Row;
  cache?: Row;
}

export const TERMINAL_STATUSES = [
  "done",
  "failed",
  "cancelled",
  "timeout",
  "partial_done",
  "prefilter_rejected",
] as const;

const DEFAULT_TASK_STATUSES: AsyncTaskStatus[] = [
  "queued",
  "running",
  "processing",
  "retrying",
  ...TERMINAL_STATUSES,
];

const ONE_HOUR_MS = 60 * 60 * 1000;
const DEFAULT_TASK_REQUEST_TIMEOUT_MS = 5000;

export interface TaskRequestOptions {
  signal?: AbortSignal | null;
  timeoutMs?: number;
}

function normalizeAsyncTask(raw: Row): AsyncTask | null {
  const taskId = String(raw.task_id || "");
  if (!taskId) return null;
  const result = (raw.result_json || raw.result || {}) as Row;
  return {
    task_id: taskId,
    task_type: String(raw.task_type || raw.job_type || ""),
    status: String(raw.status || "queued") as AsyncTaskStatus,
    progress_pct: typeof raw.progress_pct === "number" ? raw.progress_pct : Number(raw.progress_pct || 0),
    progress_text: String(raw.progress_text || ""),
    result_json: result,
    result,
    error: String(raw.error || raw.error_message || ""),
    created_at: String(raw.created_at || ""),
    started_at: raw.started_at ? String(raw.started_at) : undefined,
    finished_at: raw.finished_at ? String(raw.finished_at) : undefined,
  };
}

function isRecentTask(task: AsyncTask): boolean {
  if (!TERMINAL_STATUSES.includes(task.status as (typeof TERMINAL_STATUSES)[number])) return true;
  if (!task.finished_at) return false;
  const finishedAt = new Date(task.finished_at).getTime();
  return Number.isFinite(finishedAt) && Date.now() - finishedAt < ONE_HOUR_MS;
}

export async function listTasks(
  token: string,
  filters: { status?: AsyncTaskStatus[] } = {},
  request: TaskRequestOptions = {},
) {
  const statuses = new Set(filters.status?.length ? filters.status : DEFAULT_TASK_STATUSES);
  // The backend already supports an unfiltered bounded list.  Fetch it once
  // and filter locally instead of polling one request per status (10 requests
  // every three seconds), which amplified cold-login and Dealers page load into
  // connection-pool starvation.
  const response = await apiFetch<{ tasks?: Row[]; items?: Row[] }>(
    "/api/marketing/tasks?limit=200&compact=true",
    {
      signal: request.signal,
      timeoutMs: request.timeoutMs ?? DEFAULT_TASK_REQUEST_TIMEOUT_MS,
    },
    token,
  );
  const tasksById = new Map<string, AsyncTask>();
  const rows = response.tasks || response.items || [];
  rows.forEach((row) => {
    const task = normalizeAsyncTask(row);
    if (task && statuses.has(task.status) && isRecentTask(task)) {
      tasksById.set(task.task_id, task);
    }
  });
  return Array.from(tasksById.values()).sort((a, b) => {
    const aTime = new Date(a.created_at || a.finished_at || 0).getTime();
    const bTime = new Date(b.created_at || b.finished_at || 0).getTime();
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
  });
}

export async function getTaskRealtimeStatus(token: string, request: TaskRequestOptions = {}) {
  return apiFetch<Row>(
    "/api/admin/vkpi/tasks/realtime-status",
    {
      signal: request.signal,
      timeoutMs: request.timeoutMs ?? DEFAULT_TASK_REQUEST_TIMEOUT_MS,
    },
    token,
  );
}

export async function getTaskQueue(
  token: string,
  options: { limit?: number; recentMinutes?: number; includeLlmCalls?: boolean } = {},
) {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 50));
  params.set("recent_minutes", String(options.recentMinutes ?? 10));
  params.set("include_llm_calls", options.includeLlmCalls === false ? "false" : "true");
  return apiFetch<TaskQueueResponse>(`/api/admin/vkpi/task-queue?${params.toString()}`, {}, token);
}

export async function getTaskQueueCompact(
  token: string,
  options: { limit?: number; recentMinutes?: number } = {},
  request: TaskRequestOptions = {},
) {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 30));
  params.set("recent_minutes", String(options.recentMinutes ?? 5));
  return apiFetch<TaskQueueResponse>(
    `/api/admin/vkpi/task-queue/compact?${params.toString()}`,
    {
      signal: request.signal,
      timeoutMs: request.timeoutMs ?? DEFAULT_TASK_REQUEST_TIMEOUT_MS,
    },
    token,
  );
}

export function buildTaskEventStreamUrl(taskId: string) {
  return buildApiUrl(`/api/audit/stream/${encodeURIComponent(taskId)}`);
}

export async function cancelTask(token: string, taskId: string) {
  await apiFetch<Row>(
    `/api/marketing/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function retryTask(token: string, taskId: string) {
  const response = await apiFetch<Row>(
    `/api/marketing/tasks/${encodeURIComponent(taskId)}/retry`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
  return { task_id: String(response.task_id || "") };
}
