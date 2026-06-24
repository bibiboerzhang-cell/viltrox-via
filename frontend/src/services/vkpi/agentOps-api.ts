import { apiFetch } from "../http";

// Agent-OS 新能力读端点统一客户端(本 session 后端已建)。全只读。
type Row = Record<string, unknown>;

export async function getCapabilities(token: string): Promise<Row> {
  return apiFetch<Row>("/api/admin/vkpi/agents/capabilities", { cache: "no-store" }, token);
}

export async function getWorkspaceDigest(token: string, actionLimit = 5): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/agents/workspace-digest?action_limit=${actionLimit}`,
    { cache: "no-store" },
    token,
  );
}

export async function getLearningStatus(token: string): Promise<Row> {
  return apiFetch<Row>("/api/admin/vkpi/agents/learning-status", { cache: "no-store" }, token);
}

export async function getIndustryBoard(token: string, windowDays = 30): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/metrics/industry-board?window_days=${windowDays}`,
    { cache: "no-store" },
    token,
  );
}

export async function getHighValueKols(token: string, limit = 10): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/metrics/high-value-kols?limit=${limit}`,
    { cache: "no-store" },
    token,
  );
}

export async function getMovers(token: string, windowDays = 30, metric = "followers"): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/metrics/movers?window_days=${windowDays}&metric=${encodeURIComponent(metric)}&limit=5`,
    { cache: "no-store" },
    token,
  );
}

export async function getReportTypes(token: string): Promise<Row> {
  return apiFetch<Row>("/api/admin/vkpi/metrics/report-types", { cache: "no-store" }, token);
}

export async function getReport(token: string, reportType: string, windowDays = 30): Promise<Row> {
  return apiFetch<Row>(
    `/api/admin/vkpi/metrics/report?report_type=${encodeURIComponent(reportType)}&window_days=${windowDays}`,
    { cache: "no-store" },
    token,
  );
}
