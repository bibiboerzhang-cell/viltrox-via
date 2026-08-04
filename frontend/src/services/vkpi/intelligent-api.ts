import { apiFetch, jsonBody } from "../http";

/** Ask & Find v2 is a read-only query contract. Business mutations must remain
 * behind an explicit approval surface and are represented here as actions only. */
export type IntelligentQueryStatus =
  | "ready"
  | "partial"
  | "empty"
  | "needs_clarification"
  | "error"
  | "blocked"
  | "degraded"
  | "unavailable";
export type IntelligentQueryIntent =
  | "kol.pool.overview"
  | "kol.video_topic.count"
  | "project.search"
  | "market.viltrox.weekly_voice"
  | "unknown"
  | (string & {});
export type IntelligentConfidence = "high" | "medium" | "low";

// Kept for the existing Intelligence page while it transitions to the v2
// contract. New top-bar UI uses status/intent instead of exposing lane names.
export type IntelligentMode = "intent" | "search" | "synth" | "degraded";

export interface IntelligentFact {
  key: string;
  label: string;
  value: string | number | Array<string | number> | null;
  value_type: "integer" | "number" | "text" | "list" | (string & {});
  unit?: string;
  basis: string;
  confidence: IntelligentConfidence;
}

export interface IntelligentEvidence {
  id?: string;
  kind: string;
  source?: string;
  title?: string;
  snippet?: string;
  url?: string;
  entity_id?: number;
  observed_at?: string;
  confidence?: IntelligentConfidence;
  // Compatibility for evidence persisted by the existing Intelligence page.
  [key: string]: unknown;
}

export interface IntelligentCoverage {
  status: "complete" | "partial" | "empty" | "unknown" | (string & {});
  matched_entities?: number;
  evidence_count?: number;
  total_scope?: number;
  analyzed_count?: number;
  ratio?: number;
  notes: string[];
}

export interface IntelligentFreshness {
  status: "fresh" | "stale" | "unknown" | (string & {});
  generated_at: string;
  data_updated_at?: string;
  window_start?: string;
  window_end?: string;
  timezone: string;
}

export interface IntelligentMissingField {
  field: string;
  reason: string;
  impact: string;
}

export interface IntelligentAction {
  type?: string;
  label: string;
  route?: string;
  params?: Record<string, unknown>;
  requires_approval?: boolean;
}

export interface IntelligentTrace {
  request_id: string;
  client_request_id: string;
  thread_id: string;
  scope: Record<string, unknown>;
  mode: string;
  deterministic: boolean;
  query_version: string;
  took_ms: number;
  source_status?: Record<string, {
    status: string;
    reason?: string;
    result_count?: number;
    count?: number;
    matched_count?: number;
    candidate_count?: number;
    truncated?: boolean;
    time_semantics?: string;
    scope?: string;
    required_columns?: string[];
  }>;
}

export interface IntelligentQueryAnswer {
  schema_version: "ask_find_v2" | string;
  request_id: string;
  status: IntelligentQueryStatus;
  intent: IntelligentQueryIntent;
  answer: string;
  facts: IntelligentFact[];
  evidence: IntelligentEvidence[];
  coverage: IntelligentCoverage;
  freshness: IntelligentFreshness;
  missing_fields: IntelligentMissingField[];
  actions: IntelligentAction[];
  trace: IntelligentTrace;
  degraded_reason?: string;
  // Transitional fields consumed by IntelligentBoardPage.
  mode: IntelligentMode;
  cached: boolean;
  fallback_used?: boolean;
}

/** Existing full Intelligence page still consumes the original three-lane
 * endpoint. Keep its contract isolated until that page is deliberately
 * migrated; the top-bar must not silently change another product surface. */
export interface IntelligentAnswer {
  answer: string;
  mode: IntelligentMode;
  evidence: IntelligentEvidence[];
  actions: IntelligentAction[];
  cached: boolean;
  status?: string;
  fallback_used?: boolean;
  degraded_reason?: string;
}

export interface AskIntelligentOptions {
  signal?: AbortSignal;
  locale?: "zh-CN" | "en-US";
  threadId?: string;
  scope?: "auto" | "all" | "own" | "team" | { mode?: "auto" | "all" | "own" | "team"; staff_id?: number };
  timeRange?: null | "7d" | "30d" | { preset?: "7d" | "30d"; start?: string; end?: string };
  filters?: {
    intent?: IntelligentQueryIntent;
    topic?: string;
    keyword?: string;
    platform?: string;
    country?: string;
    stage?: string;
    limit?: number;
  };
  mode?: "auto" | "deterministic" | "search";
  clientRequestId?: string;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function optionalNumber(value: unknown): number | undefined {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function requestId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    // Fall through to a non-security identifier; this ID is correlation only.
  }
  return `ask-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function compatibilityMode(status: string, intent: string, degradedReason: string): IntelligentMode {
  if (degradedReason || ["partial", "error", "blocked", "degraded", "unavailable"].includes(status)) return "degraded";
  if (intent === "project.search" || intent === "unknown") return "search";
  return "intent";
}

/** Normalizes the boundary once so UI code never has to render arbitrary JSON
 * or assume that optional arrays survived a partial/degraded response. */
export function normalizeIntelligentQueryAnswer(raw: unknown): IntelligentQueryAnswer {
  const root = record(raw);
  const traceRaw = record(root.trace);
  const coverageRaw = record(root.coverage);
  const freshnessRaw = record(root.freshness);
  const status = stringValue(root.status, "error") as IntelligentQueryStatus;
  const intent = stringValue(root.intent, "unknown") as IntelligentQueryIntent;
  const degradedReason = stringValue(root.degraded_reason);
  const resolvedRequestId = stringValue(root.request_id) || stringValue(traceRaw.request_id);
  const sourceStatusRaw = record(traceRaw.source_status);
  const sourceStatus = Object.fromEntries(
    Object.entries(sourceStatusRaw).flatMap(([key, rawValue]) => {
      const value = record(rawValue);
      const statusValue = stringValue(value.status);
      if (!statusValue) return [];
      return [[key, {
        status: statusValue,
        reason: stringValue(value.reason) || undefined,
        result_count: optionalNumber(value.result_count),
        count: optionalNumber(value.count),
        matched_count: optionalNumber(value.matched_count),
        candidate_count: optionalNumber(value.candidate_count),
        truncated: typeof value.truncated === "boolean" ? value.truncated : undefined,
        time_semantics: stringValue(value.time_semantics) || undefined,
        scope: stringValue(value.scope) || undefined,
        required_columns: Array.isArray(value.required_columns)
          ? value.required_columns.filter((item): item is string => typeof item === "string")
          : undefined,
      }]];
    }),
  );

  const facts = (Array.isArray(root.facts) ? root.facts : []).map((item, index): IntelligentFact => {
    const value = record(item);
    const rawValue = value.value;
    return {
      key: stringValue(value.key, `fact-${index + 1}`),
      label: stringValue(value.label, stringValue(value.key, `Fact ${index + 1}`)),
      value: Array.isArray(rawValue)
        ? rawValue.filter((entry): entry is string | number => typeof entry === "string" || typeof entry === "number")
        : typeof rawValue === "string" || typeof rawValue === "number" || rawValue === null
          ? rawValue
          : null,
      value_type: stringValue(value.value_type, "text") as IntelligentFact["value_type"],
      unit: stringValue(value.unit) || undefined,
      basis: stringValue(value.basis),
      confidence: stringValue(value.confidence, "low") as IntelligentConfidence,
    };
  });

  const evidence = (Array.isArray(root.evidence) ? root.evidence : []).map((item, index): IntelligentEvidence => {
    const value = record(item);
    return {
      id: stringValue(value.id, `evidence-${index + 1}`),
      kind: stringValue(value.kind, "record"),
      source: stringValue(value.source) || undefined,
      title: stringValue(value.title) || undefined,
      snippet: stringValue(value.snippet) || undefined,
      url: stringValue(value.url) || undefined,
      entity_id: optionalNumber(value.entity_id),
      observed_at: stringValue(value.observed_at) || undefined,
      confidence: (stringValue(value.confidence) || undefined) as IntelligentConfidence | undefined,
    };
  });

  const missingFields = (Array.isArray(root.missing_fields) ? root.missing_fields : []).map((item, index): IntelligentMissingField => {
    const value = record(item);
    return {
      field: stringValue(value.field, `field-${index + 1}`),
      reason: stringValue(value.reason),
      impact: stringValue(value.impact),
    };
  });

  const actions = (Array.isArray(root.actions) ? root.actions : []).map((item, index): IntelligentAction => {
    const value = record(item);
    return {
      type: stringValue(value.type) || undefined,
      label: stringValue(value.label, `Action ${index + 1}`),
      route: stringValue(value.route) || undefined,
      params: record(value.params),
      requires_approval: Boolean(value.requires_approval),
    };
  });

  return {
    schema_version: stringValue(root.schema_version, "ask_find_v2"),
    request_id: resolvedRequestId,
    status,
    intent,
    answer: stringValue(root.answer),
    facts,
    evidence,
    coverage: {
      status: stringValue(coverageRaw.status, "unknown"),
      matched_entities: optionalNumber(coverageRaw.matched_entities),
      evidence_count: optionalNumber(coverageRaw.evidence_count),
      total_scope: optionalNumber(coverageRaw.total_scope),
      analyzed_count: optionalNumber(coverageRaw.analyzed_count),
      ratio: optionalNumber(coverageRaw.ratio),
      notes: (Array.isArray(coverageRaw.notes) ? coverageRaw.notes : []).filter((item): item is string => typeof item === "string"),
    },
    freshness: {
      status: stringValue(freshnessRaw.status, "unknown"),
      generated_at: stringValue(freshnessRaw.generated_at),
      data_updated_at: stringValue(freshnessRaw.data_updated_at) || undefined,
      window_start: stringValue(freshnessRaw.window_start) || undefined,
      window_end: stringValue(freshnessRaw.window_end) || undefined,
      timezone: stringValue(freshnessRaw.timezone, "UTC"),
    },
    missing_fields: missingFields,
    actions,
    trace: {
      request_id: stringValue(traceRaw.request_id, resolvedRequestId),
      client_request_id: stringValue(traceRaw.client_request_id),
      thread_id: stringValue(traceRaw.thread_id),
      scope: record(traceRaw.scope),
      mode: stringValue(traceRaw.mode, "deterministic"),
      deterministic: traceRaw.deterministic !== false,
      query_version: stringValue(traceRaw.query_version, "ask_find_v2"),
      took_ms: numberValue(traceRaw.took_ms),
      source_status: Object.keys(sourceStatus).length > 0 ? sourceStatus : undefined,
    },
    degraded_reason: degradedReason || undefined,
    mode: compatibilityMode(status, String(intent), degradedReason),
    cached: Boolean(root.cached),
    fallback_used: Boolean(root.fallback_used || degradedReason || status === "partial"),
  };
}

export async function queryIntelligent(
  token: string,
  question: string,
  options: AskIntelligentOptions = {},
): Promise<IntelligentQueryAnswer> {
  const clientRequestId = options.clientRequestId || requestId();
  const raw = await apiFetch<unknown>(
    "/api/admin/vkpi/intelligent/query",
    {
      method: "POST",
      body: jsonBody({
        query: question,
        locale: options.locale || "zh-CN",
        thread_id: options.threadId || "ask-find-topbar",
        scope: options.scope || "auto",
        time_range: options.timeRange ?? null,
        filters: options.filters || {},
        mode: options.mode || "auto",
        client_request_id: clientRequestId,
      }),
      signal: options.signal,
      timeoutMs: 35000,
    },
    token,
  );
  return normalizeIntelligentQueryAnswer(raw);
}

/** Legacy three-lane read-only query used by IntelligentBoardPage. */
export async function askIntelligent(token: string, question: string): Promise<IntelligentAnswer> {
  return apiFetch<IntelligentAnswer>(
    "/api/admin/vkpi/intelligent/ask",
    { method: "POST", body: jsonBody({ question }), timeoutMs: 35000 },
    token,
  );
}

export interface SuggestionsResponse {
  suggestions: string[];
  source: "seeds" | "default";
}

export interface IntelligentStatsDay {
  date: string;
  count: number;
}

export interface IntelligentStats {
  status: "ready" | "empty" | "error" | string;
  reason?: string;
  total?: number;
  last_at?: string | null;
  by_day?: IntelligentStatsDay[];
  note?: string;
}

export async function fetchIntelligentStats(token: string): Promise<IntelligentStats> {
  return apiFetch<IntelligentStats>(
    "/api/admin/vkpi/intelligent/stats",
    { cache: "no-store" },
    token,
  );
}

export async function fetchSuggestions(token: string): Promise<string[]> {
  const res = await apiFetch<SuggestionsResponse>(
    "/api/admin/vkpi/intelligent/suggestions",
    { cache: "no-store" },
    token,
  );
  return Array.isArray(res.suggestions) ? res.suggestions : [];
}
