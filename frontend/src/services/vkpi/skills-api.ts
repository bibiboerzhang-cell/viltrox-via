import { apiFetch, jsonBody } from "../http";

// VOS Skill Studio 客户端 —— 调「营销大脑」skill 运行账本 3 端点(契约与后端建端一致)。
//   POST /api/admin/vkpi/skills/{skill_name}/run  body {input:{...}} → {status, output, skill_run_id?}
//   GET  /api/admin/vkpi/skills → [{skill_name, version, runs, acceptance_rate, avg_cost_cents, avg_latency_ms}]
//   GET  /api/admin/vkpi/skills/runs?skill_name=&limit= → 运行摘要 + review candidate/hash 标识
//   GET  /api/admin/vkpi/skills/runs/{id}/review-candidate → manager-only 服务端脱敏 canonical JSON 快照
// 纯只读 + 运行触发,红线:绝不触 viltrox_fit_score。

export interface SkillSummary {
  skill_name: string;
  version?: string;
  runs?: number;
  reviewed_runs?: number;
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
  // 列表只承载摘要/状态。输入与输出必须经 manager-only review-candidate 按需回读。
  output_sha256?: string | null;
  review_candidate_available?: boolean;
  cost_cents?: number | null;
  latency_ms?: number | null;
  accepted?: boolean | null;
  human_score?: number | null;
  business_result?: string | null;
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

export interface SkillReviewRequest {
  accepted: boolean;
  human_score: number;
  business_result: string;
  evidence: Array<Record<string, unknown>>;
  correlation_id: string;
  expected_input_sha256: string;
  expected_output_sha256: string;
}

export interface SkillReviewCandidate {
  run_id: number;
  skill_name: string;
  input_snapshot_json: string;
  input_sha256: string;
  output_snapshot_json: string;
  output_summary?: string | null;
  output_sha256: string;
  model_used: string | null;
  prompt_version: string | null;
  created_at?: string | null;
  redacted?: boolean;
}

export interface SkillReviewResult {
  ok: boolean;
  run_id: number;
  event_id: number;
  accepted: boolean;
  human_score: number;
  idempotent: boolean;
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
  reviewStatus: "all" | "pending" | "reviewed" = "all",
): Promise<SkillRunRow[]> {
  const params = new URLSearchParams();
  if (skillName) params.set("skill_name", skillName);
  params.set("limit", String(limit));
  params.set("review_status", reviewStatus);
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

export async function reviewSkillRun(
  token: string,
  runId: number,
  payload: SkillReviewRequest,
): Promise<SkillReviewResult> {
  return apiFetch<SkillReviewResult>(
    `/api/admin/vkpi/skills/runs/${encodeURIComponent(String(runId))}/review`,
    { method: "POST", cache: "no-store", body: jsonBody(payload) },
    token,
  );
}

export async function getSkillReviewCandidate(
  token: string,
  runId: number,
): Promise<SkillReviewCandidate> {
  return apiFetch<SkillReviewCandidate>(
    `/api/admin/vkpi/skills/runs/${encodeURIComponent(String(runId))}/review-candidate`,
    { cache: "no-store" },
    token,
  );
}
