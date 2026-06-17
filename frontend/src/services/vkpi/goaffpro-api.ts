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

// 类型导出别名,便于页面侧引用。
export type { Row as GoaffproRow };
