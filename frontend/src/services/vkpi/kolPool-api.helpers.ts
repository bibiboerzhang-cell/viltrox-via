// 行为不变抽取:kolPool-api.ts 的全部类型/接口定义(纯类型,零运行时)。
// 原文件 import type 回去。红线:只搬不改,viltrox_fit_score 字段定义逐字保留。

export type Row = Record<string, unknown>;

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
  other_contacts_json?: string | Array<Record<string, unknown>> | Record<string, unknown>;
  contact_phone?: string;
  phone?: string;
  phone_number?: string;
  mobile?: string;
  whatsapp?: string;
  contact_channels?: string | Array<Record<string, unknown>> | Record<string, unknown>;
  contact_links_json?: string | Array<Record<string, unknown>> | Record<string, unknown>;
  /** True when contact fields are a server-side redacted projection. */
  contact_masked?: boolean;
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
  /** 新旧服务滚动升级期间均为可选；UI 不会从字段缺失推断“已就绪”。 */
  analysis_readiness?: VkpiKolAnalysisReadiness | null;
  evidence_quality?: VkpiKolEvidenceQuality | null;
  claim_status?: string | null;
}

export interface VkpiKolPoolContactRevealResponse {
  /** `revealed` is retained for rolling backend compatibility. */
  status: "full" | "restricted" | "empty" | "revealed" | "masked";
  kol_pool_id: number;
  email?: string;
  other_contacts?: Array<Record<string, unknown>> | Record<string, unknown>;
  contacts?: Array<{
    id?: number | string;
    type?: string;
    kind?: string;
    channel?: string;
    platform?: string;
    contact_type?: string;
    value?: string;
    display_value?: string;
    contact_value?: string;
    label?: string;
    source?: string;
    source_kind?: string;
    source_label?: string;
    verification_status?: string;
    verified_at?: string;
    last_verified_at?: string;
  }>;
  contact_masked: boolean;
  reason?: string;
}

export interface VkpiKolAnalysisReadiness extends Row {
  version?: string;
  level?: string;
  status?: string;
  readiness?: string;
  recommendation_status?: string;
  decision_mode?: string;
  abstain?: boolean;
  conclusion_recommended?: boolean;
  can_conclude?: boolean;
  key_sample_count?: number;
  evidence_coverage?: Row;
  scopes?: {
    overall?: VkpiKolAnalysisReadiness | null;
    content_fit?: VkpiKolAnalysisReadiness | null;
    brand_history?: VkpiKolAnalysisReadiness | null;
  };
  blocking_gaps?: unknown[];
  blockers?: unknown[];
  gaps?: unknown[];
  claim_status?: string | null;
}

export interface VkpiKolEvidenceQuality extends Row {
  version?: string;
  level?: string;
  status?: string;
  recommendation_status?: string;
  decision_mode?: string;
  abstain?: boolean;
  evidence_count?: number;
  total_count?: number;
  ready_count?: number;
  analyzed_count?: number;
  coverage_ratio?: number;
  coverage_pct?: number;
  key_sample_count?: number;
  critical_sample_count?: number;
  evidence_coverage?: Row;
  scopes?: VkpiKolAnalysisReadiness["scopes"];
  blocking_gaps?: unknown[];
  blockers?: unknown[];
  gaps?: unknown[];
  claim_status?: string | null;
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
  state: "ready" | "pending" | "queued" | "running" | "retrying" | "processing" | "blocked" | "failed" | "not_requested" | "unknown";
  entry?: VkpiKolVideoAnalysisCacheEntry | null;
  /** 分析结果未落 cache 时的真实 Worker 状态；不含完整任务 payload 或原始错误。 */
  analysis_job?: {
    id?: number | null;
    status?: string | null;
    state?: string | null;
    error_category?: string | null;
    reason?: string | null;
    reason_detail?: string | null;
    provider?: string | null;
    stage?: string | null;
    created_at?: string | null;
    started_at?: string | null;
    updated_at?: string | null;
  } | null;
  // 视频目标:后端顺带解析的 R2 缓存视频地址,供内联播放器与分镜分析共用同一轮询(历史重建也稳)。
  cached_video_url?: string | null;
}

export interface VkpiKolVideoAnalysisBatchResponse {
  target_type: string;
  derive_method: string;
  count: number;
  items: Array<Pick<VkpiKolVideoAnalysisCacheResponse, "target_id" | "state" | "entry" | "analysis_job">>;
}

export interface VkpiVideoAnalysisEnqueueResponse {
  status: "queued" | "already_analyzed" | "already_queued" | "budget_denied" | string;
  kol_pool_id?: number;
  evidence_id?: number;
  derive_method?: string;
  message?: string;
  reason?: string;
  provider_gate_reason?: string;
  state?: string;
  stage?: string;
  terminal?: boolean;
  model_readiness_status?: string;
  provider_calls?: boolean;
  write_db?: boolean;
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
  result_kind?: "llm" | "local_aggregate" | "unverified_ai" | string;
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
  analysis_readiness?: VkpiKolAnalysisReadiness | null;
  evidence_quality?: VkpiKolEvidenceQuality | null;
  claim_status?: string | null;
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
  vector_score?: number | null;
  type_rank_score?: number | null;
  recall_rank_score?: number | null;
  recall_rank_score_method?: string;
  robust_rank_score?: number | null;
  precision_rank_score?: number | null;
  retrieval_score?: number | null;
  robust_rank_method?: string;
  precision_rank_method?: string;
  retrieval_method?: string;
  // 独立展示信号(后端 profile_recall 产出,绝不并入 viltrox_fit_score)
  display_rank_score?: number | null;
  display_relevance_adjust?: number | null;
  relevance_flags?: string[];
  relevance_tier_hint?: string;
  profile_type: "creator" | "reviewer" | "mixed" | string;
  bucket: "creator" | "reviewer" | string;
  type_label: string;
  creator_type_score: number | null;
  reviewer_type_score: number | null;
  type_reason?: string;
  type_method?: string;
  /** 业务分层；旧服务可能仅返回 bucket，前端会明确标记兼容映射。 */
  candidate_bucket?: "core_vertical" | "expansion" | "exploration" | string;
  business_lane?: "core_vertical" | "expansion" | "exploration" | string;
  candidate_lane?: "core_vertical" | "expansion" | "exploration" | string;
  candidate_bucket_reason?: string;
  candidate_bucket_target?: number;
  /** strict=全部显式筛选满足；backfill=仅相关性补位，不能放松显式硬筛选。 */
  match_tier?: "strict" | "backfill" | string;
  relaxed_filters?: string[];
  unknown_fields?: string[];
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
  evidence_confidence?: number | string | null;
  evidence_quality?: VkpiKolEvidenceQuality | (Row & { coverage?: unknown }) | null;
  ranking_confidence?: Row | null;
  platform_calibration?: Row | null;
  data_truth?: Row | null;
  session_replay_contract?: string;
  session_replay_complete?: boolean;
  session_replay_missing_fields?: string[];
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
    unknown?: VkpiKolRecallItem[];
    [bucket: string]: VkpiKolRecallItem[] | undefined;
  };
  diagnostics: Row & {
    candidate_count?: number;
    creator_candidate_count?: number;
    reviewer_candidate_count?: number;
    creator_returned?: number;
    reviewer_returned?: number;
    returned_count?: number;
    requested_count?: number;
    strict_count?: number;
    backfill_count?: number;
    strict_available_count?: number;
    backfill_available_count?: number;
    final_count?: number;
    shortfall?: number;
    result_contract_satisfied?: boolean;
    hard_filter_rejected_count?: number;
    hard_filter_rejected_by?: Record<string, number>;
    hard_filters_relaxed?: boolean;
    applied_filters?: Record<string, unknown>;
    backfill_policy?: string;
    unsupported_filters?: string[];
    business_bucket_counts?: Record<string, number>;
  };
  evaluation_status?: Row & {
    state?: "not_evaluated" | "labeling" | "shareable" | "stale" | string;
    gold_set_id?: string | null;
    dataset_version?: string | null;
    algorithm_version?: string | null;
    target_count?: number;
    labeled_count?: number;
    dual_review_target?: number;
    dual_reviewed_count?: number;
    disagreement_count?: number;
    claim_status?: string;
    metrics?: Row | null;
  };
}

export interface VkpiKolSearchSessionRef {
  id?: number;
  session_id?: number;
  items_written?: number;
  jobs_linked?: number;
  items?: Row[];
  status?: string;
  phase?: string;
  progress?: Row;
}

export interface VkpiKolSearchSessionItem {
  id?: number;
  session_id?: number;
  item_type?: string;
  status?: string;
  stage?: string;
  rank?: number;
  score?: number | null;
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
  /** 可选渐进阶段；旧后端缺失时前端从 result_summary/counts 诚实派生。 */
  phase?: string;
  progress?: Row;
  /** 统一、只读的真实进度合同。排队/运行/失败不计入 successful/progress_pct。 */
  progress_contract?: Row;
  /** 与 progress_contract.worker 同源的 Apify Worker 心跳快照。 */
  worker_health?: Row;
  /** 会话创建者；历史端点按当前员工隔离，缺失时前端必须显示未知。 */
  created_by?: number | null;
  input_payload?: Row;
  result_summary?: Row;
  /** 人审锁定候选；用于 KOL 档案历史的精确 ID 关联，不按 handle 文本猜测。 */
  approved_kol_ids?: number[];
  summary?: Row;
  item_count?: number;
  items_preview?: VkpiKolSearchSessionItem[];
  active_items?: VkpiKolSearchSessionItem[];
  items?: VkpiKolSearchSessionItem[];
  counts?: Row;
  /** 触达展示闸折叠计数(后端 get_session/list_history 按 pool 现值实时重判):
   *  hidden_low_reach=低触达不展示;hidden_analyzing=档案补全中(分析后再放出)。 */
  reach_floor_display?: Row;
  archived_at?: string | null;
  archived_by?: number | null;
  archive_reason?: string;
  created_at?: string;
  updated_at?: string;
}

export interface VkpiKolSearchHistoryArchiveResponse {
  status?: string;
  archive_status?: string;
  archived_count?: number;
  archived_session_ids?: number[];
  skipped_active_count?: number;
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
    resolution_progress?: Row;
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
