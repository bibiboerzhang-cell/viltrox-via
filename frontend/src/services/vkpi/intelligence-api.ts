import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export async function getOperatingReviewStatus(token: string, limit = 25) {
  return apiFetch<Row>(
    `/api/admin/vkpi/operating-review/status?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function getRecommendationFeedbackBacklog(token: string, limit = 25, runUid = "") {
  const params = new URLSearchParams({ limit: String(limit) });
  if (runUid) params.set("run_uid", runUid);
  return apiFetch<Row>(
    `/api/admin/vkpi/learning/recommendation-feedback-backlog?${params.toString()}`,
    {},
    token,
  );
}

export async function getMemoryFeedbackBacklog(token: string, limit = 25, entityType = "kol") {
  const params = new URLSearchParams({ limit: String(limit), entity_type: entityType });
  return apiFetch<Row>(
    `/api/admin/vkpi/learning/memory-feedback-backlog?${params.toString()}`,
    {},
    token,
  );
}

export async function createMemoryFeedback(token: string, payload: Row) {
  return apiFetch<Row>(
    "/api/admin/vkpi/memory/feedback",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}
