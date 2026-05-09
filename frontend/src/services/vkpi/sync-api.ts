import { apiFetch, jsonBody } from "../http";

export interface VkpiSyncOverview {
  industry?: {
    total_accounts: number;
    sync_status_breakdown: Record<string, number>;
    last_24h_success: number;
    last_24h_failed: number;
    platforms: Array<{
      platform: string;
      total_accounts: number;
      ok_count: number;
      failed_count: number;
      ok_rate: number;
    }>;
  };
  shopify?: {
    last_run_at: string | null;
    last_run_status: string;
    recent_runs: Array<Record<string, unknown>>;
  };
  cron_jobs?: Record<string, { last_run_at: string | null; status: string; detail?: string }>;
  platform_settings?: Record<string, unknown>;
  summary?: {
    overall_health: "healthy" | "degraded" | "down";
    issues: Array<{ severity: string; category: string; message: string }>;
    checked_at: string;
  };
}

export interface VkpiSyncFailure {
  id: number;
  platform: string;
  handle: string;
  account_name?: string;
  sync_status?: string;
  last_crawled_at?: string;
  last_successful_at?: string;
  crawl_error_count?: number;
  last_error_message?: string;
}

export async function getSyncOverview(token: string): Promise<VkpiSyncOverview> {
  return apiFetch<VkpiSyncOverview>("/api/admin/vkpi/sync/overview", {}, token);
}

export async function listSyncFailures(token: string, limit = 50) {
  return apiFetch<{ failures: VkpiSyncFailure[] }>(
    `/api/admin/vkpi/sync/industry/failures?limit=${limit}`,
    {},
    token,
  );
}

export async function triggerSync(token: string, jobName: string, payload: Record<string, unknown> = {}) {
  return apiFetch<{ job: string; status: string; result_summary: Record<string, unknown> }>(
    `/api/admin/vkpi/sync/trigger/${encodeURIComponent(jobName)}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}
