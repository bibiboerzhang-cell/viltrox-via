import { apiFetch, jsonBody } from "../http";
import type { VkpiKolLookupResult, VkpiKolProfile } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiKolAssessmentResponse {
  kol_id?: number;
  score?: number;
  grade?: string;
  method?: string;
  dimensions?: Record<string, { score?: number; source?: string; reason?: string; status?: string }>;
  risk_flags?: Row[];
  recommended_action?: string;
  source_tables?: string[];
}

export interface VkpiKolProductFitResponse {
  kol_id?: number;
  items?: Array<{
    launch_id?: number | null;
    product_sku?: string;
    product_name?: string;
    launch_name?: string;
    score?: number;
    method?: string;
    status?: string;
    reasons?: string[];
    evidence?: string[];
  }>;
}

export interface VkpiKolContactsResponse {
  kol_id?: number;
  contacts?: Array<{
    id?: string;
    contact_type?: string;
    contact_value?: string;
    layer?: number;
    source?: string;
    confidence?: number;
    evidence?: string;
    verified?: boolean;
    status?: string;
  }>;
  summary?: Row;
}

export interface VkpiAddKolContactPayload {
  contactType: string;
  contactValue: string;
  evidence?: string;
  layer?: number;
  source?: string;
}

export interface VkpiKolLookupPayload {
  platform: string;
  handleOrUrl: string;
  createIfMissing?: boolean;
  email?: string;
  country?: string;
  followerCount?: number;
  avgViews?: number;
  contactEmail?: string;
  notes?: string;
  scanAccount?: boolean;
  maxPosts?: number;
  productSku?: string;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export async function getKolProfile(token: string, kolId: string) {
  return apiFetch<VkpiKolProfile>(`/api/marketing/kols/${encodeURIComponent(kolId)}/profile`, {}, token);
}

export async function getKolAssessment(token: string, kolId: string) {
  return apiFetch<VkpiKolAssessmentResponse>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/assessment`,
    {},
    token,
  );
}

export async function getKolProductFit(token: string, kolId: string, limit = 5) {
  return apiFetch<VkpiKolProductFitResponse>(
    `/api/admin/vkpi/kols/${encodeURIComponent(kolId)}/product-fit?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function listMarketingKols(token: string, params: { search?: string; platform?: string; limit?: number } = {}) {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.search) query.set("search", params.search);
  if (params.platform) query.set("platform", params.platform);
  return apiFetch<{ kols?: Row[]; scope?: Row }>(`/api/marketing/kols?${query.toString()}`, {}, token);
}

export async function lookupKol(token: string, payload: VkpiKolLookupPayload): Promise<VkpiKolLookupResult> {
  return apiFetch<VkpiKolLookupResult>(
    "/api/marketing/kols/lookup",
    {
      method: "POST",
      body: jsonBody({
        platform: payload.platform,
        handle_or_url: payload.handleOrUrl,
        url: payload.handleOrUrl,
        create_if_missing: Boolean(payload.createIfMissing),
        email: payload.email,
        contact_email: payload.contactEmail || payload.email,
        country: payload.country,
        follower_count: payload.followerCount,
        avg_views: payload.avgViews,
        notes: payload.notes,
        scan_account: Boolean(payload.scanAccount),
        max_posts: payload.maxPosts || 24,
        product_sku: payload.productSku,
      }),
    },
    token,
  );
}

export async function updateMarketingKol(token: string, kolId: string, payload: Row) {
  return apiFetch<{ kol?: Row }>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}`,
    {
      method: "PATCH",
      body: jsonBody({
        avatar_url: payload.avatarUrl,
        profile_url: payload.profileUrl,
        contact_email: payload.contactEmail,
        contact_phone: payload.contactPhone,
        notes: payload.notes,
        contact_links: payload.contactLinks,
      }),
    },
    token,
  );
}

export async function scanKolAccount(token: string, kolId: string, maxPosts = 24) {
  const scan = await apiFetch<Row>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/scan-account`,
    { method: "POST", body: jsonBody({ max_posts: maxPosts }), timeoutMs: 180000 },
    token,
  );
  if (numberValue(scan.content_count) > 0) {
    const analysis = await apiFetch<Row>(
      `/api/marketing/kols/${encodeURIComponent(kolId)}/analyze-account`,
      { method: "POST", body: jsonBody({}), timeoutMs: 120000 },
      token,
    );
    return { scan, analysis };
  }
  return { scan };
}

export async function claimKol(token: string, kolId: string, expiresDays = 14) {
  return apiFetch<Row>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/claim`,
    { method: "POST", body: jsonBody({ expires_days: expiresDays }) },
    token,
  );
}

export async function releaseKolClaim(token: string, claimId: string, reason = "employee_unfollow") {
  return apiFetch<Row>(
    `/api/marketing/claims/${encodeURIComponent(claimId)}/release`,
    { method: "POST", body: jsonBody({ reason }) },
    token,
  );
}

export async function listKolContacts(token: string, kolId: string, includeWrong = false) {
  return apiFetch<VkpiKolContactsResponse>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/contacts?include_wrong=${includeWrong ? "true" : "false"}`,
    {},
    token,
  );
}

export async function addKolContact(token: string, kolId: string, payload: VkpiAddKolContactPayload) {
  return apiFetch<VkpiKolContactsResponse>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/contacts`,
    {
      method: "POST",
      body: jsonBody({
        contact_type: payload.contactType,
        contact_value: payload.contactValue,
        evidence: payload.evidence,
        layer: payload.layer,
        source: payload.source,
      }),
    },
    token,
  );
}

export async function getKolPosts(token: string, kolId: string, params: { limit?: number; offset?: number } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 100),
    offset: String(params.offset || 0),
  });
  return apiFetch<{ items?: Row[]; page?: Row }>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/posts?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolComments(token: string, kolId: string, params: { limit?: number; offset?: number; postUrl?: string } = {}) {
  const query = new URLSearchParams({
    limit: String(params.limit || 100),
    offset: String(params.offset || 0),
  });
  if (params.postUrl) query.set("post_url", params.postUrl);
  return apiFetch<{ items?: Row[]; page?: Row }>(
    `/api/marketing/kols/${encodeURIComponent(kolId)}/comments?${query.toString()}`,
    {},
    token,
  );
}
