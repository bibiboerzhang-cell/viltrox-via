import { apiFetch, jsonBody } from "../http";
import { fetchVkpiDashboardData as fetchLegacyVkpiDashboardData } from "../vkpi.ui-api";
import type { VkpiDashboardData } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export interface VkpiDashboardFilters {
  range?: "today" | "7d" | "30d" | "mtd" | "qtd" | "custom";
  startDate?: string;
  endDate?: string;
  scope?: "self" | "team" | "all";
  staffId?: string;
  platform?: string;
  productId?: string;
  includeEstimated?: boolean;
}

export interface VkpiExportPayload extends VkpiDashboardFilters {
  reportType: "weekly" | "monthly" | "staff" | "project" | "product_roi" | "finance";
  format: "pdf" | "csv" | "xlsx";
}

export async function fetchVkpiDashboardData(
  token: string,
  filters: VkpiDashboardFilters = {},
): Promise<VkpiDashboardData> {
  return fetchLegacyVkpiDashboardData(token, filters);
}

export interface VkpiAgentInboxItem {
  id: string;
  agent_id: string;
  agent_name: string;
  type: string;
  title: string;
  summary: string;
  status: "active" | "warning" | "idle" | string;
  passed: boolean;
  mode?: string;
  generated_at?: string | null;
  last_run_at?: string | null;
  artifact_name?: string;
  source?: string;
  details?: {
    summary?: Record<string, unknown>;
    next_steps?: string[];
  };
}

export interface VkpiAgentsInboxResponse {
  items?: VkpiAgentInboxItem[];
  total?: number;
  limit?: number;
  agent_id?: string | null;
  ops_dir?: string;
  is_real?: boolean;
  source?: string;
}

export async function getDashboardAgentsInbox(
  token: string,
  params: { limit?: number; agentId?: string } = {},
) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 50));
  if (params.agentId) query.set("agent_id", params.agentId);
  return apiFetch<VkpiAgentsInboxResponse>(
    `/api/admin/vkpi/dashboard/agents/inbox?${query.toString()}`,
    {},
    token,
  );
}

export async function generateWeeklyReport(token: string, filters: VkpiDashboardFilters = {}) {
  return apiFetch<{ reportId?: string; report_id?: string; status: string; downloadUrl?: string; download_url?: string }>(
    "/api/marketing/reports/weekly/generate",
    { method: "POST", body: jsonBody(filters) },
    token,
  );
}

export async function exportVkpiReport(token: string, payload: VkpiExportPayload) {
  return apiFetch<{ exportId?: string; export_id?: string; status: string; downloadUrl?: string; download_url?: string }>(
    `/api/marketing/exports/${payload.format}`,
    { method: "POST", body: jsonBody(payload) },
    token,
  );
}

export async function runKpiRollup(token: string, ledgerDate?: string) {
  return apiFetch<Row>(
    "/api/marketing/rollups/run-now",
    { method: "POST", body: jsonBody({ ledger_date: ledgerDate || undefined }) },
    token,
  );
}

export async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}
