import { apiFetch, jsonBody } from "../http";

type Row = Record<string, unknown>;

export async function getRbacStatus(token: string, includeStaff = false) {
  const suffix = includeStaff ? "?include_staff=true" : "";
  return apiFetch<Row>(`/api/admin/vkpi/access/rbac-status${suffix}`, {}, token);
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
