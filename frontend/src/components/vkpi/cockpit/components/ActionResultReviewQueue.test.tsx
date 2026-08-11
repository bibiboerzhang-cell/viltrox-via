import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listActionInbox = vi.hoisted(() => vi.fn());
const getActionReviewCandidate = vi.hoisted(() => vi.fn());
const verifyActionResult = vi.hoisted(() => vi.fn());

vi.mock("../../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...args: unknown[]) => listActionInbox(...args),
  getActionReviewCandidate: (...args: unknown[]) => getActionReviewCandidate(...args),
  verifyActionResult: (...args: unknown[]) => verifyActionResult(...args),
}));

import { ActionResultReviewQueue } from "./ActionResultReviewQueue";

const pending = {
  id: 42,
  category: "kol_profile",
  title: "补全 KOL 真实资料",
  status: "executed",
  updated_at: "2026-08-11T08:00:00Z",
  result_checklist_json: { outcome: "tool completed" },
};

const DETAIL_HASH = "edc15e9e0603b40b1ab118a2256aa462162e387f9304f291e76a5e617dd139a8";
const CANDIDATE_HASH = "6abb819bf6727822155cf5e880539ed994f7a256484000e0b57722cdc2207dda";
const CANDIDATE_CANONICAL = `{"action_id":42,"detail_json":{"after":{"followers":120},"before":{"followers":100},"kol_id":42,"rows_updated":1},"detail_sha256":"${DETAIL_HASH}","endpoint":"internal:kol-profile-update","execution_created_at":"2026-08-11T07:59:00Z","execution_ledger_id":91,"outcome":"success","tool_run_ids":[7],"verification_plan":["核对 KOL 行","核对执行前后值"]}`;
const candidate = {
  action_id: 42,
  execution_ledger_id: 91,
  execution_created_at: "2026-08-11T07:59:00Z",
  endpoint: "internal:kol-profile-update",
  outcome: "success",
  candidate_canonical_json: CANDIDATE_CANONICAL,
  candidate_sha256: CANDIDATE_HASH,
  detail_json_canonical: '{"after":{"followers":120},"before":{"followers":100},"kol_id":42,"rows_updated":1}',
  detail_sha256: DETAIL_HASH,
  tool_run_ids: [7],
  verification_plan: ["核对 KOL 行", "核对执行前后值"],
};

beforeEach(() => {
  listActionInbox.mockReset();
  getActionReviewCandidate.mockReset();
  verifyActionResult.mockReset();
  getActionReviewCandidate.mockResolvedValue(candidate);
  listActionInbox.mockResolvedValue({
    available: true,
    items: [
      pending,
      {
        ...pending,
        id: 43,
        title: "已验收动作",
        result_checklist_json: { human_verification: { decision: "accepted" } },
      },
      { ...pending, id: 44, title: "非执行态建议", status: "suggested" },
    ],
  });
});

describe("ActionResultReviewQueue", () => {
  it("回读后端允许的 200 条 executed，并只展示未形成有效人工结论的动作", async () => {
    render(<ActionResultReviewQueue apiToken="tok" />);

    expect(await screen.findByText(/补全 KOL 真实资料/)).toBeInTheDocument();
    expect(screen.queryByText(/已验收动作/)).not.toBeInTheDocument();
    expect(screen.queryByText(/非执行态建议/)).not.toBeInTheDocument();
    expect(listActionInbox).toHaveBeenCalledWith("tok", { status: "executed", limit: 200 });
    expect(screen.getByRole("button", { name: "待复核 1" })).toBeInTheDocument();
  });

  it("先展示并校验候选回执；有效复核绑定 ledger/hash 后从队列移除", async () => {
    verifyActionResult.mockResolvedValue({
      ok: true,
      action_id: 42,
      decision: "accepted",
      ledger_id: 91,
      tool_run_ids: [7],
      correlation_id: "action-review-42-correlation",
      idempotent: false,
    });
    render(<ActionResultReviewQueue apiToken="tok" />);

    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(getActionReviewCandidate).toHaveBeenCalledWith("tok", 42);
    expect(await screen.findByText("候选执行回执与整份候选/详情指纹一致")).toBeInTheDocument();
    expect(screen.getByText(/internal:kol-profile-update/)).toBeInTheDocument();
    expect(screen.getByText(/核对 KOL 行；核对执行前后值/)).toBeInTheDocument();
    expect(screen.getByText(CANDIDATE_HASH)).toBeInTheDocument();
    expect(screen.getByText(DETAIL_HASH)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "复核通过并记录样本" }));
    expect(screen.getByText(/1–20 条/)).toBeInTheDocument();
    expect(verifyActionResult).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("复核原因"), { target: { value: "项目回执与执行结果一致" } });
    fireEvent.change(screen.getByLabelText("人工依据"), {
      target: { value: "project:VILTROX-42\nreceipt:91" },
    });
    fireEvent.click(screen.getByRole("button", { name: "复核通过并记录样本" }));

    await waitFor(() => expect(verifyActionResult).toHaveBeenCalledTimes(1));
    const [token, actionId, payload] = verifyActionResult.mock.calls[0];
    expect(token).toBe("tok");
    expect(actionId).toBe(42);
    expect(payload).toMatchObject({
      decision: "accepted",
      reason: "项目回执与执行结果一致",
      evidence: [
        { source: "manual", type: "reference", reference: "project:VILTROX-42" },
        { source: "manual", type: "reference", reference: "receipt:91" },
      ],
      expected_candidate_sha256: CANDIDATE_HASH,
      expected_execution_ledger_id: 91,
      expected_detail_sha256: DETAIL_HASH,
    });
    expect(payload.correlation_id).toMatch(/^action-review-42-[A-Za-z0-9.-]+$/);
    expect(await screen.findByText(/动作 #42 已复核通过 · 审计台账 #91/)).toBeInTheDocument();
    expect(screen.queryByText(/补全 KOL 真实资料/)).not.toBeInTheDocument();
  });

  it("验收接口失败时保留动作，明确展示真实错误", async () => {
    verifyActionResult.mockRejectedValue(new Error("successful_execution_receipt_required"));
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    await screen.findByText("候选执行回执与整份候选/详情指纹一致");
    fireEvent.change(screen.getByLabelText("复核原因"), { target: { value: "回执不足" } });
    fireEvent.change(screen.getByLabelText("人工依据"), { target: { value: "receipt:missing" } });
    fireEvent.click(screen.getByRole("button", { name: "驳回并记录复核样本" }));

    expect(await screen.findByText("successful_execution_receipt_required")).toBeInTheDocument();
    expect(screen.getByText(/补全 KOL 真实资料/)).toBeInTheDocument();
  });

  it("后端标记队列不可用时不伪装成零待验", async () => {
    listActionInbox.mockResolvedValue({ available: false, items: [], reason: "action table missing" });
    render(<ActionResultReviewQueue apiToken="tok" />);
    expect(await screen.findByText("action table missing")).toBeInTheDocument();
    expect(screen.queryByText("暂无待人工复核的已执行动作")).not.toBeInTheDocument();
  });

  it("候选回执加载失败时禁用复核，不允许盲审", async () => {
    getActionReviewCandidate.mockRejectedValue(new Error("candidate unavailable"));
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("candidate unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复核通过并记录样本" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "驳回并记录复核样本" })).toBeDisabled();
    expect(verifyActionResult).not.toHaveBeenCalled();
  });

  it("候选详情与服务端 hash 不一致时明确阻断", async () => {
    getActionReviewCandidate.mockResolvedValue({
      ...candidate,
      detail_json_canonical: '{"after":{"followers":120},"before":{"followers":100},"kol_id":42,"rows_updated":2}',
    });
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("详情快照：展示详情与服务端 SHA-256 不一致")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复核通过并记录样本" })).toBeDisabled();
  });

  it("整份候选 canonical hash 不一致时在解析和展示前阻断", async () => {
    getActionReviewCandidate.mockResolvedValue({
      ...candidate,
      candidate_canonical_json: CANDIDATE_CANONICAL.replace("internal:kol-profile-update", "internal:changed-endpoint"),
    });
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("候选快照：展示详情与服务端 SHA-256 不一致")).toBeInTheDocument();
    expect(screen.queryByText(/changed-endpoint/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复核通过并记录样本" })).toBeDisabled();
  });

  it("整份候选内 detail 与独立详情快照不一致时 fail closed", async () => {
    getActionReviewCandidate.mockResolvedValue({
      ...candidate,
      detail_json_canonical: '{"after":{"followers":120},"before":{"followers":100},"kol_id":42,"rows_updated":2}',
      detail_sha256: "71b9ff4247a4bd8eb0fbccd6f3a10e46db164fac93facd115351b0f56dd6f64a",
    });
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("候选详情与独立详情快照不一致")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复核通过并记录样本" })).toBeDisabled();
  });

  it("展示只读取已绑定 canonical 候选，不读取未绑定顶层重复字段", async () => {
    getActionReviewCandidate.mockResolvedValue({
      ...candidate,
      endpoint: "https://raw.example.test/run?token=never-show-top-level",
      verification_plan: ["never-show-unbound-plan"],
    });
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("候选执行回执与整份候选/详情指纹一致")).toBeInTheDocument();
    expect(screen.getByText(/internal:kol-profile-update/)).toBeInTheDocument();
    expect(screen.queryByText(/never-show-top-level|never-show-unbound-plan/)).not.toBeInTheDocument();
  });

  it("候选含签名 URL query 或 Provider 秘密时不渲染并禁止复核", async () => {
    getActionReviewCandidate.mockResolvedValue({
      ...candidate,
      detail_json_canonical: '{"provider_secret":"sk-never-show-this-secret","rows_updated":1,"signed_url":"https://provider.example.test/file?token=never-show-this"}',
      detail_sha256: "aa30bf1b9601781c5801d71ed50c9f227cf768517062cde2fb234eb04ee9bcf7",
      candidate_canonical_json: '{"action_id":42,"detail_json":{"provider_secret":"sk-never-show-this-secret","rows_updated":1,"signed_url":"https://provider.example.test/file?token=never-show-this"},"detail_sha256":"aa30bf1b9601781c5801d71ed50c9f227cf768517062cde2fb234eb04ee9bcf7","endpoint":"internal:kol-profile-update","execution_created_at":"2026-08-11T07:59:00Z","execution_ledger_id":91,"outcome":"success","tool_run_ids":[7],"verification_plan":["核对 KOL 行","核对执行前后值"]}',
      candidate_sha256: "2e5ef5303ada732693ba4671d5656524c5bdb351fb69f3a8a7d8c308b0812073",
    });
    render(<ActionResultReviewQueue apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /补全 KOL 真实资料/ }));
    expect(await screen.findByText("候选回执包含不可展示字段，已阻断")).toBeInTheDocument();
    expect(screen.queryByText(/never-show-this/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复核通过并记录样本" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "驳回并记录复核样本" })).toBeDisabled();
  });
});
