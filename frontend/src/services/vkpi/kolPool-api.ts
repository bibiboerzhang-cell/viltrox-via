import { apiFetch, jsonBody } from "../http";

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

export async function listKolPool(
  token: string,
  params: { search?: string; platform?: string; country?: string; limit?: number; dataStatus?: string; sortBy?: string; enrichable?: boolean; refreshIfStale?: boolean } = {},
): Promise<{ items?: VkpiKolPoolItem[]; refresh?: VkpiKolPoolRefreshState }> {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
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

export async function getKolPoolItem(token: string, kolPoolId: number, refreshIfStale = true) {
  const query = new URLSearchParams({ refresh_if_stale: String(refreshIfStale) });
  return apiFetch<{ item: VkpiKolPoolItem; freshness?: VkpiKolPoolFreshness; refresh?: VkpiKolPoolRefreshState }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}?${query.toString()}`,
    {},
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

export async function getKolPoolIntelligenceCard(token: string, kolPoolId: number, includeProductFit = true) {
  const query = new URLSearchParams({ include_product_fit: String(includeProductFit) });
  return apiFetch<VkpiKolPoolIntelligenceCard>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/intelligence-card?${query.toString()}`,
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
