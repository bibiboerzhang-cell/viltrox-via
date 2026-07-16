import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface VkpiAuthorizationEvidence {
  authorizationRef: string;
  reason: string;
  confirmedByHuman: boolean;
}

export interface VkpiAttributionPayload {
  sourcePlatform: "shopify" | "amazon" | "manual" | "custom";
  sourceRef: string;
  projectId?: string;
  linkId?: string;
  productSku?: string;
  orderId?: string;
  revenueUsd: number;
  commissionUsd?: number;
  confidence?: string;
  occurredAt?: string;
  authorizationEvidence?: VkpiAuthorizationEvidence;
}

export interface VkpiAmazonImportPayload {
  projectId?: string;
  amazonTag?: string;
  asin?: string;
  marketplace?: string;
  reportDate?: string;
  rows: Row[];
  authorizationEvidence?: VkpiAuthorizationEvidence;
}

export async function createSalesAttribution(token: string, payload: VkpiAttributionPayload) {
  return apiFetch<Row>(
    "/api/marketing/attribution",
    {
      method: "POST",
      body: jsonBody({
        source_platform: payload.sourcePlatform,
        source_ref: payload.sourceRef,
        project_id: payload.projectId ? Number(payload.projectId) : undefined,
        link_id: payload.linkId ? Number(payload.linkId) : undefined,
        product_sku: payload.productSku,
        order_id: payload.orderId ? Number(payload.orderId) : undefined,
        revenue_usd: payload.revenueUsd,
        commission_usd: payload.commissionUsd,
        confidence: payload.confidence || "pending",
        occurred_at: payload.occurredAt,
        authorization_evidence: payload.authorizationEvidence
          ? {
              authorization_ref: payload.authorizationEvidence.authorizationRef,
              reason: payload.authorizationEvidence.reason,
              confirmed_by_human: payload.authorizationEvidence.confirmedByHuman,
            }
          : undefined,
      }),
    },
    token,
  );
}

export async function importAmazonAttributionRows(token: string, payload: VkpiAmazonImportPayload) {
  return apiFetch<Row>(
    "/api/marketing/attribution/amazon/import",
    {
      method: "POST",
      body: jsonBody({
        project_id: payload.projectId ? Number(payload.projectId) : undefined,
        amazon_tag: payload.amazonTag,
        asin: payload.asin,
        marketplace: payload.marketplace || "US",
        report_date: payload.reportDate,
        rows: payload.rows,
        authorization_evidence: payload.authorizationEvidence
          ? {
              authorization_ref: payload.authorizationEvidence.authorizationRef,
              reason: payload.authorizationEvidence.reason,
              confirmed_by_human: payload.authorizationEvidence.confirmedByHuman,
            }
          : undefined,
      }),
    },
    token,
  );
}

export async function uploadAmazonAttributionReport(
  token: string,
  payload: Omit<VkpiAmazonImportPayload, "rows"> & { file: File },
) {
  const form = new FormData();
  form.set("file", payload.file);
  if (payload.projectId) form.set("project_id", payload.projectId);
  if (payload.amazonTag) form.set("amazon_tag", payload.amazonTag);
  if (payload.asin) form.set("asin", payload.asin);
  form.set("marketplace", payload.marketplace || "US");
  if (payload.reportDate) form.set("report_date", payload.reportDate);
  if (payload.authorizationEvidence) {
    form.set("authorization_ref", payload.authorizationEvidence.authorizationRef);
    form.set("authorization_reason", payload.authorizationEvidence.reason);
    form.set("confirmed_by_human", String(payload.authorizationEvidence.confirmedByHuman));
  }
  return apiFetch<Row>("/api/marketing/attribution/amazon/upload", { method: "POST", body: form }, token);
}

export async function runShopifySync(token: string, payload: Row = {}) {
  return apiFetch<Row>("/api/marketing/shopify/sync", { method: "POST", body: jsonBody(payload) }, token);
}

export async function runShopifyBackfill(token: string, payload: Row = {}) {
  return apiFetch<Row>("/api/marketing/shopify/backfill", { method: "POST", body: jsonBody(payload) }, token);
}

export interface ShopifySyncRun {
  id?: number;
  sync_uid?: string;
  source?: string;
  started_at?: string;
  completed_at?: string | null;
  status?: string;
  orders_received?: number;
  orders_matched?: number;
  orders_unmatched?: number;
  orders_failed?: number;
  error_message?: string;
  triggered_by_staff_id?: number | null;
  metadata?: Record<string, unknown>;
}

export interface ShopifyProviderStatus {
  provider_status: "connected" | "configured" | "not_configured";
  shop_domain?: string;
  shop_domain_configured?: boolean;
  access_token_configured?: boolean;
  webhook_secret_configured?: boolean;
  credential_source?: "db" | "env" | "none" | string;
  credential_fields?: Record<string, boolean>;
  webhooks?: { orders_create?: string; orders_refund_create?: string };
  sync_runs?: ShopifySyncRun[];
  message?: string;
}

export async function getShopifyStatus(token: string, limit = 10) {
  return apiFetch<ShopifyProviderStatus>(
    `/api/marketing/shopify/status?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function listAmazonAttributions(
  token: string,
  options: { staffId?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.staffId) params.set("staff_id", options.staffId);
  return apiFetch<{ attributions?: Row[] }>(
    `/api/marketing/attribution/amazon?${params.toString()}`,
    {},
    token,
  );
}

export async function getAmazonAttributionSummary(
  token: string,
  options: { staffId?: string; limit?: number } = {},
) {
  const params = new URLSearchParams({ limit: String(options.limit || 100) });
  if (options.staffId) params.set("staff_id", options.staffId);
  return apiFetch<{ items?: Row[]; totals?: Row }>(
    `/api/marketing/attribution/amazon/summary?${params.toString()}`,
    {},
    token,
  );
}
