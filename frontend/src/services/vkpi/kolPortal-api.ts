// KOL 自助门户(只读·token 隔离)前端服务。
//
// 安全/隔离:门户是 standalone(不进 admin Cockpit 壳),绝不走 admin 的 apiFetch
// (它会注入 admin 鉴权/credentials)。这里用 buildApiUrl + 裸 fetch,只读公开端点。
// 端点只返回 token 持有者(单个 KOL)自己的数据。404 → 统一「无效/过期」提示,
// 不泄露该 KOL 是否存在。
import { ApiResponseError, buildApiUrl } from "../http";

export interface PortalKol {
  kol_pool_id: number;
  platform: string;
  handle: string;
  display_name: string;
  avatar_url: string;
  profile_url: string;
  country: string;
  language: string;
  primary_topic: string;
  followers: number;
}

export interface PortalProject {
  project_id: number;
  project_name: string;
  product_sku: string;
  product_name: string;
  platform: string;
  stage: string;
  stage_status: string;
}

export interface PortalGmv {
  orders_count: number;
  gmv_cents: number;
  currency: string;
}

export interface PortalClicks {
  total: number;
  valid: number;
}

export interface PortalAttribution {
  revenue_cents: number;
  commission_cents: number;
  currency: string;
  occurred_at: string | null;
}

export interface PortalData {
  kol: PortalKol;
  projects: PortalProject[];
  gmv: PortalGmv;
  clicks: PortalClicks;
  attributions: PortalAttribution[];
}

export class PortalInvalidError extends Error {
  constructor(message = "链接无效或已过期") {
    super(message);
    this.name = "PortalInvalidError";
  }
}

// GET /api/portal/kol/{token} — 公开只读。404/网络异常 → 统一 PortalInvalidError。
export async function fetchPortal(token: string): Promise<PortalData> {
  const trimmed = String(token || "").trim();
  if (!trimmed) {
    throw new PortalInvalidError();
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl(`/api/portal/kol/${encodeURIComponent(trimmed)}`), {
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
  } catch {
    throw new PortalInvalidError("网络请求失败，请稍后再试");
  }

  const raw = await response.text();
  let parsed: unknown = {};
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = raw;
    }
  }

  if (!response.ok) {
    // 404(及其它非 2xx)统一收敛为「无效/过期」,不泄露存在性。
    if (response.status === 404) {
      throw new PortalInvalidError();
    }
    throw new ApiResponseError(response, parsed);
  }

  return parsed as PortalData;
}
