export interface KolPoolFreshness {
  kol_pool_id: number;
  tier: string;
  tier_reason?: string;
  last_refresh_at?: string;
  last_refresh_status?: string;
  threshold_days?: number;
  days_old?: number | null;
  needs_refresh?: boolean;
  reason?: string;
  search_count_30d?: number;
  last_searched_at?: string;
}

export interface KolPoolRefreshState {
  triggered: boolean;
  reason: string;
  task_id?: string;
  task_type?: string;
  lock_key?: string;
  message?: string;
  provider_calls_enabled?: boolean;
  freshness?: KolPoolFreshness;
  search_marker?: {
    tier?: string;
    tier_reason?: string;
    search_count_30d?: number;
    last_searched_at?: string;
  };
}

export interface KolPoolIntelligenceCard {
  mode?: string;
  generated_at?: string;
  provider_calls?: boolean;
  llm_calls?: boolean;
  write_db?: boolean;
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

export interface KolPoolItem {
  id: number;
  pool_uid: string;
  platform: string;
  handle: string;
  profile_url?: string;
  display_name?: string;
  avatar_url?: string;
  bio?: string;
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
  created_at?: string;
  updated_at?: string;
  last_seen_at?: string;
  sync_status?: string;
  freshness?: KolPoolFreshness;
  refresh?: KolPoolRefreshState;
}

export interface KolPoolPanelProps {
  apiToken: string;
  onListPool: (params: { search?: string; platform?: string; limit?: number; dataStatus?: string; sortBy?: string; enrichable?: boolean; refreshIfStale?: boolean }) => Promise<{ items?: KolPoolItem[]; refresh?: KolPoolRefreshState }>;
  onGetItem?: (kolPoolId: number) => Promise<{ item?: KolPoolItem; freshness?: KolPoolFreshness; refresh?: KolPoolRefreshState }>;
  onGetIntelligenceCard?: (kolPoolId: number) => Promise<KolPoolIntelligenceCard>;
  onEnrichItem?: (kolPoolId: number, maxPosts?: number) => Promise<{
    item?: KolPoolItem;
    sync_status?: string;
    provider_status?: string;
    message?: string;
    posts_sampled?: number;
  }>;
  onBatchEnrich?: (payload: {
    ids?: number[];
    platform?: string;
    query?: string;
    dataStatus?: string;
    limit?: number;
    maxPosts?: number;
  }) => Promise<{
    attempted?: number;
    enriched?: number;
    complete?: number;
    partial?: Array<Record<string, unknown>>;
    skipped?: Array<Record<string, unknown>>;
    errors?: Array<Record<string, unknown>>;
    items?: KolPoolItem[];
    capped?: boolean;
  }>;
  onPromoteToMain?: (kolPoolId: number) => Promise<{
    linked?: boolean;
    mode?: string;
    main_kol_id?: number | null;
    item?: KolPoolItem;
  }>;
  onOpenImport?: () => void;
}

export const ENRICHABLE_PLATFORMS = new Set([
  'youtube',
  'instagram',
  'tiktok',
  'xiaohongshu',
  'x',
  'bilibili',
  'facebook',
  'reddit',
]);
