import { ApiResponseError, apiFetch, buildApiUrl, jsonBody } from "../http";

export const VKPI_REPORT_SECTION_KEYS = [
  "kpiOverview",
  "attribution",
  "projects",
  "ledger",
  "risks",
  "summary",
] as const;

export type VkpiReportSectionKey = (typeof VKPI_REPORT_SECTION_KEYS)[number];
export type VkpiReportPeriod = "weekly" | "monthly";
export type VkpiReportLanguage = "zh" | "en";
export type VkpiReportLayout = "visual" | "markdown";
export type VkpiReportScope = "self" | "all";
export type VkpiReportExportFormat = "pdf" | "csv" | "xlsx";

export interface VkpiReportGenerateConfig {
  period: VkpiReportPeriod;
  date: string;
  language: VkpiReportLanguage;
  sections: readonly VkpiReportSectionKey[];
  format: VkpiReportLayout;
  scope: VkpiReportScope;
  staffId?: string | number;
}

export interface VkpiReportRequestPayload {
  report_type: VkpiReportPeriod;
  period: VkpiReportPeriod;
  period_days: number;
  date: string;
  date_from: string;
  date_to: string;
  language: VkpiReportLanguage;
  sections: VkpiReportSectionKey[];
  format: VkpiReportLayout;
  scope: VkpiReportScope;
  staff_id?: number;
}

export interface VkpiReportMetric {
  key: string;
  label: string;
  value: string | number | null;
  rawValue: string | number | null;
  dataStatus: string;
  note: string;
}

export interface VkpiGeneratedReport {
  reportId: number | null;
  reportUid: string;
  reportType: string;
  periodStart: string;
  periodEnd: string;
  status: string;
  downloadUrl: string;
  summary: string;
  dataStatus: string;
  metrics: VkpiReportMetric[];
  modelPolicy: Record<string, unknown> | null;
  claimLevel: string;
}

export interface VkpiReportHistoryItem {
  id: number;
  reportUid: string;
  reportType: string;
  periodStart: string;
  periodEnd: string;
  scopeType: string;
  scopeId: number | null;
  triggeredAt: string;
  status: string;
  summary: string;
  dataStatus: string;
  schemaVersion: string;
  archivedAt: string;
  archiveReason: string;
  truthInvalidated?: boolean;
  truthInvalidationReason?: string;
  modelPolicy: Record<string, unknown> | null;
  claimLevel: string;
}

export interface VkpiReportHistoryResponse {
  reports: VkpiReportHistoryItem[];
  count: number;
  archived: boolean;
}

export interface VkpiReportExportResult {
  exportId: number | null;
  status: string;
  downloadUrl: string;
}

type JsonRecord = Record<string, unknown>;

function recordValue(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}


function optionalRecordValue(...values: unknown[]): JsonRecord | null {
  for (const value of values) {
    if (value && typeof value === "object" && !Array.isArray(value)) return { ...(value as JsonRecord) };
  }
  return null;
}


function stringValue(...values: unknown[]): string {
  const value = values.find((item) => item !== undefined && item !== null && String(item).trim());
  return value === undefined ? "" : String(value).trim();
}


function booleanValue(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "true") return true;
  if (normalized === "false") return false;
  return null;
}


export function reportModelPolicyLabel(
  modelPolicy: Record<string, unknown> | null | undefined,
  claimLevel = "",
): string {
  if (!modelPolicy) return "策略未披露";
  const mode = stringValue(modelPolicy.mode).toLowerCase();
  const allowed = booleanValue(modelPolicy.provider_calls_allowed ?? modelPolicy.providerCallsAllowed);
  const deterministicOnly = booleanValue(modelPolicy.deterministic_only ?? modelPolicy.deterministicOnly);
  const policyClaim = stringValue(modelPolicy.claim_level, modelPolicy.claimLevel, claimLevel).toLowerCase();

  if (mode === "deterministic_descriptive" || deterministicOnly === true) {
    return "确定性描述模式（未调用模型）";
  }
  if (mode === "advanced_model") {
    if (allowed === true) return "高级模型策略（允许调用，未证明调用成功）";
    if (allowed === false) return "高级模型策略（调用未获授权）";
    return "高级模型策略（调用状态未披露）";
  }
  if (mode) return `后端策略：${mode}`;
  if (policyClaim === "descriptive_only") return "描述性结论（调用状态未披露）";
  if (policyClaim) return `结论口径：${policyClaim}（调用状态未披露）`;
  return "策略未披露";
}

function numberValue(...values: unknown[]): number | null {
  for (const value of values) {
    if (value === undefined || value === null || value === "") continue;
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function dateOffset(date: string, offsetDays: number): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error("请选择有效的报告截止日期。");
  }
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("请选择有效的报告截止日期。");
  }
  parsed.setUTCDate(parsed.getUTCDate() + offsetDays);
  return parsed.toISOString().slice(0, 10);
}

export function buildVkpiReportPayload(config: VkpiReportGenerateConfig): VkpiReportRequestPayload {
  const periodDays = config.period === "monthly" ? 30 : 7;
  const staffId = numberValue(config.staffId);
  const sections = VKPI_REPORT_SECTION_KEYS.filter((key) => config.sections.includes(key));
  return {
    report_type: config.period,
    period: config.period,
    period_days: periodDays,
    date: config.date,
    date_from: dateOffset(config.date, -(periodDays - 1)),
    date_to: config.date,
    language: config.language,
    sections,
    format: config.format,
    scope: config.scope,
    ...(config.scope === "self" && staffId && staffId > 0 ? { staff_id: staffId } : {}),
  };
}

function normalizeMetric(value: unknown): VkpiReportMetric {
  const row = recordValue(value);
  const rawValue = row.raw_value ?? row.rawValue ?? row.value ?? null;
  return {
    key: stringValue(row.key, row.metric_key),
    label: stringValue(row.label, row.metric_label, row.key),
    value: row.value === undefined ? null : row.value as string | number | null,
    rawValue: rawValue as string | number | null,
    dataStatus: stringValue(row.data_status, row.dataStatus),
    note: stringValue(row.note),
  };
}

function normalizeGeneratedReport(value: unknown): VkpiGeneratedReport {
  const row = recordValue(value);
  const context = recordValue(row.context);
  const reportSpec = recordValue(context.report_spec);
  const metrics = Array.isArray(context.kpis) ? context.kpis.map(normalizeMetric) : [];
  const modelPolicy = optionalRecordValue(
    row.model_policy,
    row.modelPolicy,
    context.model_policy,
    context.modelPolicy,
  );
  return {
    reportId: numberValue(row.report_run_id, row.reportRunId, row.id),
    reportUid: stringValue(row.report_uid, row.reportId, row.report_id),
    reportType: stringValue(row.report_type, context.report_type, reportSpec.report_type),
    periodStart: stringValue(row.period_start, context.period_start),
    periodEnd: stringValue(row.period_end, context.period_end),
    status: stringValue(row.status) || "unknown",
    downloadUrl: stringValue(row.download_url, row.downloadUrl, recordValue(row.file).download_url),
    summary: stringValue(row.summary, row.summary_text, context.summary_text),
    dataStatus: stringValue(row.data_status, context.data_status),
    metrics,
    modelPolicy,
    claimLevel: stringValue(
      row.claim_level,
      row.claimLevel,
      context.claim_level,
      context.claimLevel,
      modelPolicy?.claim_level,
      modelPolicy?.claimLevel,
    ),
  };
}

function normalizeHistoryItem(value: unknown): VkpiReportHistoryItem | null {
  const row = recordValue(value);
  const id = numberValue(row.id, row.report_run_id, row.reportRunId);
  if (!id || id < 1) return null;
  const modelPolicy = optionalRecordValue(row.model_policy, row.modelPolicy);
  return {
    id,
    reportUid: stringValue(row.report_uid, row.reportUid) || `report-${id}`,
    reportType: stringValue(row.report_type, row.reportType) || "weekly",
    periodStart: stringValue(row.period_start, row.periodStart),
    periodEnd: stringValue(row.period_end, row.periodEnd),
    scopeType: stringValue(row.scope_type, row.scopeType),
    scopeId: numberValue(row.scope_id, row.scopeId),
    triggeredAt: stringValue(row.triggered_at, row.triggeredAt),
    status: stringValue(row.status) || "unknown",
    summary: stringValue(row.summary_text, row.summary),
    dataStatus: stringValue(row.data_status, row.dataStatus),
    schemaVersion: stringValue(row.schema_version, row.schemaVersion),
    archivedAt: stringValue(row.archived_at, row.archivedAt),
    archiveReason: stringValue(row.archive_reason, row.archiveReason),
    truthInvalidated: Boolean(row.truth_invalidated ?? row.truthInvalidated),
    truthInvalidationReason: stringValue(row.truth_invalidation_reason, row.truthInvalidationReason),
    modelPolicy,
    claimLevel: stringValue(
      row.claim_level,
      row.claimLevel,
      modelPolicy?.claim_level,
      modelPolicy?.claimLevel,
    ),
  };
}

export async function generateVkpiReport(token: string, config: VkpiReportGenerateConfig): Promise<VkpiGeneratedReport> {
  const result = await apiFetch<unknown>(
    "/api/admin/vkpi/reports/weekly/generate",
    { method: "POST", body: jsonBody(buildVkpiReportPayload(config)), timeoutMs: 120_000 },
    token,
  );
  return normalizeGeneratedReport(result);
}

export async function listVkpiReports(token: string, archived = false, limit = 50): Promise<VkpiReportHistoryResponse> {
  const result = await apiFetch<unknown>(
    `/api/admin/vkpi/reports?archived=${archived ? "true" : "false"}&limit=${Math.max(1, Math.min(200, limit))}`,
    {},
    token,
  );
  const row = recordValue(result);
  const reports = (Array.isArray(row.reports) ? row.reports : [])
    .map(normalizeHistoryItem)
    .filter((item): item is VkpiReportHistoryItem => item !== null);
  return {
    reports,
    count: numberValue(row.count) ?? reports.length,
    archived: Boolean(row.archived ?? archived),
  };
}

export async function archiveVkpiReport(token: string, reportId: number): Promise<void> {
  await apiFetch(
    `/api/admin/vkpi/reports/${encodeURIComponent(String(reportId))}`,
    { method: "DELETE", body: jsonBody({ reason: "user_archived" }) },
    token,
  );
}

export async function restoreVkpiReport(token: string, reportId: number): Promise<void> {
  await apiFetch(
    `/api/admin/vkpi/reports/${encodeURIComponent(String(reportId))}/restore`,
    { method: "POST" },
    token,
  );
}

export async function createVkpiReportExport(
  token: string,
  exportFormat: VkpiReportExportFormat,
  config: VkpiReportGenerateConfig,
): Promise<VkpiReportExportResult> {
  const result = await apiFetch<unknown>(
    `/api/admin/vkpi/exports/${exportFormat}`,
    { method: "POST", body: jsonBody(buildVkpiReportPayload(config)), timeoutMs: 120_000 },
    token,
  );
  const row = recordValue(result);
  return {
    exportId: numberValue(row.export_id, row.exportId, row.id),
    status: stringValue(row.status) || "unknown",
    downloadUrl: stringValue(row.download_url, row.downloadUrl),
  };
}

export function vkpiReportDownloadPath(reportId: number, format: VkpiReportExportFormat = "pdf"): string {
  return `/api/admin/vkpi/reports/files/${encodeURIComponent(String(reportId))}/download?format=${format}`;
}

function filenameFromDisposition(value: string, fallback: string): string {
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const quoted = value.match(/filename="([^"]+)"/i)?.[1];
  const plain = value.match(/filename=([^;]+)/i)?.[1];
  let filename = quoted || plain || fallback;
  if (encoded) {
    try {
      filename = decodeURIComponent(encoded);
    } catch {
      filename = fallback;
    }
  }
  filename = filename.trim().replace(/^['"]|['"]$/g, "").replace(/[\\/]/g, "-");
  return filename || fallback;
}

async function responsePayload(response: Response): Promise<unknown> {
  const raw = await response.text();
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export async function downloadVkpiFile(token: string, downloadUrl: string, fallbackFilename: string): Promise<string> {
  if (!downloadUrl.trim()) throw new Error("接口没有返回可用的下载链接。");
  const baseOrigin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const apiRoot = new URL(buildApiUrl("/"), baseOrigin);
  const target = new URL(buildApiUrl(downloadUrl), baseOrigin);
  if (target.origin !== apiRoot.origin) {
    throw new Error("下载链接不属于当前 API，已停止发送登录凭证。");
  }
  const headers = new Headers({ "X-Requested-With": "XMLHttpRequest" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(target.toString(), {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    throw new ApiResponseError(response, await responsePayload(response));
  }
  const blob = await response.blob();
  if (!blob.size) throw new Error("下载文件为空，系统未产出可用文件。");
  const filename = filenameFromDisposition(response.headers.get("Content-Disposition") || "", fallbackFilename);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
  return filename;
}

export function reportApiErrorStatus(error: unknown): number | null {
  const status = Number(recordValue(error).status);
  return Number.isFinite(status) && status > 0 ? status : null;
}

export function reportApiErrorMessage(error: unknown, fallback: string): string {
  const status = reportApiErrorStatus(error);
  if (status === 401) return "登录状态已失效，无法访问报告。";
  if (status === 403) return "权限不足：你不能执行此报告操作。";
  if (status === 404) return "报告或下载文件不存在。";
  if (status === 409) return "报告当前状态不允许此操作，请刷新历史后重试。";
  if (status === 410) return "下载文件已过期，请重新生成或导出。";
  if (status && status >= 500) return `${fallback}，系统未产出可用结果。`;
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}
