import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const permission = vi.hoisted(() => ({ manager: true, write: true }));
const listActionInbox = vi.hoisted(() => vi.fn());
const listPendingGtmVerdicts = vi.hoisted(() => vi.fn());
const getOutreachBindingStatus = vi.hoisted(() => vi.fn());
const createOutreachBinding = vi.hoisted(() => vi.fn());
const getOutreachReplyReviewCandidate = vi.hoisted(() => vi.fn());
const verifyOutreachReply = vi.hoisted(() => vi.fn());
const validateOutreachReplyCandidate = vi.hoisted(() => vi.fn());
const validateStoredOutreachReply = vi.hoisted(() => vi.fn());

vi.mock("../../../../hooks/usePermissions", () => ({
  usePermissions: () => ({
    isManager: () => permission.manager,
    hasPermission: (_tab: string, level: string) => level === "write" && permission.write,
  }),
}));

vi.mock("../../../../services/vkpi/actionInbox-api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../../services/vkpi/actionInbox-api")>(),
  listActionInbox: (...args: unknown[]) => listActionInbox(...args),
}));

vi.mock("../../../../services/vkpi/outreach-truth-api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../../services/vkpi/outreach-truth-api")>(),
  listPendingGtmVerdicts: (...args: unknown[]) => listPendingGtmVerdicts(...args),
  getOutreachBindingStatus: (...args: unknown[]) => getOutreachBindingStatus(...args),
  createOutreachBinding: (...args: unknown[]) => createOutreachBinding(...args),
  getOutreachReplyReviewCandidate: (...args: unknown[]) => getOutreachReplyReviewCandidate(...args),
  verifyOutreachReply: (...args: unknown[]) => verifyOutreachReply(...args),
}));

vi.mock("../../../../services/vkpi/outreach-reply-candidate", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../../../services/vkpi/outreach-reply-candidate")>(),
  validateOutreachReplyCandidate: (...args: unknown[]) => validateOutreachReplyCandidate(...args),
  validateStoredOutreachReply: (...args: unknown[]) => validateStoredOutreachReply(...args),
}));

import { OutreachTruthReviewQueue } from "./OutreachTruthReviewQueue";

const H64 = "a".repeat(64);
const action = {
  id: 42,
  dedupe_key: "gtm_bet:plan:kol:17",
  category: "gtm_bet",
  title: "外联 KOL @alice",
  detail: "",
  priority: "medium",
  entity_type: "kol",
  entity_id: "17",
  suggested_endpoint: "",
  estimated_cost_cents: 0,
  writes_business_data: false,
  uses_llm: false,
  requires_approval: true,
  owner_staff_id: null,
  reason: "",
  payload_json: { bet: { action_type: "kol_outreach", review_at: "2026-08-18" } },
  status: "approved",
  created_at: "2026-08-11T08:00:00Z",
  updated_at: "2026-08-11T08:00:00Z",
};

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

const snapshot = {
  schema: "vkpi_action_outreach_reply_review_candidate/v1" as const,
  organization_id: 1 as const,
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
  requested_outcome: "replied" as const,
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
    review_content: {
      body_excerpt: "Hello from Viltrox",
      snippet_excerpt: "hello",
      reviewable_content: true,
      body_sha256: H64,
      snippet_sha256: H64,
      evidence_host: "mail.example",
      evidence_ref_sha256: H64,
      raw_evidence_url_returned: false as const,
    },
  },
  resolved_inbound: {
    message_id: 101,
    captured_at: "2026-08-13T09:00:00.000Z",
    created_at: "2026-08-13T09:00:00.000Z",
    source_class: "email",
    source_is_client_writable: true,
    review_content: {
      body_excerpt: "Thanks, I am interested.",
      snippet_excerpt: "interested",
      reviewable_content: true,
      body_sha256: H64,
      snippet_sha256: H64,
      evidence_host: "mail.example",
      evidence_ref_sha256: H64,
      raw_evidence_url_returned: false as const,
    },
  },
};

const boundStatus = {
  ok: true as const,
  status: "bound_pending_reply_verification" as const,
  binding,
  reply_verification: null,
};

const unboundStatus = {
  ok: true as const,
  status: "unbound" as const,
  bound: false as const,
  bindable: true,
  eligibility_reason: "eligible" as const,
  action_inbox_id: 42,
  binding: null,
  reply_verification: null,
};

const verifiedStatus = {
  ok: true as const,
  status: "reply_verified" as const,
  binding,
  reply_verification: {
    id: 81,
    outcome: "replied" as const,
    verified_at: "2026-08-18T10:01:00.000Z",
    review_candidate_sha256: H64,
    review_candidate: snapshot,
    review_candidate_canonical_json: JSON.stringify(snapshot),
  },
};

function sourceWithAction() {
  listActionInbox.mockImplementation((_token: string, params: { status: string }) => Promise.resolve({
    available: true,
    items: params.status === "approved" ? [action] : [],
  }));
  listPendingGtmVerdicts.mockResolvedValue({ status: "ready", count: 0, due_total: 0, items: [] });
}

beforeEach(() => {
  permission.manager = true;
  permission.write = true;
  listActionInbox.mockReset();
  listPendingGtmVerdicts.mockReset();
  getOutreachBindingStatus.mockReset();
  createOutreachBinding.mockReset();
  getOutreachReplyReviewCandidate.mockReset();
  verifyOutreachReply.mockReset();
  validateOutreachReplyCandidate.mockReset();
  validateStoredOutreachReply.mockReset();
  sourceWithAction();
  getOutreachBindingStatus.mockResolvedValue(boundStatus);
  getOutreachReplyReviewCandidate.mockResolvedValue({ candidate: {}, candidate_sha256: H64 });
  validateOutreachReplyCandidate.mockResolvedValue({
    ok: true,
    snapshot,
    expectedHash: H64,
    candidateObservedAt: snapshot.server_now,
    ttlSeconds: 900,
    canVerify: true,
  });
  validateStoredOutreachReply.mockResolvedValue({
    ok: true,
    snapshot,
    expectedHash: H64,
    candidateObservedAt: snapshot.server_now,
    ttlSeconds: 900,
    canVerify: true,
  });
});

describe("OutreachTruthReviewQueue", () => {
  it("manager + vkpi:write 双闸失败时完全不可达且零 API", () => {
    permission.manager = false;
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    expect(screen.queryByTestId("outreach-truth-review-queue")).not.toBeInTheDocument();
    expect(listActionInbox).not.toHaveBeenCalled();
    expect(getOutreachBindingStatus).not.toHaveBeenCalled();
  });

  it("Action source unavailable 明确报错，不伪装成空队列", async () => {
    listActionInbox.mockResolvedValue({ available: false, items: [], reason: "action_schema_unavailable" });
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    expect(await screen.findByText("action_schema_unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/暂无外联动作/)).not.toBeInTheDocument();
    expect(getOutreachBindingStatus).not.toHaveBeenCalled();
  });

  it("GTM pending source 失败时仍显示 Action 行，同时明确部分不可用", async () => {
    listPendingGtmVerdicts.mockRejectedValue(new Error("pending_source_failed"));
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    expect(await screen.findByText("pending_source_failed")).toBeInTheDocument();
    expect(screen.getByText(/外联 KOL @alice/)).toBeInTheDocument();
    expect(screen.queryByText(/暂无外联动作/)).not.toBeInTheDocument();
  });

  it("显式 200 unbound Action 的网络失败重试复用同一 correlation；body 不含 project/message", async () => {
    getOutreachBindingStatus
      .mockResolvedValueOnce(unboundStatus)
      .mockResolvedValue(boundStatus);
    createOutreachBinding
      .mockRejectedValueOnce(new Error("network_failed"))
      .mockResolvedValueOnce({ ok: true, id: 9, idempotent: true });
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    const button = await screen.findByRole("button", { name: "绑定服务端首条外联" });
    fireEvent.click(button);
    expect(await screen.findByText("network_failed")).toBeInTheDocument();
    fireEvent.click(button);

    await waitFor(() => expect(createOutreachBinding).toHaveBeenCalledTimes(2));
    expect(createOutreachBinding.mock.calls[0][2]).toBe(createOutreachBinding.mock.calls[1][2]);
    expect(createOutreachBinding.mock.calls[0][2]).toMatch(/^outreach-bind-42-/);
    expect(JSON.stringify(createOutreachBinding.mock.calls[0])).not.toMatch(/project_id|message_id/);
    expect(await screen.findByText(/绑定回执 #9.*状态端点复核/)).toBeInTheDocument();
  });

  it("绑定写返回回执但状态仍 unbound 时不宣称闭环", async () => {
    getOutreachBindingStatus.mockResolvedValue(unboundStatus);
    createOutreachBinding.mockResolvedValue({ ok: true, id: 9, idempotent: false });
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    fireEvent.click(await screen.findByRole("button", { name: "绑定服务端首条外联" }));
    expect(await screen.findByText(/写入返回绑定回执 #9.*状态仍未绑定/)).toBeInTheDocument();
    expect(screen.queryByText(/绑定回执 #9.*状态端点复核/)).not.toBeInTheDocument();
  });

  it("不存在 Action 的 404 是错误而不是未绑定正常态", async () => {
    getOutreachBindingStatus.mockRejectedValue({
      status: 404,
      message: "outreach_action_not_found",
    });
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    expect(await screen.findByText("outreach_action_not_found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "绑定服务端首条外联" })).not.toBeInTheDocument();
  });

  it("服务端判定不可绑定时展示原因并保持按钮禁用", async () => {
    getOutreachBindingStatus.mockResolvedValue({
      ...unboundStatus,
      bindable: false,
      eligibility_reason: "outreach_action_approval_proof_invalid",
    });
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    expect(await screen.findByText(/outreach_action_approval_proof_invalid/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "绑定服务端首条外联" })).toBeDisabled();
    expect(createOutreachBinding).not.toHaveBeenCalled();
  });

  it("服务端 bindable 也不能绕过本地 Action 状态闸", async () => {
    const suggested = { ...action, status: "suggested" };
    listActionInbox.mockResolvedValue({ available: true, items: [suggested] });
    getOutreachBindingStatus.mockResolvedValue(unboundStatus);
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    expect(await screen.findByText(/action_status_not_bindable:suggested/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "绑定服务端首条外联" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(createOutreachBinding).not.toHaveBeenCalled();
  });

  it("两步审阅后只提交 exact hash/observedAt，并在网络失败时原样重试 correlation", async () => {
    verifyOutreachReply
      .mockRejectedValueOnce(new Error("response_lost"))
      .mockResolvedValueOnce({ ok: true, id: 81, idempotent: true });
    getOutreachBindingStatus
      .mockResolvedValueOnce(boundStatus)
      .mockResolvedValueOnce(verifiedStatus);
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    expect(await screen.findByText(/绑定 #9/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并签署 replied" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    expect(await screen.findByText("Thanks, I am interested.")).toBeInTheDocument();
    expect(screen.getByText(/首条外联仍严格一致/)).toBeInTheDocument();
    expect(screen.getByText(/只作描述性证据，不进入 validated 学习指标/)).toBeInTheDocument();
    expect(screen.getAllByText(/host: mail.example/)).toHaveLength(2);

    const verifyButton = screen.getByRole("button", { name: "确认并签署 replied" });
    fireEvent.click(verifyButton);
    expect(await screen.findByText("response_lost")).toBeInTheDocument();
    fireEvent.click(verifyButton);
    await waitFor(() => expect(verifyOutreachReply).toHaveBeenCalledTimes(2));

    const firstPayload = verifyOutreachReply.mock.calls[0][2];
    const secondPayload = verifyOutreachReply.mock.calls[1][2];
    expect(firstPayload).toMatchObject({
      outcome: "replied",
      expected_candidate_sha256: H64,
      candidate_observed_at: snapshot.server_now,
    });
    expect(secondPayload.correlation_id).toBe(firstPayload.correlation_id);
    expect(JSON.stringify(firstPayload)).not.toMatch(/project_id|message_id|body_excerpt/);
    expect(await screen.findByText(/已核验 replied · 不可变回执 #81/)).toBeInTheDocument();
    expect(await screen.findByText(/回复核验回执 #81.*已回读确认/)).toBeInTheDocument();
  });

  it("核验写返回回执但状态仍 bound_pending 时不宣称已核验", async () => {
    getOutreachBindingStatus.mockResolvedValue(boundStatus);
    verifyOutreachReply.mockResolvedValue({ ok: true, id: 81, idempotent: false });
    render(<OutreachTruthReviewQueue apiToken="tok" />);

    await screen.findByText(/绑定 #9/);
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    await screen.findByText("Thanks, I am interested.");
    fireEvent.click(screen.getByRole("button", { name: "确认并签署 replied" }));

    expect(await screen.findByText(/写入返回回执 #81.*状态回读未通过/)).toBeInTheDocument();
    expect(screen.queryByText(/回复核验回执 #81.*已回读确认/)).not.toBeInTheDocument();
  });

  it("409/TOCTOU 丢弃旧候选，不静默重试或自动签", async () => {
    verifyOutreachReply.mockRejectedValue({ status: 409, message: "outreach_reply_candidate_changed" });
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    await screen.findByText(/绑定 #9/);
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    await screen.findByText("Thanks, I am interested.");
    fireEvent.click(screen.getByRole("button", { name: "确认并签署 replied" }));

    expect(await screen.findByText(/必须重新获取并人工复核/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并签署 replied" })).toBeDisabled();
    expect(verifyOutreachReply).toHaveBeenCalledTimes(1);
  });

  it("切换 outcome 会作废旧候选，并为新候选生成新 correlation", async () => {
    verifyOutreachReply.mockRejectedValue(new Error("response_lost"));
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    await screen.findByText(/绑定 #9/);
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    await screen.findByText("Thanks, I am interested.");
    fireEvent.click(screen.getByRole("button", { name: "确认并签署 replied" }));
    await waitFor(() => expect(verifyOutreachReply).toHaveBeenCalledTimes(1));
    const repliedCorrelation = verifyOutreachReply.mock.calls[0][2].correlation_id;

    fireEvent.click(screen.getByRole("button", { name: "审阅 no_reply" }));
    expect(screen.queryByText("Thanks, I am interested.")).not.toBeInTheDocument();
    validateOutreachReplyCandidate.mockResolvedValue({
      ok: true,
      snapshot: { ...snapshot, requested_outcome: "no_reply", resolved_inbound: null },
      expectedHash: "b".repeat(64),
      candidateObservedAt: snapshot.server_now,
      ttlSeconds: 900,
      canVerify: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    await screen.findByText(/未解析到入站回复候选/);
    fireEvent.click(screen.getByRole("button", { name: "确认并签署 no_reply" }));
    await waitFor(() => expect(verifyOutreachReply).toHaveBeenCalledTimes(2));
    const noReplyPayload = verifyOutreachReply.mock.calls[1][2];
    expect(noReplyPayload.outcome).toBe("no_reply");
    expect(noReplyPayload.expected_candidate_sha256).toBe("b".repeat(64));
    expect(noReplyPayload.correlation_id).not.toBe(repliedCorrelation);
  });

  it("客户端 TTL 到期立即禁用旧候选，要求重新获取和重审", async () => {
    validateOutreachReplyCandidate.mockResolvedValue({
      ok: true,
      snapshot,
      expectedHash: H64,
      candidateObservedAt: snapshot.server_now,
      ttlSeconds: 0.01,
      canVerify: true,
    });
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    await screen.findByText(/绑定 #9/);
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    await screen.findByText("Thanks, I am interested.");
    expect(await screen.findByText(/候选已超过服务端 TTL/, {}, { timeout: 500 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并签署 replied" })).toBeDisabled();
    expect(verifyOutreachReply).not.toHaveBeenCalled();
  });

  it("服务端 ineligible 候选仅解释原因，不开放签署", async () => {
    validateOutreachReplyCandidate.mockResolvedValue({
      ok: true,
      snapshot: { ...snapshot, eligible: false, eligibility_reason: "observation_window_open", window_closed: false },
      expectedHash: H64,
      candidateObservedAt: snapshot.server_now,
      ttlSeconds: 900,
      canVerify: false,
    });
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    await screen.findByText(/绑定 #9/);
    fireEvent.click(screen.getByRole("button", { name: "获取服务端候选" }));
    expect(await screen.findByText(/观察窗口尚未关闭/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并签署 replied" })).toBeDisabled();
    expect(verifyOutreachReply).not.toHaveBeenCalled();
  });

  it("已核验状态必须通过 stored canonical/hash 校验才能显示回执", async () => {
    getOutreachBindingStatus.mockResolvedValue(verifiedStatus);
    validateStoredOutreachReply.mockResolvedValue({ ok: false, reason: "候选快照：SHA-256 不一致" });
    render(<OutreachTruthReviewQueue apiToken="tok" />);
    expect(await screen.findByText(/已存回执完整性校验失败/)).toBeInTheDocument();
    expect(screen.queryByText(/已核验 replied/)).not.toBeInTheDocument();
  });
});
