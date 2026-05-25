import { apiFetch, jsonBody } from "../http";
import type { VkpiProductCatalogItem } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const raw = value.trim();
  if (!raw) return value;
  try {
    return JSON.parse(raw);
  } catch {
    return value;
  }
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function buildProductCatalog(rows: Row[]): VkpiProductCatalogItem[] {
  return rows.map((row) => ({
    sku: String(row.sku || row.product_sku || ""),
    categoryMain: String(row.category_main || ""),
    categoryDetail: String(row.category_detail || ""),
    modelName: String(row.model_name || row.product_name || ""),
    marketingName: String(row.marketing_name || ""),
    priceUsd: row.price_usd === null || row.price_usd === undefined || row.price_usd === "" ? null : numberValue(row.price_usd),
    status: String(row.status || ""),
    description: String(row.description || ""),
    sourceFile: String(row.source_file || ""),
    series: String(row.series || ""),
    mount: String(row.mount || ""),
    productUrl: String(row.product_url || ""),
    specs: objectValue(parseJsonValue(row.specs_json)),
    fitTags: arrayValue(parseJsonValue(row.fit_tags_json)).map(String),
    sourceUrl: String(row.source_url || ""),
    sourceCheckedAt: String(row.source_checked_at || ""),
    sourceConfidence: numberValue(row.source_confidence),
  })).filter((row) => row.sku);
}

export async function runProductCompare(token: string, payload: Row) {
  return apiFetch<Row>(
    "/api/admin/vkpi/analytics/compare",
    { method: "POST", body: jsonBody(payload), timeoutMs: 180000 },
    token,
  );
}

export async function runProductMonitor(token: string, payload: Row) {
  return apiFetch<Row>(
    "/api/admin/vkpi/analytics/monitor",
    { method: "POST", body: jsonBody(payload), timeoutMs: 180000 },
    token,
  );
}

export async function listAnalyticsProducts(token: string) {
  return apiFetch<{ products?: Row[] }>("/api/admin/vkpi/analytics/products?limit=100", {}, token);
}

export async function listProductCatalog(
  token: string,
  params: { categories?: string[]; status?: string; query?: string; limit?: number } = {},
) {
  const query = new URLSearchParams({ limit: String(params.limit || 300) });
  if (params.categories?.length) query.set("categories", params.categories.join(","));
  if (params.status) query.set("status", params.status);
  if (params.query) query.set("query", params.query);
  const response = await apiFetch<{ products?: Row[]; summary?: Row[]; count?: number }>(
    `/api/marketing/product-catalog?${query.toString()}`,
    {},
    token,
  );
  return {
    products: buildProductCatalog(response.products || []),
    summary: response.summary || [],
    count: Number(response.count ?? 0),
  };
}

export async function upsertAnalyticsProduct(token: string, payload: Row) {
  return apiFetch<Row>("/api/admin/vkpi/analytics/products", { method: "POST", body: jsonBody(payload) }, token);
}

export async function listOutreachSuggestions(token: string, status = "new") {
  return apiFetch<{ suggestions?: Row[] }>(
    `/api/admin/vkpi/analytics/suggestions?status=${encodeURIComponent(status)}&limit=100`,
    {},
    token,
  );
}

export async function listDailyOutreachDigest(token: string, staffId?: string) {
  const params = new URLSearchParams({ limit: "100" });
  if (staffId) params.set("staff_id", staffId);
  return apiFetch<{ digest?: Row | null; items?: Row[]; digest_date?: string }>(
    `/api/admin/vkpi/analytics/daily-digest?${params.toString()}`,
    {},
    token,
  );
}

export async function getDailyOutreachDigestStatus(token: string, productSku = "") {
  const params = new URLSearchParams({ limit: "100" });
  if (productSku.trim()) params.set("product_sku", productSku.trim());
  return apiFetch<Row>(`/api/admin/vkpi/analytics/daily-digest/status?${params.toString()}`, {}, token);
}

export async function generateDailyOutreachDigest(token: string, productSku = "") {
  const body: Row = { limit: 100 };
  if (productSku.trim()) body.product_sku = productSku.trim();
  return apiFetch<Row>(
    "/api/admin/vkpi/analytics/daily-digest/generate",
    { method: "POST", body: jsonBody(body) },
    token,
  );
}

export async function claimOutreachSuggestion(token: string, suggestionId: string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/claim`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function createProjectFromOutreachSuggestion(token: string, suggestionId: string, payload: Row = {}) {
  return apiFetch<Row>(
    `/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/create-project`,
    { method: "POST", body: jsonBody({ auto_create_link: true, ...payload }) },
    token,
  );
}

export async function dismissOutreachSuggestion(token: string, suggestionId: string, reason = "") {
  return apiFetch<Row>(
    `/api/admin/vkpi/analytics/suggestions/${encodeURIComponent(suggestionId)}/dismiss`,
    { method: "POST", body: jsonBody({ reason }) },
    token,
  );
}

export async function listProductLaunches(token: string, options: { status?: string; limit?: number } = {}) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.status) params.set("status", options.status);
  return apiFetch<{ launches?: Row[] }>(
    `/api/admin/vkpi/product-analysis/launches?${params.toString()}`,
    {},
    token,
  );
}

export async function createProductLaunch(token: string, payload: Row) {
  return apiFetch<Row>("/api/admin/vkpi/product-analysis/launches", { method: "POST", body: jsonBody(payload) }, token);
}

export async function listProductKolPool(
  token: string,
  options: { platform?: string; query?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.platform) params.set("platform", options.platform);
  if (options.query) params.set("query", options.query);
  return apiFetch<{ items?: Row[]; rows?: Row[] }>(
    `/api/admin/vkpi/product-analysis/kol-pool?${params.toString()}`,
    {},
    token,
  );
}

export async function importProductKolPool(token: string, payload: { platform?: string; source_type?: string; source_ref?: string; items: Row[] }) {
  return apiFetch<Row>(
    "/api/admin/vkpi/product-analysis/kol-pool/import",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function runProductRecommendations(token: string, payload: Row) {
  return apiFetch<Row>(
    "/api/admin/vkpi/product-analysis/recommendations/run",
    { method: "POST", body: jsonBody(payload), timeoutMs: 120000 },
    token,
  );
}

export async function listProductRecommendations(
  token: string,
  options: { launchId?: string; runId?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.launchId) params.set("launch_id", options.launchId);
  if (options.runId) params.set("run_id", options.runId);
  return apiFetch<{ recommendations?: Row[] }>(
    `/api/admin/vkpi/product-analysis/recommendations?${params.toString()}`,
    {},
    token,
  );
}

export async function listProductRecommendationRuns(
  token: string,
  options: { strategyVersion?: string; status?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.strategyVersion) params.set("strategy_version", options.strategyVersion);
  if (options.status) params.set("status", options.status);
  return apiFetch<{ runs?: Row[] }>(
    `/api/admin/vkpi/product-analysis/recommendation-runs?${params.toString()}`,
    {},
    token,
  );
}

export async function getProductRecommendationOutcomeSummary(
  token: string,
  options: { launchId?: string; runId?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 50) });
  if (options.launchId) params.set("launch_id", options.launchId);
  if (options.runId) params.set("run_id", options.runId);
  return apiFetch<{ totals?: Row; conversion?: Row; by_status?: Row[]; by_platform?: Row[]; source_rows?: Row[]; source_count?: number }>(
    `/api/admin/vkpi/product-analysis/outcomes/summary?${params.toString()}`,
    {},
    token,
  );
}

export async function getProductRecommendationEvidence(token: string, recommendationId: string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/product-analysis/recommendations/${encodeURIComponent(recommendationId)}/evidence`,
    {},
    token,
  );
}

export async function productRecommendationAction(
  token: string,
  recommendationId: string,
  action: "shortlist" | "reject" | "feedback" | "claim" | "create_project",
  payload: Row = {},
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/product-analysis/recommendations/${encodeURIComponent(recommendationId)}/${encodeURIComponent(action)}`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}
