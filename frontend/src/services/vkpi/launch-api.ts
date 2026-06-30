import { apiFetch, jsonBody } from "../http";

// N3 Launch 计划:对应后端 backend/app/api/routers/vkpi_launch.py
//   POST /api/admin/vkpi/launch/{project_id}/plan
// 返回 generate_launch_plan(project_id) 的计划 dict(只读,不写库)。

export interface VkpiLaunchKolCandidate {
  rank: number | null;
  kol_entity_uid: string | null;
  kol_pool_id: number | null;
  platform: string | null;
  handle: string | null;
  display_name: string | null;
  country: string | null;
  score: number | null;
  review_required: boolean | null;
}

export interface VkpiLaunchKolCandidates {
  available: boolean;
  reason: string;
  items: VkpiLaunchKolCandidate[];
  summary: Record<string, unknown>;
  target_family_name?: string;
}

export interface VkpiLaunchContentTask {
  task_type: string;
  sequence: number;
  status: string;
  hypothesis: string;
  target_audience: string;
  competitors: string[];
}

export interface VkpiLaunchObservationWindow {
  window_type: string;
  status: string;
  market: string;
  duration_days: number;
}

export interface VkpiLaunchPlanSummary {
  candidate_count: number;
  candidates_available: boolean;
  content_task_count: number;
  observation_window_count: number;
}

export interface VkpiLaunchPlan {
  generated_at: string;
  project_id: number;
  project_type: string;
  product_query: string;
  launch: Record<string, unknown>;
  kol_candidates: VkpiLaunchKolCandidates;
  content_validation_tasks: VkpiLaunchContentTask[];
  observation_windows: VkpiLaunchObservationWindow[];
  summary: VkpiLaunchPlanSummary;
}

export interface GenerateLaunchPlanOptions {
  candidateLimit?: number;
}

export async function generateLaunchPlan(
  token: string,
  projectId: string,
  options: GenerateLaunchPlanOptions = {},
): Promise<VkpiLaunchPlan> {
  const init: RequestInit & { timeoutMs?: number } = { method: "POST", timeoutMs: 120000 };
  if (typeof options.candidateLimit === "number") {
    init.body = jsonBody({ candidate_limit: options.candidateLimit });
  }
  return apiFetch<VkpiLaunchPlan>(
    `/api/admin/vkpi/launch/${encodeURIComponent(projectId)}/plan`,
    init,
    token,
  );
}
