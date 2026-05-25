import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export interface VkpiKolDecisionPayload {
  kolPoolId: string | number;
  decisionKey: "contact" | "watch" | "caution" | "avoid";
  decisionLabel?: string;
  severity?: string;
  rationale?: string;
  sourceTable?: string;
  sourceId?: string | number;
  query?: string;
  evidenceSections?: string[];
  evidenceSnapshot?: Row;
  metadata?: Row;
}

export interface VkpiNaturalKolSearchResponse {
  query?: string;
  parsed?: Row;
  items?: Row[];
  method?: string;
  degraded?: boolean;
  notes?: string[];
}

export interface VkpiPlatformSearchResponse {
  status?: string;
  platform?: string;
  query?: string;
  items?: Row[];
  candidate_ids?: number[];
  saved_candidates?: number;
  message?: string;
  metadata?: Row;
}

export async function searchVkpi(token: string, query: string, limit = 20) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return apiFetch<{ items?: Row[]; total?: number; provider_calls?: boolean; write_db?: boolean; tokens?: string[] }>(
    `/api/admin/vkpi/search?${params.toString()}`,
    {},
    token,
  );
}

export async function createKolDecisionAudit(token: string, payload: VkpiKolDecisionPayload) {
  return apiFetch<{ decision?: Row; ok?: boolean }>(
    "/api/admin/vkpi/kol-decisions",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function searchMarketingKolsNatural(token: string, payload: { query: string; platform?: string; limit?: number }) {
  return apiFetch<VkpiNaturalKolSearchResponse>(
    "/api/marketing/kol/search/natural",
    {
      method: "POST",
      body: jsonBody({
        query: payload.query,
        platform: payload.platform,
        limit: payload.limit || 100,
      }),
    },
    token,
  );
}

export async function searchPlatformKols(token: string, payload: { query: string; platform: string; maxResults?: number }) {
  return apiFetch<VkpiPlatformSearchResponse>(
    "/api/admin/kol/search/platform",
    {
      method: "POST",
      body: jsonBody({
        query: payload.query,
        platform: payload.platform,
        max_results: payload.maxResults || 25,
      }),
      timeoutMs: 300000,
    },
    token,
  );
}
