import { apiFetch, jsonBody } from "../http";

// 经销商地图(Dealer Map)真后端 API —— 镜像 inventory-api.ts 风格。
// 前缀 /api/admin/vkpi/dealers;纯地理数据源(美国相机零售商),无评分/无 v6_fit。

export interface VkpiDealer {
  id: string | number;
  name: string;
  address: string;
  city?: string | null;
  state?: string | null;
  lat?: number | null;
  lng?: number | null;
  source?: string | null;
  created_at?: string;
}

export interface VkpiDealerPin {
  name: string;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  lat: number;
  lng: number;
  color?: string;
}

export interface VkpiDealerScrapePayload {
  source?: string;
  limit?: number; // <= 20(后端 HARD CAP)
  record_only?: boolean; // 默认 true = 纯预检,no blast
}

export interface VkpiDealerScrapeResult {
  ok: boolean;
  source: string;
  requested: number;
  inserted: number;
  skipped: number;
  geocoded: number;
  pending_geocode: number;
  record_only?: boolean;
  plan?: Array<{
    name?: string | null;
    address?: string | null;
    city?: string | null;
    state?: string | null;
    will_geocode?: boolean;
  }>;
  errors: Array<{ name?: string | null; error: string }>;
}

export async function listDealers(
  token: string,
  opts: { limit?: number; state?: string } = {},
): Promise<{ dealers?: VkpiDealer[] }> {
  const query = new URLSearchParams();
  if (opts.limit != null) query.set("limit", String(opts.limit));
  if (opts.state) query.set("state", opts.state);
  const qs = query.toString();
  return apiFetch<{ dealers?: VkpiDealer[] }>(
    `/api/admin/vkpi/dealers${qs ? `?${qs}` : ""}`,
    {},
    token,
  );
}

export async function getDealerLocations(
  token: string,
): Promise<{ pins?: VkpiDealerPin[] }> {
  return apiFetch<{ pins?: VkpiDealerPin[] }>(
    "/api/admin/vkpi/dealers/locations",
    {},
    token,
  );
}

export async function scrapeDealersEnqueue(
  token: string,
  payload: VkpiDealerScrapePayload = {},
): Promise<VkpiDealerScrapeResult> {
  return apiFetch<VkpiDealerScrapeResult>(
    "/api/admin/vkpi/dealers/scrape-enqueue",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}
