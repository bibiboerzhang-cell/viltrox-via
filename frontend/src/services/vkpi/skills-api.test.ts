import { describe, it, expect, vi, beforeEach } from "vitest";

// skills-api 单测:listSkills/listSkillRuns/runSkill 的入参拼装 + 形状归一(裸数组 vs 包壳)。
const apiFetch = vi.fn();
const jsonBody = (payload: unknown) => JSON.stringify(payload);
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { listSkills, listSkillRuns, runSkill } from "./skills-api";

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
    expect(apiFetch.mock.calls[0][2]).toBe("tok");
  });

  it("空 skill_name 时不带 skill_name query", async () => {
    apiFetch.mockResolvedValue([]);
    await listSkillRuns("tok", "", 10);
    const path = apiFetch.mock.calls[0][0] as string;
    expect(path).not.toContain("skill_name=");
    expect(path).toContain("limit=10");
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
