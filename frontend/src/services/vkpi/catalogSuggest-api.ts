// 顶栏 Ask「$SKU / 镜头」直达候选:GET /api/admin/vkpi/catalog/suggest?q=&limit=20
// 只回三列 {sku, display_name, lens_key};source_status 区分真零结果与来源故障。

import { apiFetch } from "../http";
import { readStoredApiToken } from "./globalSearch-api";

export interface CatalogSuggestItem {
  sku: string;
  display_name: string;
  lens_key: string;
}

export interface CatalogSuggestSourceStatus {
  status: "ready" | "error" | "absent" | string;
  result_count: number;
  reason?: string;
}

export interface CatalogSuggestResult {
  status: "ready" | "partial" | "empty" | "error" | string;
  q: string;
  items: CatalogSuggestItem[];
  source_status: Record<string, CatalogSuggestSourceStatus>;
}

export async function catalogSuggest(
  q: string,
  opts: { token?: string; signal?: AbortSignal; limit?: number } = {},
): Promise<CatalogSuggestResult> {
  const token = opts.token || readStoredApiToken();
  const limit = Math.max(1, Math.min(20, Number(opts.limit) || 20));
  const res = await apiFetch<Partial<CatalogSuggestResult>>(
    `/api/admin/vkpi/catalog/suggest?q=${encodeURIComponent(q)}&limit=${limit}`,
    { signal: opts.signal, timeoutMs: 10000 },
    token || undefined,
  );
  const items = Array.isArray(res?.items)
    ? res.items
        .filter((item): item is CatalogSuggestItem => Boolean(item) && typeof item === "object")
        .map((item) => ({
          sku: typeof item.sku === "string" ? item.sku : "",
          display_name: typeof item.display_name === "string" ? item.display_name : "",
          lens_key: typeof item.lens_key === "string" ? item.lens_key : "",
        }))
        .filter((item) => item.display_name || item.sku)
    : [];
  const sourceStatus: Record<string, CatalogSuggestSourceStatus> = {};
  if (res?.source_status && typeof res.source_status === "object" && !Array.isArray(res.source_status)) {
    for (const [key, value] of Object.entries(res.source_status as Record<string, unknown>)) {
      if (!value || typeof value !== "object") continue;
      const row = value as Record<string, unknown>;
      sourceStatus[key] = {
        status: typeof row.status === "string" ? row.status : "error",
        result_count: Number.isFinite(Number(row.result_count)) ? Math.max(0, Number(row.result_count)) : 0,
        reason: typeof row.reason === "string" && row.reason ? row.reason : undefined,
      };
    }
  }
  return {
    status: typeof res?.status === "string" ? res.status : items.length > 0 ? "ready" : "empty",
    q: typeof res?.q === "string" ? res.q : q,
    items,
    source_status: sourceStatus,
  };
}
