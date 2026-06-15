// Shopify Hub (三区合一) service layer — T2-shopify-hub-fe.
//
// Talks to the NEW T1 Shopify endpoints (credentials / promo-links / summary)
// plus a few EXISTING marketing list endpoints used to feed the generator
// dropdowns. All requests go through the shared apiFetch (auto Bearer + JSON).
//
// Secrets policy: saveShopifyCreds NEVER logs the request body. The backend
// stores credentials encrypted at rest and never echoes them back.

import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

// ---------------------------------------------------------------------------
// Region ① — Connect: save Shopify credentials (encrypted at rest, T1).
// ---------------------------------------------------------------------------

export interface ShopifyCredsInput {
  storeDomain: string;
  accessToken: string;
  webhookSecret: string;
}

export interface SaveShopifyCredsResult {
  status?: string;
  provider_status?: string;
  message?: string;
}

export async function saveShopifyCreds(
  token: string,
  { storeDomain, accessToken, webhookSecret }: ShopifyCredsInput,
): Promise<SaveShopifyCredsResult> {
  // Do NOT log the body — it carries secrets.
  return apiFetch<SaveShopifyCredsResult>(
    "/api/admin/vkpi/shopify/credentials",
    {
      method: "POST",
      body: jsonBody({
        store_domain: storeDomain,
        access_token: accessToken,
        webhook_secret: webhookSecret,
      }),
    },
    token,
  );
}

// ---------------------------------------------------------------------------
// Region ② — Generate: dropdown sources + promo link generation.
// ---------------------------------------------------------------------------

export interface ListSourcesResult {
  projects: Row[];
  events: Row[];
}

// Sources for the two-group dropdown: 项目 (marketing projects) + 活动 (vkpi events).
export async function listSources(token: string): Promise<ListSourcesResult> {
  const [projectsRes, eventsRes] = await Promise.all([
    apiFetch<{ projects?: Row[] }>("/api/marketing/projects?limit=100", {}, token),
    apiFetch<{ items?: Row[] }>("/api/admin/vkpi/events?limit=100", {}, token),
  ]);
  return {
    projects: Array.isArray(projectsRes?.projects) ? projectsRes.projects : [],
    events: Array.isArray(eventsRes?.items) ? eventsRes.items : [],
  };
}

export async function listPromoKols(token: string): Promise<{ kols?: Row[] }> {
  return apiFetch<{ kols?: Row[] }>("/api/marketing/kols?limit=300", {}, token);
}

export async function listPromoProducts(token: string): Promise<{ products?: Row[] }> {
  return apiFetch<{ products?: Row[] }>("/api/marketing/product-catalog?limit=300", {}, token);
}

export interface GeneratePromoLinkInput {
  sourceType: "project" | "event";
  sourceId: string;
  kolId: string;
  productSku: string;
  discountPercent?: number;
}

export interface GeneratePromoLinkResult {
  discount_code?: string;
  tracking_url?: string;
  discount_url?: string;
  slug?: string;
  message?: string;
}

export async function generatePromoLink(
  token: string,
  { sourceType, sourceId, kolId, productSku, discountPercent }: GeneratePromoLinkInput,
): Promise<GeneratePromoLinkResult> {
  return apiFetch<GeneratePromoLinkResult>(
    "/api/admin/vkpi/shopify/promo-links",
    {
      method: "POST",
      body: jsonBody({
        source_type: sourceType,
        source_id: sourceId,
        kol_id: kolId,
        product_sku: productSku,
        discount_percent: discountPercent,
      }),
    },
    token,
  );
}

// ---------------------------------------------------------------------------
// Region ③ — Track: promo attribution summary (KOL/来源/产品/点击/订单/GMV/ROI).
// ---------------------------------------------------------------------------

export interface PromoAttributionRow {
  kol_name?: string;
  source_label?: string;
  source_type?: string;
  product_sku?: string;
  clicks?: number;
  orders?: number;
  gmv_usd?: number;
  roi?: number;
}

export interface PromoAttributionSummary {
  items: PromoAttributionRow[];
  totals?: Record<string, unknown>;
}

export async function getPromoAttributionSummary(
  token: string,
  { limit = 50 }: { limit?: number } = {},
): Promise<PromoAttributionSummary> {
  const res = await apiFetch<{ items?: PromoAttributionRow[]; totals?: Record<string, unknown> }>(
    `/api/admin/vkpi/shopify/promo-links/summary?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
  return {
    items: Array.isArray(res?.items) ? res.items : [],
    totals: res?.totals,
  };
}
