// V-KPI Metric Lineage frontend API client
//
// PASTE this file as:
//   frontend/src/services/vkpi.lineage-api.ts
//
// The apiFetch / jsonBody helpers are duplicated here intentionally so this
// file remains paste-ready and does not depend on another service module.
//
// All endpoints are routed through the existing /api/marketing/* alias
// middleware so they match the rest of the V-KPI frontend conventions.

const API_PREFIX = "/api/marketing/lineage";
const VKPI_API_PREFIX = "/api/marketing";

type Row = Record<string, unknown>;

interface ApiFetchInit extends RequestInit {
  body?: BodyInit | null;
}

async function apiFetch<T>(path: string, init: ApiFetchInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

function jsonBody(body: unknown): string {
  return JSON.stringify(body ?? {});
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type VkpiMetricLineageScope = "all" | "staff" | "project" | "kol" | "product";
export type VkpiMetricLineageTrigger = "dashboard" | "weekly_report" | "manual_export" | "cron";

export interface VkpiMetricRunHeader {
  id: number;
  run_uid: string;
  period_start: string;
  period_end: string;
  scope_type: VkpiMetricLineageScope;
  scope_id: number | null;
  trigger_source: VkpiMetricLineageTrigger;
  generated_by_staff_id: number | null;
  generated_at: string;
  definition_version: string;
  status: "pending" | "ready" | "failed";
}

export interface VkpiMetricValue {
  id: number;
  run_id: number;
  metric_key: string;
  value_numeric: number | null;
  currency: string;
  unit: string;
  calculation_json: string;          // JSON string from backend
  source_count: number;
  created_at: string;
}

export interface VkpiMetricRun {
  run: VkpiMetricRunHeader;
  values: VkpiMetricValue[];
}

export interface VkpiDrilldownEntity {
  id: number;
  name?: string;
  email?: string;
  platform?: string;
  uid?: string;
}

export interface VkpiDrilldownRow {
  id: number;
  source_id: number;
  source_type: string;
  evidence_type: string;
  evidence_ref: string;
  contribution_amount: number;
  contribution_percent: number;
  occurred_at: string | null;
  project: VkpiDrilldownEntity | null;
  kol: VkpiDrilldownEntity | null;
  staff: VkpiDrilldownEntity | null;
  snapshot: Row;
  order_snapshot?: Row | null;
}

export interface VkpiDrilldownResponse {
  value: {
    id: number;
    run_id: number;
    metric_key: string;
    value_numeric: number | null;
    currency: string;
    unit: string;
    calculation: Row;
    source_count: number;
  } | null;
  run: {
    uid: string;
    period_start: string;
    period_end: string;
    scope_type: string;
    scope_id: number | null;
    generated_at: string;
    definition_version: string;
    trigger_source: string;
    generated_by_staff_id: number | null;
  } | null;
  rows: VkpiDrilldownRow[];
  filtered: { project_id?: number | null; kol_id?: number | null; staff_id?: number | null };
  row_count: number;
  empty_reason?: "no_run_yet" | "source_rows_deleted_or_void";
}

export interface VkpiOfficialViewsEvidenceRow {
  id: string;
  metric: "views";
  label: string;
  source: string;
  amount: number;
  amountUnit: "number";
  ownerName?: string;
  kolName?: string;
  confidence?: string;
  occurredAt?: string;
  rawRef?: string;
  platform?: string;
  platformLabel?: string;
  attributionType?: string;
  accountId?: number;
  accountName?: string;
  accountHandle?: string;
  accountUrl?: string;
  staffId?: number;
  staffName?: string;
  staffEmail?: string;
  staffRole?: string;
  postId?: string | number;
  mediaUrl?: string;
}

export interface VkpiOfficialViewsPost {
  id?: string | number;
  title?: string;
  url?: string;
  media_url?: string;
  posted_at?: string;
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  account_level?: boolean;
}

export interface VkpiOfficialViewsAccount {
  id: number;
  platform: string;
  platform_label?: string;
  staff_id?: number;
  staff_name?: string;
  staff_email?: string;
  staff_role?: string;
  staff_avatar_url?: string;
  handle?: string;
  display_name?: string;
  account_url?: string;
  avatar_url?: string;
  sync_status?: string;
  last_sync_at?: string;
  followers?: number;
  posts_count?: number;
  total_views?: number;
  total_likes?: number;
  total_comments?: number;
  engagement_rate?: number;
  posts?: VkpiOfficialViewsPost[];
}

export interface VkpiOfficialViewsPlatform {
  platform: string;
  label: string;
  total_views: number;
  total_posts: number;
  total_followers: number;
  accounts: VkpiOfficialViewsAccount[];
}

export interface VkpiOfficialViewsEvidenceResponse {
  rows: VkpiOfficialViewsEvidenceRow[];
  account_count: number;
  post_count: number;
  total_views: number;
  evidence_views?: number;
  returned_rows?: number;
  platforms: VkpiOfficialViewsPlatform[];
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export interface CreateLineageRunPayload {
  periodDays?: number;
  scopeType?: VkpiMetricLineageScope;
  scopeId?: number | null;
  triggerSource?: VkpiMetricLineageTrigger;
  metrics?: string[];
  metadata?: Row;
}

export async function createLineageRun(token: string, payload: CreateLineageRunPayload = {}) {
  return apiFetch<{ run_id: number; run_uid: string; metric_count: number; source_count: number }>(
    `${API_PREFIX}/runs`,
    {
      method: "POST",
      body: jsonBody({
        period_days: payload.periodDays ?? 30,
        scope_type: payload.scopeType ?? "all",
        scope_id: payload.scopeId ?? null,
        trigger_source: payload.triggerSource ?? "dashboard",
        metrics: payload.metrics,
        metadata: payload.metadata,
      }),
    },
    token,
  );
}

export async function listLineageRuns(
  token: string,
  filters: { triggerSource?: VkpiMetricLineageTrigger; scopeType?: VkpiMetricLineageScope; limit?: number } = {},
) {
  const qs = new URLSearchParams();
  if (filters.triggerSource) qs.set("trigger_source", filters.triggerSource);
  if (filters.scopeType) qs.set("scope_type", filters.scopeType);
  qs.set("limit", String(filters.limit ?? 20));
  return apiFetch<{ runs: VkpiMetricRunHeader[] }>(`${API_PREFIX}/runs?${qs.toString()}`, {}, token);
}

export async function getLineageRun(token: string, runId: number) {
  return apiFetch<VkpiMetricRun>(`${API_PREFIX}/runs/${encodeURIComponent(String(runId))}`, {}, token);
}

export interface DrilldownFilters {
  projectId?: number | null;
  kolId?: number | null;
  staffId?: number | null;
  limit?: number;
}

export async function drilldownByValueId(
  token: string,
  metricValueId: number,
  filters: DrilldownFilters = {},
) {
  const qs = new URLSearchParams();
  if (filters.projectId) qs.set("project_id", String(filters.projectId));
  if (filters.kolId) qs.set("kol_id", String(filters.kolId));
  if (filters.staffId) qs.set("staff_id", String(filters.staffId));
  qs.set("limit", String(filters.limit ?? 200));
  return apiFetch<VkpiDrilldownResponse>(
    `${API_PREFIX}/values/${encodeURIComponent(String(metricValueId))}/drilldown?${qs.toString()}`,
    {},
    token,
  );
}

export interface DrilldownLatestFilters extends DrilldownFilters {
  scopeType?: VkpiMetricLineageScope;
  scopeId?: number | null;
}

export async function drilldownLatestByMetric(
  token: string,
  metricKey: string,
  filters: DrilldownLatestFilters = {},
) {
  const qs = new URLSearchParams();
  qs.set("scope_type", filters.scopeType ?? "all");
  if (filters.scopeId) qs.set("scope_id", String(filters.scopeId));
  if (filters.projectId) qs.set("project_id", String(filters.projectId));
  if (filters.kolId) qs.set("kol_id", String(filters.kolId));
  if (filters.staffId) qs.set("staff_id", String(filters.staffId));
  qs.set("limit", String(filters.limit ?? 200));
  return apiFetch<VkpiDrilldownResponse>(
    `${API_PREFIX}/metrics/${encodeURIComponent(metricKey)}/drilldown?${qs.toString()}`,
    {},
    token,
  );
}

export async function getOfficialViewsEvidence(
  token: string,
  filters: { limit?: number; viewAsStaffId?: number | null } = {},
) {
  const qs = new URLSearchParams();
  qs.set("limit", String(filters.limit ?? 120));
  if (filters.viewAsStaffId) qs.set("view_as_staff_id", String(filters.viewAsStaffId));
  return apiFetch<VkpiOfficialViewsEvidenceResponse>(
    `${VKPI_API_PREFIX}/channels/official-views-evidence?${qs.toString()}`,
    {},
    token,
  );
}
