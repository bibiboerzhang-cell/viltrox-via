import { apiFetch, jsonBody } from "../http";

// 经销商地图(Dealer Map)真后端 API —— 镜像 inventory-api.ts 风格。
// 前缀 /api/admin/vkpi/dealers;纯地理数据源(美国相机零售商),无评分/无 v6_fit。

export interface VkpiDealer {
  id: string | number;
  name: string;
  address: string;
  city?: string | null;
  state?: string | null;
  lat?: number | null;
  lng?: number | null;
  source?: string | null;
  country?: string | null;
  brand_listing_url?: string | null;
  location_source_url?: string | null;
  source_status?: "public_listing_verified" | "unverified" | string;
  authorization_status?: "needs_viltrox_confirmation" | "authorized_confirmed" | string;
  stored_authorization_status?: "needs_viltrox_confirmation" | "authorized_confirmed" | string;
  source_checked_at?: string | null;
  verification_note?: string | null;
  postal_code?: string | null;
  phone?: string | null;
  contact_email?: string | null;
  store_hours?: string | null;
  public_services?: string | null;
  website_url?: string | null;
  social_links?: Array<{ platform?: string; url: string }>;
  social_status?: "not_collected" | "available" | string;
  brand_codes?: string[];
  brand_relationships?: VkpiDealerBrandRelationship[];
  publication_status?: "draft" | "published" | string;
  published_at?: string | null;
  viltrox_deployment?: VkpiDealerViltroxDeployment;
  activity?: VkpiDealerActivity;
  last_verified_at?: string | null;
  freshness_status?: "fresh" | "stale" | "unavailable" | "unverified" | "invalid_future" | string;
  coverage_scope?: {
    scope?: "registered_location_only" | string;
    country?: string | null;
    state?: string | null;
    city?: string | null;
    service_area?: string | null;
    claim_status?: "descriptive_only" | string;
  };
  channel_evidence?: {
    physical_location_registered?: boolean;
    offline_location?: "public_listing_verified" | "candidate" | "unavailable" | string;
    online_product_page?: "declared_public_url" | "verified_public_url" | "current_public_url" | "unavailable" | string;
    online_sales?: "unknown" | string;
    current_inventory?: "unknown" | string;
  };
  truth_status?: {
    candidate?: boolean;
    public_listing?: "verified" | "unverified" | string;
    product_evidence?: "declared_public_url" | "verified_public_url" | "current_public_url" | "unavailable" | string;
    viltrox_authorization?: "confirmed" | "pending" | string;
    current_inventory?: "unknown" | string;
  };
  product_evidence?: {
    status?: "declared_public_url" | "verified_public_url" | "current_public_url" | "unavailable" | string;
    url?: string | null;
    checked_at?: string | null;
    current_inventory?: "unknown" | string;
    claim_status?: "descriptive_only" | string;
  };
  authorization_evidence?: {
    status?: "authorized_confirmed" | "needs_viltrox_confirmation" | string;
    official_viltrox_source_url?: string | null;
    verified_at?: string | null;
    stored_status?: string | null;
    block_reason?: string | null;
    claim_status?: "descriptive_only" | string;
  };
  provenance?: Record<string, {
    status?: string;
    source_url?: string | null;
    checked_at?: string | null;
  }>;
  created_at?: string;
}

export interface VkpiDealerBrandRelationship {
  brand_key: string;
  relationship_status?: string | null;
  authorization_status?: string | null;
  evidence_url?: string | null;
  source_checked_at?: string | null;
}

/** Internal map deployment. This is deliberately separate from authorization and inventory. */
export interface VkpiDealerViltroxDeployment {
  status: "not_deployed" | "planned" | "deployed" | "paused" | string;
  deployed_at?: string | null;
  note?: string | null;
}

/** Public event/activity observation only; `unknown` must never be rendered as no activity. */
export interface VkpiDealerActivity {
  status: "unknown" | "none_observed" | "active" | string;
  page_url?: string | null;
  checked_at?: string | null;
  next_event_at?: string | null;
  note?: string | null;
}

export interface VkpiDealerLinkedActivity {
  id: string | number;
  title: string;
  lane?: string | null;
  organizer?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  timezone?: string | null;
  local_time_text?: string | null;
  venue?: string | null;
  address?: string | null;
  city?: string | null;
  region?: string | null;
  country_code?: string | null;
  official_url?: string | null;
  registration_url?: string | null;
  event_status?: string | null;
  decision_status?: string | null;
  verification_status?: string | null;
  source_name?: string | null;
  source_kind?: string | null;
  association?: "exact_dealer_id" | string;
  claim_status?: "descriptive_only" | string;
}

export interface VkpiDealerActivityFeed {
  status: "ready" | "empty" | "migration_pending" | string;
  dealer_id: string | number;
  dealer_name?: string | null;
  activities: VkpiDealerLinkedActivity[];
  count: number;
  linked_count?: number;
  suppressed_count?: number;
  suppression_reason?: "source_not_active_or_enabled" | string | null;
  returned?: number;
  next_activity_at?: string | null;
  association_policy: "exact_dealer_id_only" | string;
  automatic_sync?: boolean;
  source?: string | null;
  business_rows_written?: 0 | number;
  claim_status?: "descriptive_only" | string;
}

export interface VkpiDealerPin {
  id?: string | number | null;
  name: string;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  lat: number;
  lng: number;
  color?: string;
  website_url?: string | null;
  phone?: string | null;
  contact_email?: string | null;
  social_links?: VkpiDealer["social_links"];
  social_status?: VkpiDealer["social_status"];
  brand_codes?: string[];
  brand_relationships?: VkpiDealerBrandRelationship[];
  publication_status?: VkpiDealer["publication_status"];
  published_at?: string | null;
  viltrox_deployment?: VkpiDealerViltroxDeployment;
  activity?: VkpiDealerActivity;
  brand_listing_url?: string | null;
  location_source_url?: string | null;
  source_status?: VkpiDealer["source_status"];
  authorization_status?: VkpiDealer["authorization_status"];
  source_checked_at?: string | null;
  last_verified_at?: string | null;
  freshness_status?: VkpiDealer["freshness_status"];
  verification_note?: string | null;
  coverage_scope?: VkpiDealer["coverage_scope"];
  channel_evidence?: VkpiDealer["channel_evidence"];
  truth_status?: VkpiDealer["truth_status"];
  product_evidence?: VkpiDealer["product_evidence"];
  authorization_evidence?: VkpiDealer["authorization_evidence"];
  provenance?: VkpiDealer["provenance"];
}

export type VkpiDealerChannelFilter = "all" | "offline_location" | "online_product_page" | "both";
export type VkpiDealerEvidenceFilter = "all" | "candidate" | "public_listing_verified";
export type VkpiDealerProductFilter = "all" | "available" | "missing";
export type VkpiDealerAuthorizationFilter = "all" | "confirmed" | "pending";

export interface VkpiDealerQueryOptions {
  limit?: number;
  offset?: number;
  state?: string;
  city?: string;
  channel?: VkpiDealerChannelFilter;
  evidenceStatus?: VkpiDealerEvidenceFilter;
  productEvidence?: VkpiDealerProductFilter;
  authorization?: VkpiDealerAuthorizationFilter;
  brand?: string;
  publishedOnly?: boolean;
}

export interface VkpiDealerLocationQueryOptions extends VkpiDealerQueryOptions {
  signal?: AbortSignal;
  /**
   * west,south,east,north. The endpoint already accepts this contract, but the
   * current RealMap wrapper does not yet emit viewport changes; callers must
   * not claim viewport pagination until that P1 wiring is added.
   */
  bbox?: [number, number, number, number];
}

export interface VkpiDealerListResponse {
  dealers?: VkpiDealer[];
  count?: number;
  total_count?: number;
  offset?: number;
  limit?: number;
  page?: {
    limit: number;
    offset: number;
    returned: number;
    next_offset: number | null;
    has_more: boolean;
  };
}

function cleanText(value: unknown): string {
  return String(value || "").trim();
}

function normalizeBrandKey(value: unknown): string {
  return cleanText(value).toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function normalizeBrandRelationships(raw: VkpiDealer): VkpiDealerBrandRelationship[] {
  const rows = Array.isArray(raw.brand_relationships) ? raw.brand_relationships : [];
  const byKey = new Map<string, VkpiDealerBrandRelationship>();
  for (const row of rows) {
    const brandKey = normalizeBrandKey(row?.brand_key);
    if (!brandKey) continue;
    byKey.set(brandKey, { ...row, brand_key: brandKey });
  }
  for (const value of Array.isArray(raw.brand_codes) ? raw.brand_codes : []) {
    const brandKey = normalizeBrandKey(value);
    if (brandKey && !byKey.has(brandKey)) byKey.set(brandKey, { brand_key: brandKey });
  }
  return [...byKey.values()];
}

function sourceWebsite(...urls: Array<string | null | undefined>): string | null {
  for (const raw of urls) {
    const value = cleanText(raw);
    if (!value) continue;
    try {
      const parsed = new URL(value);
      if (["http:", "https:"].includes(parsed.protocol) && parsed.hostname) return parsed.origin;
    } catch {
      // Invalid or non-public URL stays unavailable; never guess a website.
    }
  }
  return null;
}

function officialViltroxUrl(value: unknown): string | null {
  const text = cleanText(value);
  if (!text) return null;
  try {
    const parsed = new URL(text);
    const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
    if (!["http:", "https:"].includes(parsed.protocol)) return null;
    return host === "viltrox.com" || host.endsWith(".viltrox.com") ? text : null;
  } catch {
    return null;
  }
}

const PRODUCT_STATUSES = new Set([
  "declared_public_url",
  "verified_public_url",
  "current_public_url",
  "unavailable",
]);

function normalizedProductStatus(raw: VkpiDealer, productUrl: string | null, listingVerified: boolean, checkedAt: string | null): string {
  const explicit = cleanText(
    raw.product_evidence?.status
      || raw.truth_status?.product_evidence
      || raw.channel_evidence?.online_product_page,
  );
  if (PRODUCT_STATUSES.has(explicit)) return explicit;
  if (!productUrl) return "unavailable";
  // A legacy endpoint can establish that a URL and review timestamp exist,
  // but only the backend's clock may call it current.
  return listingVerified && checkedAt ? "verified_public_url" : "declared_public_url";
}

function validPastTimestamp(value: unknown): string | null {
  const text = cleanText(value);
  if (!text) return null;
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed) || parsed > Date.now() + 5 * 60_000) return null;
  return text;
}

/**
 * Normalize legacy/raw Dealer rows into the same evidence contract as the new
 * backend projection. Every derived value is anchored to a returned raw field;
 * missing social, inventory, sales, and authorization evidence stays missing.
 */
export function normalizeDealer(raw: VkpiDealer): VkpiDealer {
  const listingVerified = raw.source_status === "public_listing_verified";
  const productUrl = cleanText(raw.brand_listing_url) || null;
  const locationUrl = cleanText(raw.location_source_url) || null;
  const physicalRegistered = [raw.address, raw.city, raw.state].every((value) => Boolean(cleanText(value)));
  const contactAvailable = Boolean(cleanText(raw.phone) || cleanText(raw.contact_email));
  const checkedAt = cleanText(raw.source_checked_at) || null;
  const productStatus = normalizedProductStatus(raw, productUrl, listingVerified, checkedAt);
  const officialAuthorizationUrl = officialViltroxUrl(raw.authorization_evidence?.official_viltrox_source_url);
  const authorizationVerifiedAt = validPastTimestamp(raw.authorization_evidence?.verified_at);
  const storedAuthorizationStatus = raw.stored_authorization_status || raw.authorization_status || "needs_viltrox_confirmation";
  const authorizationConfirmed = Boolean(
    storedAuthorizationStatus === "authorized_confirmed"
      && officialAuthorizationUrl
      && authorizationVerifiedAt,
  );
  const authorizationStatus = authorizationConfirmed ? "authorized_confirmed" : "needs_viltrox_confirmation";
  const websiteUrl = raw.website_url || sourceWebsite(locationUrl, productUrl);
  const brandRelationships = normalizeBrandRelationships(raw);
  const brandCodes = brandRelationships.map((row) => row.brand_key);
  const truthStatus: NonNullable<VkpiDealer["truth_status"]> = {
    candidate: !listingVerified,
    public_listing: listingVerified ? "verified" : "unverified",
    current_inventory: "unknown",
    ...(raw.truth_status || {}),
    product_evidence: productStatus,
    viltrox_authorization: authorizationConfirmed ? "confirmed" : "pending",
  };
  const channelEvidence: NonNullable<VkpiDealer["channel_evidence"]> = {
    physical_location_registered: physicalRegistered,
    offline_location: listingVerified && physicalRegistered && locationUrl
      ? "public_listing_verified"
      : physicalRegistered ? "candidate" : "unavailable",
    online_sales: "unknown",
    current_inventory: "unknown",
    ...(raw.channel_evidence || {}),
    online_product_page: productStatus,
  };
  const fallbackProvenance: NonNullable<VkpiDealer["provenance"]> = {
    public_listing: {
      status: listingVerified ? "verified" : "unverified",
      source_url: locationUrl,
      checked_at: listingVerified ? checkedAt : null,
    },
    product: {
      status: productStatus,
      source_url: productUrl,
      checked_at: productUrl ? checkedAt : null,
    },
    contact: {
      status: contactAvailable ? (listingVerified ? "public_listing_contact" : "unverified") : "unavailable",
      source_url: contactAvailable ? locationUrl : null,
      checked_at: contactAvailable ? checkedAt : null,
    },
    website: {
      status: websiteUrl ? "derived_from_public_source_url" : "unavailable",
      source_url: locationUrl || productUrl,
      checked_at: websiteUrl ? checkedAt : null,
    },
    social: { status: "not_collected", source_url: null, checked_at: null },
    viltrox_authorization: {
      status: authorizationConfirmed ? "confirmed" : "pending",
      source_url: officialAuthorizationUrl,
      checked_at: authorizationConfirmed ? authorizationVerifiedAt : null,
    },
  };
  return {
    ...raw,
    stored_authorization_status: storedAuthorizationStatus,
    authorization_status: authorizationStatus,
    website_url: websiteUrl,
    social_links: Array.isArray(raw.social_links) ? raw.social_links : [],
    social_status: raw.social_status || "not_collected",
    brand_codes: brandCodes,
    brand_relationships: brandRelationships,
    publication_status: raw.publication_status || "legacy_visible",
    published_at: raw.published_at || null,
    viltrox_deployment: raw.viltrox_deployment || { status: "not_deployed", deployed_at: null, note: null },
    activity: raw.activity || { status: "unknown", page_url: null, checked_at: null, next_event_at: null, note: null },
    last_verified_at: raw.last_verified_at || (listingVerified ? checkedAt : null),
    coverage_scope: raw.coverage_scope || {
      scope: "registered_location_only",
      country: cleanText(raw.country) || "US",
      state: cleanText(raw.state) || null,
      city: cleanText(raw.city) || null,
      service_area: null,
      claim_status: "descriptive_only",
    },
    channel_evidence: channelEvidence,
    truth_status: truthStatus,
    product_evidence: {
      ...(raw.product_evidence || {}),
      status: productStatus,
      url: productUrl,
      checked_at: productUrl ? checkedAt : null,
      current_inventory: "unknown",
      claim_status: "descriptive_only",
    },
    authorization_evidence: {
      ...(raw.authorization_evidence || {}),
      status: authorizationStatus,
      official_viltrox_source_url: officialAuthorizationUrl,
      verified_at: authorizationConfirmed ? authorizationVerifiedAt : null,
      stored_status: storedAuthorizationStatus,
      block_reason: authorizationConfirmed ? null : "official_viltrox_source_url_and_verified_at_required",
      claim_status: "descriptive_only",
    },
    provenance: {
      ...fallbackProvenance,
      ...(raw.provenance || {}),
      product: {
        ...(fallbackProvenance.product || {}),
        ...(raw.provenance?.product || {}),
        status: productStatus,
      },
      viltrox_authorization: {
        ...(fallbackProvenance.viltrox_authorization || {}),
        status: authorizationConfirmed ? "confirmed" : "pending",
        source_url: officialAuthorizationUrl,
        checked_at: authorizationConfirmed ? authorizationVerifiedAt : null,
      },
    },
  };
}

function normalizeDealerPin(raw: VkpiDealerPin): VkpiDealerPin {
  const normalized = normalizeDealer({
    ...raw,
    id: raw.id ?? `${raw.name}-${raw.address || ""}`,
    address: raw.address || "",
  });
  return { ...raw, ...normalized, lat: raw.lat, lng: raw.lng };
}

export interface VkpiDealerCoverage {
  status: "ready" | "empty" | "migration_pending" | string;
  total: number;
  public_listing_verified: number;
  authorized_confirmed: number;
  authorization_pending: number;
  located: number;
  coordinate_present?: number;
  /** Explicitly published + coordinate-complete map rows; not an evidence score. */
  published_map_pins?: number;
  states: number;
  countries: number;
  product_page_declared: number;
  contacts: { phone: number; email: number; hours: number; services: number };
  freshness: { fresh: number; stale: number; unavailable: number };
  identity: { reviewed_alias_dealers: number; exact_location_dealers: number };
  passports: { dealer_locations: number; verified_fresh: number };
  us_jurisdiction_matrix?: {
    scope: "registered_rows_with_us_state_or_dc_only" | string;
    covered_states: string[];
    missing_states: string[];
    covered_count: number;
    jurisdiction_count: number;
    authoritative_market_denominator: null;
    coverage_rate: null;
    claim_status: "descriptive_only" | string;
    dealer_counts_by_state_dc?: Record<string, number>;
    public_listing_verified_counts_by_state_dc?: Record<string, number>;
    coordinate_present_counts_by_state_dc?: Record<string, number>;
    map_eligible_counts_by_state_dc?: Record<string, number>;
    located_counts_by_state_dc?: Record<string, number>;
    dealer_entity_count?: number;
    map_precision?: "registered_state_dc_aggregate_not_store_coordinates" | string;
  };
  coverage_claim: "registered_public_listings_only" | string;
  global_complete: false;
  global_denominator?: null;
  global_coverage_rate?: null;
  claim_status: "descriptive_only" | string;
  stale_after_days?: number;
  as_of?: string;
}

export interface VkpiCandidateStagingSummary {
  status: "ready" | "empty" | "migration_pending" | string;
  organization_id?: number;
  candidate_type: "dealer_location" | "event_opportunity" | string;
  total: number;
  review_status: Record<string, number>;
  promotion_gate_status: Record<string, number>;
  linked_field_evidence: number;
  claim_status: "descriptive_only" | string;
  automatic_promotion: false;
  business_rows_written: number;
}

export interface VkpiUsDealerDiscoverySource {
  id: string;
  name: string;
  source_kind: "manufacturer_dealer_directory" | string;
  publisher: string;
  canonical_url: string;
  state_codes?: string[];
  geographic_scope?: string | null;
  manufacturer_authorization_scope?: string | null;
  status?: string | null;
  enabled?: boolean;
  direct_import_allowed?: boolean;
  requires_human_review?: boolean;
  candidate_only?: boolean;
  site_has_viltrox_product?: "unknown" | string;
  viltrox_authorized?: "unknown" | string;
}

export interface VkpiUsSourceJurisdictionMatrix {
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

export interface VkpiDealerAdapterSourceReadiness {
  source_registry_id: string;
  publisher?: string;
  source_kind?: string;
  adapter?: string | null;
  format_mapped: boolean;
  source_fixture_verified: boolean;
  terms_robots_status: string;
  terms_robots_reviewed: boolean;
  source_enabled: boolean;
  snapshot_import_readiness: "blocked" | string;
  candidate_envelope_readiness: "blocked" | string;
  direct_business_import: boolean;
  blockers: string[];
  claim_status: "descriptive_only" | string;
}

export interface VkpiUsDealerSourceRegistry {
  ok: boolean;
  country_code: "US" | string;
  coverage_claim: "registered_publisher_owned_public_entries_only" | string;
  full_us_coverage: false;
  claim_status: "descriptive_only" | string;
  truth_note?: string | null;
  checked_at?: string | null;
  jurisdiction_truth_note?: string | null;
  source_jurisdiction_matrix?: {
    dealer_discovery_sources?: VkpiUsSourceJurisdictionMatrix;
  };
  dealer_discovery_sources: VkpiUsDealerDiscoverySource[];
  counts?: {
    dealer_discovery_sources?: number;
    dealer_source_kinds?: Record<string, number>;
    dealer_source_jurisdictions?: number;
    dealer_discovery_scopes?: number;
    dealer_manufacturer_scopes?: number;
    enabled?: number;
    direct_import_allowed?: number;
  };
  import_gate?: { allowed?: boolean; reason?: string | null };
  adapter_source_readiness?: VkpiDealerAdapterSourceReadiness[];
  reviewed_persistence_readiness?: {
    supported: boolean;
    status: "ready" | "migration_required" | string;
    reason?: string | null;
    contract_version?: number;
    required_durable_fields?: string[];
    missing_durable_fields?: string[];
    automatic_promotion?: false;
    claim_status?: "descriptive_only" | string;
    read_only?: true;
    database_accessed?: true;
    business_rows_written?: 0;
  };
  adapter_readiness?: {
    registered_source_count?: number;
    adapter_source_count?: number;
    mapped_adapter_source_count?: number;
    sources_without_mapped_adapter?: string[];
    all_registered_sources_have_mapped_adapter?: boolean;
    readiness_level?: string;
    source_fixture_verified_count?: number;
    sources_without_source_fixture_verification?: string[];
    all_registered_sources_have_source_fixture_verification?: boolean;
    /** @deprecated compatibility aliases; mapping is not source-fixture verification. */
    sources_without_verified_adapter?: string[];
    /** @deprecated compatibility alias; always false until fixture verification exists. */
    all_registered_sources_have_verified_adapter?: boolean;
    blocker?: string | null;
    source_coverage_is_not_entity_coverage?: boolean;
  };
}

export interface VkpiDealerScrapePayload {
  source?: string;
  limit?: number; // <= 20(后端 HARD CAP)
  record_only?: boolean; // 默认 true = 纯预检,no blast
}

export interface VkpiDealerScrapeResult {
  ok: boolean;
  source: string;
  /** Import must remain disabled unless the latest record-only preview explicitly returns true. */
  import_allowed?: boolean;
  import_block_reason?: string | null;
  quality_status?: string | null;
  claim_status?: string | null;
  requested: number;
  inserted: number;
  updated?: number;
  skipped: number;
  geocoded: number;
  pending_geocode: number;
  record_only?: boolean;
  plan?: Array<{
    name?: string | null;
    address?: string | null;
    city?: string | null;
    state?: string | null;
    brand_listing_url?: string | null;
    location_source_url?: string | null;
    source_status?: string | null;
    authorization_status?: string | null;
    postal_code?: string | null;
    phone?: string | null;
    contact_email?: string | null;
    store_hours?: string | null;
    public_services?: string | null;
    will_geocode?: boolean;
  }>;
  errors: Array<{ name?: string | null; error: string }>;
}

export async function listDealers(
  token: string,
  opts: VkpiDealerQueryOptions = {},
): Promise<VkpiDealerListResponse> {
  const query = new URLSearchParams();
  if (opts.limit != null) query.set("limit", String(opts.limit));
  if (opts.offset != null) query.set("offset", String(opts.offset));
  if (opts.state) query.set("state", opts.state);
  if (opts.city) query.set("city", opts.city);
  if (opts.channel && opts.channel !== "all") query.set("channel", opts.channel);
  if (opts.evidenceStatus && opts.evidenceStatus !== "all") query.set("evidence_status", opts.evidenceStatus);
  if (opts.productEvidence && opts.productEvidence !== "all") query.set("product_evidence", opts.productEvidence);
  if (opts.authorization && opts.authorization !== "all") query.set("authorization", opts.authorization);
  if (opts.brand) query.set("brand", normalizeBrandKey(opts.brand));
  if (opts.publishedOnly != null) query.set("published_only", String(opts.publishedOnly));
  const qs = query.toString();
  const response = await apiFetch<VkpiDealerListResponse>(
    `/api/admin/vkpi/dealers${qs ? `?${qs}` : ""}`,
    {},
    token,
  );
  return {
    ...response,
    dealers: Array.isArray(response.dealers) ? response.dealers.map(normalizeDealer) : response.dealers,
  };
}

/** Load every server-reported Dealer page without treating a 500-row page as the directory. */
export async function listAllDealers(
  token: string,
  opts: Omit<VkpiDealerQueryOptions, "limit" | "offset"> = {},
): Promise<VkpiDealerListResponse> {
  const pageSize = 500;
  const dealers: VkpiDealer[] = [];
  let offset = 0;
  let lastResponse: VkpiDealerListResponse = {};
  const seenOffsets = new Set<number>();
  while (true) {
    if (seenOffsets.has(offset)) throw new Error("Dealer pagination did not advance");
    seenOffsets.add(offset);
    const response = await listDealers(token, {
      ...opts,
      limit: pageSize,
      ...(offset > 0 ? { offset } : {}),
    });
    lastResponse = response;
    const pageDealers = response.dealers || [];
    dealers.push(...pageDealers);
    if (!response.page && !Number.isFinite(Number(response.total_count)) && pageDealers.length >= pageSize) {
      throw new Error("Dealer directory may be truncated: server did not return total_count pagination metadata");
    }
    const nextOffset = response.page?.next_offset;
    if (nextOffset == null || response.page?.has_more !== true) break;
    if (!Number.isFinite(nextOffset) || nextOffset <= offset) {
      throw new Error("Dealer pagination returned an invalid next_offset");
    }
    offset = nextOffset;
  }
  const totalCount = Number(lastResponse.total_count);
  if (Number.isFinite(totalCount) && totalCount !== dealers.length) {
    throw new Error(`Dealer directory incomplete: loaded ${dealers.length} of ${totalCount}`);
  }
  return {
    ...lastResponse,
    dealers,
    count: dealers.length,
    total_count: Number.isFinite(totalCount) ? totalCount : dealers.length,
    offset: 0,
    limit: pageSize,
    page: {
      limit: pageSize,
      offset: 0,
      returned: dealers.length,
      next_offset: null,
      has_more: false,
    },
  };
}

export async function getDealerLocations(
  token: string,
  opts: VkpiDealerLocationQueryOptions = {},
): Promise<{ pins?: VkpiDealerPin[] }> {
  const query = new URLSearchParams();
  if (opts.state) query.set("state", opts.state);
  if (opts.city) query.set("city", opts.city);
  if (opts.channel && opts.channel !== "all") query.set("channel", opts.channel);
  if (opts.evidenceStatus && opts.evidenceStatus !== "all") query.set("evidence_status", opts.evidenceStatus);
  if (opts.productEvidence && opts.productEvidence !== "all") query.set("product_evidence", opts.productEvidence);
  if (opts.authorization && opts.authorization !== "all") query.set("authorization", opts.authorization);
  if (opts.brand) query.set("brand", normalizeBrandKey(opts.brand));
  if (opts.publishedOnly != null) query.set("published_only", String(opts.publishedOnly));
  if (opts.bbox) query.set("bbox", opts.bbox.map((value) => String(value)).join(","));
  const qs = query.toString();
  const response = await apiFetch<{ pins?: VkpiDealerPin[] }>(
    `/api/admin/vkpi/dealers/locations${qs ? `?${qs}` : ""}`,
    { signal: opts.signal },
    token,
  );
  return {
    ...response,
    pins: Array.isArray(response.pins) ? response.pins.map(normalizeDealerPin) : response.pins,
  };
}

export async function getDealerCoverage(
  token: string,
  staleAfterDays = 30,
): Promise<VkpiDealerCoverage> {
  const query = new URLSearchParams({ stale_after_days: String(staleAfterDays) });
  return apiFetch<VkpiDealerCoverage>(
    `/api/admin/vkpi/dealers/coverage?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function getDealerCandidateStagingSummary(
  token: string,
): Promise<VkpiCandidateStagingSummary> {
  return apiFetch<VkpiCandidateStagingSummary>(
    "/api/admin/vkpi/dealers/candidate-staging",
    { cache: "no-store" },
    token,
  );
}

export async function getDealerUsSourceRegistry(
  token: string,
): Promise<VkpiUsDealerSourceRegistry> {
  return apiFetch<VkpiUsDealerSourceRegistry>(
    "/api/admin/vkpi/dealers/us-source-registry",
    { cache: "no-store" },
    token,
  );
}

export async function getDealerActivities(
  token: string,
  dealerId: string | number,
  limit = 20,
): Promise<VkpiDealerActivityFeed> {
  const query = new URLSearchParams({ limit: String(Math.max(1, Math.min(limit, 100))) });
  return apiFetch<VkpiDealerActivityFeed>(
    `/api/admin/vkpi/dealers/${encodeURIComponent(String(dealerId))}/activities?${query.toString()}`,
    { cache: "no-store" },
    token,
  );
}

export async function scrapeDealersEnqueue(
  token: string,
  payload: VkpiDealerScrapePayload = {},
): Promise<VkpiDealerScrapeResult> {
  return apiFetch<VkpiDealerScrapeResult>(
    "/api/admin/vkpi/dealers/scrape-enqueue",
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

// 手动新增单个经销商(name+address 幂等)。无 lat/lng 时进 pending_geocode,地图待经纬度齐才显。
export async function createDealer(
  token: string,
  payload: VkpiDealerWritePayload,
): Promise<VkpiDealer> {
  const response = await apiFetch<VkpiDealer>(
    "/api/admin/vkpi/dealers",
    { method: "POST", body: jsonBody(payload) },
    token,
  );
  return normalizeDealer(response);
}

export interface VkpiDealerWritePayload {
  name?: string;
  address?: string;
  city?: string;
  state?: string;
  postal_code?: string;
  country?: string;
  lat?: number;
  lng?: number;
  phone?: string;
  contact_email?: string;
  website_url?: string;
  social_links?: Array<{ platform?: string; url: string }>;
  source?: string;
  brands?: Array<string | VkpiDealerBrandRelationship>;
  viltrox_deployment?: VkpiDealerViltroxDeployment;
  activity?: VkpiDealerActivity;
}

export async function updateDealer(
  token: string,
  dealerId: string | number,
  payload: VkpiDealerWritePayload,
): Promise<VkpiDealer> {
  const response = await apiFetch<VkpiDealer>(
    `/api/admin/vkpi/dealers/${encodeURIComponent(String(dealerId))}`,
    { method: "PATCH", body: jsonBody(payload) },
    token,
  );
  return normalizeDealer(response);
}

export async function publishDealer(token: string, dealerId: string | number): Promise<VkpiDealer> {
  const response = await apiFetch<VkpiDealer>(
    `/api/admin/vkpi/dealers/${encodeURIComponent(String(dealerId))}/publish`,
    { method: "POST" },
    token,
  );
  return normalizeDealer(response);
}

export async function unpublishDealer(token: string, dealerId: string | number): Promise<VkpiDealer> {
  const response = await apiFetch<VkpiDealer>(
    `/api/admin/vkpi/dealers/${encodeURIComponent(String(dealerId))}/unpublish`,
    { method: "POST" },
    token,
  );
  return normalizeDealer(response);
}
