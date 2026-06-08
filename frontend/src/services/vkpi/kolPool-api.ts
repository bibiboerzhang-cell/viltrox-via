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
  profile_type: "creator" | "reviewer" | "mixed" | string;
  bucket: "creator" | "reviewer" | string;
  type_label: string;
  creator_type_score: number;
  reviewer_type_score: number;
  type_reason?: string;
  type_method?: string;
  recall_reason?: string;
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
    evidence_result?: Row;
    enqueue_result?: Row;
    profile_flow?: Row;
    viltrox_fit_score_changed_ids?: number[];
    viltrox_fit_score_untouched?: boolean;
  };
  video_metadata?: Row | null;
  creator_identity?: Row | null;
  safety?: Row;
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
  params: { maxPosts?: number; mode?: string; timeoutMs?: number } = {},
): Promise<VkpiKolUrlDeepCrawlResponse> {
  const body: Row = {
    url,
    execute,
  };
  if (typeof params.maxPosts === "number") body.max_posts = params.maxPosts;
  if (params.mode) body.mode = params.mode;
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

export async function getKolPoolItem(token: string, kolPoolId: number, refreshIfStale = true) {
  const query = new URLSearchParams({ refresh_if_stale: String(refreshIfStale) });
  return apiFetch<{ item: VkpiKolPoolItem; freshness?: VkpiKolPoolFreshness; refresh?: VkpiKolPoolRefreshState }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}?${query.toString()}`,
    {},
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
