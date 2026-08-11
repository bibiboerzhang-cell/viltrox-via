import type {
  OutreachBindingSummary,
  OutreachBindingStatusResponse,
  OutreachReplyCandidateResponse,
  OutreachReplyOutcome,
  OutreachStoredReplyVerification,
} from "./outreach-truth-api";
import {
  normalizeSha256,
  parseReviewJsonSnapshot,
  reviewHashReasonLabel,
  reviewJsonValuesEqual,
  reviewSnapshotSafetyFindings,
  verifyReviewJsonStringHash,
} from "./review-integrity";

type JsonRecord = Record<string, unknown>;

export interface OutreachReviewContent {
  body_excerpt: string;
  snippet_excerpt: string;
  reviewable_content: boolean;
  body_sha256: string;
  snippet_sha256: string;
  evidence_host: string | null;
  evidence_ref_sha256: string;
  raw_evidence_url_returned: false;
}

export interface OutreachReviewMessage {
  message_id: number;
  captured_at: string;
  created_at: string;
  evidence_class?: string;
  source_class?: string;
  source_is_client_writable?: boolean;
  review_content: OutreachReviewContent;
}

export interface OutreachReplyReviewSnapshot extends JsonRecord {
  schema: "vkpi_action_outreach_reply_review_candidate/v1";
  organization_id: 1;
  binding_id: number;
  binding_fingerprint: string;
  action_inbox_id: number;
  prediction_run_id: string;
  project_id: number;
  kol_pool_id: number;
  kol_id: number;
  product_sku: string;
  channel: string;
  action_approved_at: string;
  approval_snapshot_sha256: string;
  observation_start_at: string;
  observation_end_at: string;
  requested_outcome: OutreachReplyOutcome;
  server_now: string;
  window_closed: boolean;
  binding_first_outbound_still_exact: boolean;
  outbound_scope_has_no_invalid_candidates: boolean;
  eligible: boolean;
  eligibility_reason: string;
  first_outbound: OutreachReviewMessage;
  resolved_inbound: OutreachReviewMessage | null;
}

export type OutreachCandidateValidation =
  | {
      ok: true;
      snapshot: OutreachReplyReviewSnapshot;
      expectedHash: string;
      candidateObservedAt: string;
      ttlSeconds: number;
      canVerify: boolean;
    }
  | { ok: false; reason: string };

export type OutreachBindingStatusValidation =
  | { ok: true; value: OutreachBindingStatusResponse }
  | { ok: false; reason: string };

function record(value: unknown): JsonRecord | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function positiveInt(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}

function validDate(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && Number.isFinite(Date.parse(value));
}

function validText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function validateReviewContent(value: unknown, label: string): string[] {
  const item = record(value);
  if (!item) return [`${label}内容形状无效`];
  const problems: string[] = [];
  for (const key of ["body_excerpt", "snippet_excerpt"]) {
    if (typeof item[key] !== "string") problems.push(`${label}.${key} 缺失`);
  }
  if (typeof item.reviewable_content !== "boolean") problems.push(`${label}.reviewable_content 缺失`);
  for (const key of ["body_sha256", "snippet_sha256", "evidence_ref_sha256"]) {
    if (!normalizeSha256(item[key])) problems.push(`${label}.${key} 无效`);
  }
  if (item.evidence_host !== null && typeof item.evidence_host !== "string") {
    problems.push(`${label}.evidence_host 无效`);
  }
  if (item.raw_evidence_url_returned !== false) problems.push(`${label}包含原始 evidence URL`);
  return problems;
}

function validateMessage(value: unknown, label: string): string[] {
  const item = record(value);
  if (!item) return [`${label}形状无效`];
  const problems: string[] = [];
  if (!positiveInt(item.message_id)) problems.push(`${label}.message_id 无效`);
  if (!validDate(item.captured_at)) problems.push(`${label}.captured_at 无效`);
  if (!validDate(item.created_at)) problems.push(`${label}.created_at 无效`);
  problems.push(...validateReviewContent(item.review_content, `${label}.review_content`));
  return problems;
}

function bindingProblems(snapshot: JsonRecord, binding: OutreachBindingSummary): string[] {
  const checks: Array<[unknown, unknown, string]> = [
    [snapshot.binding_id, binding.id, "binding_id"],
    [snapshot.action_inbox_id, binding.action_inbox_id, "action_inbox_id"],
    [snapshot.prediction_run_id, binding.prediction_run_id, "prediction_run_id"],
    [snapshot.project_id, binding.project_id, "project_id"],
    [snapshot.kol_pool_id, binding.kol_pool_id, "kol_pool_id"],
    [snapshot.product_sku, binding.product_sku, "product_sku"],
    [String(snapshot.channel || "").toLowerCase(), String(binding.channel || "").toLowerCase(), "channel"],
    [snapshot.observation_start_at, binding.observation_start_at, "observation_start_at"],
    [snapshot.observation_end_at, binding.observation_end_at, "observation_end_at"],
    [snapshot.binding_fingerprint, binding.binding_fingerprint, "binding_fingerprint"],
  ];
  const problems = checks.filter(([left, right]) => left !== right).map(([, , key]) => `${key} 与绑定状态不一致`);
  const firstOutbound = record(snapshot.first_outbound);
  if (firstOutbound?.captured_at !== binding.first_outbound_at) {
    problems.push("first_outbound.captured_at 与绑定状态不一致");
  }
  return problems;
}

export function validateOutreachBindingStatus(
  value: OutreachBindingStatusResponse,
  actionId: number,
): OutreachBindingStatusValidation {
  const root = record(value);
  if (!root) return { ok: false, reason: "绑定状态形状无效" };
  if (root.status === "unbound") {
    const problems: string[] = [];
    if (root.ok !== true) problems.push("ok 不为 true");
    if (root.bound !== false) problems.push("bound 不为 false");
    if (typeof root.bindable !== "boolean") problems.push("bindable 缺失");
    const eligibilityReason = String(root.eligibility_reason || "");
    if (!["eligible", "outreach_action_not_approved_gtm_bet", "outreach_action_approval_proof_invalid"].includes(eligibilityReason)) {
      problems.push("eligibility_reason 无效");
    }
    if ((root.bindable === true) !== (eligibilityReason === "eligible")) {
      problems.push("bindable 与 eligibility_reason 不一致");
    }
    if (positiveInt(root.action_inbox_id) !== actionId) problems.push("action_inbox_id 不一致");
    if (root.binding !== null) problems.push("unbound 状态包含 binding");
    if (root.reply_verification !== null) problems.push("unbound 状态包含 reply_verification");
    if (problems.length > 0) {
      return { ok: false, reason: `未绑定状态不可用：${problems.join("；")}` };
    }
    return { ok: true, value };
  }
  const binding = record(root?.binding);
  if (!binding) return { ok: false, reason: "绑定状态形状无效" };
  const problems: string[] = [];
  if (root.ok !== true) problems.push("ok 不为 true");
  if (!["bound_pending_reply_verification", "reply_verified"].includes(String(root.status || ""))) {
    problems.push("status 无效");
  }
  if (positiveInt(binding.id) == null) problems.push("binding.id 无效");
  if (positiveInt(binding.action_inbox_id) !== actionId) problems.push("action_inbox_id 不一致");
  for (const key of ["project_id", "kol_pool_id"] as const) {
    if (!positiveInt(binding[key])) problems.push(`${key} 无效`);
  }
  for (const key of ["prediction_run_id", "product_sku", "channel"] as const) {
    if (!validText(binding[key])) problems.push(`${key} 缺失`);
  }
  for (const key of ["first_outbound_at", "observation_start_at", "observation_end_at"] as const) {
    if (!validDate(binding[key])) problems.push(`${key} 无效`);
  }
  if (!normalizeSha256(binding.binding_fingerprint)) problems.push("binding_fingerprint 无效");
  const verified = root.status === "reply_verified";
  if (verified !== (record(root.reply_verification) !== null)) {
    problems.push("status 与 reply_verification 不一致");
  }
  if (problems.length > 0) return { ok: false, reason: `绑定状态不可用：${problems.join("；")}` };
  return { ok: true, value };
}

async function validateCandidate(
  candidate: unknown,
  canonicalJson: unknown,
  hash: unknown,
  context: {
    actionId: number;
    binding: OutreachBindingSummary;
    outcome: OutreachReplyOutcome;
    observedAt: string;
    ttlSeconds: number;
  },
): Promise<OutreachCandidateValidation> {
  const expectedHash = normalizeSha256(hash);
  if (!expectedHash) return { ok: false, reason: "候选 SHA-256 无效" };
  const hashCheck = await verifyReviewJsonStringHash(canonicalJson, expectedHash);
  if (!hashCheck.valid) return { ok: false, reason: `候选快照：${reviewHashReasonLabel(hashCheck.reason)}` };
  const parsed = parseReviewJsonSnapshot(canonicalJson);
  if (!parsed.ok) return { ok: false, reason: parsed.reason };
  const snapshot = record(parsed.value);
  if (!snapshot) return { ok: false, reason: "canonical JSON 候选形状无效" };
  if (!reviewJsonValuesEqual(snapshot, candidate)) {
    return { ok: false, reason: "候选对象与 canonical JSON 不一致" };
  }
  if (reviewSnapshotSafetyFindings(snapshot).length > 0) {
    return { ok: false, reason: "候选包含不可展示字段，已阻断" };
  }

  const problems: string[] = [];
  if (snapshot.schema !== "vkpi_action_outreach_reply_review_candidate/v1") problems.push("schema 无效");
  if (snapshot.organization_id !== 1) problems.push("organization_id 不是 1");
  if (positiveInt(snapshot.action_inbox_id) !== context.actionId) problems.push("action_inbox_id 不一致");
  if (positiveInt(snapshot.binding_id) !== context.binding.id) problems.push("binding_id 不一致");
  if (snapshot.requested_outcome !== context.outcome) problems.push("requested_outcome 不一致");
  for (const key of ["prediction_run_id", "product_sku", "channel", "binding_fingerprint"] as const) {
    if (!validText(snapshot[key])) problems.push(`${key} 缺失`);
  }
  for (const key of ["project_id", "kol_pool_id", "kol_id"] as const) {
    if (!positiveInt(snapshot[key])) problems.push(`${key} 无效`);
  }
  for (const key of ["action_approved_at", "observation_start_at", "observation_end_at", "server_now"] as const) {
    if (!validDate(snapshot[key])) problems.push(`${key} 无效`);
  }
  if (!normalizeSha256(snapshot.approval_snapshot_sha256)) problems.push("approval_snapshot_sha256 无效");
  for (const key of ["window_closed", "binding_first_outbound_still_exact", "outbound_scope_has_no_invalid_candidates", "eligible"] as const) {
    if (typeof snapshot[key] !== "boolean") problems.push(`${key} 缺失`);
  }
  if (!validText(snapshot.eligibility_reason)) problems.push("eligibility_reason 缺失");
  problems.push(...validateMessage(snapshot.first_outbound, "first_outbound"));
  if (snapshot.resolved_inbound !== null) problems.push(...validateMessage(snapshot.resolved_inbound, "resolved_inbound"));
  problems.push(...bindingProblems(snapshot, context.binding));
  if (!validDate(context.observedAt)) problems.push("candidate_observed_at 无效");
  if (snapshot.server_now !== context.observedAt) problems.push("candidate_observed_at 与 canonical server_now 不一致");
  if (!Number.isInteger(context.ttlSeconds) || context.ttlSeconds <= 0 || context.ttlSeconds > 3600) {
    problems.push("candidate_ttl_seconds 无效");
  }
  if (
    validDate(snapshot.observation_start_at)
    && validDate(snapshot.observation_end_at)
    && Date.parse(snapshot.observation_start_at) > Date.parse(snapshot.observation_end_at)
  ) {
    problems.push("观察窗起止顺序无效");
  }
  if (validDate(snapshot.server_now) && validDate(snapshot.observation_end_at)) {
    const derivedClosed = Date.parse(snapshot.server_now) >= Date.parse(snapshot.observation_end_at);
    if (snapshot.window_closed !== derivedClosed) problems.push("window_closed 与 canonical 时间不一致");
  }
  if (problems.length > 0) return { ok: false, reason: `候选不可复核：${problems.join("；")}` };

  const typed = snapshot as OutreachReplyReviewSnapshot;
  const inboundMatches = context.outcome === "replied"
    ? typed.resolved_inbound !== null && typed.resolved_inbound.review_content.reviewable_content === true
    : typed.resolved_inbound === null;
  const canVerify = Boolean(
    typed.eligible
    && typed.eligibility_reason === "eligible"
    && typed.window_closed
    && typed.binding_first_outbound_still_exact
    && typed.outbound_scope_has_no_invalid_candidates
    && typed.first_outbound.review_content.reviewable_content
    && inboundMatches
  );
  return {
    ok: true,
    snapshot: typed,
    expectedHash,
    candidateObservedAt: context.observedAt,
    ttlSeconds: context.ttlSeconds,
    canVerify,
  };
}

export async function validateOutreachReplyCandidate(
  response: OutreachReplyCandidateResponse,
  context: { actionId: number; binding: OutreachBindingSummary; outcome: OutreachReplyOutcome },
): Promise<OutreachCandidateValidation> {
  if (!response || response.ok !== true) return { ok: false, reason: "候选响应形状无效" };
  return validateCandidate(
    response.candidate,
    response.candidate_canonical_json,
    response.candidate_sha256,
    {
      ...context,
      observedAt: response.candidate_observed_at,
      ttlSeconds: Number(response.candidate_ttl_seconds),
    },
  );
}

export async function validateStoredOutreachReply(
  receipt: OutreachStoredReplyVerification,
  context: { actionId: number; binding: OutreachBindingSummary },
): Promise<OutreachCandidateValidation> {
  if (
    !positiveInt(receipt?.id)
    || !["replied", "no_reply"].includes(String(receipt?.outcome || ""))
    || !validDate(receipt?.verified_at)
  ) {
    return { ok: false, reason: "已存回复回执形状无效" };
  }
  const candidate = record(receipt.review_candidate);
  const outcome = receipt.outcome;
  const observedAt = typeof candidate?.server_now === "string" ? candidate.server_now : "";
  return validateCandidate(
    receipt.review_candidate,
    receipt.review_candidate_canonical_json,
    receipt.review_candidate_sha256,
    { ...context, outcome, observedAt, ttlSeconds: 900 },
  );
}
