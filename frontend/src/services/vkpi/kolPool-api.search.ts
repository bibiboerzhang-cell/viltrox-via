import { apiFetch, jsonBody } from "../http";
import type {
  Row,
  VkpiKolRecallResponse,
  VkpiKolSearchHistoryItem,
  VkpiKolSearchHistoryArchiveResponse,
  VkpiKolSearchHistoryResponse,
  VkpiKolUrlDeepCrawlResponse,
  VkpiKolSmartSearchResponse,
  VkpiKolSmartSearchProfileAdvanceResponse,
} from "./kolPool-api.helpers";

export async function deepCrawlKolUrl(
  token: string,
  url: string,
  execute = false,
  params: { maxPosts?: number; mode?: string; timeoutMs?: number; sessionId?: number; createSession?: boolean; source?: string; forceFullHistory?: boolean; representativeVideoLimit?: number; deferToQueue?: boolean; localEvaluation?: boolean } = {},
): Promise<VkpiKolUrlDeepCrawlResponse> {
  const body: Row = {
    url,
    execute,
  };
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (params.mode) body.mode = params.mode;
  if (params.forceFullHistory) body.force_full_history = true;  // 项⑥:account_deep 重跑全量历史视频
  // 刀2·流2 路A:profile_with_video 模式下自动跑 N 条代表视频 final_v1,dossier 才出真 LLM 账号分。
  if (typeof params.representativeVideoLimit === "number") body.representative_video_limit = params.representativeVideoLimit;
  if (params.deferToQueue) body.defer_to_queue = true;
  // Explicit only: ordinary retries and automatic URL execution never enter
  // the local-evaluation capability lane.
  if (params.localEvaluation === true) body.local_evaluation = true;
  if (typeof params.sessionId === "number") body.session_id = params.sessionId;
  if (typeof params.createSession === "boolean") body.create_session = params.createSession;
  if (params.source) body.source = params.source;
  const timeoutMs = params.timeoutMs ?? (execute ? 300000 : undefined);
  return apiFetch<VkpiKolUrlDeepCrawlResponse>(
    "/api/admin/vkpi/kol-url-deep-crawl",
    {
      method: "POST",
      body: jsonBody(body),
      ...(timeoutMs ? { timeoutMs } : {}),
    },
    token,
  );
}

export async function smartKolSearch(
  token: string,
  input: string,
  params: {
    mode?: "auto" | "url" | "text" | "recall" | string;
    execute?: boolean;
    maxPosts?: number;
    candidateLimit?: number;
    limit?: number;
    creatorQuota?: number;
    reviewerQuota?: number;
    /** 筛选完成后期望返回的候选数；与旧 limit 并行发送，兼容滚动升级期间的服务端。 */
    resultLimit?: number;
    searchStrategy?: "vertical" | "balanced" | "expansion";
    filters?: {
      platforms?: string[];
      countries?: string[];
      languages?: string[];
      followers_min?: number;
      followers_max?: number;
      verticals?: string[];
      gear_content?: "any" | "yes" | "no";
    };
    bucketPolicy?: { core_vertical: number; expansion: number; exploration: number };
    productSku?: string;
    sessionId?: number;
    createSession?: boolean;
    excludeChinese?: boolean;
    market?: string;
    platforms?: string[];
    languages?: string[];
    profileTypes?: string[];
    localQualificationSpec?: Row;
    timeoutMs?: number;
  } = {},
): Promise<VkpiKolSmartSearchResponse> {
  const body: Row = {
    input,
    mode: params.mode || "auto",
    create_session: params.createSession ?? true,
    response_projection: "smart_local_compact_v1",
  };
  if (params.execute) body.execute = true;
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (typeof params.candidateLimit === "number") body.candidate_limit = params.candidateLimit;
  if (typeof params.limit === "number") body.limit = params.limit;
  if (typeof params.creatorQuota === "number") body.creator_quota = params.creatorQuota;
  if (typeof params.reviewerQuota === "number") body.reviewer_quota = params.reviewerQuota;
  if (typeof params.resultLimit === "number") body.result_limit = params.resultLimit;
  if (params.searchStrategy) body.search_strategy = params.searchStrategy;
  if (params.filters && Object.keys(params.filters).length) {
    body.filters = params.filters;
    // 旧服务只读顶层 platforms；滚动升级时保留兼容字段，筛选真值仍以后端 diagnostics 为准。
    if (params.filters.platforms?.length) body.platforms = params.filters.platforms;
  }
  if (params.bucketPolicy) body.bucket_policy = params.bucketPolicy;
  if (params.productSku) body.product_sku = params.productSku;
  if (typeof params.sessionId === "number") body.session_id = params.sessionId;
  if (typeof params.excludeChinese === "boolean") body.exclude_chinese = params.excludeChinese;
  if (params.market) body.market = params.market;
  if (params.platforms?.length) body.platforms = params.platforms;
  if (params.languages?.length) body.languages = params.languages;
  if (params.profileTypes?.length) body.profile_types = params.profileTypes;
  if (params.localQualificationSpec) body.local_qualification_spec = params.localQualificationSpec;
  return apiFetch<VkpiKolSmartSearchResponse>(
    "/api/admin/vkpi/kol-smart-search",
    {
      method: "POST",
      body: jsonBody(body),
      ...(params.timeoutMs ? { timeoutMs: params.timeoutMs } : {}),
    },
    token,
  );
}

export async function smartKolSearchProfileAdvanceJob(
  token: string,
  input: string,
  params: {
    candidateLimit?: number;
    limit?: number;
    creatorQuota?: number;
    reviewerQuota?: number;
    /** 筛选完成后期望返回的候选数；与旧 limit/advance_limit 并行发送。 */
    resultLimit?: number;
    searchStrategy?: "vertical" | "balanced" | "expansion";
    filters?: {
      platforms?: string[];
      countries?: string[];
      languages?: string[];
      followers_min?: number;
      followers_max?: number;
      verticals?: string[];
      gear_content?: "any" | "yes" | "no";
    };
    bucketPolicy?: { core_vertical: number; expansion: number; exploration: number };
    advanceLimit?: number;
    maxPosts?: number;
    representativeVideoLimit?: number;
    includeNewDiscovery?: boolean;
    newDiscoveryLimit?: number;
    newDiscoveryPerPlatformLimit?: number;
    /** 每平台上限覆盖（{平台: 上限}）；缺省时后端全平台沿用上面的标量。 */
    newDiscoveryPerPlatformLimits?: Readonly<Record<string, number>>;
    newDiscoveryPlatforms?: string[];
    excludeChinese?: boolean;
    market?: string;
    languages?: string[];
    profileTypes?: string[];
    localQualificationSpec?: Row;
    onlineQualificationSpec?: Row;
    sessionId?: number;
    timeoutMs?: number;
  } = {},
): Promise<VkpiKolSmartSearchProfileAdvanceResponse> {
  const body: Row = {
    input,
    queue_pipeline: true,
    include_new_discovery: params.includeNewDiscovery ?? true,
  };
  if (params.newDiscoveryPlatforms?.length) body.new_discovery_platforms = params.newDiscoveryPlatforms;
  // 目标市场与内容语言独立传递；未选择语言时不附 languages，不从国家码推断。
  if (params.market) body.market = params.market;
  if (params.languages?.length) body.languages = params.languages;
  if (params.profileTypes?.length) body.profile_types = params.profileTypes;
  if (params.localQualificationSpec) body.local_qualification_spec = params.localQualificationSpec;
  if (params.onlineQualificationSpec) body.online_qualification_spec = params.onlineQualificationSpec;
  if (typeof params.sessionId === "number" && params.sessionId > 0) body.session_id = params.sessionId;
  if (typeof params.excludeChinese === "boolean") body.exclude_chinese = params.excludeChinese;
  if (typeof params.candidateLimit === "number") body.candidate_limit = params.candidateLimit;
  if (typeof params.limit === "number") body.limit = params.limit;
  if (typeof params.creatorQuota === "number") body.creator_quota = params.creatorQuota;
  if (typeof params.reviewerQuota === "number") body.reviewer_quota = params.reviewerQuota;
  if (typeof params.resultLimit === "number") body.result_limit = params.resultLimit;
  if (params.searchStrategy) body.search_strategy = params.searchStrategy;
  if (params.filters && Object.keys(params.filters).length) {
    body.filters = params.filters;
    if (params.filters.platforms?.length) body.platforms = params.filters.platforms;
  }
  if (params.bucketPolicy) body.bucket_policy = params.bucketPolicy;
  if (typeof params.advanceLimit === "number") body.advance_limit = params.advanceLimit;
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (typeof params.representativeVideoLimit === "number") body.representative_video_limit = params.representativeVideoLimit;
  if (typeof params.newDiscoveryLimit === "number") body.new_discovery_limit = params.newDiscoveryLimit;
  if (typeof params.newDiscoveryPerPlatformLimit === "number") body.new_discovery_per_platform_limit = params.newDiscoveryPerPlatformLimit;
  if (params.newDiscoveryPerPlatformLimits && Object.keys(params.newDiscoveryPerPlatformLimits).length) {
    body.new_discovery_per_platform_limits = params.newDiscoveryPerPlatformLimits;
  }
  return apiFetch<VkpiKolSmartSearchProfileAdvanceResponse>(
    "/api/admin/vkpi/kol-smart-search/profile-advance-job",
    {
      method: "POST",
      body: jsonBody(body),
      ...(params.timeoutMs ? { timeoutMs: params.timeoutMs } : {}),
    },
    token,
  );
}

export async function listKolSearchHistory(
  token: string,
  params: { limit?: number; status?: string; queryType?: string; itemLimit?: number; archived?: boolean } = {},
): Promise<VkpiKolSearchHistoryResponse> {
  const query = new URLSearchParams({
    limit: String(params.limit || 12),
    item_limit: String(params.itemLimit ?? 5),
  });
  if (params.status) query.set("status", params.status);
  if (params.queryType) query.set("query_type", params.queryType);
  if (typeof params.archived === "boolean") query.set("archived", String(params.archived));
  return apiFetch<VkpiKolSearchHistoryResponse>(
    `/api/admin/vkpi/kol-search-history?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function archiveKolSearchHistorySession(
  token: string,
  sessionId: string | number,
): Promise<VkpiKolSearchHistoryArchiveResponse> {
  return apiFetch<VkpiKolSearchHistoryArchiveResponse>(
    `/api/admin/vkpi/kol-search-history/${encodeURIComponent(String(sessionId))}`,
    { method: "DELETE" },
    token,
  );
}

export async function restoreKolSearchHistorySession(
  token: string,
  sessionId: string | number,
): Promise<VkpiKolSearchHistoryArchiveResponse> {
  return apiFetch<VkpiKolSearchHistoryArchiveResponse>(
    `/api/admin/vkpi/kol-search-history/${encodeURIComponent(String(sessionId))}/restore`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function archiveAllKolSearchHistory(
  token: string,
): Promise<VkpiKolSearchHistoryArchiveResponse> {
  return apiFetch<VkpiKolSearchHistoryArchiveResponse>(
    "/api/admin/vkpi/kol-search-history",
    { method: "DELETE" },
    token,
  );
}

// KOL 推荐卡:数据完整度档(A-D)+ 为什么推荐 + 信号(档位非 fit)。
export async function getKolRecommendationCard(
  token: string,
  kolPoolId: string | number,
): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/recommendation-card`,
    { cache: "no-store" },
    token,
  );
}

export async function getKolTwin(token: string, kolPoolId: string | number): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/twin`,
    { cache: "no-store" },
    token,
  );
}

export async function getKolSearchSession(
  token: string,
  sessionId: string | number,
): Promise<VkpiKolSearchHistoryItem> {
  return apiFetch<VkpiKolSearchHistoryItem>(
    `/api/admin/vkpi/kol-search-sessions/${encodeURIComponent(String(sessionId))}`,
    { cache: "no-store" },
    token,
  );
}

// ── M1 找人闭合(R1-R4):批准锁定 → 成本估算 → 建项目草案 → 话术草案 ──────────────
// 红线:批准只锁候选;草案/话术只「生成」不外发不承诺价格;一切走后端 require_tab + 预算闸。

function _sessionPath(sessionId: string | number, suffix: string): string {
  return `/api/admin/vkpi/kol-search-sessions/${encodeURIComponent(String(sessionId))}/${suffix}`;
}

export async function approveKolSearchSession(
  token: string,
  sessionId: string | number,
  kolPoolIds: number[],
): Promise<Row> {
  return apiFetch<Row>(
    _sessionPath(sessionId, "approve"),
    { method: "POST", body: jsonBody({ kol_pool_ids: kolPoolIds }), cache: "no-store" },
    token,
  );
}

export async function estimateKolSearchSessionCost(
  token: string,
  sessionId: string | number,
  params: { kolPoolIds?: number[]; postsPerCreator?: number } = {},
): Promise<Row> {
  const body: Row = {};
  if (Array.isArray(params.kolPoolIds) && params.kolPoolIds.length) body.kol_pool_ids = params.kolPoolIds;
  if (typeof params.postsPerCreator === "number") body.posts_per_creator = params.postsPerCreator;
  return apiFetch<Row>(
    _sessionPath(sessionId, "cost-estimate"),
    { method: "POST", body: jsonBody(body), cache: "no-store" },
    token,
  );
}

export async function createProjectDraftFromSession(
  token: string,
  sessionId: string | number,
  params: {
    projectName?: string;
    productSku?: string;
    productName?: string;
    platform?: string;
    productPositioning?: string;
    targetPersona?: string;
  } = {},
): Promise<Row> {
  const body: Row = {};
  if (params.projectName) body.project_name = params.projectName;
  if (params.productSku) body.product_sku = params.productSku;
  if (params.productName) body.product_name = params.productName;
  if (params.platform) body.platform = params.platform;
  if (params.productPositioning) body.product_positioning = params.productPositioning;
  if (params.targetPersona) body.target_persona = params.targetPersona;
  return apiFetch<Row>(
    _sessionPath(sessionId, "create-project-draft"),
    { method: "POST", body: jsonBody(body), cache: "no-store" },
    token,
  );
}

export async function generateKolSearchSessionOutreach(
  token: string,
  sessionId: string | number,
  params: { productPositioning?: string; targetPersona?: string; productName?: string } = {},
): Promise<Row> {
  const body: Row = {};
  if (params.productPositioning) body.product_positioning = params.productPositioning;
  if (params.targetPersona) body.target_persona = params.targetPersona;
  if (params.productName) body.product_name = params.productName;
  return apiFetch<Row>(
    _sessionPath(sessionId, "generate-outreach"),
    { method: "POST", body: jsonBody(body), cache: "no-store" },
    token,
  );
}

export async function recallKolProfiles(
  token: string,
  params: {
    queryText?: string;
    productSku?: string;
    candidateLimit?: number;
    limit?: number;
    creatorQuota?: number;
    reviewerQuota?: number;
    ratioPolicy?: "soft" | string;
    mixedPolicy?: "dominant" | string;
    dedupe?: boolean;
  } = {},
): Promise<VkpiKolRecallResponse> {
  const query = new URLSearchParams({
    candidate_limit: String(params.candidateLimit || 50),
    limit: String(params.limit || 10),
    creator_quota: String(params.creatorQuota ?? 7),
    reviewer_quota: String(params.reviewerQuota ?? 3),
    ratio_policy: params.ratioPolicy || "soft",
    mixed_policy: params.mixedPolicy || "dominant",
    dedupe: String(params.dedupe ?? true),
  });
  if (params.queryText) query.set("query_text", params.queryText);
  if (params.productSku) query.set("product_sku", params.productSku);
  return apiFetch<VkpiKolRecallResponse>(
    `/api/admin/vkpi/kol-recall?${query.toString()}`,
    {},
    token,
  );
}
