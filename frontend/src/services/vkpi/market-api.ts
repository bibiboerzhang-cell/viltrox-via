import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface CompetitorBrainSignalOptions {
  reviewStatus?: string;
  brand?: string;
  signalType?: string;
  limit?: number;
}

export interface ContentBrainPostOptions {
  status?: string;
  platform?: string;
  query?: string;
  limit?: number;
}

export interface MarketIntelligenceCardsOptions {
  limit?: number;
  runId?: number;
  brandLimit?: number;
  includeLatestLlmArtifact?: boolean;
  includeLatestExternalSmoke?: boolean;
}

export interface MarketExternalDailyPlanOptions {
  sourceGroup?: string;
  maxHttpCalls?: number;
  limitPerSource?: number;
}

export interface BrandSignalOptions {
  source?: string;
  since?: string;
  status?: string;
  signalType?: string;
  brandRole?: string;
  limit?: number;
}

export async function getCompetitorBrainStatus(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/industry-data/competitor-brain/status", {}, token);
}

export async function getContentBrainStatus(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/industry-data/content-brain/status", {}, token);
}

export async function listContentBrainPosts(token: string, options: ContentBrainPostOptions = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.status) params.set("status", options.status);
  if (options.platform) params.set("platform", options.platform);
  if (options.query) params.set("query", options.query);
  return apiFetch<{ posts?: Row[]; count?: number; schema_ready?: boolean }>(
    `/api/admin/vkpi/industry-data/content-brain/posts?${params.toString()}`,
    {},
    token,
  );
}

export async function listCompetitorBrainSignals(token: string, options: CompetitorBrainSignalOptions = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.reviewStatus) params.set("review_status", options.reviewStatus);
  if (options.brand) params.set("brand", options.brand);
  if (options.signalType) params.set("signal_type", options.signalType);
  return apiFetch<{ signals?: Row[]; count?: number; schema_ready?: boolean }>(
    `/api/admin/vkpi/industry-data/competitor-brain/signals?${params.toString()}`,
    {},
    token,
  );
}

export async function getCompetitorBrainReviewSuggestions(
  token: string,
  options: { reviewStatus?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({
    review_status: options.reviewStatus || "pending_review",
    limit: String(options.limit || 100),
  });
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/competitor-brain/review-suggestions?${params.toString()}`,
    {},
    token,
  );
}

export async function reviewCompetitorBrainSignal(
  token: string,
  signalId: string | number,
  payload: { action: string; note?: string },
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/competitor-brain/signals/${encodeURIComponent(String(signalId))}/review`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function getMarketIntelligenceV0(token: string, limit = 120) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/market-intelligence/v0?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function getMarketIntelligenceCardsV0(
  token: string,
  options: MarketIntelligenceCardsOptions = {},
) {
  const params = new URLSearchParams({
    limit: String(options.limit || 120),
    brand_limit: String(options.brandLimit || 5),
    include_latest_llm_artifact: String(options.includeLatestLlmArtifact ?? true),
    include_latest_external_smoke: String(options.includeLatestExternalSmoke ?? true),
  });
  if (options.runId) params.set("run_id", String(options.runId));
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/market-intelligence/cards/v0?${params.toString()}`,
    {},
    token,
  );
}

export async function getMarketExternalDailyPlanV0(
  token: string,
  options: MarketExternalDailyPlanOptions = {},
) {
  const params = new URLSearchParams({
    max_http_calls: String(options.maxHttpCalls || 6),
    limit_per_source: String(options.limitPerSource || 5),
  });
  if (options.sourceGroup) params.set("source_group", options.sourceGroup);
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/market-external-daily-plan/v0?${params.toString()}`,
    {},
    token,
  );
}

export async function previewBrandSignals(token: string, options: BrandSignalOptions = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 200) });
  if (options.source) params.set("source", options.source);
  if (options.since) params.set("since", options.since);
  return apiFetch<Row>(`/api/admin/vkpi/brand-signals/preview?${params.toString()}`, {}, token);
}

export async function listBrandSignals(token: string, options: BrandSignalOptions = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.status) params.set("status", options.status);
  if (options.signalType) params.set("signal_type", options.signalType);
  if (options.brandRole) params.set("brand_role", options.brandRole);
  return apiFetch<{ signals?: Row[]; count?: number; total_count?: number; schema_ready?: boolean }>(
    `/api/admin/vkpi/brand-signals?${params.toString()}`,
    {},
    token,
  );
}

export async function scanBrandSignals(
  token: string,
  payload: { source?: string; since?: string; limit?: number; write_db?: boolean },
) {
  return apiFetch<Row>(
    "/api/admin/vkpi/brand-signals/scan",
    { method: "POST", body: jsonBody(payload), timeoutMs: 120000 },
    token,
  );
}

export async function reviewBrandSignal(
  token: string,
  signalId: string | number,
  payload: { action: "contact" | "ignore" | "flag" | "compete" },
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/brand-signals/${encodeURIComponent(String(signalId))}/action`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}
