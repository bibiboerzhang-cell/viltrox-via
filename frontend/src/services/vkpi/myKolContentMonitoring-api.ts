import { apiFetch, jsonBody } from "../http";

/** Single-KOL recent-content monitoring is an explicit subscription. */
export interface VkpiKolContentMonitoringSubscription {
  id?: number | null;
  status?: "active" | "paused" | string;
  cadence_hours?: number;
  next_due_at?: string | null;
  last_job_id?: number | null;
  last_job_status?: string;
  last_enqueued_at?: string | null;
  last_success_at?: string | null;
  pause_reason?: string;
  updated_at?: string | null;
  window?: {
    kind?: "recent_posts" | string;
    max_posts?: number;
    full_history?: boolean;
  };
}

export interface VkpiKolContentMonitoringScheduler {
  task_key?: string;
  configured?: boolean;
  enabled?: boolean | null;
  last_run_at?: string | null;
  last_success_at?: string | null;
  last_error?: string;
}

export interface VkpiKolContentMonitoringResponse {
  status?: "ready" | "enabled" | "resumed" | "already_active" | "paused" | "already_paused" | "not_subscribed" | string;
  kol_pool_id?: number;
  subscription?: VkpiKolContentMonitoringSubscription | null;
  own_subscription?: boolean;
  active_subscription_count?: number;
  read_only?: boolean;
  can_enable_or_pause_own?: boolean;
  scope?: "own" | "target_aggregate" | "none" | string;
  scheduler?: VkpiKolContentMonitoringScheduler;
  /** GET/POST/DELETE never call a provider inside the HTTP request. */
  provider_calls_performed?: boolean;
}

/** Read subscription, scheduler, and last-success truth without enqueueing. */
export async function getMyKolContentMonitoring(token: string, kolPoolId: number | string) {
  return apiFetch<VkpiKolContentMonitoringResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/content-monitoring`,
    {},
    token,
  );
}

/** Enable or resume the caller's subscription; background execution stays separate. */
export async function enableMyKolContentMonitoring(
  token: string,
  kolPoolId: number | string,
  cadenceHours = 24,
) {
  return apiFetch<VkpiKolContentMonitoringResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/content-monitoring`,
    { method: "POST", body: jsonBody({ cadence_hours: cadenceHours }) },
    token,
  );
}

/** Pause the caller's subscription; the server fences any stale in-flight job. */
export async function pauseMyKolContentMonitoring(token: string, kolPoolId: number | string) {
  return apiFetch<VkpiKolContentMonitoringResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/content-monitoring`,
    { method: "DELETE" },
    token,
  );
}
