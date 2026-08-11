import { describe, it, expect, vi, beforeEach } from "vitest";

// skills-api 单测:listSkills/listSkillRuns/runSkill 的入参拼装 + 形状归一(裸数组 vs 包壳)。
const apiFetch = vi.fn();
const jsonBody = (payload: unknown) => JSON.stringify(payload);
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { getSkillReviewCandidate, listSkills, listSkillRuns, reviewSkillRun, runSkill } from "./skills-api";

beforeEach(() => {
  apiFetch.mockReset();
});

describe("listSkills", () => {
  it("吃裸数组", async () => {
    apiFetch.mockResolvedValue([{ skill_name: "creator_match" }]);
    const r = await listSkills("tok");
    expect(r).toHaveLength(1);
    expect(r[0].skill_name).toBe("creator_match");
    expect(apiFetch).toHaveBeenCalledWith("/api/admin/vkpi/skills", { cache: "no-store" }, "tok");
  });

  it("吃 {skills:[...]} 包壳", async () => {
    apiFetch.mockResolvedValue({ skills: [{ skill_name: "roi_review" }] });
    const r = await listSkills("tok");
    expect(r.map((s) => s.skill_name)).toEqual(["roi_review"]);
  });

  it("非数组/非已知壳 → 空数组", async () => {
    apiFetch.mockResolvedValue({ nope: 1 });
    expect(await listSkills("tok")).toEqual([]);
  });
});

describe("listSkillRuns", () => {
  it("带 skill_name + limit query", async () => {
    apiFetch.mockResolvedValue({ runs: [{ id: 1, skill_name: "creator_match" }] });
    const r = await listSkillRuns("tok", "creator_match", 5);
    expect(r).toHaveLength(1);
    const path = apiFetch.mock.calls[0][0] as string;
    expect(path).toContain("skill_name=creator_match");
    expect(path).toContain("limit=5");
    expect(path).toContain("review_status=all");
    expect(apiFetch.mock.calls[0][2]).toBe("tok");
  });

  it("空 skill_name 时不带 skill_name query", async () => {
    apiFetch.mockResolvedValue([]);
    await listSkillRuns("tok", "", 10);
    const path = apiFetch.mock.calls[0][0] as string;
    expect(path).not.toContain("skill_name=");
    expect(path).toContain("limit=10");
  });

  it("把待评筛选交给后端，避免只在当前页过滤", async () => {
    apiFetch.mockResolvedValue([]);
    await listSkillRuns("tok", "creator_match", 100, "pending");
    const path = apiFetch.mock.calls[0][0] as string;
    expect(path).toContain("limit=100");
    expect(path).toContain("review_status=pending");
  });
});

describe("runSkill", () => {
  it("POST 到 /{skill}/run,body 包成 {input}", async () => {
    apiFetch.mockResolvedValue({ status: "ok", output: { ok: 1 }, skill_run_id: 7 });
    const r = await runSkill("tok", "creator_match", { product: "x" });
    expect(r.skill_run_id).toBe(7);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/skills/creator_match/run");
    expect((init as any).method).toBe("POST");
    expect(JSON.parse((init as any).body)).toEqual({ input: { product: "x" } });
    expect(token).toBe("tok");
  });

  it("skill_name 含特殊字符时 encode", async () => {
    apiFetch.mockResolvedValue({ status: "ok" });
    await runSkill("tok", "a/b", {});
    expect(apiFetch.mock.calls[0][0]).toBe("/api/admin/vkpi/skills/a%2Fb/run");
  });
});

describe("reviewSkillRun", () => {
  it("POST 到 run 的人工评审端点并完整透传证据合同", async () => {
    apiFetch.mockResolvedValue({ ok: true, run_id: 7, event_id: 9, accepted: true, human_score: 4.5, idempotent: false });
    const payload = {
      accepted: true,
      human_score: 4.5,
      business_result: "进入 shortlist",
      evidence: [{ source: "manual", type: "reference", reference: "project:42" }],
      correlation_id: "skill-review-7-correlation",
      expected_input_sha256: "a".repeat(64),
      expected_output_sha256: "b".repeat(64),
    };
    const result = await reviewSkillRun("tok", 7, payload);
    expect(result.event_id).toBe(9);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/skills/runs/7/review");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual(payload);
    expect(token).toBe("tok");
  });

  it("从 manager-only 端点按需回读脱敏候选，而不是依赖 runs 列表详情", async () => {
    apiFetch.mockResolvedValue({
      run_id: 7,
      skill_name: "creator_match",
      input_snapshot_json: '{"product":"AF 16mm F1.8"}',
      input_sha256: "a".repeat(64),
      output_snapshot_json: '{"status":"ok"}',
      output_sha256: "b".repeat(64),
      model_used: "gpt-5.2",
      prompt_version: "creator-match-v3",
    });
    const result = await getSkillReviewCandidate("tok", 7);
    expect(result.run_id).toBe(7);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/skills/runs/7/review-candidate",
      { cache: "no-store" },
      "tok",
    );
  });
});
