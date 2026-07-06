import { apiFetch, jsonBody } from "../http";

// 件1 · Intelligent 问答(三车道)客户端。
// 后端:backend/app/api/routers/vkpi_intelligent.py(前缀 /api/admin/vkpi/intelligent)。
//   POST /ask          -> IntelligentAnswer(三车道分诊,当日缓存)
//   GET  /suggestions  -> { suggestions: string[]; source: "seeds" | "default" }
// 全只读;前端只传 question,绝不拼 SQL / 不触 fit_score。

// 车道:intent=意图秒回 | search=检索候选 | synth=LLM 综合 | degraded=诚实降级到检索。
export type IntelligentMode = "intent" | "search" | "synth" | "degraded";

// 证据条目:后端按 kind 区分(intent_result / search_results / synth)。前端按 kind 渲染。
export interface IntelligentEvidence {
  kind: string;
  [key: string]: unknown;
}

// 动作按钮:label 展示,route 直跳 cockpit 路由。
export interface IntelligentAction {
  label: string;
  route: string;
}

export interface IntelligentAnswer {
  answer: string;
  mode: IntelligentMode;
  evidence: IntelligentEvidence[];
  actions: IntelligentAction[];
  cached: boolean;
}

export interface SuggestionsResponse {
  suggestions: string[];
  source: "seeds" | "default";
}

// POST /ask:综合车道后端最长 30s,前端给到 35s 容超时不误杀。
export async function askIntelligent(
  token: string,
  question: string,
): Promise<IntelligentAnswer> {
  return apiFetch<IntelligentAnswer>(
    "/api/admin/vkpi/intelligent/ask",
    { method: "POST", body: jsonBody({ question }), timeoutMs: 35000 },
    token,
  );
}

// GET /suggestions:当日异动种 chips(缺表回默认集)。
export async function fetchSuggestions(token: string): Promise<string[]> {
  const res = await apiFetch<SuggestionsResponse>(
    "/api/admin/vkpi/intelligent/suggestions",
    { cache: "no-store" },
    token,
  );
  return Array.isArray(res.suggestions) ? res.suggestions : [];
}
