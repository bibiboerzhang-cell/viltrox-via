import { apiFetch, jsonBody } from "../http";

// VOS Skill Studio 客户端 —— 调「营销大脑」skill 运行账本 3 端点(契约与后端建端一致)。
//   POST /api/admin/vkpi/skills/{skill_name}/run  body {input:{...}} → {status, output, skill_run_id?}
//   GET  /api/admin/vkpi/skills → [{skill_name, version, runs, acceptance_rate, avg_cost_cents, avg_latency_ms}]
//   GET  /api/admin/vkpi/skills/runs?skill_name=&limit= → [{id, skill_name, model_used, cost_cents, latency_ms, accepted, created_at}]
// 纯只读 + 运行触发,红线:绝不触 viltrox_fit_score。

export interface SkillSummary {
  skill_name: string;
  version?: string;
  runs?: number;
  acceptance_rate?: number | null;
  avg_cost_cents?: number | null;
  avg_latency_ms?: number | null;
  // 后端可附带的可选字段(input_schema 用于驱动表单;无则前端回退到本地字段猜测)
  input_schema?: Record<string, unknown> | null;
  [k: string]: unknown;
}

export interface SkillRunRow {
  id: number;
  skill_name: string;
  model_used?: string | null;
  cost_cents?: number | null;
  latency_ms?: number | null;
  accepted?: boolean | null;
  created_at?: string | null;
  [k: string]: unknown;
}

export interface SkillRunResult {
  status: string;
  output?: Record<string, unknown> | null;
  skill_run_id?: number | null;
  error?: string | null;
  [k: string]: unknown;
}

// 后端可能返回裸数组,也可能包成 {skills:[...]} / {runs:[...]};两种都吃。
function asArray<T>(payload: unknown, ...keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object") {
    for (const k of keys) {
      const v = (payload as Record<string, unknown>)[k];
      if (Array.isArray(v)) return v as T[];
    }
  }
  return [];
}

export async function listSkills(token: string): Promise<SkillSummary[]> {
  const r = await apiFetch<unknown>("/api/admin/vkpi/skills", { cache: "no-store" }, token);
  return asArray<SkillSummary>(r, "skills", "items");
}

export async function listSkillRuns(
  token: string,
  skillName = "",
  limit = 20,
): Promise<SkillRunRow[]> {
  const params = new URLSearchParams();
  if (skillName) params.set("skill_name", skillName);
  params.set("limit", String(limit));
  const r = await apiFetch<unknown>(
    `/api/admin/vkpi/skills/runs?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
  return asArray<SkillRunRow>(r, "runs", "items");
}

export async function runSkill(
  token: string,
  skillName: string,
  input: Record<string, unknown>,
): Promise<SkillRunResult> {
  return apiFetch<SkillRunResult>(
    `/api/admin/vkpi/skills/${encodeURIComponent(skillName)}/run`,
    { method: "POST", cache: "no-store", body: jsonBody({ input: input || {} }) },
    token,
  );
}
