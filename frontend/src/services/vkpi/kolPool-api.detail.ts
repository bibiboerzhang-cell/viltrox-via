import { apiFetch, jsonBody } from "../http";
import { cachedApiFetch } from "../../lib/apiCache";
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
  VkpiKolPoolContactRevealResponse,
} from "./kolPool-api.helpers";

// M7(c) 详情读缓存:纯读投影走 lib/apiCache 的 cachedApiFetch —— 45s 内存 TTL + 同 URL
// 并发去重,重复打开同一个 KOL(抽屉 ↔ 档案页来回、切 tab 重挂)不再重发整串 GET。
//
// 谁不进缓存(读代码坐实的三类,进了会把「状态翻面」压住 45 秒):
//   · content-fit —— useKolContentFitState 拿它 2-6s 轮询任务态,缓存=轮询空转;
//   · account-dossier —— SmartKolInputPanel.UrlSummary 管线活跃时 6-10s 追同一条;
//   · cooperation —— CooperationPanel 写完动作可能回读(res 无 status 时 load()),缓存=读到写前值。
//   · detail-bundle —— 同样在 UrlSummary 的 6-10s 轮询里,所以默认直透(cacheTtlMs 缺省 0),
//     只让「不轮询」的调用方按需显式开(见 cacheTtlMs 参数)。
// 手动刷新一律经 forceRefresh 绕开读缓存(仍写缓存、仍去重),否则刷新按钮在窗口内变空点。
const DETAIL_CACHE_TTL_MS = 45_000;

/** 只读投影的缓存开关:调用方手动刷新时传 forceRefresh 绕过读缓存。 */
export type KolPoolReadCacheOptions = { forceRefresh?: boolean };

function readCacheInit(options: KolPoolReadCacheOptions = {}, ttlMs: number = DETAIL_CACHE_TTL_MS) {
  return { ttlMs, forceRefresh: options.forceRefresh === true };
}

export async function getKolPoolItem(
  token: string,
  kolPoolId: number,
  refreshIfStale = true,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ refresh_if_stale: String(refreshIfStale) });
  return cachedApiFetch<{ item: VkpiKolPoolItem; freshness?: VkpiKolPoolFreshness; refresh?: VkpiKolPoolRefreshState }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolDetailBundle(
  token: string,
  kolPoolId: string | number,
  options: {
    videoLimit?: number;
    llmLimit?: number;
    /** 缺省 0 = 直透(保住 UrlSummary 的 6-10s 管线轮询);不轮询的调用方可显式开 45000。 */
    cacheTtlMs?: number;
    forceRefresh?: boolean;
  } = {},
) {
  const params = new URLSearchParams({
    // P9:默认从 3 抬到 24,单账号详情默认展示该账号(基本)全部视频(后端 max 200,可按需再加载)。
    video_limit: String(options.videoLimit || 24),
    llm_limit: String(options.llmLimit || 20),
  });
  const path = `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/detail-bundle?${params.toString()}`;
  const ttlMs = Number(options.cacheTtlMs) > 0 ? Number(options.cacheTtlMs) : 0;
  if (ttlMs <= 0) return apiFetch<VkpiKolPoolDetailBundleResponse>(path, { cache: "no-store" }, token);
  return cachedApiFetch<VkpiKolPoolDetailBundleResponse>(path, readCacheInit(options, ttlMs), token);
}

/** Disclosure tier attached to each revealed contact (B 方案两档). */
export type VkpiKolContactTier = "verified" | "observed";

type VkpiKolPoolContactRevealEntry = NonNullable<VkpiKolPoolContactRevealResponse["contacts"]>[number];

/**
 * Reveal response with per-contact `tier`. `verified` rows carry evidence-backed
 * public-business verification; `observed` rows come from scans/declarations
 * and are not yet verified. Older backends omit the field (treated as untiered).
 */
export interface VkpiKolPoolContactRevealTieredResponse extends Omit<VkpiKolPoolContactRevealResponse, "contacts"> {
  contacts?: Array<VkpiKolPoolContactRevealEntry & { tier?: VkpiKolContactTier | string }>;
  verified_count?: number;
  observed_count?: number;
}

/**
 * Explicit audited reveal boundary (POST confirm + purpose).  Ordinary GET
 * item/detail projections stay value-free; plaintext only arrives here.
 */
export async function revealKolPoolContact(
  token: string,
  kolPoolId: string | number,
  options: {
    signal?: AbortSignal;
    purpose?: "kol_detail_view" | "compose_outreach";
  } = {},
) {
  return apiFetch<VkpiKolPoolContactRevealTieredResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/contacts/reveal`,
    {
      method: "POST",
      body: jsonBody({ confirm: true, purpose: options.purpose || "kol_detail_view" }),
      cache: "no-store",
      signal: options.signal,
    },
    token,
  );
}

export async function getKolPoolCompetitors(
  token: string,
  kolPoolId: string | number,
  options: KolPoolReadCacheOptions = {},
) {
  return cachedApiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/competitors`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolDimensions11(
  token: string,
  kolPoolId: string | number,
  options: { requirePersisted?: boolean } & KolPoolReadCacheOptions = {},
) {
  const params = new URLSearchParams();
  if (options.requirePersisted) params.set("require_persisted", "true");
  const query = params.toString();
  return cachedApiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/dimensions11${query ? `?${query}` : ""}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolLlmDeepAnalysis(
  token: string,
  kolPoolId: string | number,
  limit = 20,
  options: KolPoolReadCacheOptions = {},
) {
  const params = new URLSearchParams({ limit: String(limit) });
  return cachedApiFetch<VkpiKolLlmDeepAnalysisResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/llm-deep-analysis?${params.toString()}`,
    readCacheInit(options),
    token,
  );
}

// 地基B 内容契合深析(content_fit_v1):GET 永远只读缓存。生成必须走下方显式 POST。
export async function getKolPoolContentFit(
  token: string,
  kolPoolId: string | number,
  options: { productSku?: string; jobId?: number } = {},
) {
  const params = new URLSearchParams();
  if (options.productSku) params.set("product_sku", options.productSku);
  if (Number(options.jobId) > 0) params.set("job_id", String(Number(options.jobId)));
  const query = params.toString();
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/content-fit${query ? `?${query}` : ""}`,
    { cache: "no-store" },
    token,
  );
}

export async function analyzeKolPoolContentFit(
  token: string,
  kolPoolId: string | number,
  options: { force?: boolean; productSku?: string } = {},
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/content-fit/analyze`,
    {
      method: "POST",
      body: jsonBody({
        force: options.force === true,
        ...(options.productSku ? { product_sku: options.productSku } : {}),
      }),
      cache: "no-store",
    },
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

export async function getKolPoolIntelligenceCard(
  token: string,
  kolPoolId: number | string,
  includeProductFit = true,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return cachedApiFetch<VkpiKolPoolIntelligenceCard>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/intelligence-card?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolEvidenceSummary(
  token: string,
  kolPoolId: number,
  includeProductFit = true,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return cachedApiFetch<VkpiKolPoolEvidenceSummary>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/evidence-summary?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolAiBrief(
  token: string,
  kolPoolId: number,
  includeProductFit = true,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return cachedApiFetch<VkpiKolPoolAiBrief>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/ai-brief?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolGeminiPreflight(
  token: string,
  kolPoolId: number,
  candidateLimit = 24,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ candidate_limit: String(candidateLimit), include_budget_preflight: "true" });
  return cachedApiFetch<VkpiKolPoolGeminiPreflight>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/gemini-preflight?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}

export async function getKolPoolGeminiGoNoGo(
  token: string,
  kolPoolId: number,
  candidateLimit = 24,
  options: KolPoolReadCacheOptions = {},
) {
  const query = new URLSearchParams({ candidate_limit: String(candidateLimit) });
  return cachedApiFetch<VkpiKolPoolGeminiGoNoGo>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/gemini-go-no-go?${query.toString()}`,
    readCacheInit(options),
    token,
  );
}
