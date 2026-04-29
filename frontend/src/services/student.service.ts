import { apiFetch, jsonBody } from "./http";
import type {
  BasicStatusResponse,
  CreatorPublicClickPayload,
  CreatorPublicPageData,
  PublicVidProfileResponse,
  PublicVidShareCardResponse,
  StudentClaimMetadataResponse,
  StudentPassResponse,
  StudentSignupResponse,
} from "../types/api";

export function fetchStudentClaim(qrId: string, claimToken: string, signature: string) {
  return apiFetch<StudentClaimMetadataResponse>(
    `/api/student/claim/${encodeURIComponent(qrId)}?claim=${encodeURIComponent(claimToken)}&sig=${encodeURIComponent(signature)}`,
  );
}

export function signupStudent(payload: {
  qr_id: string;
  claim_token: string;
  signature: string;
  student_id: string;
  email: string;
  password: string;
  name?: string;
  major?: string;
  year?: string;
}) {
  return apiFetch<StudentSignupResponse>("/api/student/signup", {
    method: "POST",
    body: jsonBody(payload),
  });
}

export function fetchPublicVidProfile(vid: string) {
  return apiFetch<PublicVidProfileResponse>(`/api/public/vid/${encodeURIComponent(vid)}`);
}

export function fetchCreatorPublicPageData(vid: string) {
  return apiFetch<CreatorPublicPageData>(`/api/creator-public/${encodeURIComponent(vid)}`);
}

export function trackCreatorPublicClick(payload: CreatorPublicClickPayload) {
  return apiFetch<BasicStatusResponse>("/api/creator-public/click", {
    method: "POST",
    body: jsonBody(payload),
    timeoutMs: 5000,
  });
}

export function fetchPublicVidShareCard(vid: string) {
  return apiFetch<PublicVidShareCardResponse>(`/api/public/vid/${encodeURIComponent(vid)}/share-card`, {
    timeoutMs: 30000,
  });
}

export function requestAppleWalletPass(vid: string) {
  return apiFetch<BasicStatusResponse>(`/api/public/vid/${encodeURIComponent(vid)}/apple-wallet`);
}

export function fetchStudentPass(token: string) {
  return apiFetch<StudentPassResponse>("/api/student/pass", {}, token);
}
