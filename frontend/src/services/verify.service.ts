import { apiFetch, jsonBody } from "./http";
import type {
  BasicStatusResponse,
  VerificationListResponse,
  VerificationPreviewResponse,
  VerificationStartResponse,
} from "../types/api";

export function previewVerificationTarget(
  token: string,
  payload: { profile_url?: string; platform?: string; handle?: string },
) {
  return apiFetch<VerificationPreviewResponse>("/api/verify/preview", {
    method: "POST",
    body: jsonBody(payload),
  }, token);
}

export function startVerification(
  token: string,
  payload: { profile_url?: string; platform?: string; handle?: string },
) {
  return apiFetch<VerificationStartResponse>("/api/verify/start", {
    method: "POST",
    body: jsonBody(payload),
  }, token);
}

export function markVerificationPosted(token: string, verificationId: number) {
  return apiFetch<BasicStatusResponse & { verification_id?: number; job_id?: string; scan_status?: string }>(
    "/api/verify/posted",
    {
      method: "POST",
      body: jsonBody({ verification_id: verificationId }),
    },
    token,
  );
}

export async function listMyVerifications(token: string) {
  const response = await apiFetch<VerificationListResponse>("/api/verify/my", {}, token);
  return response.items ?? response.verifications ?? [];
}
