import { apiFetch } from "../http";

// 失败 Triage 队列(10E·人工)客户端。后端:backend/app/api/routers/vkpi_triage.py
// 只读列表 + 单条重新入队(status: triage -> queued)。零触 fit_score。

export interface TriageJob {
  id: number;
  job_type: string | null;
  url: string | null;
  category: string | null;
  error: string | null;
  updated_at: string | null;
}

export interface TriageJobsResponse {
  items: TriageJob[];
  count: number;
  limit: number;
}

export interface TriageRequeueResponse {
  id: number;
  status: string;
  requeued: boolean;
}

export async function listTriageJobs(token: string, limit = 100): Promise<TriageJobsResponse> {
  return apiFetch<TriageJobsResponse>(
    `/api/admin/vkpi/triage/jobs?limit=${limit}`,
    { cache: "no-store" },
    token,
  );
}

export async function requeueTriageJob(token: string, jobId: number): Promise<TriageRequeueResponse> {
  return apiFetch<TriageRequeueResponse>(
    `/api/admin/vkpi/triage/jobs/${jobId}/requeue`,
    { method: "POST", cache: "no-store" },
    token,
  );
}
