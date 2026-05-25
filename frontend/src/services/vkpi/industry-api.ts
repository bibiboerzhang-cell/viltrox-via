import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export async function listIndustryProjects(token: string, options: { activeOnly?: boolean; limit?: number } = {}) {
  const params = new URLSearchParams({
    limit: String(options.limit || 100),
    active_only: String(options.activeOnly ?? true),
  });
  return apiFetch<{ projects?: Row[] }>(
    `/api/admin/vkpi/industry-data/projects?${params.toString()}`,
    {},
    token,
  );
}

export async function createIndustryProject(token: string, payload: Row) {
  return apiFetch<Row>("/api/admin/vkpi/industry-data/projects", { method: "POST", body: jsonBody(payload) }, token);
}

export async function listIndustryAccounts(token: string, projectId: string, limit = 300) {
  return apiFetch<{ accounts?: Row[] }>(
    `/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/accounts?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function addIndustryAccount(token: string, projectId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/accounts`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function importIndustryApifyHistory(
  token: string,
  projectId: string,
  payload: { source_type?: string; source_ref?: string; items: Row[] },
) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/apify/import`,
    { method: "POST", body: jsonBody(payload), timeoutMs: 120000 },
    token,
  );
}

export async function getIndustryAccount(token: string, accountId: string, limit = 500) {
  return apiFetch<{ account?: Row; snapshots?: Row[]; posts?: Row[] }>(
    `/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function refreshIndustryAccount(token: string, accountId: string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}/refresh`,
    { method: "POST", body: jsonBody({}), timeoutMs: 120000 },
    token,
  );
}

export async function updateIndustryAccount(token: string, accountId: string, payload: Row) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/accounts/${encodeURIComponent(accountId)}`,
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function getIndustryCrossPlatform(token: string, projectId: string) {
  return apiFetch<Row>(
    `/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/cross-platform`,
    {},
    token,
  );
}

export async function listIndustryPosts(token: string, projectId: string, limit = 500) {
  return apiFetch<{ posts?: Row[] }>(
    `/api/admin/vkpi/industry-data/projects/${encodeURIComponent(projectId)}/posts?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function analyzeDataAnalysisPostUrl(
  token: string,
  payload: { url: string; platform?: string; creatorHandle?: string },
) {
  return apiFetch<Row>(
    "/api/admin/kol/tools/analyze-url",
    {
      method: "POST",
      body: jsonBody({
        url: payload.url,
        platform: payload.platform || "",
        creator_handle: payload.creatorHandle || "",
      }),
      timeoutMs: 300000,
    },
    token,
  );
}
