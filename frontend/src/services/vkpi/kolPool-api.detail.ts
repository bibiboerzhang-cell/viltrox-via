import { apiFetch, jsonBody } from "../http";
import type {
  Row,
  VkpiKolPoolItem,
  VkpiKolLlmDeepAnalysisResponse,
  VkpiKolPoolFreshness,
  VkpiKolPoolRefreshState,
  VkpiKolPoolDetailBundleResponse,
  VkpiKolPoolIntelligenceCard,
  VkpiKolPoolEvidenceSummary,
  VkpiKolPoolAiBrief,
  VkpiKolPoolGeminiPreflight,
  VkpiKolPoolGeminiGoNoGo,
} from "./kolPool-api.helpers";

export async function getKolPoolItem(token: string, kolPoolId: number, refreshIfStale = true) {
  const query = new URLSearchParams({ refresh_if_stale: String(refreshIfStale) });
  return apiFetch<{ item: VkpiKolPoolItem; freshness?: VkpiKolPoolFreshness; refresh?: VkpiKolPoolRefreshState }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolDetailBundle(
  token: string,
  kolPoolId: string | number,
  options: { videoLimit?: number; llmLimit?: number } = {},
) {
  const params = new URLSearchParams({
    // P9:默认从 3 抬到 24,单账号详情默认展示该账号(基本)全部视频(后端 max 200,可按需再加载)。
    video_limit: String(options.videoLimit || 24),
    llm_limit: String(options.llmLimit || 20),
  });
  return apiFetch<VkpiKolPoolDetailBundleResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/detail-bundle?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getKolPoolCompetitors(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/competitors`,
    {},
    token,
  );
}

export async function getKolPoolDimensions11(
  token: string,
  kolPoolId: string | number,
  options: { requirePersisted?: boolean } = {},
) {
  const params = new URLSearchParams();
  if (options.requirePersisted) params.set("require_persisted", "true");
  const query = params.toString();
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/dimensions11${query ? `?${query}` : ""}`,
    {},
    token,
  );
}

export async function getKolPoolLlmDeepAnalysis(token: string, kolPoolId: string | number, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiFetch<VkpiKolLlmDeepAnalysisResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/llm-deep-analysis?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

// 地基B 内容契合深析(content_fit_v1):默认只读缓存;analyze=true 才按需触发深析(读已有
// 视频分析+评论 → 非 flash LLM → 落 cache)。红线:零触 viltrox_fit_score,不新跑 Gemini。
export async function getKolPoolContentFit(
  token: string,
  kolPoolId: string | number,
  options: { analyze?: boolean; force?: boolean; productSku?: string } = {},
) {
  const params = new URLSearchParams();
  if (options.analyze) params.set("analyze", "true");
  if (options.force) params.set("force", "true");
  if (options.productSku) params.set("product_sku", options.productSku);
  const query = params.toString();
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/content-fit${query ? `?${query}` : ""}`,
    { cache: "no-store" },
    token,
  );
}

// item4 账号档案:只读本地聚合(零 provider/LLM/写库),返回 profile/coverage/judgment/gaps/
// crawl_history/events。红线:diagnostics.viltrox_fit_score_write=false。
export async function getKolPoolAccountDossier(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/account-dossier`,
    { cache: "no-store" },
    token,
  );
}

// P1-1 KOL 合作动作(平台为基准):读当前状态+时间线 / 记一条动作。
export async function getKolCooperation(token: string, kolPoolId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/cooperation`,
    { cache: "no-store" },
    token,
  );
}

export async function recordKolCooperation(
  token: string,
  kolPoolId: string | number,
  action: string,
  note = "",
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/cooperation`,
    {
      method: "POST",
      body: jsonBody({ action, note }),
    },
    token,
  );
}

export async function getKolPoolIntelligenceCard(token: string, kolPoolId: number | string, includeProductFit = true) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return apiFetch<VkpiKolPoolIntelligenceCard>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/intelligence-card?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolEvidenceSummary(token: string, kolPoolId: number, includeProductFit = true) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return apiFetch<VkpiKolPoolEvidenceSummary>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/evidence-summary?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolAiBrief(token: string, kolPoolId: number, includeProductFit = true) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return apiFetch<VkpiKolPoolAiBrief>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/ai-brief?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolGeminiPreflight(token: string, kolPoolId: number, candidateLimit = 24) {
  const query = new URLSearchParams({ candidate_limit: String(candidateLimit), include_budget_preflight: "true" });
  return apiFetch<VkpiKolPoolGeminiPreflight>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/gemini-preflight?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolGeminiGoNoGo(token: string, kolPoolId: number, candidateLimit = 24) {
  const query = new URLSearchParams({ candidate_limit: String(candidateLimit) });
  return apiFetch<VkpiKolPoolGeminiGoNoGo>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/gemini-go-no-go?${query.toString()}`,
    {},
    token,
  );
}
