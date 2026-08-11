import { describe, expect, it } from "vitest";

import {
  validateOutreachBindingStatus,
  validateOutreachReplyCandidate,
  validateStoredOutreachReply,
} from "./outreach-reply-candidate";
import type {
  OutreachBindingStatusResponse,
  OutreachReplyCandidateResponse,
} from "./outreach-truth-api";

const H64 = "a".repeat(64);

const binding = {
  id: 9,
  action_inbox_id: 42,
  prediction_run_id: "pred-42",
  project_id: 11,
  kol_pool_id: 17,
  product_sku: "AF-85",
  channel: "youtube",
  first_outbound_at: "2026-08-11T09:00:00.000Z",
  observation_start_at: "2026-08-11T09:00:00.000Z",
  observation_end_at: "2026-08-18T09:00:00.000Z",
  binding_fingerprint: H64,
};

function candidate(outcome: "replied" | "no_reply" = "replied") {
  const review = {
    body_excerpt: outcome === "replied" ? "Thanks, I am interested." : "Hello from Viltrox",
    snippet_excerpt: "reviewable excerpt",
    reviewable_content: true,
    body_sha256: H64,
    snippet_sha256: H64,
    evidence_host: "evidence.example",
    evidence_ref_sha256: H64,
    raw_evidence_url_returned: false,
  };
  return {
    schema: "vkpi_action_outreach_reply_review_candidate/v1",
    organization_id: 1,
    binding_id: 9,
    binding_fingerprint: H64,
    action_inbox_id: 42,
    prediction_run_id: "pred-42",
    project_id: 11,
    kol_pool_id: 17,
    kol_id: 23,
    product_sku: "AF-85",
    channel: "youtube",
    action_approved_at: "2026-08-11T08:00:00.000Z",
    approval_snapshot_sha256: H64,
    observation_start_at: "2026-08-11T09:00:00.000Z",
    observation_end_at: "2026-08-18T09:00:00.000Z",
    requested_outcome: outcome,
    server_now: "2026-08-18T10:00:00.000Z",
    window_closed: true,
    binding_first_outbound_still_exact: true,
    outbound_scope_has_no_invalid_candidates: true,
    eligible: true,
    eligibility_reason: "eligible",
    first_outbound: {
      message_id: 100,
      captured_at: "2026-08-11T09:00:00.000Z",
      created_at: "2026-08-11T09:00:00.000Z",
      evidence_class: "manager_attested_mutable_message_snapshot",
      review_content: { ...review, body_excerpt: "Hello from Viltrox" },
    },
    resolved_inbound: outcome === "replied" ? {
      message_id: 101,
      captured_at: "2026-08-13T09:00:00.000Z",
      created_at: "2026-08-13T09:00:00.000Z",
      source_class: "email",
      source_is_client_writable: true,
      review_content: review,
    } : null,
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function response(outcome: "replied" | "no_reply" = "replied"): Promise<OutreachReplyCandidateResponse> {
  const value = candidate(outcome);
  const canonical = JSON.stringify(value);
  return {
    ok: true,
    candidate: value,
    candidate_canonical_json: canonical,
    candidate_sha256: await sha256(canonical),
    candidate_observed_at: value.server_now,
    candidate_ttl_seconds: 900,
  };
}

describe("outreach candidate integrity", () => {
  it("只从 exact canonical JSON 解析可签署候选，并绑定 Action/Binding/outcome", async () => {
    const result = await validateOutreachReplyCandidate(await response(), {
      actionId: 42,
      binding,
      outcome: "replied",
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.canVerify).toBe(true);
      expect(result.snapshot.first_outbound.message_id).toBe(100);
      expect(result.snapshot.resolved_inbound?.message_id).toBe(101);
      expect(result.expectedHash).toMatch(/^[a-f0-9]{64}$/);
      expect(result.candidateObservedAt).toBe("2026-08-18T10:00:00.000Z");
    }
  });

  it("顶层对象与 canonical 候选不一致时不读取伪造字段", async () => {
    const payload = await response();
    payload.candidate = { ...payload.candidate, project_id: 999 };
    const result = await validateOutreachReplyCandidate(payload, { actionId: 42, binding, outcome: "replied" });
    expect(result).toEqual({ ok: false, reason: "候选对象与 canonical JSON 不一致" });
  });

  it("canonical hash 不一致、Action/outcome 漂移均 fail closed", async () => {
    const badHash = await response();
    badHash.candidate_canonical_json = badHash.candidate_canonical_json.replace("AF-85", "AF-99");
    const hashResult = await validateOutreachReplyCandidate(badHash, { actionId: 42, binding, outcome: "replied" });
    expect(hashResult.ok).toBe(false);
    expect(!hashResult.ok && hashResult.reason).toContain("SHA-256 不一致");

    const wrongOutcome = await validateOutreachReplyCandidate(await response(), {
      actionId: 42,
      binding,
      outcome: "no_reply",
    });
    expect(wrongOutcome.ok).toBe(false);
    expect(!wrongOutcome.ok && wrongOutcome.reason).toContain("requested_outcome 不一致");
  });

  it("递归拦截签名 URL/secret，finding 不把秘密渲染进原因", async () => {
    const value = candidate();
    (value.resolved_inbound!.review_content as Record<string, unknown>).provider_secret = "sk-never-render-this";
    const canonical = JSON.stringify(value);
    const result = await validateOutreachReplyCandidate({
      ok: true,
      candidate: value,
      candidate_canonical_json: canonical,
      candidate_sha256: await sha256(canonical),
      candidate_observed_at: value.server_now,
      candidate_ttl_seconds: 900,
    }, { actionId: 42, binding, outcome: "replied" });
    expect(result).toEqual({ ok: false, reason: "候选包含不可展示字段，已阻断" });
    expect(JSON.stringify(result)).not.toContain("never-render-this");
  });

  it("ineligible 候选可供解释但不能签署；no_reply 必须窗口关闭且无 inbound", async () => {
    const value = candidate("no_reply");
    value.window_closed = false;
    value.eligible = false;
    value.eligibility_reason = "observation_window_open";
    value.server_now = "2026-08-17T10:00:00.000Z";
    const canonical = JSON.stringify(value);
    const result = await validateOutreachReplyCandidate({
      ok: true,
      candidate: value,
      candidate_canonical_json: canonical,
      candidate_sha256: await sha256(canonical),
      candidate_observed_at: value.server_now,
      candidate_ttl_seconds: 900,
    }, { actionId: 42, binding, outcome: "no_reply" });
    expect(result.ok).toBe(true);
    expect(result.ok && result.canVerify).toBe(false);
  });

  it("candidate_observed_at 与 canonical server_now 不一致时拒绝", async () => {
    const payload = await response();
    payload.candidate_observed_at = "2026-08-18T10:00:01.000Z";
    const result = await validateOutreachReplyCandidate(payload, { actionId: 42, binding, outcome: "replied" });
    expect(result.ok).toBe(false);
    expect(!result.ok && result.reason).toContain("canonical server_now 不一致");
  });

  it("首条外联时间与不可变 binding 状态漂移时拒绝", async () => {
    const payload = await response();
    const result = await validateOutreachReplyCandidate(payload, {
      actionId: 42,
      binding: { ...binding, first_outbound_at: "2026-08-11T09:00:01.000Z" },
      outcome: "replied",
    });
    expect(result.ok).toBe(false);
    expect(!result.ok && result.reason).toContain("first_outbound.captured_at 与绑定状态不一致");
  });

  it("绑定状态先锁 action identity；stored receipt 也重新校验 canonical/hash", async () => {
    const payload = await response();
    const status: OutreachBindingStatusResponse = {
      ok: true,
      status: "reply_verified",
      binding,
      reply_verification: {
        id: 80,
        outcome: "replied",
        verified_at: "2026-08-18T10:01:00.000Z",
        review_candidate_sha256: payload.candidate_sha256,
        review_candidate: payload.candidate,
        review_candidate_canonical_json: payload.candidate_canonical_json,
      },
    };
    expect(validateOutreachBindingStatus(status, 42).ok).toBe(true);
    expect(validateOutreachBindingStatus(status, 43).ok).toBe(false);
    const stored = await validateStoredOutreachReply(status.reply_verification!, { actionId: 42, binding });
    expect(stored.ok).toBe(true);

    status.reply_verification!.review_candidate_sha256 = "f".repeat(64);
    const tampered = await validateStoredOutreachReply(status.reply_verification!, { actionId: 42, binding });
    expect(tampered.ok).toBe(false);
  });

  it("只接受与 Action identity 对齐的显式 unbound 状态", () => {
    const status: OutreachBindingStatusResponse = {
      ok: true,
      status: "unbound",
      bound: false,
      bindable: true,
      eligibility_reason: "eligible",
      action_inbox_id: 42,
      binding: null,
      reply_verification: null,
    };
    expect(validateOutreachBindingStatus(status, 42)).toEqual({ ok: true, value: status });
    expect(validateOutreachBindingStatus(status, 43).ok).toBe(false);
    expect(validateOutreachBindingStatus({ ...status, binding: {} } as never, 42).ok).toBe(false);
    const { bound: _bound, ...missingBound } = status;
    expect(validateOutreachBindingStatus(missingBound as never, 42).ok).toBe(false);
    expect(validateOutreachBindingStatus({ ...status, bound: true } as never, 42).ok).toBe(false);
    expect(validateOutreachBindingStatus({
      ...status,
      bindable: false,
      eligibility_reason: "eligible",
    } as never, 42).ok).toBe(false);
  });
});
