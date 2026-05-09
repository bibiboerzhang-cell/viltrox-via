import { apiFetch, jsonBody } from "../http";

export interface VkpiKolPoolItem {
  id: number;
  pool_uid: string;
  platform: string;
  handle: string;
  display_name?: string;
  avatar_url?: string;
  followers?: number;
  avg_views?: number;
  engagement_rate?: number;
  viltrox_fit_score?: number;
  linked_main_kol_id?: number | null;
  source_type?: string;
  source_ref?: string;
  created_at?: string;
}

export async function listKolPool(
  token: string,
  params: { search?: string; platform?: string; limit?: number } = {},
): Promise<{ items?: VkpiKolPoolItem[] }> {
  const query = new URLSearchParams({ limit: String(params.limit || 100) });
  if (params.search) query.set("query", params.search);
  if (params.platform) query.set("platform", params.platform);
  return apiFetch<{ items?: VkpiKolPoolItem[] }>(
    `/api/admin/vkpi/kol-pool?${query.toString()}`,
    {},
    token,
  );
}

export async function getKolPoolItem(token: string, kolPoolId: number) {
  return apiFetch<{ item: VkpiKolPoolItem }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}`,
    {},
    token,
  );
}

export async function importKolPool(
  token: string,
  payload: {
    items: Array<Record<string, unknown>>;
    sourceType?: string;
    sourceRef?: string;
    platform?: string;
    force?: boolean;
  },
) {
  return apiFetch<{ imported: number; skipped: number; items: VkpiKolPoolItem[] }>(
    "/api/admin/vkpi/kol-pool/import",
    {
      method: "POST",
      body: jsonBody({
        items: payload.items,
        source_type: payload.sourceType || "manual",
        source_ref: payload.sourceRef || "",
        platform: payload.platform || "",
        force: payload.force || false,
      }),
    },
    token,
  );
}

export async function linkKolPoolToMain(token: string, kolPoolId: number, mainKolId: number) {
  return apiFetch<{ linked: boolean; kol_pool_id: number; main_kol_id: number }>(
    `/api/admin/vkpi/kol-pool/${kolPoolId}/link`,
    {
      method: "POST",
      body: jsonBody({ main_kol_id: mainKolId }),
    },
    token,
  );
}
