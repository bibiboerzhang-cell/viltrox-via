import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
const jsonBody = (payload: unknown) => JSON.stringify(payload);

vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => jsonBody(payload),
}));

import {
  createOutreachBinding,
  getOutreachBindingStatus,
  getOutreachReplyReviewCandidate,
  isOutreachBindingMissing,
  isOutreachCandidateConflict,
  listPendingGtmVerdicts,
  verifyOutreachReply,
} from "./outreach-truth-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ ok: true });
});

describe("outreach truth API contract", () => {
  it("按 Action 回读绑定状态", async () => {
    await getOutreachBindingStatus("tok", 42);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/gtm/actions/42/outreach-binding-status",
      { cache: "no-store" },
      "tok",
    );
  });

  it("绑定只提交稳定 correlation，不允许客户端选择 project/message/provider", async () => {
    await createOutreachBinding("tok", 42, "outreach-bind-42-fixed");
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/gtm/actions/42/outreach-binding");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual({ correlation_id: "outreach-bind-42-fixed" });
    expect(init.body).not.toMatch(/project_id|message_id|provider|handler/);
    expect(token).toBe("tok");
  });

  it("候选 GET 只带 outcome，并对路径和 query 编码", async () => {
    await getOutreachReplyReviewCandidate("tok", 9, "no_reply");
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/gtm/outreach-bindings/9/reply-review-candidate?outcome=no_reply",
      { cache: "no-store" },
      "tok",
    );
  });

  it("核验只提交 exact candidate hash/observedAt/correlation/outcome", async () => {
    const payload = {
      outcome: "replied" as const,
      correlation_id: "outreach-reply-9-fixed",
      expected_candidate_sha256: "a".repeat(64),
      candidate_observed_at: "2026-08-11T08:00:00+00:00",
    };
    await verifyOutreachReply("tok", 9, payload);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/gtm/outreach-bindings/9/reply-verification");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual(payload);
    expect(init.body).not.toMatch(/project_id|message_id|actual_value|metadata/);
    expect(token).toBe("tok");
  });

  it("有界读取 GTM pending source", async () => {
    await listPendingGtmVerdicts("tok", 200);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/gtm/verdicts/pending?limit=200",
      { cache: "no-store" },
      "tok",
    );
  });

  it("严格区分 legitimate unbound 与其他 404/409", () => {
    expect(isOutreachBindingMissing({ status: 404, message: "outreach_binding_not_found" })).toBe(true);
    expect(isOutreachBindingMissing({ status: 404, message: "outreach_action_not_found" })).toBe(false);
    expect(isOutreachCandidateConflict({ status: 409, message: "outreach_reply_candidate_changed" })).toBe(true);
    expect(isOutreachCandidateConflict({ status: 409, message: "outreach_reply_correlation_conflict" })).toBe(false);
  });
});
