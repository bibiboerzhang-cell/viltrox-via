// GOAFFPRO 联盟营销接入 service layer — 自建短链退役迁移。
//
// 用户拍板:先接 GOAFFPRO,后隐藏自建短链。本模块薄封装后端
// /api/admin/vkpi/goaffpro/* 端点(creds 连接状态 / 保存凭据 / affiliate 列表),
// 全程走共享 apiFetch(自动 Bearer + JSON)。
//
// Secrets policy: saveGoaffproCredentials 绝不 log 请求体;token 由后端加密落库,
// 绝不回显明文(connection_status 只回 masked + *_configured 布尔)。
//
// 端点确认(对照 backend/app/api/routers/vkpi_goaffpro.py,前缀
// /api/admin/vkpi/goaffpro):
//   GET  /creds        -> connection_status:{api_base, token(masked),
//                          access_token_configured, public_token_configured,
//                          private_token_configured, status, source}
//   POST /creds        -> save_credentials,body {access_token, public_token?,
//                          private_token?, api_base?} -> {ok, api_base,
//                          token(masked), *_configured, status, source}
//   GET  /affiliates?limit=&offset= -> list_affiliates:
//                          {ok, affiliates, count, total} | {ok:false, reason}

import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

// ---------------------------------------------------------------------------
// 连接状态(GET /creds)— masked-only,绝不含明文 token。
// ---------------------------------------------------------------------------

export interface GoaffproStatus {
  api_base?: string;
  token?: string; // masked,如 "abcd...wxyz",绝不是明文
  access_token_configured?: boolean;
  public_token_configured?: boolean;
  private_token_configured?: boolean;
  // not_configured | pending | connected | error | revoked
  status?: string;
  // db | env | none
  source?: string;
}

export async function getGoaffproStatus(token: string): Promise<GoaffproStatus> {
  return apiFetch<GoaffproStatus>(
    "/api/admin/vkpi/goaffpro/creds",
    {},
    token,
  );
}

// refreshGoaffproStatus 与 getGoaffproStatus 同端点;语义别名,供「刷新状态」按钮调用。
export async function refreshGoaffproStatus(token: string): Promise<GoaffproStatus> {
  return getGoaffproStatus(token);
}

// ---------------------------------------------------------------------------
// 保存凭据(POST /creds)— 绝不 log body(携密),后端加密落库不回显明文。
// ---------------------------------------------------------------------------

export interface GoaffproCredsInput {
  access_token: string; // X-GOAFFPRO-ACCESS-TOKEN(管理私钥,主鉴权)
  public_token?: string; // X-GOAFFPRO-PUBLIC-TOKEN(公钥,可选)
  api_base?: string; // 覆盖默认 https://api.goaffpro.com/v1(可选)
}

export interface SaveGoaffproCredsResult {
  ok?: boolean;
  api_base?: string;
  token?: string; // masked
  access_token_configured?: boolean;
  public_token_configured?: boolean;
  private_token_configured?: boolean;
  status?: string;
  source?: string;
}

export async function saveGoaffproCredentials(
  token: string,
  { access_token, public_token, api_base }: GoaffproCredsInput,
): Promise<SaveGoaffproCredsResult> {
  // 绝不 log body —— 携带 GOAFFPRO 密钥。
  return apiFetch<SaveGoaffproCredsResult>(
    "/api/admin/vkpi/goaffpro/creds",
    {
      method: "POST",
      body: jsonBody({
        access_token,
        public_token,
        api_base,
      }),
    },
    token,
  );
}

// ---------------------------------------------------------------------------
// Affiliate 预览(GET /affiliates)— 校准用:看 total + 前几条 _raw_keys/字段。
// ---------------------------------------------------------------------------

export interface GoaffproAffiliate {
  id?: unknown;
  name?: string;
  email?: string;
  referral_code?: string;
  status?: string;
  total_sales?: unknown;
  total_commissions?: unknown;
  // 后端映射保留 _raw_keys 供「待 key 校准」对照真实字段名。
  _raw_keys?: string[];
  [k: string]: unknown;
}

export interface ListGoaffproAffiliatesResult {
  ok?: boolean;
  affiliates?: GoaffproAffiliate[];
  count?: number;
  total?: number | null;
  // 未配置 / 透传错误时:{ok:false, reason} 或 {ok:false, error}
  reason?: string;
  error?: string;
}

export async function listGoaffproAffiliates(
  token: string,
  { limit = 5, offset }: { limit?: number; offset?: number } = {},
): Promise<ListGoaffproAffiliatesResult> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (offset !== undefined) params.set("offset", String(offset));
  const res = await apiFetch<ListGoaffproAffiliatesResult>(
    `/api/admin/vkpi/goaffpro/affiliates?${params.toString()}`,
    {},
    token,
  );
  return {
    ok: res?.ok,
    affiliates: Array.isArray(res?.affiliates) ? (res.affiliates as GoaffproAffiliate[]) : [],
    count: typeof res?.count === "number" ? res.count : (Array.isArray(res?.affiliates) ? res.affiliates.length : 0),
    total: res?.total ?? null,
    reason: res?.reason,
    error: res?.error,
  };
}

// ---------------------------------------------------------------------------
// D2:一键给 KOL 建 affiliate + 追踪链 + 优惠码(KOL 零注册)。
//
// 端点确认(对照 backend/app/api/routers/vkpi_goaffpro.py):
//   POST /kol/{kol_pool_id}/link -> {ok, linked, affiliate_id, ref_code,
//                                     tracking_url, coupon, already_linked?, raw?}
//                                    | {ok:false, error, reason?, status_code?, raw?}
//   GET  /kol/{kol_pool_id}/link -> {linked:true, kol_pool_id, affiliate_id,
//                                     ref_code, tracking_url, coupon, created_at}
//                                    | {linked:false, kol_pool_id}
// ---------------------------------------------------------------------------

export interface GoaffproKolLink {
  ok?: boolean;
  linked?: boolean;
  already_linked?: boolean;
  kol_pool_id?: number;
  affiliate_id?: string;
  ref_code?: string;
  tracking_url?: string;
  coupon?: string;
  created_at?: string;
  status?: string;
  tracks_now?: boolean;
  commission_rate?: string; // 佣金比例人话(如 "10%")
  // 早期废映射(GOAFFPRO 里搜不到该 affiliate)→ 前端显「重新生成」触发 POST 真建号。
  needs_regenerate?: boolean;
  created?: boolean;
  // 按产品出链:传 product 时后端解析 handle 后附带(解析不到则为 null,退首页链)。
  product_url?: string | null;
  product_handle?: string | null;
  product_name?: string | null;
  // 出错(create_affiliate 失败)时透出,便于「联系管理员校准」调试。
  error?: string;
  reason?: string;
  status_code?: number;
  raw?: unknown;
}

// product 可选:传产品名/SKU → 后端解析 handle → 响应附带 product_url(按产品出链)。
function withProduct(path: string, product?: string): string {
  const p = String(product || "").trim();
  return p ? `${path}?product=${encodeURIComponent(p)}` : path;
}

// POST:生成(或幂等返回已有)KOL↔affiliate 映射 + 追踪链 + 优惠码。product → 附带产品链。
export async function generateKolGoaffproLink(
  token: string,
  kolPoolId: string | number,
  product?: string,
): Promise<GoaffproKolLink> {
  return apiFetch<GoaffproKolLink>(
    withProduct(`/api/admin/vkpi/goaffpro/kol/${encodeURIComponent(String(kolPoolId))}/link`, product),
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

// GET:读已有 KOL↔affiliate 映射;无映射 -> {linked:false}。product → 附带产品链。
export async function getKolGoaffproLink(
  token: string,
  kolPoolId: string | number,
  product?: string,
): Promise<GoaffproKolLink> {
  return apiFetch<GoaffproKolLink>(
    withProduct(`/api/admin/vkpi/goaffpro/kol/${encodeURIComponent(String(kolPoolId))}/link`, product),
    {},
    token,
  );
}

export interface GoaffproSummaryRow {
  kol_pool_id?: number;
  kol_name?: string;
  kol_handle?: string;
  kol_avatar?: string;
  kol_platform?: string;
  affiliate_id?: string;
  ref_code?: string;
  coupon?: string;
  commission_rate?: string;
  status?: string;
  tracking_url?: string;
  source_label?: string;
  source_type?: string;
  product_sku?: string;
  clicks?: number;
  orders?: number;
  gmv_usd?: number;
  commission_usd?: number;
  currency?: string;
  partial?: boolean;
}

export interface GoaffproSummaryTotals {
  kol_count?: number;
  clicks?: number;
  orders?: number;
  gmv_usd?: number;
  commission_usd?: number;
}

export interface GoaffproSummaryResult {
  ok?: boolean;
  items: GoaffproSummaryRow[];
  count?: number;
  totals?: GoaffproSummaryTotals;
  last_synced_at?: string | null;
  stale_count?: number;
  note?: string | null;
}

// POST:刷新 GOAFFPRO 指标缓存(点击/订单/GMV/佣金落库)→ 之后 summary 读库秒出。
export async function syncGoaffproMetrics(
  token: string,
): Promise<{ ok?: boolean; synced?: number; errors?: number; synced_at?: string }> {
  return apiFetch(
    `/api/admin/vkpi/goaffpro/sync-metrics`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

// GET:归因汇总(每个已建链 KOL 一行 + totals 汇总,实时来自 GOAFFPRO)。
// project_id → 只汇总该项目下的 KOL(供项目卡)。
export async function getGoaffproSummary(
  token: string,
  opts?: { limit?: number; projectId?: string | number; search?: string },
): Promise<GoaffproSummaryResult> {
  const limit = opts?.limit ?? 200;
  const qs = new URLSearchParams({ limit: String(limit) });
  if (opts?.projectId != null && String(opts.projectId).length) qs.set("project_id", String(opts.projectId));
  if (opts?.search && opts.search.trim()) qs.set("search", opts.search.trim());
  const res = await apiFetch<GoaffproSummaryResult>(
    `/api/admin/vkpi/goaffpro/summary?${qs.toString()}`,
    {},
    token,
  );
  return {
    items: Array.isArray(res?.items) ? res.items : [],
    count: res?.count,
    totals: res?.totals,
    note: res?.note,
    ok: res?.ok,
    // 透传落库同步时间戳(此前被映射吞掉 → 「更新于」永远空;补齐真值,绝不编时间)。
    last_synced_at: res?.last_synced_at ?? null,
    stale_count: res?.stale_count,
  };
}

// POST:调整 KOL affiliate 佣金比例 → 后端 PATCH 推回 GOAFFPRO 总台。返回 {ok, commission_rate}。
export async function updateKolCommission(
  token: string,
  kolPoolId: string | number,
  rate: number,
  opts?: { type?: "percentage" | "fixed_amount"; on?: "product" | "order" },
): Promise<{ ok?: boolean; commission_rate?: string; error?: string }> {
  return apiFetch(
    `/api/admin/vkpi/goaffpro/kol/${encodeURIComponent(String(kolPoolId))}/commission`,
    { method: "POST", body: jsonBody({ rate, type: opts?.type || "percentage", on: opts?.on || "product" }) },
    token,
  );
}

// POST:设/改 KOL 专属优惠码 → 后端 PATCH 推回 GOAFFPRO 总台。返回 {ok, coupon}。
export async function updateKolCoupon(
  token: string,
  kolPoolId: string | number,
  code: string,
  opts?: { discount_value?: number; discount_type?: "percentage" | "fixed_amount" | "free_shipping" },
): Promise<{ ok?: boolean; coupon?: string; error?: string }> {
  return apiFetch(
    `/api/admin/vkpi/goaffpro/kol/${encodeURIComponent(String(kolPoolId))}/coupon`,
    {
      method: "POST",
      body: jsonBody({ code, discount_value: opts?.discount_value ?? 10, discount_type: opts?.discount_type || "percentage" }),
    },
    token,
  );
}

// 类型导出别名,便于页面侧引用。
export type { Row as GoaffproRow };
