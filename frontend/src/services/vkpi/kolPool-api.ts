import { apiFetch, jsonBody } from "../http";
import type {
  Row,
  VkpiKolPoolItem,
  VkpiKolVideoAnalysisCacheResponse,
  VkpiVideoAnalysisEnqueueResponse,
  VkpiKolPoolRefreshState,
  VkpiKolPoolWorkspaceResponse,
  VkpiKolNeedsAnalysisItem,
} from "./kolPool-api.helpers";

export type {
  Row,
  VkpiKolPoolItem,
  VkpiKolVideoAnalysisCacheEntry,
  VkpiKolVideoAnalysisCacheResponse,
  VkpiVideoAnalysisEnqueueResponse,
  VkpiKolLlmDeepAnalysisResult,
  VkpiKolLlmDeepAnalysisResponse,
  VkpiKolPoolFreshness,
  VkpiKolPoolRefreshState,
  VkpiKolPoolWorkspaceResponse,
  VkpiKolPoolDetailBundleResponse,
  VkpiKolPoolIntelligenceCard,
  VkpiKolPoolEvidenceSummary,
  VkpiKolPoolAiBrief,
  VkpiKolPoolGeminiPreflight,
  VkpiKolPoolGeminiGoNoGo,
  VkpiKolRecallItem,
  VkpiKolRecallResponse,
  VkpiKolSearchSessionRef,
  VkpiKolSearchSessionItem,
  VkpiKolSearchHistoryItem,
  VkpiKolSearchHistoryResponse,
  VkpiKolUrlDeepCrawlResponse,
  VkpiKolSmartSearchResponse,
  VkpiKolSmartSearchProfileAdvanceResponse,
  VkpiKolNeedsAnalysisItem,
} from "./kolPool-api.helpers";

export async function listKolPool(
  token: string,
  params: { search?: string; platform?: string; country?: string; limit?: number; offset?: number; dataStatus?: string; sortBy?: string; enrichable?: boolean; refreshIfStale?: boolean } = {},
): Promise<{ items?: VkpiKolPoolItem[]; refresh?: VkpiKolPoolRefreshState }> {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (typeof params.offset === "number") query.set("offset", String(params.offset));
  if (params.search) query.set("query", params.search);
  if (params.platform) query.set("platform", params.platform);
  if (params.country) query.set("country", params.country);
  if (params.dataStatus) query.set("data_status", params.dataStatus);
  if (params.sortBy) query.set("sort_by", params.sortBy);
  if (typeof params.enrichable === "boolean") query.set("enrichable", String(params.enrichable));
  if (typeof params.refreshIfStale === "boolean") query.set("refresh_if_stale", String(params.refreshIfStale));
  return apiFetch<{ items?: VkpiKolPoolItem[]; refresh?: VkpiKolPoolRefreshState }>(
    `/api/admin/vkpi/kol-pool?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolWorkspace(
  token: string,
  params: { search?: string; platform?: string; country?: string; limit?: number; offset?: number; dataStatus?: string; sortBy?: string; enrichable?: boolean } = {},
): Promise<VkpiKolPoolWorkspaceResponse> {
  const query = new URLSearchParams({ limit: String(params.limit || 1200) });
  if (typeof params.offset === "number") query.set("offset", String(params.offset));
  if (params.search) query.set("query", params.search);
  if (params.platform) query.set("platform", params.platform);
  if (params.country) query.set("country", params.country);
  if (params.dataStatus) query.set("data_status", params.dataStatus);
  if (params.sortBy) query.set("sort_by", params.sortBy);
  if (typeof params.enrichable === "boolean") query.set("enrichable", String(params.enrichable));
  return apiFetch<VkpiKolPoolWorkspaceResponse>(
    `/api/admin/vkpi/kol-pool/workspace?${query.toString()}`,
    {},
    token,
  );
}

export {
  deepCrawlKolUrl,
  smartKolSearch,
  smartKolSearchProfileAdvanceJob,
  listKolSearchHistory,
  getKolRecommendationCard,
  getKolTwin,
  getKolSearchSession,
  approveKolSearchSession,
  estimateKolSearchSessionCost,
  createProjectDraftFromSession,
  generateKolSearchSessionOutreach,
  recallKolProfiles,
} from "./kolPool-api.search";

export async function getKolVideoAnalysisCache(
  token: string,
  evidenceId: string | number,
  deriveMethod: string,
): Promise<VkpiKolVideoAnalysisCacheResponse> {
  const params = new URLSearchParams({
    target_type: "video",
    target_id: String(evidenceId),
    derive_method: deriveMethod,
    _ts: String(Date.now()),
  });
  return apiFetch<VkpiKolVideoAnalysisCacheResponse>(
    `/api/admin/vkpi/analysis-cache?${params.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function enqueueVideoAnalysis(
  token: string,
  kolPoolId: string | number,
  evidenceId: string | number,
): Promise<VkpiVideoAnalysisEnqueueResponse> {
  return apiFetch<VkpiVideoAnalysisEnqueueResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/enqueue-video-analysis`,
    {
      method: "POST",
      body: jsonBody({ evidence_id: evidenceId }),
    },
    token,
  );
}

export async function listKolsNeedingAnalysis(token: string, limit = 50): Promise<{ items?: VkpiKolNeedsAnalysisItem[]; count?: number }> {
  return apiFetch<{ items?: VkpiKolNeedsAnalysisItem[]; count?: number }>(
    `/api/admin/vkpi/kol-pool/needs-analysis?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}
export async function enqueueVideoAnalysisBatch(
  token: string,
  items: { kol_pool_id: number; evidence_id: number }[],
): Promise<{ queued?: number; skipped?: number; errors?: number; results?: unknown[] }> {
  return apiFetch<{ queued?: number; skipped?: number; errors?: number; results?: unknown[] }>(
    "/api/admin/vkpi/kol-pool/enqueue-video-analysis-batch",
    { method: "POST", body: jsonBody({ items }) },
    token,
  );
}

// 全视频跑:该 KOL 全部视频证据各入队一条 final_v1(非单一代表作),发完后综合评估。
// 已 ready / 在队的自动跳过。红线:只写 apify_jobs,零触 viltrox_fit_score。
export async function enqueueAllKolVideos(
  token: string,
  kolPoolId: string | number,
): Promise<{ status?: string; queued?: number; skipped?: number; errors?: number; evidence_total?: number; requested?: number; reason?: string }> {
  return apiFetch<{ status?: string; queued?: number; skipped?: number; errors?: number; evidence_total?: number; requested?: number; reason?: string }>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/enqueue-all-videos`,
    { method: "POST" },
    token,
  );
}

export {
  getKolPoolItem,
  getKolPoolDetailBundle,
  getKolPoolCompetitors,
  getKolPoolDimensions11,
  getKolPoolLlmDeepAnalysis,
  getKolPoolContentFit,
  getKolPoolAccountDossier,
  getKolCooperation,
  recordKolCooperation,
  getKolPoolIntelligenceCard,
  getKolPoolEvidenceSummary,
  getKolPoolAiBrief,
  getKolPoolGeminiPreflight,
  getKolPoolGeminiGoNoGo,
} from "./kolPool-api.detail";

export async function refreshKolPoolItem(token: string, kolPoolId: number, force = false) {
  return apiFetch<VkpiKolPoolRefreshState>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/refresh`,
    {
      method: "POST",
      body: jsonBody({ force }),
    },
    token,
  );
}

export async function enrichKolPoolItem(token: string, kolPoolId: number, maxPosts = 12) {
  return apiFetch<{
    item?: VkpiKolPoolItem;
    sync_status?: string;
    provider_status?: string;
    message?: string;
    posts_sampled?: number;
    score_breakdown?: Record<string, unknown>;
  }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/enrich`,
    {
      method: "POST",
      body: jsonBody({ max_posts: maxPosts }),
      timeoutMs: 120000,
    },
    token,
  );
}

export async function batchEnrichKolPool(
  token: string,
  payload: {
    ids?: number[];
    platform?: string;
    query?: string;
    dataStatus?: string;
    limit?: number;
    maxPosts?: number;
  } = {},
) {
  return apiFetch<{
    requested: number;
    attempted: number;
    enriched: number;
    complete?: number;
    partial?: Array<Record<string, unknown>>;
    skipped?: Array<Record<string, unknown>>;
    errors?: Array<Record<string, unknown>>;
    items?: VkpiKolPoolItem[];
    limit: number;
    max_posts: number;
    capped?: boolean;
  }>(
    "/api/admin/vkpi/kol-pool/batch-enrich",
    {
      method: "POST",
      body: jsonBody({
        ids: payload.ids || [],
        platform: payload.platform || "",
        query: payload.query || "",
        data_status: payload.dataStatus || "missing",
        limit: payload.limit || 3,
        max_posts: payload.maxPosts || 6,
      }),
      timeoutMs: 180000,
    },
    token,
  );
}

export async function importKolPool(
  token: string,
  payload: {
    items: Array<Record<string, unknown>>;
    sourceType?: string;
    sourceRef?: string;
    platform?: string;
    force?: boolean;
  },
) {
  return apiFetch<{ imported: number; skipped: number; items: VkpiKolPoolItem[] }>(
    "/api/admin/vkpi/kol-pool/import",
    {
      method: "POST",
      body: jsonBody({
        items: payload.items,
        source_type: payload.sourceType || "manual",
        source_ref: payload.sourceRef || "",
        platform: payload.platform || "",
        force: payload.force || false,
      }),
    },
    token,
  );
}

export async function linkKolPoolToMain(token: string, kolPoolId: number, mainKolId: number) {
  return apiFetch<{ linked: boolean; kol_pool_id: number; main_kol_id: number }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/link`,
    {
      method: "POST",
      body: jsonBody({ main_kol_id: mainKolId }),
    },
    token,
  );
}

export async function listKolPoolMainCandidates(token: string, kolPoolId: number, limit = 5) {
  return apiFetch<{
    kol_pool_id: number;
    item?: VkpiKolPoolItem;
    candidates?: Array<Record<string, unknown>>;
  }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/main-candidates?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function promoteKolPoolToMain(token: string, kolPoolId: number) {
  return apiFetch<{
    linked: boolean;
    mode: "already_linked" | "matched" | "created" | "no_match" | string;
    kol_pool_id: number;
    main_kol_id?: number | null;
    item?: VkpiKolPoolItem;
    main_kol?: Record<string, unknown>;
    candidates?: Array<Record<string, unknown>>;
  }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/promote`,
    {
      method: "POST",
      body: jsonBody({ mode: "match_or_create" }),
    },
    token,
  );
}

export async function getKolPoolSummary(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/kol-pool/summary", {}, token);
}

// #25 发现卡英文 bio → 简体中文(gpt-4o-mini,后端预算闸+按原文缓存)。译空=失败/预算挡,前端回退原文。
export async function translateBio(token: string, text: string) {
  return apiFetch<{ translated: string; lang: string; cached?: boolean }>(
    "/api/admin/vkpi/kol-pool/translate-bio",
    { method: "POST", body: jsonBody({ text }) },
    token,
  );
}

// #17 按 handle 解析到主池真记录(供 mover 弹窗 #5 / KOLDetailModal 真指标 #22)。
// 命中:{ matched:true, kol_pool_id, followers, avg_views, profile_url, ...合作摘要 };未命中:{ matched:false }。
export async function resolveKolPool(token: string, handle: string, platform = "") {
  const params = new URLSearchParams({ handle });
  if (platform) params.set("platform", platform);
  return apiFetch<Record<string, any>>(
    `/api/admin/vkpi/kol-pool/resolve?${params.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolCompetitorDashboard(
  token: string,
  options: { brand?: string; limit?: number; sourceType?: string } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 1200) });
  if (options.brand) params.set("brand", options.brand);
  if (options.sourceType !== undefined) params.set("source_type", options.sourceType);
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/competitors/dashboard?${params.toString()}`,
    {},
    token,
  );
}

// ── C2 收藏三端点包装(四环漏斗第一段;后端随 107 apply 激活)──
export async function favoriteKolPool(token: string, kolPoolId: number | string, note = "") {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/favorite`,
    { method: "POST", body: JSON.stringify({ note }), headers: { "Content-Type": "application/json" } },
    token,
  );
}

export async function unfavoriteKolPool(token: string, kolPoolId: number | string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/favorite`,
    { method: "DELETE" },
    token,
  );
}

export async function listKolPoolFavorites(token: string, limit = 2000) {
  return apiFetch<{ items?: Row[]; total?: number }>(
    `/api/admin/vkpi/kol-pool/favorites?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function listKolPoolVideos(token: string, kolPoolId: number | string, limit = 50) {
  return apiFetch<{ items?: Row[]; total?: number }>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/videos?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function enqueueKolProfileDeepCrawl(token: string, url: string, kolPoolId?: number) {
  return apiFetch<Row>(
    "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue",
    // 12 = IG profile-scraper 单跑 latestPosts 上限(YT/TT 同步取齐);LLM 仍只析 1 条代表作。
    // 真全量(全部历史视频)= E5 account_full_sync 单独件。
    { method: "POST", body: JSON.stringify({ url, kol_pool_id: kolPoolId, max_posts: 12 }), headers: { "Content-Type": "application/json" } },
    token,
  );
}

export async function enqueueKolPoolCommentsCollect(token: string, kolPoolId: number) {
  return apiFetch<Row>(
    "/api/admin/vkpi/kol-pool/comments-collect/enqueue",
    { method: "POST", body: JSON.stringify({ kol_pool_id: kolPoolId }), headers: { "Content-Type": "application/json" } },
    token,
  );
}

export async function listKolPoolVideoComments(token: string, kolPoolId: number, evidenceId: number, limit = 100) {
  return apiFetch<{ items?: Row[]; page?: Row }>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/video-comments?evidence_id=${encodeURIComponent(String(evidenceId))}&limit=${limit}`,
    {},
    token,
  );
}
