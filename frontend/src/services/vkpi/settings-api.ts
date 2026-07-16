import { apiFetch, jsonBody, buildApiUrl } from "../http";

type Row = Record<string, unknown>;

export type BusinessIntegrationState = "connected" | "pending" | "not_configured" | "error";
export type BusinessIntegrationDataQuality = "empty" | "unverified" | "partial" | "real";
export type BusinessIntegrationOperatorStatus = "verified" | "awaiting_authorization" | "awaiting_configuration" | "error";

export interface BusinessIntegrationCard {
  key: "shopify" | "dealers" | "inventory" | "costs" | "attribution" | "r2" | string;
  title: string;
  state: BusinessIntegrationState;
  summary: string;
  data_quality: BusinessIntegrationDataQuality;
  evidence: Row;
  source: string;
  next_action: string;
  operator_status?: BusinessIntegrationOperatorStatus;
  operator_label?: "已验证" | "待授权" | "待配置" | "异常" | string;
}

export interface BusinessIntegrationsStatus {
  generated_at?: string;
  claim_status?: "descriptive_only" | string;
  write_performed?: boolean;
  secrets_returned?: boolean;
  counts?: Partial<Record<BusinessIntegrationState, number>>;
  operator_counts?: Partial<Record<BusinessIntegrationOperatorStatus, number>>;
  integrations?: BusinessIntegrationCard[];
}

// F3 运行态信任块:GET /health 顶层 trust 字段(server/client/worker sha · 对齐 · worker 在线 · 迁移号)。
// /health 是公开只读端点(无需 token),直接 fetch;任何字段缺失返回 null,绝不编造。
export interface VkpiHealthTrust {
  server_git_sha: string | null;
  client_git_sha: string | null;
  worker_sha: string | null;
  worker_sha_source?: string | null;
  sha_aligned: boolean | null;
  worker_online: boolean | null;
  worker_heartbeat: string | null;
  db_migration_max: string | null;
}

export async function getHealthTrust(clientBuildSha?: string): Promise<VkpiHealthTrust | null> {
  try {
    const suffix = clientBuildSha ? `?client_build=${encodeURIComponent(clientBuildSha)}` : "";
    const response = await fetch(buildApiUrl(`/health${suffix}`), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!response.ok) return null;
    const payload = await response.json();
    const trust = (payload && typeof payload === "object" ? (payload as Row).trust : null) as Row | null;
    if (!trust || typeof trust !== "object") return null;
    const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
    const boolN = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
    return {
      server_git_sha: str(trust.server_git_sha),
      client_git_sha: str(trust.client_git_sha),
      worker_sha: str(trust.worker_sha),
      worker_sha_source: str(trust.worker_sha_source),
      sha_aligned: boolN(trust.sha_aligned),
      worker_online: boolN(trust.worker_online),
      worker_heartbeat: str(trust.worker_heartbeat),
      db_migration_max: str(trust.db_migration_max),
    };
  } catch {
    return null;
  }
}

export async function getRbacStatus(token: string, includeStaff = false) {
  const suffix = includeStaff ? "?include_staff=true" : "";
  return apiFetch<Row>(`/api/admin/vkpi/access/rbac-status${suffix}`, {}, token);
}

export async function getBusinessIntegrationsStatus(token: string) {
  return apiFetch<BusinessIntegrationsStatus>(
    "/api/admin/vkpi/settings/business-integrations",
    { timeoutMs: 15000 },
    token,
  );
}

export async function listProviderStatuses(token: string) {
  return apiFetch<{ providers?: Row[]; full_key_readable?: boolean }>("/api/marketing/settings/providers", {}, token);
}

export async function probeProviderStatus(token: string, provider: string) {
  return apiFetch<Row>(
    `/api/marketing/settings/providers/${encodeURIComponent(provider)}/probe`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function listFeatureFlags(token: string) {
  return apiFetch<{ flags?: Row[] }>("/api/admin/vkpi/settings/feature-flags", {}, token);
}

export async function updateFeatureFlags(token: string, flags: Row[]) {
  return apiFetch<Row>(
    "/api/admin/vkpi/settings/feature-flags",
    { method: "PATCH", body: jsonBody({ flags }) },
    token,
  );
}

export async function listPlatformCrawlSettings(token: string) {
  return apiFetch<{ platforms?: Row[] }>("/api/admin/vkpi/settings/platform-crawl", {}, token);
}

export async function updatePlatformCrawlSettings(token: string, platforms: Row[]) {
  return apiFetch<Row>(
    "/api/admin/vkpi/settings/platform-crawl",
    { method: "PATCH", body: jsonBody({ platforms }) },
    token,
  );
}

export async function listBudgetSettings(token: string) {
  return apiFetch<{ budgets?: Row[] }>("/api/admin/vkpi/settings/budgets", {}, token);
}

export async function updateBudgetSettings(token: string, budgets: Row[]) {
  return apiFetch<Row>(
    "/api/admin/vkpi/settings/budgets",
    { method: "PATCH", body: jsonBody({ budgets }) },
    token,
  );
}

// 多账号 API key 池(设置位,7月手动填轮转)。key 单向:只写入/不回读;响应只回是否已配置。
export async function listApiKeyPool(token: string) {
  return apiFetch<{ keys?: Row[] }>("/api/admin/vkpi/settings/api-key-pool", {}, token);
}

export async function upsertApiKey(token: string, row: Row) {
  return apiFetch<Row>(
    "/api/admin/vkpi/settings/api-key-pool",
    { method: "POST", body: jsonBody(row) },
    token,
  );
}

export async function deleteApiKey(token: string, id: number) {
  return apiFetch<Row>(
    "/api/admin/vkpi/settings/api-key-pool",
    { method: "POST", body: jsonBody({ action: "delete", id }) },
    token,
  );
}

export async function getControlStatus(token: string) {
  return apiFetch<Row>("/api/admin/vkpi/settings/control-status", {}, token);
}

// 调度任务注册表(S1):仅注册 + 可见 + enable 开关;不执行任何任务。
export async function listSchedulerTasks(token: string) {
  return apiFetch<{ tasks?: Row[]; status?: Row }>(
    "/api/admin/vkpi/settings/scheduler-tasks",
    {},
    token,
  );
}

export async function setSchedulerTaskEnabled(token: string, taskKey: string, enabled: boolean) {
  return apiFetch<{ task?: Row; status?: Row }>(
    `/api/admin/vkpi/settings/scheduler-tasks/${encodeURIComponent(taskKey)}`,
    { method: "PATCH", body: jsonBody({ enabled }) },
    token,
  );
}

export async function getCommentAlertSettings(token: string) {
  return apiFetch<{ settings?: Row }>("/api/admin/vkpi/settings/comment-alerts", {}, token);
}

export async function updateCommentAlertSettings(token: string, payload: Row) {
  return apiFetch<{ settings?: Row }>(
    "/api/admin/vkpi/settings/comment-alerts",
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function runVkpiAutomation(token: string, job: string, payload: Row = {}) {
  return apiFetch<Row>(
    `/api/admin/vkpi/cron/${encodeURIComponent(job)}/run`,
    { method: "POST", body: jsonBody(payload), timeoutMs: 120000 },
    token,
  );
}

export async function getPreferenceSettings(token: string, staffId?: number) {
  const q = staffId ? `?staff_id=${encodeURIComponent(String(staffId))}` : "";
  return apiFetch<{ preference?: Row; full_scope?: boolean }>(
    `/api/admin/vkpi/settings/preferences${q}`,
    {},
    token,
  );
}

export async function updatePreferenceSettings(token: string, payload: Row) {
  return apiFetch<{ preference?: Row; full_scope?: boolean }>(
    "/api/admin/vkpi/settings/preferences",
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function listPreferenceSettings(token: string, limit = 200) {
  return apiFetch<{ preferences?: Row[]; full_scope?: boolean }>(
    `/api/admin/vkpi/settings/preferences/list?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}

export async function getNotificationSettings(token: string, staffId?: number) {
  const q = staffId ? `?staff_id=${encodeURIComponent(String(staffId))}` : "";
  return apiFetch<{ notification_settings?: Row; full_scope?: boolean }>(
    `/api/admin/vkpi/settings/notifications${q}`,
    {},
    token,
  );
}

export async function updateNotificationSettings(token: string, payload: Row) {
  return apiFetch<{ notification_settings?: Row; full_scope?: boolean }>(
    "/api/admin/vkpi/settings/notifications",
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
}

export async function listNotificationSettings(token: string, limit = 200) {
  return apiFetch<{ notification_settings?: Row[]; full_scope?: boolean }>(
    `/api/admin/vkpi/settings/notifications/list?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );
}
