import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface VkpiCommentIntelligenceOverview {
  days: number;
  health: "ok" | "degraded" | "attention" | string;
  provider_calls?: boolean;
  llm_calls?: boolean;
  write_db?: boolean;
  runs: {
    total: number;
    by_status?: Record<string, number>;
    success_rate?: number | null;
    recent?: Row[];
    recent_failures?: Row[];
  };
  coverage: {
    comments_total: number;
    comments_with_sentiment: number;
    comments_with_pillar: number;
    sentiment_coverage?: number | null;
    comment_pillar_coverage?: number | null;
    pending_sentiment: number;
    pending_comment_pillar_links: number;
    posts_total: number;
    posts_with_primary_pillar: number;
    post_pillar_coverage?: number | null;
    sample_cap?: number;
    sample_status?: string;
  };
  comment_contract?: {
    declared?: number | null;
    cached?: number;
    cap?: number;
    status?: string;
  };
  rule_v0?: {
    status?: string;
    method?: string;
    source?: string;
    provider_calls?: boolean;
    llm_calls?: boolean;
    write_db?: boolean;
    contract?: {
      declared?: number | null;
      cached?: number;
      cap?: number;
      status?: string;
    };
    counts?: Record<string, number>;
    samples?: Row[];
  };
  distributions?: {
    sentiment?: Array<{ label?: string; count?: number }>;
    emotion?: Array<{ label?: string; count?: number }>;
    brand_attitude?: Array<{ label?: string; count?: number }>;
    pillars?: Array<{
      pillar_key?: string;
      display_name?: string;
      layer?: number;
      count?: number;
      primary_count?: number;
    }>;
  };
}

export async function getCommentIntelligenceOverview(
  token: string,
  options: { days?: number; recentLimit?: number } = {},
) {
  const params = new URLSearchParams({
    days: String(options.days || 7),
    recent_limit: String(options.recentLimit || 8),
  });
  return apiFetch<VkpiCommentIntelligenceOverview>(
    `/api/admin/vkpi/comment-intelligence/overview?${params.toString()}`,
    {},
    token,
  );
}

export async function processRecentCommentIntelligence(
  token: string,
  options: {
    platform?: string;
    days?: number;
    limit?: number;
    collectComments?: boolean;
    analyzeSentiment?: boolean;
    classifyPillar?: boolean;
    forceReprocess?: boolean;
  } = {},
) {
  const params = new URLSearchParams({
    days: String(options.days || 7),
    limit: String(options.limit || 10),
    collect_comments: String(options.collectComments ?? false),
    analyze_sentiment: String(options.analyzeSentiment ?? true),
    classify_pillar: String(options.classifyPillar ?? true),
    force_reprocess: String(options.forceReprocess ?? false),
  });
  if (options.platform) params.set("platform", options.platform);
  return apiFetch<Row>(
    `/api/admin/vkpi/comment-intelligence/process-recent?${params.toString()}`,
    { method: "POST", body: jsonBody({}), timeoutMs: 180000 },
    token,
  );
}

export async function retryCommentIntelligenceRun(token: string, runId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/comment-intelligence/runs/${encodeURIComponent(String(runId))}/retry`,
    { method: "POST", body: jsonBody({}), timeoutMs: 180000 },
    token,
  );
}
