import { apiFetch } from "../http";

export interface VkpiMyKolClosureFlow {
  state?: string;
  requires_employee_choice?: boolean;
  requires_human_confirmation_for_detected?: boolean;
  provider_calls_performed?: boolean;
  scheduler?: {
    task_key?: string;
    registered?: boolean;
    enabled?: boolean | null;
    last_run_at?: string | null;
    last_success_at?: string | null;
  };
  auto_enroll_scheduler?: {
    task_key?: string;
    registered?: boolean;
    enabled?: boolean | null;
  };
}

export interface VkpiMyKolClosureReadinessResponse {
  contract?: string;
  status?: "ready" | "attention" | "empty" | string;
  scope?: { staff_scope_id?: number | null; mode?: "own" | "team" | string };
  counts?: {
    kol_count?: number;
    writable_kol_count?: number;
    monitoring_active_kols?: number;
    monitoring_succeeded_kols?: number;
    share_grants?: number;
    candidate_videos?: number;
    trackable_videos?: number;
    tracked_videos?: number;
    measured_tracked_videos?: number;
    legacy_only_tracked_videos?: number;
    sku_linked_tracked_videos?: number;
    sku_manual_videos?: number;
    sku_detected_videos?: number;
    sku_detected_pending_videos?: number;
    sku_confirmed_videos?: number;
    final_v1_ready_videos?: number;
    lens_scanned_videos?: number;
    lens_mention_videos?: number;
  };
  flows?: {
    content_monitoring?: VkpiMyKolClosureFlow;
    sharing?: VkpiMyKolClosureFlow;
    video_tracking?: VkpiMyKolClosureFlow;
    sku_linking?: VkpiMyKolClosureFlow;
    gemini_analysis?: VkpiMyKolClosureFlow;
  };
  blockers?: Array<{
    code?: string;
    count?: number;
    owner?: "employee" | "manager" | "system" | string;
    approval_required?: boolean;
  }>;
  summary?: {
    configured_actions?: number;
    blocker_kinds?: number;
    automatic_changes_performed?: number;
  };
  claim_status?: "descriptive_only" | string;
  generated_at?: string;
}

export async function getMyKolClosureReadiness(
  token: string,
  params: { staffId?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.staffId != null) query.set("staff_id", String(params.staffId));
  const suffix = query.toString();
  return apiFetch<VkpiMyKolClosureReadinessResponse>(
    `/api/admin/vkpi/my-kol/closure-readiness${suffix ? `?${suffix}` : ""}`,
    {},
    token,
  );
}
