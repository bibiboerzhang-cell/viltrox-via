import { apiFetch, jsonBody } from "../http";

/**
 * External event intelligence is kept separate from vkpi_events. These rows are
 * source-backed opportunities until an operator explicitly promotes one into an
 * internal Event.
 */
export type EventRadarLane = "dealer_event" | "local_activity" | "major_expo" | string;
export type EventRadarDecisionStatus = "new" | "watching" | "approved" | "dismissed" | "promoted" | "needs_review" | string;

export interface EventRadarOpportunity {
  id: string | number;
  title: string;
  lane: EventRadarLane;
  organizer?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  timezone?: string | null;
  local_time_text?: string | null;
  venue?: string | null;
  city?: string | null;
  region?: string | null;
  country_code?: string | null;
  official_url?: string | null;
  registration_url?: string | null;
  event_status?: string | null;
  decision_status: EventRadarDecisionStatus;
  evidence_grade?: string | null;
  verification_status?: string | null;
  freshness_status?: string | null;
  confidence?: number | null;
  relevance_score?: number | null;
  relevance_basis?: string | null;
  viltrox_presence_status?: string | null;
  dealer_id?: string | number | null;
  dealer_name?: string | null;
  source_name?: string | null;
  /** Runtime source gate returned by the joined watch-target row. */
  source_status?: string | null;
  /** Operational kill switch; only enabled + active sources are actionable. */
  source_enabled?: boolean | null;
  source_requires_human_review?: boolean | null;
  /** Registered source category; descriptive only and never proof of organizer identity or authorization. */
  source_kind?: string | null;
  last_verified_at?: string | null;
  source_checked_at?: string | null;
  change_count?: number | null;
  converted_event_id?: string | null;
  /** Bundled discovery preview only; never an organization-scoped Event row. */
  preview_only?: boolean;
  catalog_item_id?: string | null;
  claim_status?: string | null;
}

export interface EventRadarListResponse {
  items: EventRadarOpportunity[];
  count: number;
  page?: {
    limit: number;
    offset: number;
    returned: number;
    next_offset: number | null;
    has_more: boolean;
  };
  /** Server-authored coverage statement; it must not be upgraded to a global-coverage claim in the UI. */
  coverage_claim?: string | null;
}

export interface EventRadarSummary {
  total?: number;
  lane_counts?: Record<string, number>;
  decision_counts?: Record<string, number>;
  verification_counts?: Record<string, number>;
  evidence_counts?: Record<string, number>;
  country_count?: number;
  source_count?: number;
  source_freshness_counts?: Record<string, number>;
  source_identity_verified_count?: number;
  source_identity_coverage_rate?: number | null;
  stale_count?: number;
  conflict_count?: number;
  converted_count?: number;
  last_refresh_at?: string | null;
  coverage_claim?: string | null;
  us_jurisdiction_matrix?: {
    scope?: string;
    covered_states?: string[];
    missing_states?: string[];
    covered_count?: number;
    jurisdiction_count?: number;
    /** Organization-scoped opportunity rows grouped by state/DC, never a market denominator. */
    opportunity_counts_by_state_dc?: Record<string, number>;
    /** Subset whose opportunity verification_status is verified/current; freshness remains separate. */
    verification_marked_counts_by_state_dc?: Record<string, number>;
    opportunity_entity_count?: number;
    map_precision?: "state_dc_aggregate_not_venue_coordinates" | string;
    authoritative_market_denominator?: null;
    coverage_rate?: null;
    claim_status?: string;
  };
  sources?: {
    total?: number;
    countries?: number;
    by_status?: Record<string, number>;
    by_kind?: Record<string, number>;
    by_freshness?: Record<string, number>;
    verified_fresh_passports?: number;
    passport_coverage_rate?: number | null;
  };
  opportunities?: {
    total?: number;
    by_lane?: Record<string, number>;
    by_decision?: Record<string, number>;
    by_verification?: Record<string, number>;
    by_evidence?: Record<string, number>;
    by_freshness?: Record<string, number>;
    change_records?: number;
    promoted?: number;
    verified_fresh_passports?: number;
    passport_coverage_rate?: number | null;
  };
  [key: string]: unknown;
}

export interface EventRadarRefreshResult {
  ok?: boolean;
  record_only?: boolean;
  /** The write endpoint stays locked unless the latest preview explicitly returns true. */
  import_allowed?: boolean;
  quality_status?: string | null;
  claim_status?: string | null;
  requested?: number;
  source_count?: number;
  opportunity_count?: number;
  preview_item_count?: number;
  preview_items?: EventRadarOpportunity[];
  preview_contract?: {
    read_only?: boolean;
    network_accessed?: boolean;
    database_accessed?: boolean;
    business_rows_written?: number;
    automatic_promotion?: boolean;
    claim_status?: string | null;
  };
  discovered?: number;
  inserted?: number;
  updated?: number;
  unchanged?: number;
  conflicted?: number;
  stale?: number;
  errors?: Array<{ source?: string | null; error?: string | null }>;
  coverage_claim?: string | null;
  [key: string]: unknown;
}

export interface EventRadarPromoteResponse {
  ok: boolean;
  event_id?: string | null;
  item?: EventRadarOpportunity | null;
}

export interface ListEventRadarParams {
  limit?: number;
  offset?: number;
  lane?: string;
  source_kind?: string;
  decision_status?: string;
  evidence_status?: "verified" | "review" | "conflict";
  time_window?: "upcoming" | "30d" | "90d" | "past" | "date_pending";
  country?: string;
  region?: string;
  include_past?: boolean;
}

export interface CandidateStagingSummary {
  status: "ready" | "empty" | "migration_pending" | string;
  organization_id?: number;
  candidate_type: "event_opportunity" | "dealer_location" | string;
  total: number;
  review_status: Record<string, number>;
  promotion_gate_status: Record<string, number>;
  linked_field_evidence: number;
  claim_status: "descriptive_only" | string;
  automatic_promotion: false;
  business_rows_written: number;
}

export interface UsCoverageSource {
  id: string;
  name: string;
  source_kind: string;
  publisher: string;
  canonical_url: string;
  geographic_scope?: string | null;
  published_scope_note?: string | null;
  state_codes?: string[];
  status?: string | null;
  enabled?: boolean;
  direct_import_allowed?: boolean;
  requires_human_review?: boolean;
  claim_status?: string | null;
  current_feed_state?: string | null;
  candidate_generation_allowed?: boolean;
  live_audit_note?: string | null;
}

export interface UsSourceJurisdictionMatrix {
  scope: "registered_source_discovery_jurisdictions_only" | string;
  covered_states_dc: string[];
  missing_states_dc: string[];
  covered_count: number;
  jurisdiction_count: number;
  source_discovery_rate: number;
  extracted_candidate_count: null;
  verified_business_row_count: null;
  entity_coverage_rate: null;
  claim_status: "descriptive_only" | string;
  truth_note?: string | null;
}

export interface EventUsSourceRegistry {
  ok: boolean;
  country_code: "US" | string;
  coverage_claim: "registered_publisher_owned_public_entries_only" | string;
  full_us_coverage: false;
  claim_status: "descriptive_only" | string;
  truth_note?: string | null;
  checked_at?: string | null;
  jurisdiction_truth_note?: string | null;
  source_jurisdiction_matrix?: {
    event_sources?: UsSourceJurisdictionMatrix;
  };
  event_sources: UsCoverageSource[];
  counts?: {
    event_sources?: number;
    event_source_kinds?: Record<string, number>;
    event_source_jurisdictions?: number;
    enabled?: number;
    direct_import_allowed?: number;
  };
  import_gate?: { allowed?: boolean; reason?: string | null };
}

const ROOT = "/api/admin/vkpi/event-radar";

export async function listEventRadar(
  token: string,
  params: ListEventRadarParams = {},
): Promise<EventRadarListResponse> {
  const query = new URLSearchParams({ limit: String(params.limit ?? 100) });
  if (params.offset) query.set("offset", String(params.offset));
  if (params.lane) query.set("lane", params.lane);
  if (params.source_kind) query.set("source_kind", params.source_kind);
  if (params.decision_status) query.set("decision_status", params.decision_status);
  if (params.evidence_status) query.set("evidence_status", params.evidence_status);
  if (params.time_window) query.set("time_window", params.time_window);
  if (params.country) query.set("country", params.country);
  if (params.region) query.set("region", params.region);
  if (params.include_past) query.set("include_past", "true");
  return apiFetch<EventRadarListResponse>(
    `${ROOT}?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getEventCandidateStagingSummary(token: string): Promise<CandidateStagingSummary> {
  return apiFetch<CandidateStagingSummary>(`${ROOT}/candidate-staging`, { cache: "no-store" }, token);
}

export async function getEventUsSourceRegistry(token: string): Promise<EventUsSourceRegistry> {
  return apiFetch<EventUsSourceRegistry>(`${ROOT}/us-source-registry`, { cache: "no-store" }, token);
}

export async function getEventRadarSummary(token: string): Promise<EventRadarSummary> {
  return apiFetch<EventRadarSummary>(`${ROOT}/summary`, { cache: "no-store" }, token);
}

/** Validate the bundled reviewed catalog without network access or persistence. */
export async function previewEventRadarRefresh(token: string): Promise<EventRadarRefreshResult> {
  return apiFetch<EventRadarRefreshResult>(
    `${ROOT}/refresh-preview`,
    { method: "POST", cache: "no-store" },
    token,
  );
}

/** Explicit manager action that imports the bundled reviewed catalog; it does not crawl the web. */
export async function refreshEventRadar(token: string): Promise<EventRadarRefreshResult> {
  return apiFetch<EventRadarRefreshResult>(
    `${ROOT}/refresh`,
    { method: "POST", body: jsonBody({ record_only: false }), cache: "no-store" },
    token,
  );
}

export async function setEventRadarDecision(
  token: string,
  id: string | number,
  decisionStatus: EventRadarDecisionStatus,
): Promise<{ ok?: boolean; item?: EventRadarOpportunity } | EventRadarOpportunity> {
  return apiFetch<{ ok?: boolean; item?: EventRadarOpportunity } | EventRadarOpportunity>(
    `${ROOT}/${encodeURIComponent(String(id))}/decision`,
    {
      method: "PATCH",
      body: jsonBody({ decision_status: decisionStatus }),
      cache: "no-store",
    },
    token,
  );
}

export async function promoteEventRadarOpportunity(
  token: string,
  id: string | number,
): Promise<EventRadarPromoteResponse> {
  return apiFetch<EventRadarPromoteResponse>(
    `${ROOT}/${encodeURIComponent(String(id))}/promote`,
    { method: "POST", cache: "no-store" },
    token,
  );
}
