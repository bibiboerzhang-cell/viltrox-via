import { apiFetch, jsonBody } from "../http";

export type OutreachReplyOutcome = "replied" | "no_reply";

export interface OutreachBindingSummary {
  id: number;
  action_inbox_id: number;
  prediction_run_id: string;
  project_id: number;
  kol_pool_id: number;
  product_sku: string;
  channel: string;
  first_outbound_at: string;
  observation_start_at: string;
  observation_end_at: string;
  binding_fingerprint: string;
}

export interface OutreachStoredReplyVerification {
  id: number;
  outcome: OutreachReplyOutcome;
  verified_at: string;
  review_candidate_sha256: string;
  review_candidate: Record<string, unknown>;
  review_candidate_canonical_json: string;
}

export interface OutreachBoundStatusResponse {
  ok: true;
  status: "bound_pending_reply_verification" | "reply_verified";
  binding: OutreachBindingSummary;
  reply_verification: OutreachStoredReplyVerification | null;
}

export interface OutreachUnboundStatusResponse {
  ok: true;
  status: "unbound";
  bound: false;
  bindable: boolean;
  eligibility_reason:
    | "eligible"
    | "outreach_action_not_approved_gtm_bet"
    | "outreach_action_approval_proof_invalid";
  action_inbox_id: number;
  binding: null;
  reply_verification: null;
}

export type OutreachBindingStatusResponse =
  | OutreachBoundStatusResponse
  | OutreachUnboundStatusResponse;

export interface OutreachBindingCreateResponse {
  ok: true;
  id: number;
  action_inbox_id: number;
  project_id: number;
  first_outbound_message_id: number;
  prediction_run_id: string;
  correlation_id: string;
  idempotent: boolean;
}

export interface OutreachReplyCandidateResponse {
  ok: true;
  candidate: Record<string, unknown>;
  candidate_canonical_json: string;
  candidate_sha256: string;
  candidate_observed_at: string;
  candidate_ttl_seconds: number;
}

export interface OutreachReplyVerificationRequest {
  outcome: OutreachReplyOutcome;
  correlation_id: string;
  expected_candidate_sha256: string;
  candidate_observed_at: string;
}

export interface OutreachReplyVerificationResponse {
  ok: true;
  id: number;
  binding_id: number;
  outcome: OutreachReplyOutcome;
  inbound_message_id: number | null;
  correlation_id: string;
  idempotent: boolean;
}

export interface PendingGtmVerdictItem {
  id: number;
  bet_inbox_id: number;
  title: string;
  category: string;
  status: string;
  review_at?: string | null;
  overdue_days?: number;
  bet?: Record<string, unknown>;
  open_outcome_id?: number | null;
}

export interface PendingGtmVerdictsResponse {
  status: "ready" | "empty" | "error" | string;
  items: PendingGtmVerdictItem[];
  count: number;
  due_total?: number;
  malformed_review_at?: number;
  reason?: string;
}

function idPath(value: number): string {
  return encodeURIComponent(String(value));
}

export async function getOutreachBindingStatus(
  token: string,
  actionId: number,
): Promise<OutreachBindingStatusResponse> {
  return apiFetch<OutreachBindingStatusResponse>(
    `/api/admin/vkpi/gtm/actions/${idPath(actionId)}/outreach-binding-status`,
    { cache: "no-store" },
    token,
  );
}

export async function createOutreachBinding(
  token: string,
  actionId: number,
  correlationId: string,
): Promise<OutreachBindingCreateResponse> {
  return apiFetch<OutreachBindingCreateResponse>(
    `/api/admin/vkpi/gtm/actions/${idPath(actionId)}/outreach-binding`,
    {
      method: "POST",
      body: jsonBody({ correlation_id: correlationId }),
      cache: "no-store",
    },
    token,
  );
}

export async function getOutreachReplyReviewCandidate(
  token: string,
  bindingId: number,
  outcome: OutreachReplyOutcome,
): Promise<OutreachReplyCandidateResponse> {
  const query = new URLSearchParams({ outcome });
  return apiFetch<OutreachReplyCandidateResponse>(
    `/api/admin/vkpi/gtm/outreach-bindings/${idPath(bindingId)}/reply-review-candidate?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function verifyOutreachReply(
  token: string,
  bindingId: number,
  payload: OutreachReplyVerificationRequest,
): Promise<OutreachReplyVerificationResponse> {
  return apiFetch<OutreachReplyVerificationResponse>(
    `/api/admin/vkpi/gtm/outreach-bindings/${idPath(bindingId)}/reply-verification`,
    { method: "POST", body: jsonBody(payload), cache: "no-store" },
    token,
  );
}

export async function listPendingGtmVerdicts(
  token: string,
  limit = 200,
): Promise<PendingGtmVerdictsResponse> {
  return apiFetch<PendingGtmVerdictsResponse>(
    `/api/admin/vkpi/gtm/verdicts/pending?limit=${encodeURIComponent(String(limit))}`,
    { cache: "no-store" },
    token,
  );
}

export function outreachApiError(error: unknown): { status: number | null; reason: string } {
  const row = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const status = Number(row.status);
  return {
    status: Number.isInteger(status) && status > 0 ? status : null,
    reason: String(row.message || row.detail || "outreach_truth_unavailable"),
  };
}

export function isOutreachCandidateConflict(error: unknown): boolean {
  const { status, reason } = outreachApiError(error);
  return status === 409 && [
    "outreach_reply_candidate_changed",
    "outreach_reply_already_verified",
    "outreach_reply_exists",
    "outreach_no_reply_window_open",
  ].includes(reason);
}
