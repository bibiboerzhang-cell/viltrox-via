import { apiFetch, jsonBody } from "../http";
import type { VkpiKolLookupResult, VkpiKolProfile } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

// P-KOL-5 管理层 KOL 贡献度聚合(每个负责人一行,各指标分列)。
export interface VkpiContributionRollupItem {
  staff_id: number | null;
  staff_name: string | null;
  staff_email: string | null;
  active_kol_count: number;
  published_count: number;
  attributed_sales_usd: number;
  attribution_count: number;
  has_attribution: boolean;
}

export interface VkpiContributionRollupResponse {
  items?: VkpiContributionRollupItem[];
  window_days?: number;
  scope?: Row;
  data_notes?: Record<string, string>;
}

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

export interface VkpiMyKolAggregateResponse {
  staff?: Row;
  window_days?: number;
  official_matrix?: Row;
  // pool_favorites mirrors pool_favorites.list_favorites field names; the
  // aggregate hands `projects` (already-parsed) rather than `projects_json`.
  // 【M5】共享行(is_shared=true)另带 shared_by_staff_id / shared_by_name(共享人展示名)。
  pool_favorites?: Row[];
  projects?: Row[];
  claims?: Row[];
  kpi_summary?: Record<string, number>;
}

// 【M3/M5】KOL 详情抽屉的观看者上下文:该 KOL 是否共享给我(来自谁)+ active 认领(可否释放)。
//   GET /api/admin/vkpi/my-kol/{kol_pool_id}/viewer-context(纯只读)
export interface VkpiMyKolViewerContext {
  kol_pool_id?: number;
  share_origin?: {
    shared_by?: number | null;
    shared_by_name?: string | null;
    created_at?: string | null;
  } | null;
  claim?: {
    id?: number | null;
    staff_id?: number | null;
    staff_name?: string | null;
    claimed_at?: string | null;
    expires_at?: string | null;
    is_mine?: boolean;
    can_release?: boolean;
  } | null;
}

export async function getMyKolViewerContext(token: string, kolPoolId: string | number) {
  return apiFetch<VkpiMyKolViewerContext>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/viewer-context`,
    { cache: "no-store" },
    token,
  );
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

// P5: unified MY KOL read — the single source that includes pool_favorites.
// Employees always get their own aggregate; managers may pass staffId.
export async function getMyKolAggregate(
  token: string,
  params: { staffId?: number; windowDays?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.staffId != null) query.set("staff_id", String(params.staffId));
  if (params.windowDays != null) query.set("window_days", String(params.windowDays));
  const suffix = query.toString();
  return apiFetch<VkpiMyKolAggregateResponse>(
    `/api/admin/vkpi/my-kol/aggregate${suffix ? `?${suffix}` : ""}`,
    {},
    token,
  );
}

export async function getKolProfile(token: string, kolId: string) {
  return apiFetch<VkpiKolProfile>(`/api/marketing/kols/${encodeURIComponent(kolId)}/profile`, {}, token);
}

// P-KOL-5: 管理层视角 KOL 贡献度聚合(只读)。每个负责人一行,各指标分列。
// 后端用 scope.can_view_all 二次 gate;非管理层返回 403。
export async function fetchContributionRollup(
  token: string,
  params: { windowDays?: number } = {},
) {
  const query = new URLSearchParams();
  if (params.windowDays != null) query.set("window_days", String(params.windowDays));
  const suffix = query.toString();
  return apiFetch<VkpiContributionRollupResponse>(
    `/api/admin/vkpi/my-kol/contribution-rollup${suffix ? `?${suffix}` : ""}`,
    {},
    token,
  );
}

// P-GROUP-7 共享 KOL 池:把某 kol_pool_id 显式共享给某成员(只读授予)。
//   POST   /api/admin/vkpi/my-kol/{kol_pool_id}/share          body {staff_id}
//   DELETE /api/admin/vkpi/my-kol/{kol_pool_id}/share/{staff_id}
//   GET    /api/admin/vkpi/my-kol/{kol_pool_id}/share/members
export interface VkpiKolShareMember {
  id?: number | null;
  kol_pool_id?: number | null;
  staff_id?: number | null;
  shared_by?: number | null;
  created_at?: string | null;
  name?: string | null;
  email?: string | null;
}

export interface VkpiKolShareMembersResponse {
  kol_pool_id?: number;
  count?: number;
  items?: VkpiKolShareMember[];
}

export async function shareKolToStaff(token: string, kolPoolId: string | number, staffId: string | number) {
  return apiFetch<{ status?: string; kol_pool_id?: number; staff_id?: number; shared_by?: number | null }>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/share`,
    { method: "POST", body: jsonBody({ staff_id: staffId }) },
    token,
  );
}

export async function unshareKolFromStaff(token: string, kolPoolId: string | number, staffId: string | number) {
  return apiFetch<{ status?: string; kol_pool_id?: number; staff_id?: number }>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/share/${encodeURIComponent(String(staffId))}`,
    { method: "DELETE" },
    token,
  );
}

export async function listKolShareMembers(token: string, kolPoolId: string | number) {
  return apiFetch<VkpiKolShareMembersResponse>(
    `/api/admin/vkpi/my-kol/${encodeURIComponent(String(kolPoolId))}/share/members?_ts=${Date.now()}`,
    { cache: "no-store" },
    token,
  );
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
