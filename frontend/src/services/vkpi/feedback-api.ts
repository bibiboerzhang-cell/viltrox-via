import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface VkpiTeamFeedbackPayload {
  feedbackType?: string;
  severity?: string;
  pagePath?: string;
  title: string;
  detail?: string;
  metadata?: Row;
}

export async function submitTeamFeedback(token: string, payload: VkpiTeamFeedbackPayload) {
  return apiFetch<{ feedback?: Row; ok?: boolean }>(
    "/api/admin/vkpi/feedback",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function listTeamFeedback(token: string, status = "", limit = 100) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  params.set("limit", String(limit));
  return apiFetch<{ feedback?: Row[]; count?: number }>(
    `/api/admin/vkpi/feedback?${params.toString()}`,
    {},
    token,
  );
}

export async function updateTeamFeedbackStatus(token: string, uid: string, status: string) {
  return apiFetch<{ feedback?: Row; ok?: boolean }>(
    `/api/admin/vkpi/feedback/${encodeURIComponent(uid)}`,
    { method: "PATCH", body: jsonBody({ status }) },
    token,
  );
}
