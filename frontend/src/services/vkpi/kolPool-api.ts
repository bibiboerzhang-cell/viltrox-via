import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface VkpiKolPoolItem {
  id: number;
  pool_uid: string;
  platform: string;
  handle: string;
  profile_url?: string;
  display_name?: string;
  avatar_url?: string;
  bio?: string;
  country?: string;
  email?: string;
  followers?: number;
  following?: number;
  posts_count?: number;
  avg_views?: number;
  avg_likes?: number;
  avg_comments?: number;
  engagement_rate?: number;
  primary_topic?: string;
  content_style?: string;
  production_quality?: string;
  viltrox_fit_score?: number;
  viltrox_fit_reason?: string;
  linked_main_kol_id?: number | null;
  source_type?: string;
  source_ref?: string;
  raw_platform_data?: string | Record<string, unknown>;
  recommended_product_lines_json?: string | unknown[];
  potential_concerns_json?: string | unknown[];
  brand_collaborations_json?: string | unknown[];
  sync_status?: string;
  created_at?: string;
  updated_at?: string;
  last_seen_at?: string;
  video_evidence?: Array<Record<string, unknown>>;
}

export interface VkpiKolVideoAnalysisCacheEntry {
  target_type: string;
  target_id: string;
  derive_method: string;
  model?: string | null;
  cost?: number | null;
  status: string;
  result?: unknown;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface VkpiKolVideoAnalysisCacheResponse {
  target_type: string;
  target_id: string;
  derive_method?: string | null;
  state: "ready" | "pending";
  entry?: VkpiKolVideoAnalysisCacheEntry | null;
}

export interface VkpiVideoAnalysisEnqueueResponse {
  status: "queued" | "already_analyzed" | "already_queued" | "budget_denied" | string;
  kol_pool_id?: number;
  evidence_id?: number;
  derive_method?: string;
  message?: string;
  reason?: string;
  job?: Record<string, unknown> | null;
  cache?: Record<string, unknown> | null;
  budget?: Record<string, unknown> | null;
}

export interface VkpiKolLlmDeepAnalysisResult {
  id?: number;
  kol_pool_id?: number;
  source_url?: string;
  source_evidence_id?: number | null;
  analysis_kind?: string;
  llm_v6_fit?: number | null;
  llm_dimensions_11?: Record<string, unknown>;
  method?: string;
  provider?: string;
  confidence?: number | null;
  source_cache_id?: number | null;
  status?: string;
  created_at?: string;
  llm_has_qa?: boolean;
  llm_qa_pass?: boolean | null;
}

export interface VkpiKolLlmDeepAnalysisResponse {
  status: "ready" | "missing";
  kol_pool_id: number;
  summary?: Record<string, unknown>;
  primary_result?: VkpiKolLlmDeepAnalysisResult | null;
  items?: VkpiKolLlmDeepAnalysisResult[];
  count?: number;
}

export interface VkpiKolPoolFreshness {
  kol_pool_id: number;
  tier: string;
  tier_reason?: string;
  last_refresh_at?: string;
  last_refresh_status?: string;
  threshold_days?: number;
  days_old?: number | null;
  needs_refresh?: boolean;
  reason?: string;
}

export interface VkpiKolPoolRefreshState {
  triggered: boolean;
  reason: string;
  task_id?: string;
  task_type?: string;
  lock_key?: string;
  freshness?: VkpiKolPoolFreshness;
  message?: string;
}

export interface VkpiKolPoolWorkspaceResponse {
  status: "ready" | string;
  method?: string;
  query?: Row;
  summary?: Row;
  counts?: Row;
  filter_options?: Row;
  market_coverage?: Row;
  list?: {
    items?: VkpiKolPoolItem[];
    limit?: number;
    offset?: number;
    sort_by?: string;
    returned?: number;
    has_more?: boolean;
  };
  diagnostics?: Row;
}

export interface VkpiKolPoolDetailBundleResponse {
  status: "ready" | string;
  method?: string;
  kol_pool_id: number;
  item?: VkpiKolPoolItem;
  dimensions11?: Row | null;
  llm_deep_analysis?: VkpiKolLlmDeepAnalysisResponse | null;
  video_analysis?: {
    items?: Array<{
      video?: Row;
      final_entry?: VkpiKolVideoAnalysisCacheEntry | null;
      qa_entry?: VkpiKolVideoAnalysisCacheEntry | null;
      state?: string;
    }>;
    summary?: Row;
  };
  diagnostics?: Row;
}

export interface VkpiKolPoolIntelligenceCard {
  mode: string;
  generated_at: string;
  provider_calls: boolean;
  llm_calls: boolean;
  write_db: boolean;
  kol_pool_id: number;
  item: VkpiKolPoolItem;
  freshness?: Record<string, unknown>;
  dimensions11?: Record<string, unknown>;
  competitors?: Record<string, unknown>;
  brand_signal?: Record<string, unknown>;
  comment_intelligence?: Record<string, unknown>;
  video_analysis?: Record<string, unknown>;
  memory_card?: Record<string, unknown>;
  product_fit?: Record<string, unknown>;
  decision_support?: Record<string, unknown>;
  evidence_index?: Array<Record<string, unknown>>;
}

export interface VkpiKolPoolEvidenceSummary {
  mode: string;
  generated_at: string;
  kol_pool_id: number;
  provider_calls: boolean;
  llm_calls: boolean;
  write_db: boolean;
  passed?: boolean;
  policy?: Record<string, unknown>;
  checks?: Record<string, unknown>;
  summaries?: Array<Record<string, unknown>>;
  summary_count?: number;
  evidence_ref_count?: number;
  llm_budget_preflight?: Record<string, unknown>;
}

export interface VkpiKolPoolAiBrief {
  mode: string;
  generated_at: string;
  kol_pool_id: number;
  provider_calls: boolean;
  llm_calls: boolean;
  write_db: boolean;
  passed?: boolean;
  policy?: Record<string, unknown>;
  checks?: Record<string, unknown>;
  headline?: string;
  brief_items?: Array<Record<string, unknown>>;
  next_actions?: Array<Record<string, unknown>>;
  evidence_backlinks?: Array<Record<string, unknown>>;
  brief_item_count?: number;
  next_action_count?: number;
  evidence_backlink_count?: number;
}

export interface VkpiKolPoolGeminiPreflight {
  mode: string;
  generated_at: string;
  kol_pool_id: number;
  provider_calls: boolean;
  llm_calls: boolean;
  write_db: boolean;
  sync_triggered?: boolean;
  task_enqueued?: boolean;
  policy?: Record<string, unknown>;
  item?: Record<string, unknown>;
  candidate_strategy?: Record<string, unknown>;
  top_candidate?: Record<string, unknown>;
  candidate_sample?: Array<Record<string, unknown>>;
  url_readiness?: Record<string, unknown>;
  field_contract?: Record<string, unknown>;
  budget_preflight?: Record<string, unknown>;
  go_no_go?: Record<string, unknown>;
  checks?: Record<string, unknown>;
}

export interface VkpiKolPoolGeminiGoNoGo {
  mode: string;
  generated_at: string;
  kol_pool_id: number;
  decision: string;
  decision_reason?: string;
  blockers?: string[];
  provider_calls: boolean;
  llm_calls: boolean;
  write_db: boolean;
  sync_triggered?: boolean;
  task_enqueued?: boolean;
  summary?: Record<string, unknown>;
  budget_gate?: Record<string, unknown>;
  operator_gates?: Record<string, unknown>;
  risks?: Array<Record<string, unknown>>;
  next_steps?: string[];
  checks?: Record<string, unknown>;
  passed?: boolean;
  preflight?: VkpiKolPoolGeminiPreflight;
}

export interface VkpiKolRecallItem {
  kol_pool_id: number;
  handle: string;
  display_name?: string;
  platform?: string;
  profile_url?: string;
  avatar_url?: string;
  followers?: number | null;
  bio?: string;
  vector_score: number;
  type_rank_score?: number;
  recall_rank_score?: number;
  recall_rank_score_method?: string;
  // 独立展示信号(后端 profile_recall 产出,绝不并入 viltrox_fit_score)
  display_rank_score?: number;
  display_relevance_adjust?: number;
  relevance_flags?: string[];
  relevance_tier_hint?: string;
  profile_type: "creator" | "reviewer" | "mixed" | string;
  bucket: "creator" | "reviewer" | string;
  type_label: string;
  creator_type_score: number;
  reviewer_type_score: number;
  type_reason?: string;
  type_method?: string;
  recall_reason?: string;
  why_fit?: string;
  used_lenses?: string[];
  used_lenses_note?: string;
  representative_evidence?: Array<{
    title?: string;
    content_url?: string;
    thumbnail_url?: string;
    view_count?: number | null;
    like_count?: number | null;
  }>;
  source_fields?: Record<string, unknown>;
}

export interface VkpiKolRecallResponse {
  method: "vector_recall" | string;
  query: Row;
  ratio: {
    creator_quota: number;
    reviewer_quota: number;
    policy: string;
    mixed_policy: string;
    dedupe: boolean;
  };
  items: VkpiKolRecallItem[];
  buckets: {
    creator: VkpiKolRecallItem[];
    reviewer: VkpiKolRecallItem[];
  };
  diagnostics: Row & {
    candidate_count?: number;
    creator_candidate_count?: number;
    reviewer_candidate_count?: number;
    creator_returned?: number;
    reviewer_returned?: number;
    returned_count?: number;
  };
}

export interface VkpiKolSearchSessionRef {
  id?: number;
  session_id?: number;
  items_written?: number;
  jobs_linked?: number;
  items?: Row[];
  status?: string;
}

export interface VkpiKolSearchSessionItem {
  id?: number;
  session_id?: number;
  item_type?: string;
  status?: string;
  stage?: string;
  rank?: number;
  score?: number;
  kol_pool_id?: number;
  evidence_id?: number;
  job_id?: number;
  source_url?: string;
  payload?: Row;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiKolSearchHistoryItem {
  id?: number;
  query_text?: string;
  query_type?: "url_video" | "url_profile" | "text_recall" | "unknown" | string;
  source?: string;
  status?: string;
  input_payload?: Row;
  result_summary?: Row;
  summary?: Row;
  item_count?: number;
  items_preview?: VkpiKolSearchSessionItem[];
  active_items?: VkpiKolSearchSessionItem[];
  items?: VkpiKolSearchSessionItem[];
  counts?: Row;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiKolSearchHistoryResponse {
  status: "ready" | string;
  count?: number;
  items?: VkpiKolSearchHistoryItem[];
  filters?: Row;
}

export interface VkpiKolUrlDeepCrawlResponse {
  method?: string;
  dry_run?: boolean;
  execute?: boolean;
  writes_performed?: boolean;
  provider_calls_performed?: boolean;
  url?: {
    input?: string;
    normalized?: string;
  };
  url_type?: "profile" | "video" | "unknown" | string;
  platform?: string | null;
  handle?: string | null;
  channel_id?: string | null;
  video_id?: string | null;
  in_pool?: boolean;
  matched_kol_pool_id?: number | null;
  candidates?: Array<{
    kol_pool_id?: number;
    platform?: string;
    handle?: string;
    display_name?: string;
    profile_url?: string;
    match_source?: string;
    match_priority?: number;
  }>;
  next_action?: string | Row;
  profile_flow?: Row & {
    status?: string;
    message?: string;
    operation?: "update" | "insert" | string;
    kol_pool_id?: number | null;
    target?: string;
    max_posts?: number;
    would_crawl?: Row;
    safe_writer_dry_run?: Row & {
      fields_to_write?: string[];
      ignored_fields?: string[];
      missing_columns?: string[];
      viltrox_fit_score_changed_ids?: number[];
      viltrox_fit_score_untouched?: boolean;
    };
  };
  video_flow?: Row & {
    status?: string;
    message?: string;
    operation?: "video_creator_resolve" | "existing_creator_video_analysis" | "new_creator_video_analysis" | string;
    kol_pool_id?: number | null;
    evidence_id?: number | null;
    run_id?: number | null;
    run_status?: string;
    worker_touched?: boolean;
    creator_resolution_status?: "resolved" | "unresolved" | string;
    creator_identity?: Row | null;
    video_metadata?: Row | null;
    cached_video_url?: string | null;
    evidence_result?: Row;
    enqueue_result?: Row;
    profile_flow?: Row;
    viltrox_fit_score_changed_ids?: number[];
    viltrox_fit_score_untouched?: boolean;
  };
  video_metadata?: Row | null;
  creator_identity?: Row | null;
  safety?: Row;
  search_session?: VkpiKolSearchSessionRef | Row;
}

export interface VkpiKolSmartSearchResponse {
  status: "ready" | string;
  mode: "url" | "text" | string;
  query_type?: "url_video" | "url_profile" | "text_recall" | "unknown" | string;
  branch?: "kol_url_deep_crawl" | "kol_recall" | string;
  result?: VkpiKolUrlDeepCrawlResponse | VkpiKolRecallResponse | Row;
  search_session?: VkpiKolSearchSessionRef | Row;
  provider_calls?: boolean;
  provider_note?: string;
  llm_query_plan?: Row;
  new_discovery_status?: string;
  viltrox_fit_score_untouched?: boolean;
}

export interface VkpiKolSmartSearchProfileAdvanceResponse {
  status: "queued" | "already_queued" | "nothing_to_queue" | string;
  mode?: "text" | string;
  query_type?: "text_recall" | string;
  branch?: string;
  query?: string;
  original_query?: string;
  llm_query_plan?: Row;
  search_session?: VkpiKolSearchSessionRef | Row;
  advance_job?: Row;
  provider_calls?: boolean;
  provider_note?: string;
  write_db?: boolean;
  writes?: string[];
  viltrox_fit_score_changed_ids?: number[];
  viltrox_fit_score_untouched?: boolean;
}

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

export async function deepCrawlKolUrl(
  token: string,
  url: string,
  execute = false,
  params: { maxPosts?: number; mode?: string; timeoutMs?: number; sessionId?: number; createSession?: boolean; source?: string; forceFullHistory?: boolean; representativeVideoLimit?: number } = {},
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
    productSku?: string;
    sessionId?: number;
    createSession?: boolean;
    excludeChinese?: boolean;
    timeoutMs?: number;
  } = {},
): Promise<VkpiKolSmartSearchResponse> {
  const body: Row = {
    input,
    mode: params.mode || "auto",
    create_session: params.createSession ?? true,
  };
  if (params.execute) body.execute = true;
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (typeof params.candidateLimit === "number") body.candidate_limit = params.candidateLimit;
  if (typeof params.limit === "number") body.limit = params.limit;
  if (typeof params.creatorQuota === "number") body.creator_quota = params.creatorQuota;
  if (typeof params.reviewerQuota === "number") body.reviewer_quota = params.reviewerQuota;
  if (params.productSku) body.product_sku = params.productSku;
  if (typeof params.sessionId === "number") body.session_id = params.sessionId;
  if (typeof params.excludeChinese === "boolean") body.exclude_chinese = params.excludeChinese;
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
    advanceLimit?: number;
    maxPosts?: number;
    representativeVideoLimit?: number;
    includeNewDiscovery?: boolean;
    newDiscoveryLimit?: number;
    newDiscoveryPlatforms?: string[];
    excludeChinese?: boolean;
    market?: string;
    timeoutMs?: number;
  } = {},
): Promise<VkpiKolSmartSearchProfileAdvanceResponse> {
  const body: Row = {
    input,
    queue_pipeline: true,
    include_new_discovery: params.includeNewDiscovery ?? true,
  };
  if (params.newDiscoveryPlatforms?.length) body.new_discovery_platforms = params.newDiscoveryPlatforms;
  // 区域语言本地化:目标市场码(JP/KR/DE/...)→ 后端按该区语言搜平台;空=全球英文。
  if (params.market) body.market = params.market;
  if (typeof params.excludeChinese === "boolean") body.exclude_chinese = params.excludeChinese;
  if (typeof params.candidateLimit === "number") body.candidate_limit = params.candidateLimit;
  if (typeof params.limit === "number") body.limit = params.limit;
  if (typeof params.creatorQuota === "number") body.creator_quota = params.creatorQuota;
  if (typeof params.reviewerQuota === "number") body.reviewer_quota = params.reviewerQuota;
  if (typeof params.advanceLimit === "number") body.advance_limit = params.advanceLimit;
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (typeof params.representativeVideoLimit === "number") body.representative_video_limit = params.representativeVideoLimit;
  if (typeof params.newDiscoveryLimit === "number") body.new_discovery_limit = params.newDiscoveryLimit;
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
  params: { limit?: number; status?: string; queryType?: string; itemLimit?: number } = {},
): Promise<VkpiKolSearchHistoryResponse> {
  const query = new URLSearchParams({
    limit: String(params.limit || 12),
    item_limit: String(params.itemLimit ?? 5),
  });
  if (params.status) query.set("status", params.status);
  if (params.queryType) query.set("query_type", params.queryType);
  return apiFetch<VkpiKolSearchHistoryResponse>(
    `/api/admin/vkpi/kol-search-history?${query.toString()}`,
    { cache: "no-store" },
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
    kolPoolIds?: number[];
    projectName?: string;
    productSku?: string;
    productName?: string;
    platform?: string;
    productPositioning?: string;
    targetPersona?: string;
  } = {},
): Promise<Row> {
  const body: Row = {};
  if (Array.isArray(params.kolPoolIds) && params.kolPoolIds.length) body.kol_pool_ids = params.kolPoolIds;
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
  params: { kolPoolIds?: number[]; productPositioning?: string; targetPersona?: string; productName?: string } = {},
): Promise<Row> {
  const body: Row = {};
  if (Array.isArray(params.kolPoolIds) && params.kolPoolIds.length) body.kol_pool_ids = params.kolPoolIds;
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

// 待分析:库内有视频证据但还没 ready 深析的 KOL（供「批量入队分析」）。
export interface VkpiKolNeedsAnalysisItem {
  kol_pool_id: number;
  handle: string;
  platform: string;
  display_name?: string;
  avatar_url?: string | null;
  followers?: number | null;
  evidence_id: number | null;
  evidence_count?: number;
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
