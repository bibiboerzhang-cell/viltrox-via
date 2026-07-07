import { apiFetch, jsonBody } from "../http";
import type { AnalyticsRow } from "./analyticsQuery-api";

// A4 问数页 · 预设问题库客户端(零 LLM,确定性 SQL 聚合,出数带来源)。
// 后端:backend/app/api/routers/vkpi_canned_queries.py(前缀 /api/admin/vkpi)。
//   GET  /canned-queries           -> { questions: CannedQuestion[] }
//   POST /canned-queries/{key}/run -> CannedRunResult
// 全只读;前端绝不拼 SQL,只传 key / range。

export interface CannedQuestion {
  key: string;
  title: string;
  description: string;
  columns: string[];
  source_tables: string[];
  uses_range: boolean;
}

export interface CannedQuestionsResponse {
  questions: CannedQuestion[];
}

// 运行结果:summary=一句话摘要(后端 Python 拼装);source_tables+row_count=可追溯来源。
// 单问聚合失败时后端不 500,回 status="error"+reason(诚实缺席)。
export interface CannedRunResult {
  key: string;
  title: string;
  columns: string[];
  rows: AnalyticsRow[];
  row_count: number;
  source_tables: string[];
  summary: string;
  sql_explain: string;
  range_days: number | null;
  generated_at: string;
  status?: string;
  reason?: string;
}

export async function fetchCannedQuestions(token: string): Promise<CannedQuestion[]> {
  const res = await apiFetch<CannedQuestionsResponse>(
    "/api/admin/vkpi/canned-queries",
    { cache: "no-store" },
    token,
  );
  return Array.isArray(res.questions) ? res.questions : [];
}

export async function runCannedQuery(
  token: string,
  key: string,
  range?: number,
): Promise<CannedRunResult> {
  const body: Record<string, unknown> = {};
  if (typeof range === "number") body.range = range;
  return apiFetch<CannedRunResult>(
    `/api/admin/vkpi/canned-queries/${encodeURIComponent(key)}/run`,
    { method: "POST", body: jsonBody(body), timeoutMs: 20000 },
    token,
  );
}
