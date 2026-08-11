import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listSkills = vi.hoisted(() => vi.fn());
const listSkillRuns = vi.hoisted(() => vi.fn());
const getSkillReviewCandidate = vi.hoisted(() => vi.fn());
const reviewSkillRun = vi.hoisted(() => vi.fn());
const runSkill = vi.hoisted(() => vi.fn());

vi.mock("../../../services/vkpi/skills-api", () => ({
  listSkills: (...args: unknown[]) => listSkills(...args),
  listSkillRuns: (...args: unknown[]) => listSkillRuns(...args),
  getSkillReviewCandidate: (...args: unknown[]) => getSkillReviewCandidate(...args),
  reviewSkillRun: (...args: unknown[]) => reviewSkillRun(...args),
  runSkill: (...args: unknown[]) => runSkill(...args),
}));

import { SkillStudioPage } from "./SkillStudioPage";

const pendingRun = {
  id: 73,
  skill_name: "creator_match",
  output_sha256: "f1336d64068e1ba4252d4870b74ef29b7f522db7aa04f0dd01e5644191619128",
  review_candidate_available: true,
  cost_cents: 0,
  latency_ms: 12,
  accepted: null,
  human_score: null,
  business_result: null,
  created_at: "2026-08-11T08:00:00Z",
};

const reviewCandidate = {
  run_id: 73,
  skill_name: "creator_match",
  input_snapshot_json: '{"limit":30,"market":"US","product":"AF 16mm F1.8"}',
  input_sha256: "62a6c0a3ce8f1db9d87934eb31d66f504ad18a9b5fdcf2a12ce8f09bb2918cd7",
  output_snapshot_json: '{"ranked_ids":[11,12,13],"status":"ok","summary":"找到 3 位垂直创作者"}',
  output_summary: "summary: 找到 3 位垂直创作者",
  output_sha256: pendingRun.output_sha256,
  model_used: "gpt-5.2",
  prompt_version: "creator-match-v3",
  redacted: true,
};

beforeEach(() => {
  listSkills.mockReset();
  listSkillRuns.mockReset();
  getSkillReviewCandidate.mockReset();
  reviewSkillRun.mockReset();
  runSkill.mockReset();
  listSkills.mockResolvedValue([
    { skill_name: "creator_match", version: "v1", runs: 3, reviewed_runs: 1, acceptance_rate: 1 },
  ]);
  listSkillRuns.mockResolvedValue([pendingRun]);
  getSkillReviewCandidate.mockResolvedValue(reviewCandidate);
});

describe("SkillStudioPage human review", () => {
  it("员工视角不调管理员运行与复核接口", async () => {
    render(<SkillStudioPage apiToken="tok" viewMode="employee" />);
    expect(await screen.findByText(/仅对管理视角开放/)).toBeInTheDocument();
    expect(listSkills).not.toHaveBeenCalled();
    expect(listSkillRuns).not.toHaveBeenCalled();
    expect(runSkill).not.toHaveBeenCalled();
    expect(getSkillReviewCandidate).not.toHaveBeenCalled();
  });

  it("展示输入/输出/版本并校验 hash 后，才允许写人工复核样本", async () => {
    reviewSkillRun.mockResolvedValue({
      ok: true,
      run_id: 73,
      event_id: 501,
      accepted: true,
      human_score: 4.5,
      idempotent: false,
    });
    render(<SkillStudioPage apiToken="tok" />);

    expect(await screen.findByRole("button", { name: "查看并复核" })).toBeInTheDocument();
    expect(listSkillRuns).toHaveBeenCalledWith("tok", "creator_match", 100, "pending");
    fireEvent.click(screen.getByRole("button", { name: "查看并复核" }));
    expect(getSkillReviewCandidate).toHaveBeenCalledWith("tok", 73);
    expect(await screen.findByText("脱敏复核候选与输入/输出指纹一致")).toBeInTheDocument();
    expect(screen.getByText("summary: 找到 3 位垂直创作者")).toBeInTheDocument();
    expect(screen.getByText("完整脱敏输出 JSON")).toBeInTheDocument();
    expect(screen.getByText("creator-match-v3")).toBeInTheDocument();
    expect(screen.getByText(reviewCandidate.input_sha256)).toBeInTheDocument();
    expect(screen.getByText(pendingRun.output_sha256)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "采纳并记录人工复核样本" }));
    expect(screen.getByText(/1–20 条/)).toBeInTheDocument();
    expect(reviewSkillRun).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/人工评分 0–5/), { target: { value: "4.5" } });
    fireEvent.change(screen.getByLabelText("人工复核结论"), { target: { value: "进入 shortlist，结果与输出一致" } });
    fireEvent.change(screen.getByLabelText(/人工依据（每行一条/), {
      target: { value: "project:VILTROX-42\nreceipt:shortlist-73" },
    });
    fireEvent.click(screen.getByRole("button", { name: "采纳并记录人工复核样本" }));

    await waitFor(() => expect(reviewSkillRun).toHaveBeenCalledTimes(1));
    const [token, runId, payload] = reviewSkillRun.mock.calls[0];
    expect(token).toBe("tok");
    expect(runId).toBe(73);
    expect(payload).toMatchObject({
      accepted: true,
      human_score: 4.5,
      business_result: "进入 shortlist，结果与输出一致",
      evidence: [
        { source: "manual", type: "reference", reference: "project:VILTROX-42" },
        { source: "manual", type: "reference", reference: "receipt:shortlist-73" },
      ],
      expected_input_sha256: reviewCandidate.input_sha256,
      expected_output_sha256: reviewCandidate.output_sha256,
    });
    expect(payload.correlation_id).toMatch(/^skill-review-73-[A-Za-z0-9.-]+$/);
    expect(await screen.findByText(/已人工复核 #73 · 采纳 · 事件 #501/)).toBeInTheDocument();
  });

  it("切换已评时由后端筛选，不在前端当前页假过滤", async () => {
    render(<SkillStudioPage apiToken="tok" />);
    await screen.findByRole("button", { name: "查看并复核" });
    fireEvent.click(screen.getByRole("button", { name: "已评" }));
    await waitFor(() => {
      expect(listSkillRuns).toHaveBeenCalledWith("tok", "creator_match", 100, "reviewed");
    });
  });

  it("runs 端点失败明确标成不可读，不把故障伪装成暂无记录", async () => {
    listSkillRuns.mockRejectedValue(new Error("skills database offline"));
    render(<SkillStudioPage apiToken="tok" />);
    expect(await screen.findByText(/运行记录加载失败，不等于暂无记录/)).toBeInTheDocument();
    expect(screen.getByText(/skills database offline/)).toBeInTheDocument();
    expect(screen.queryByText("暂无运行记录")).not.toBeInTheDocument();
  });

  it("候选详情或版本缺失时不渲染详情，复核按钮保持禁用", async () => {
    getSkillReviewCandidate.mockResolvedValue({ ...reviewCandidate, prompt_version: null });
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText(/复核候选不可用：提示版本/)).toBeInTheDocument();
    expect(screen.queryByText("完整脱敏输出 JSON")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采纳并记录人工复核样本" })).toBeDisabled();
    expect(reviewSkillRun).not.toHaveBeenCalled();
  });

  it("候选详情齐全但服务端 hash 非法时仍禁止复核", async () => {
    getSkillReviewCandidate.mockResolvedValue({ ...reviewCandidate, output_sha256: "bad-hash" });
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText(/复核候选不可用：输出 SHA-256/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采纳并记录人工复核样本" })).toBeDisabled();
    expect(reviewSkillRun).not.toHaveBeenCalled();
  });

  it("输入 canonical JSON 与 input hash 不一致时禁止展示和复核", async () => {
    getSkillReviewCandidate.mockResolvedValue({
      ...reviewCandidate,
      input_snapshot_json: '{"limit":99,"market":"US","product":"AF 16mm F1.8"}',
    });
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText("输入快照：展示详情与服务端 SHA-256 不一致")).toBeInTheDocument();
    expect(screen.queryByText("完整脱敏输出 JSON")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采纳并记录人工复核样本" })).toBeDisabled();
  });

  it("展示只读取 hash 绑定 canonical 字符串，不读取候选的未绑定原始对象", async () => {
    getSkillReviewCandidate.mockResolvedValue({
      ...reviewCandidate,
      input_snapshot: { provider_secret: "never-show-unbound-input" },
      output_snapshot: { summary: "never-show-unbound-output" },
    });
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText("脱敏复核候选与输入/输出指纹一致")).toBeInTheDocument();
    expect(screen.queryByText(/never-show-unbound/)).not.toBeInTheDocument();
  });

  it("manager-only 候选加载失败时明确失败并禁止盲审", async () => {
    getSkillReviewCandidate.mockRejectedValue(new Error("candidate forbidden"));
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText("candidate forbidden")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采纳并记录人工复核样本" })).toBeDisabled();
  });

  it("候选含签名 query 或 Provider 秘密时不写入 DOM 且禁止复核", async () => {
    getSkillReviewCandidate.mockResolvedValue({
      ...reviewCandidate,
      input_snapshot_json: JSON.stringify({
        preview_url: "https://cdn.example.test/file?X-Amz-Signature=never-show-this",
        provider_secret: "sk-never-show-this-secret",
      }),
      input_sha256: "70d28a7299a04ec80949904ebea2fad077939eb2752baf10aab90a36062ec809",
    });
    render(<SkillStudioPage apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看并复核" }));
    expect(await screen.findByText("复核候选包含不可展示字段，已阻断")).toBeInTheDocument();
    expect(screen.queryByText(/never-show-this/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采纳并记录人工复核样本" })).toBeDisabled();
  });
});
