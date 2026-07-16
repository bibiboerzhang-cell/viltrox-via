import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Dealers 改版冒烟(金样板 ShopifyBoardPage.smoke 同构):
// - 页壳:pagehead(经销商 + 家数徽 + 刷新 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡:有真数才真值(经销商数 / 已定位 / 覆盖州 / 国家数);
//   vkpi_dealers 0 行 → 全带 pending 诚实空态注明公开候选待核验(本刀核心验收);
// - 地区分布:有数据才画(NY/CA 真行);0 行 → 诚实空,绝不编条形;
// - 地图 embed:RealMap 零改动收编(jsdom 无 Leaflet 运行时 → 桩,同旧页冒烟);
//   0 定位点 → 角标诚实注明;
// - 旧页零丢失:预检(record_only=true)/ 有界抓取(≤20,record_only=false + 重拉)/
//   回执行真字段 / 手动添加(必填闸 + 幂等 POST + 成功清空 + 重拉)/ 待补定位清单;
// - 全量 + 连续翻:名录单条详情 ‹#n/N› + 方向键 + 绝对入库时间;
// - 布局键 v2 + 只读迁移 v1 + 不传 apiToken 给板 → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口)+ RealMap 桩,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// RealMap 用 Leaflet,jsdom 里无地图运行时 → 桩成占位 div(旧页冒烟同款;
// 零改动收编红线:真组件文件本刀一字未动)。
vi.mock("../components/RealMap", () => ({
  RealMap: (props: Record<string, unknown>) => React.createElement(
    "button",
    {
      type: "button",
      "data-testid": "real-map-stub",
      "data-pins": Array.isArray(props.pins) ? (props.pins as unknown[]).length : 0,
      onClick: () => {
        const first = Array.isArray(props.pins) ? (props.pins as unknown[])[0] : null;
        if (first && typeof props.onPinClick === "function") (props.onPinClick as (pin: unknown) => void)(first);
      },
    },
    "mock map",
  ),
}));

import { DealersBoardPage } from "./DealersBoardPage";
import { dealerLocationLabel, scrapeReceiptText } from "./DealersBoardPage.modules";
import { ThemeProvider } from "../../../../app/providers/ThemeProvider";

/* ============ 真形状 mock(对照 vkpi_dealers 列 id/name/address/city/state/lat/lng/source/created_at;
   本地库实况 0 行 → 空态用真空形,有数据形按后端字段演) ============ */

const DEALERS_OK = {
  dealers: [
    {
      id: 1, name: "B&H Photo", address: "420 9th Ave", city: "New York", state: "NY", lat: 40.7539, lng: -73.9962, source: "manual",
      brand_codes: ["VILTROX", "NIKON", "CANON"], publication_status: "published", published_at: "2026-07-10T10:00:00Z",
      viltrox_deployment: { status: "deployed", deployed_at: "2026-07-10T10:00:00Z", note: "local map pilot" },
      activity: { status: "active", page_url: "https://www.bhphotovideo.com/find/EventSpace.jsp", next_event_at: "2026-08-01T15:00:00Z" },
      phone: "212-555-0100", website_url: "https://www.bhphotovideo.com", social_links: [{ platform: "Instagram", url: "https://instagram.com/bhphoto" }], created_at: "2026-07-01T10:00:00Z",
    },
    {
      id: 2, name: "Adorama", address: "42 W 18th St", city: "New York", state: "NY", lat: 40.7405, lng: -73.9936,
      source: "scrape", source_status: "public_listing_verified", authorization_status: "needs_viltrox_confirmation",
      brand_listing_url: "https://www.adorama.com/brands/Viltrox", location_source_url: "https://www.adorama.com/g/nyc-store",
      source_checked_at: "2026-07-13T12:00:00Z", verification_note: "Retailer product and location pages reviewed",
      phone: "212-741-0063", created_at: "2026-07-02T10:00:00Z",
      brand_codes: ["SONY"], publication_status: "published", viltrox_deployment: { status: "not_deployed" }, activity: { status: "none_observed" },
    },
    { id: 3, name: "Samy's Camera", address: "431 S Fairfax Ave", city: "Los Angeles", state: "CA", lat: null, lng: null, source: "manual", brand_codes: ["FUJIFILM"], publication_status: "draft", viltrox_deployment: { status: "planned" }, activity: { status: "unknown" }, created_at: "2026-07-03T10:00:00Z" },
  ],
};

// locations 端点只吐 lat/lng 齐全行(后端 list_dealer_pins 口径);color 为服务端下发值
const LOCS_OK = {
  pins: [
    {
      id: 1, name: "B&H Photo", address: "420 9th Ave", city: "New York", state: "NY", lat: 40.7539, lng: -73.9962, color: "#10b981",
      source_status: "public_listing_verified", authorization_status: "needs_viltrox_confirmation",
      brand_listing_url: "https://dealer.example/viltrox",
      channel_evidence: { physical_location_registered: true, online_product_page: "declared_public_url" },
      truth_status: { public_listing: "verified", product_evidence: "declared_public_url", viltrox_authorization: "pending" },
      location_verification: {
        schema_visible: true,
        contract_version: 1,
        canonical_location_status: "official_site_verified",
        physical_store_status: "verified_physical_store",
        coordinate: { provider: "us_census_geocoder", match_level: "exact_address", value_status: "observed", google_derived: false, provenance_valid: true },
        google_place_cross_check: { status: "pending", place_id: null, maps_url: null, canonical_source: false },
        map_eligible: true,
        claim_status: "descriptive_only",
      },
      brand_codes: ["VILTROX", "NIKON", "CANON"], publication_status: "published",
      viltrox_deployment: { status: "deployed", note: "local map pilot" },
      activity: { status: "active", page_url: "https://www.bhphotovideo.com/find/EventSpace.jsp", next_event_at: "2026-08-01T15:00:00Z" },
      phone: "212-555-0100", website_url: "https://www.bhphotovideo.com", social_links: [{ platform: "Instagram", url: "https://instagram.com/bhphoto" }],
    },
    {
      id: 2, name: "Adorama", address: "42 W 18th St", city: "New York", state: "NY", lat: 40.7405, lng: -73.9936, color: "#10b981",
      source_status: "public_listing_verified", authorization_status: "authorized_confirmed",
      channel_evidence: { physical_location_registered: true, online_product_page: "unavailable" },
      truth_status: { public_listing: "verified", product_evidence: "unavailable", viltrox_authorization: "confirmed" },
      authorization_evidence: {
        status: "authorized_confirmed",
        official_viltrox_source_url: "https://www.viltrox.com/dealers/adorama",
        verified_at: "2026-07-13T12:00:00Z",
      },
      brand_codes: ["SONY"], publication_status: "published", viltrox_deployment: { status: "not_deployed" }, activity: { status: "none_observed" },
    },
  ],
};

const COVERAGE_OK = {
  status: "ready",
  total: 3,
  public_listing_verified: 2,
  authorized_confirmed: 0,
  authorization_pending: 3,
  located: 2,
  published_map_pins: 2,
  states: 2,
  countries: 1,
  product_page_declared: 2,
  contacts: { phone: 2, email: 1, hours: 2, services: 2 },
  freshness: { fresh: 2, stale: 0, unavailable: 1 },
  identity: { reviewed_alias_dealers: 0, exact_location_dealers: 0 },
  passports: { dealer_locations: 0, verified_fresh: 0 },
  us_jurisdiction_matrix: {
    scope: "registered_rows_with_us_state_or_dc_only",
    covered_states: ["CA", "NY"],
    missing_states: [],
    covered_count: 2,
    jurisdiction_count: 51,
    authoritative_market_denominator: null,
    coverage_rate: null,
    claim_status: "descriptive_only",
    dealer_counts_by_state_dc: { CA: 1, NY: 2 },
    public_listing_verified_counts_by_state_dc: { NY: 2 },
    coordinate_present_counts_by_state_dc: { CA: 1, NY: 2 },
    map_eligible_counts_by_state_dc: { NY: 2 },
    located_counts_by_state_dc: { NY: 2 },
    dealer_entity_count: 3,
    map_precision: "registered_state_dc_aggregate_not_store_coordinates",
  },
  coverage_claim: "registered_public_listings_only",
  global_complete: false,
  claim_status: "descriptive_only",
};

const CANDIDATE_STAGING_OK = {
  status: "ready",
  candidate_type: "dealer_location",
  total: 12,
  review_status: { pending: 9, approved: 3 },
  promotion_gate_status: { blocked: 12 },
  linked_field_evidence: 18,
  claim_status: "descriptive_only",
  automatic_promotion: false,
  business_rows_written: 0,
};

const US_SOURCE_REGISTRY_OK = {
  ok: true,
  country_code: "US",
  coverage_claim: "registered_publisher_owned_public_entries_only",
  full_us_coverage: false,
  claim_status: "descriptive_only",
  dealer_discovery_sources: [
    { id: "nikon", name: "Nikon dealer directory", source_kind: "manufacturer_dealer_directory", publisher: "Nikon USA", canonical_url: "https://example.com/nikon", state_codes: ["CA", "NY"], candidate_only: true, manufacturer_authorization_scope: "Nikon Imaging", source_snapshot: { listed_entry_count: 175, unique_organization_count: 175, record_granularity: "authorized_dealer_organization_not_branch_location", map_location_import_ready: false } },
    { id: "canon", name: "Canon where to buy", source_kind: "manufacturer_dealer_directory", publisher: "Canon USA", canonical_url: "https://example.com/canon", state_codes: ["CA"], candidate_only: true },
  ],
  source_jurisdiction_matrix: {
    dealer_discovery_sources: {
      scope: "registered_source_discovery_jurisdictions_only",
      covered_states_dc: ["CA", "NY"],
      missing_states_dc: [],
      covered_count: 51,
      jurisdiction_count: 51,
      source_discovery_rate: 1,
      extracted_candidate_count: null,
      verified_business_row_count: null,
      entity_coverage_rate: null,
      claim_status: "descriptive_only",
    },
  },
  counts: {
    dealer_discovery_sources: 2,
    dealer_source_kinds: { manufacturer_dealer_directory: 2, retailer_location_directory: 0 },
    dealer_manufacturer_scopes: 2,
    enabled: 0,
    direct_import_allowed: 0,
  },
  adapter_readiness: {
    registered_source_count: 2,
    adapter_source_count: 2,
    mapped_adapter_source_count: 2,
    sources_without_mapped_adapter: [],
    all_registered_sources_have_mapped_adapter: true,
    readiness_level: "format_mapping_only_not_source_fixture_verified",
    source_fixture_verified_count: 0,
    sources_without_source_fixture_verification: ["nikon", "canon"],
    all_registered_sources_have_source_fixture_verification: false,
    sources_without_verified_adapter: ["nikon", "canon"],
    all_registered_sources_have_verified_adapter: false,
    blocker: "source_specific_fixture_and_terms_robots_review_required",
    source_coverage_is_not_entity_coverage: true,
  },
  adapter_source_readiness: [
    { source_registry_id: "nikon", format_mapped: true, source_fixture_verified: false, terms_robots_status: "pending_review", terms_robots_reviewed: false, source_enabled: false, snapshot_import_readiness: "blocked", candidate_envelope_readiness: "blocked", direct_business_import: false, blockers: ["source_specific_fixture_not_verified", "source_registry_disabled", "terms_robots_review_pending"], claim_status: "descriptive_only" },
    { source_registry_id: "canon", format_mapped: true, source_fixture_verified: false, terms_robots_status: "pending_review", terms_robots_reviewed: false, source_enabled: false, snapshot_import_readiness: "blocked", candidate_envelope_readiness: "blocked", direct_business_import: false, blockers: ["source_specific_fixture_not_verified", "source_registry_disabled", "terms_robots_review_pending"], claim_status: "descriptive_only" },
  ],
  reviewed_persistence_readiness: {
    supported: false,
    status: "migration_required",
    reason: "reviewed_identity_and_evidence_columns_unavailable",
    missing_durable_fields: ["source_id"],
    automatic_promotion: false,
    claim_status: "descriptive_only",
    read_only: true,
    database_accessed: true,
    business_rows_written: 0,
  },
};

const SCRAPE_PREVIEW = {
  ok: true,
  source: "usa_camera_retailers",
  requested: 20,
  inserted: 0,
  skipped: 0,
  geocoded: 0,
  pending_geocode: 0,
  record_only: true,
  import_allowed: false,
  import_block_reason: "reviewed_identity_and_evidence_columns_unavailable",
  quality_status: "partial_descriptive",
  persistence_contract: {
    supported: false,
    reason: "reviewed_identity_and_evidence_columns_unavailable",
  },
  plan: [],
  errors: [],
};
const SCRAPE_RUN = { ok: true, source: "usa_camera_retailers", requested: 20, inserted: 5, skipped: 15, geocoded: 4, pending_geocode: 1, record_only: false, errors: [{ name: "X", error: "geocode miss" }] };

const DEALER_ACTIVITIES_OK = {
  status: "ready",
  dealer_id: 1,
  dealer_name: "B&H Photo",
  count: 1,
  returned: 1,
  next_activity_at: "2026-08-01",
  association_policy: "exact_dealer_id_only",
  automatic_sync: true,
  source: "vkpi_event_opportunity_dealers",
  business_rows_written: 0,
  claim_status: "descriptive_only",
  activities: [{
    id: "event-1",
    title: "B&H Creator Workshop",
    lane: "dealer_event",
    start_date: "2026-08-01",
    local_time_text: "14:00 ET",
    city: "New York",
    region: "NY",
    decision_status: "new",
    verification_status: "verified",
    converted_event_id: null,
    is_internal_event: false,
    official_url: "https://events.bhphotovideo.com/workshop",
    association: "exact_dealer_id",
  }],
};

// board-series?board=dealers 真形状(对照 backend board_series._dealers_board 出参;
// 缺省用例走 Error:端点失败 → 卡面照旧 spempty 诚实虚线的回归锁;本地 0 行时
// 端点真实返回 status=empty + 空序列 —— 绝不 0 填平线)。
const BOARD_SERIES_OK = {
  status: "ready",
  board: "dealers",
  days: 30,
  window: { since: "2026-06-13", until: "2026-07-12", prev_since: "2026-05-14", prev_until: "2026-06-12" },
  series: {
    dealers_new: [
      { date: "2026-07-10", count: 1 },
      { date: "2026-07-11", count: 0 },
      { date: "2026-07-12", count: 2 },
    ],
  },
  metrics: {
    dealers_new: { status: "ready", current: 3, previous: 0, delta_pct: null, table: "vkpi_dealers", unit: "rows" },
  },
  basis: {},
  method: "board_series_v1",
  generated_at: "2026-07-12T02:00:00+00:00",
};

const BOARD_SERIES_EMPTY = {
  status: "empty",
  board: "dealers",
  days: 30,
  reason: "vkpi_dealers 全表 0 行(数据在线上库)——诚实空,不摆 0 填平线冒充有数据流。",
  series: { dealers_new: [] },
  metrics: { dealers_new: { status: "empty", table: "vkpi_dealers" } },
  basis: {},
  method: "board_series_v1",
  generated_at: "2026-07-12T02:00:00+00:00",
};

type Overrides = {
  dealers?: unknown;
  locs?: unknown;
  coverage?: unknown;
  candidateStaging?: unknown;
  usSourceRegistry?: unknown;
  boardSeries?: unknown;
  scrapePreview?: unknown;
  scrapeRun?: unknown;
  dealerActivities?: unknown;
};

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    const pick = (value: unknown, fallback: unknown) => {
      const v = value ?? fallback;
      if (v instanceof Error) throw v;
      return v;
    };
    if (p.startsWith("/api/admin/vkpi/board-series")) return pick(overrides.boardSeries, new Error("board-series 未接通"));
    if (p.startsWith("/api/admin/vkpi/dealers/candidate-staging")) return pick(overrides.candidateStaging, CANDIDATE_STAGING_OK);
    if (p.startsWith("/api/admin/vkpi/dealers/us-source-registry")) return pick(overrides.usSourceRegistry, US_SOURCE_REGISTRY_OK);
    if (p.startsWith("/api/admin/vkpi/dealers/coverage")) return pick(overrides.coverage, COVERAGE_OK);
    if (p.startsWith("/api/admin/vkpi/dealers/locations")) return pick(overrides.locs, LOCS_OK);
    if (/\/api\/admin\/vkpi\/dealers\/\d+\/activities\?/.test(p)) return pick(overrides.dealerActivities, DEALER_ACTIVITIES_OK);
    if (p.startsWith("/api/admin/vkpi/dealers/scrape-enqueue") && method === "POST") {
      const body = JSON.parse(String(init?.body || "{}"));
      return body.record_only
        ? pick(overrides.scrapePreview, SCRAPE_PREVIEW)
        : pick(overrides.scrapeRun, SCRAPE_RUN);
    }
    if (/\/api\/admin\/vkpi\/dealers\/\d+\/publish$/.test(p) && method === "POST") return { ...DEALERS_OK.dealers[2], id: Number(p.split("/").at(-2)), publication_status: "published" };
    if (/\/api\/admin\/vkpi\/dealers\/\d+\/unpublish$/.test(p) && method === "POST") return { ...DEALERS_OK.dealers[0], id: Number(p.split("/").at(-2)), publication_status: "draft" };
    if (/\/api\/admin\/vkpi\/dealers\/\d+$/.test(p) && method === "PATCH") return { ...DEALERS_OK.dealers[0], ...JSON.parse(String(init?.body || "{}")) };
    if (p === "/api/admin/vkpi/dealers" && method === "POST") return { id: 9, name: "KEH Camera", address: "4900 Highlands Pkwy", publication_status: "draft", geocoded: false, pending_geocode: true };
    if (p.startsWith("/api/admin/vkpi/dealers")) return pick(overrides.dealers, DEALERS_OK);
    throw new Error(`unexpected apiFetch: ${method} ${p}`);
  });
}

// 地图收编件运行时读 --ds-accent(useTheme 依赖)→ 冒烟同真栈包 ThemeProvider
const renderBoard = () =>
  render(
    <ThemeProvider>
      <DealersBoardPage apiToken="t" />
    </ThemeProvider>,
  );

const renderEmbedded = (embeddedModuleKey: string) =>
  render(
    <ThemeProvider>
      <DealersBoardPage apiToken="t" embeddedModuleKey={embeddedModuleKey} />
    </ThemeProvider>,
  );

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

/* ============ 回执文案单测(字段全来自端点真实返回) ============ */
describe("scrapeReceiptText(回执行真字段)", () => {
  it("预检 / 抓取动词按 record_only 分流;errors 有才带失败段", () => {
    expect(scrapeReceiptText(SCRAPE_PREVIEW as never)).toBe("预检:请求 20 · 新增 0 · 更新 0 · 跳过 0 · 已定位 0 · 待补 0");
    expect(scrapeReceiptText(SCRAPE_RUN as never)).toBe("抓取:请求 20 · 新增 5 · 更新 0 · 跳过 15 · 已定位 4 · 待补 1 · 失败 1");
  });

  it("城市名已是 Dealer 名称时不重复拼接", () => {
    expect(dealerLocationLabel({ name: "Melrose", city: "Melrose", state: "MA" })).toBe("MA");
    expect(dealerLocationLabel({ name: "Camera Shop", city: "Melrose", state: "MA" })).toBe("Melrose, MA");
  });
});

/* ============ 页壳 + KPI 带 + 注册表 ============ */
describe("DealersBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡真值:经销商数 3 / 已定位 2 / 覆盖州 2 / 国家数 1;地区条形 NY/CA;端点全被调", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect((await screen.findAllByText("经销商数")).length).toBeGreaterThan(0);
    ["已发布地图点", "覆盖州", "国家数"].forEach((label) => {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    });
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    const bandText = Array.from(kpis).map((el) => el.textContent || "").join("|");
    expect(bandText).toContain("3");
    expect(bandText).toContain("2");
    expect(bandText).toContain("1");
    ["门店地址来源已核验", "坐标已完整", "地图已发布", "公开电话已登记"].forEach((label) => {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    });
    // 地区分布条形(有数据才画):NY 2 / CA 1
    expect(screen.getAllByText("NY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("CA").length).toBeGreaterThan(0);
    // 地图 embed 收编在场(桩),吃到 2 个定位 pin
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");
    // 待补定位清单:缺经纬度的 Samy's 在列
    expect(screen.getAllByText("Samy's Camera").length).toBeGreaterThan(0);
    // 真端点全被调
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/dealers?") || p === "/api/admin/vkpi/dealers")).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/dealers/locations"))).toBe(true);
    expect(screen.getByTestId("dealer-us-jurisdiction-matrix").textContent).toContain("2 / 51");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("美国记录部分覆盖");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("来源发现州 / DC");
    expect(screen.getByTestId("dealer-us-jurisdiction-matrix").textContent).toContain("实体地理");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("已入库业务行3");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain(
      "是否上图另看公开来源核验 + 坐标",
    );
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("公开来源已核验2");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("待核验候选12");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("不上图");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("登记发现来源2");
    expect(screen.getByTestId("dealer-business-jurisdiction-matrix").textContent).toContain("已入库门店州 / DC 2/51");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("制造商目录 2");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("零售商门店目录 0");
    expect(screen.getByTestId("dealer-trust-pipeline").textContent).toContain("来源启用 0/2 · 直接导入 0");
    expect(screen.getByTestId("dealer-adapter-readiness").textContent).toContain("离线格式已映射 2/2");
    expect(screen.getByTestId("dealer-source-fixture-readiness").textContent).toContain("来源快照已核验 0/2");
    expect(screen.getByTestId("dealer-source-terms-readiness").textContent).toContain("条款 / robots 已复核 0/2");
    expect(screen.getByTestId("dealer-reviewed-persistence-readiness").textContent).toContain("人工核验落库 待迁移");
    expect(screen.getByTestId("dealer-source-readiness-nikon").textContent).toContain("格式已映射");
    expect(screen.getByTestId("dealer-source-readiness-nikon").textContent).toContain("导入阻断");
    expect(screen.getByTestId("dealer-source-readiness-nikon").textContent).toContain("175 个官方目录组织/名称");
    expect(screen.getByTestId("dealer-source-readiness-nikon").textContent).toContain("不可直接当分店坐标");
    expect(screen.getAllByTestId(/^dealer-map-state-/)).toHaveLength(51);
    expect(screen.getByTestId("dealer-map-state-NY").getAttribute("aria-label")).toContain("已入库门店实体 2");
    expect(screen.getByTestId("dealer-map-state-NY").getAttribute("aria-label")).toContain("公开来源核验 2");
    expect(screen.getByTestId("dealer-map-state-NY").getAttribute("aria-label")).toContain("可上图实体 2");
    expect(screen.getByTestId("dealer-map-state-CA").getAttribute("aria-label")).toContain("已入库门店实体 1");
    expect(calledPaths()).toContain("/api/admin/vkpi/dealers/candidate-staging");
    expect(calledPaths()).toContain("/api/admin/vkpi/dealers/us-source-registry");
  });

  it("地图只绘制已显式发布且坐标齐全的业务行，来源候选队列不上图", async () => {
    renderBoard();
    expect(await screen.findByTestId("dealer-trust-pipeline")).toBeTruthy();
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");
    expect(screen.getByText("来源候选待审 12 · 不上图")).toBeTruthy();
    expect(screen.getByText("地图层：已显式发布 + 坐标齐全")).toBeTruthy();
    expect(screen.getByRole("option", { name: "全部美国已发布定位记录" })).toBeTruthy();

    // 州级色块只改变真实 pin 筛选；source-only / 0 pin 州不会造点。
    fireEvent.click(screen.getByTestId("dealer-map-state-CA"));
    expect(screen.getByTestId("dealer-map-state-CA").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("0");
    fireEvent.click(screen.getByTestId("dealer-map-state-CA"));
    expect(screen.getByTestId("dealer-map-state-CA").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");

    fireEvent.change(screen.getByLabelText("州 / DC"), { target: { value: "NY" } });
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");
    fireEvent.change(screen.getByLabelText("品牌"), { target: { value: "SONY" } });
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("1");
    fireEvent.change(screen.getByLabelText("品牌"), { target: { value: "" } });
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");
    fireEvent.change(screen.getByLabelText("证据状态"), { target: { value: "candidate" } });
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("0");
    expect(screen.queryByLabelText("Viltrox 产品证据")).toBeNull();
    expect(screen.queryByLabelText("Viltrox 授权")).toBeNull();
  });

  it("地图 pin 可点击打开地址联系详情，并按多厂商筛选", async () => {
    renderBoard();
    const map = await screen.findByTestId("real-map-stub");
    expect(map.getAttribute("data-pins")).toBe("2");

    fireEvent.change(screen.getByLabelText("品牌"), { target: { value: "SONY" } });
    expect(map.getAttribute("data-pins")).toBe("1");
    fireEvent.change(screen.getByLabelText("品牌"), { target: { value: "" } });
    expect(map.getAttribute("data-pins")).toBe("2");

    fireEvent.click(map);
    const detail = await screen.findByRole("dialog", { name: /Dealer 地图详情 B&H Photo/ });
    expect(within(detail).getByText("NIKON")).toBeTruthy();
    expect(within(detail).getByText("CANON")).toBeTruthy();
    expect(within(detail).getByText("有公开活动")).toBeTruthy();
    expect(within(detail).getByText("Google Maps 待复核")).toBeTruthy();
    expect(within(detail).getByText("US Census 地址级地理编码（非 Google）")).toBeTruthy();
    expect(within(detail).getByRole("link", { name: "在 Google Maps 复核（待核验）" }).getAttribute("href"))
      .toBe("https://www.google.com/maps/search/?api=1&query=B%26H%20Photo%2C%20420%209th%20Ave%2C%20New%20York%2C%20NY");
    expect(within(detail).getByRole("link", { name: "活动页" }).getAttribute("href")).toContain("EventSpace");
    expect(await within(detail).findByText("B&H Creator Workshop")).toBeTruthy();
    expect(within(detail).getByText("外部机会候选 · 未转 Event")).toBeTruthy();
    expect(within(detail).getByText("人工判断 待判断")).toBeTruthy();
    expect(within(detail).getByText("证据 已核验")).toBeTruthy();
    expect(within(detail).getByRole("link", { name: "登记来源" }).getAttribute("href")).toContain("events.bhphotovideo.com");
  });

  it("只有 promotion receipt 指向内部 Event 才标记正式 Event", async () => {
    routeApi({
      dealerActivities: {
        ...DEALER_ACTIVITIES_OK,
        activities: [{
          ...DEALER_ACTIVITIES_OK.activities[0],
          decision_status: "promoted",
          converted_event_id: "evt_radar_123",
          is_internal_event: true,
        }],
      },
    });
    renderBoard();
    const map = await screen.findByTestId("real-map-stub");
    fireEvent.click(map);
    const detail = await screen.findByRole("dialog", { name: /Dealer 地图详情 B&H Photo/ });
    expect(await within(detail).findByText("正式 Event · evt_radar_123")).toBeTruthy();
    expect(within(detail).queryByText("外部机会候选 · 未转 Event")).toBeNull();
  });

  it("人工记录可在显式发布后上私有地图，但 pin 详情仍标记来源待核验", async () => {
    routeApi({
      locs: {
        pins: [{
          id: 88,
          name: "Manual Camera Store",
          address: "1 Main St",
          city: "Austin",
          state: "TX",
          lat: 30.27,
          lng: -97.74,
          source_status: "unverified",
          publication_status: "published",
          brand_codes: ["PENTAX"],
          truth_status: { candidate: true, public_listing: "unverified", product_evidence: "unavailable", viltrox_authorization: "pending" },
        }],
      },
    });
    renderBoard();
    const map = await screen.findByTestId("real-map-stub");
    expect(map.getAttribute("data-pins")).toBe("1");
    fireEvent.change(screen.getByLabelText("证据状态"), { target: { value: "candidate" } });
    expect(map.getAttribute("data-pins")).toBe("1");
    fireEvent.click(map);
    const detail = await screen.findByRole("dialog", { name: /Manual Camera Store/ });
    expect(within(detail).getByText("人工 / 公开来源待核验")).toBeTruthy();
    expect(within(detail).getByText(/地图状态 已发布/)).toBeTruthy();
    expect(within(detail).getByText(/Event Radar 关联活动/)).toBeTruthy();
  });

  it("精确活动关联的来源未启用时只显示受限计数，不泄漏活动详情", async () => {
    routeApi({
      dealerActivities: {
        status: "pending_source_activation",
        dealer_id: 1,
        activities: [],
        count: 0,
        linked_count: 3,
        suppressed_count: 3,
        suppression_reason: "source_not_active_or_enabled",
        association_policy: "exact_dealer_id_only",
      },
    });
    renderBoard();
    const map = await screen.findByTestId("real-map-stub");
    fireEvent.click(map);
    const detail = await screen.findByRole("dialog", { name: /B&H Photo/ });
    expect(await within(detail).findByText(/已精确关联 3 条/)).toBeTruthy();
    expect(within(detail).getByText(/安全闸下暂不展示活动详情/)).toBeTruthy();
    expect(within(detail).queryByText("B&H Creator Workshop")).toBeNull();
  });

  it("Dealer 活动扩展严格区分 active / none_observed / unknown", async () => {
    renderBoard();
    const activity = await screen.findByTestId("dealer-activity-extension");
    expect(within(activity).getByText("当前页·有公开活动")).toBeTruthy();
    expect(within(activity).getByText("当前页·本次未观测")).toBeTruthy();
    expect(within(activity).getByText("当前页·未检索 / 未知")).toBeTruthy();
    expect(within(activity).getByText("B&H Photo")).toBeTruthy();
  });

  it("KPI 分母来自 coverage 全表，不把名录最多 500 行或定位点推断当成全量", async () => {
    routeApi({
      dealers: DEALERS_OK,
      locs: LOCS_OK,
      coverage: { ...COVERAGE_OK, total: 842, located: 730, published_map_pins: 730, states: 49, countries: 2 },
    });
    renderBoard();
    expect((await screen.findAllByText("经销商数")).length).toBeGreaterThan(0);
    const bandText = Array.from(document.querySelectorAll(".ds-kpi"))
      .map((el) => el.textContent || "")
      .join("|");
    expect(bandText).toContain("842");
    expect(bandText).toContain("730");
    expect(bandText).toContain("49");
    expect(bandText).toContain("2");
  });

  it("board-series 就绪 → 经销商数卡点亮真 sparkline(关联指标零环比药丸)", async () => {
    routeApi({ boardSeries: BOARD_SERIES_OK });
    renderBoard();
    expect((await screen.findAllByText("经销商数")).length).toBeGreaterThan(0);
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(1));
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(3);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    const bs = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/board-series"));
    expect(bs.length).toBeGreaterThan(0);
    expect(bs[0]).toContain("board=dealers");
  });

  it("board-series 端点 empty(全表 0 行)→ 空序列 → 绝不画 0 填平线,四卡虚线如实", async () => {
    routeApi({ boardSeries: BOARD_SERIES_EMPTY });
    renderBoard();
    expect((await screen.findAllByText("经销商数")).length).toBeGreaterThan(0);
    // 端点已真实返回(empty + 空序列)→ 依旧零 sparkline(诚实虚线,不编平线)
    await waitFor(() => {
      const bs = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/board-series"));
      expect(bs.length).toBeGreaterThan(0);
    });
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
  });

  it("vkpi_dealers 0 行 → KPI 带全 pending 诚实空态注明公开候选待核验;地区不画;地图角标如实", async () => {
    routeApi({
      dealers: { dealers: [] },
      locs: { pins: [] },
      coverage: {
        ...COVERAGE_OK,
        status: "empty",
        total: 0,
        public_listing_verified: 0,
        authorized_confirmed: 0,
        authorization_pending: 0,
        located: 0,
        published_map_pins: 0,
        states: 0,
        countries: 0,
        product_page_declared: 0,
        contacts: { phone: 0, email: 0, hours: 0, services: 0 },
        freshness: { fresh: 0, stale: 0, unavailable: 0 },
      },
    });
    renderBoard();
    expect((await screen.findAllByText(/公开零售商候选待核验导入/)).length).toBeGreaterThan(0);
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    // 四卡全 pending(值位 — 空态),绝不编数
    Array.from(kpis).forEach((el) => {
      expect(el.textContent).toContain("—");
      expect(el.textContent).toContain("本地库 0 行");
    });
    // 地区分布:有数据才画 → 诚实空,零条形
    expect(screen.getAllByText(/有数据才画分布/).length).toBeGreaterThan(0);
    // 地图 embed 仍在(0 pin)+ 角标诚实注明
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("0");
    expect(screen.getAllByText(/0 个定位点/).length).toBeGreaterThan(0);
  });

  it("默认布局六模块在场，名录内嵌活动扩展；不传 apiToken 给板 → 绝不写账户级布局", async () => {
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    ["地区分布", "当前页待补定位", "经销商地图", "经销商名录", "录入与采集", "Dealer 活动扩展 · 当前页"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });

  it("布局键 v1 只读迁移到 v2，保留旧模块并补上真实性模块", async () => {
    window.localStorage.setItem("vkpi-dealers-layout-v1", JSON.stringify([{ moduleKey: "kpiD", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    expect(screen.getByText("美国经销商来源与实体覆盖")).toBeTruthy();
    expect(screen.queryByText("经销商名录")).toBeNull();
    expect(screen.queryByText("录入与采集")).toBeNull();
    expect(window.localStorage.getItem("vkpi-dealers-layout-v1")).not.toBeNull();
    await waitFor(() => expect(window.localStorage.getItem("vkpi-dealers-layout-v2-truth")).not.toBeNull());
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });
});

describe("DealersBoardPage Dashboard 单模块取数收口", () => {
  it("地区分布只读名录，不放大成六路请求", async () => {
    renderEmbedded("regionD");
    expect(await screen.findByText("NY")).toBeTruthy();
    expect(new Set(calledPaths())).toEqual(new Set([
      "/api/admin/vkpi/dealers?limit=100&offset=0",
      "/api/admin/vkpi/dealers/coverage?stale_after_days=30",
    ]));
  });

  it("地图只读名录 + 定位 + 候选汇总，不拉 coverage / source registry / series", async () => {
    renderEmbedded("mapD");
    await waitFor(() => expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2"));
    expect(calledPaths()).toHaveLength(5);
    expect(new Set(calledPaths())).toEqual(new Set([
      "/api/admin/vkpi/dealers?limit=100&offset=0",
      "/api/admin/vkpi/dealers/locations?published_only=true",
      "/api/admin/vkpi/dealers/coverage?stale_after_days=30",
      "/api/admin/vkpi/dealers/candidate-staging",
      "/api/admin/vkpi/dealers/us-source-registry",
    ]));
  });

  it("录入与采集模块挂载时零 GET；未知模块也零 GET", async () => {
    const first = renderEmbedded("opsD");
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    expect(calledPaths()).toHaveLength(0);
    first.unmount();

    renderEmbedded("removedDealerModule");
    expect(await screen.findByText(/Dealers 注册表移除/)).toBeTruthy();
    expect(calledPaths()).toHaveLength(0);
  });
});

/* ============ 旧页零丢失(预检 / 有界抓取 / 手动添加 / 待补清单) ============ */
describe("DealersBoardPage 旧功能零丢失", () => {
  it("预检:POST scrape-enqueue record_only=true,回执行真字段,不重拉", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(screen.getByRole("button", { name: "预检" }));
    expect(await screen.findByText("预检:请求 20 · 新增 0 · 更新 0 · 跳过 0 · 已定位 0 · 待补 0")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "导入门禁已阻断 · import_allowed=false · quality_status=partial_descriptive · reason=reviewed_identity_and_evidence_columns_unavailable",
    );
    expect((screen.getByRole("button", { name: "导入已核验候选(≤20)" }) as HTMLButtonElement).disabled).toBe(true);
    const scrapeCall = apiFetchMock.mock.calls.find((call) => {
      if (!String(call[0]).includes("scrape-enqueue")) return false;
      return JSON.parse(String((call[1] as RequestInit).body || "{}")).record_only === true;
    });
    expect(scrapeCall).toBeTruthy();
    expect(JSON.parse(String((scrapeCall![1] as RequestInit).body))).toEqual({ limit: 20, record_only: true });
    // 预检不重拉(旧页同款:record-only 零副作用)
    const dealersCallsAfter = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    expect(dealersCallsAfter).toBe(dealersCallsBefore);
  });

  it("有界抓取:record_only=false + 回执带失败段 + 成功后重拉", async () => {
    routeApi({
      scrapePreview: {
        ...SCRAPE_PREVIEW,
        import_allowed: true,
        import_block_reason: null,
        persistence_contract: { supported: true },
      },
    });
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const runButton = screen.getByRole("button", { name: "导入已核验候选(≤20)" }) as HTMLButtonElement;
    expect(runButton.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "预检" }));
    await waitFor(() => expect(runButton.disabled).toBe(false));
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(runButton);
    expect(await screen.findByText("抓取:请求 20 · 新增 5 · 更新 0 · 跳过 15 · 已定位 4 · 待补 1 · 失败 1")).toBeTruthy();
    const scrapeCall = apiFetchMock.mock.calls.find((call) => {
      if (!String(call[0]).includes("scrape-enqueue")) return false;
      return JSON.parse(String((call[1] as RequestInit).body || "{}")).record_only === false;
    });
    expect(JSON.parse(String((scrapeCall![1] as RequestInit).body))).toEqual({ limit: 20, record_only: false });
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
      expect(after).toBeGreaterThan(dealersCallsBefore);
    });
  });

  it("质量预检阻断时显示真实状态并保持导入禁用", async () => {
    routeApi({
      scrapePreview: {
        ...SCRAPE_PREVIEW,
        import_allowed: false,
        quality_status: "blocked_for_import",
      },
    });
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();

    const runButton = screen.getByRole("button", { name: "导入已核验候选(≤20)" }) as HTMLButtonElement;
    expect(runButton.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "预检" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "导入门禁已阻断 · import_allowed=false · quality_status=blocked_for_import",
    );
    expect(runButton.disabled).toBe(true);
    fireEvent.click(runButton);
    expect(apiFetchMock.mock.calls.filter((call) => {
      if (!String(call[0]).includes("scrape-enqueue")) return false;
      const body = JSON.parse(String((call[1] as RequestInit).body || "{}"));
      return body.record_only === false;
    })).toHaveLength(0);
  });

  it("手动添加:名称+地址必填闸(按钮禁用);填齐 → POST 幂等 payload + 成功清空 + 重拉", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const addButton = screen.getByRole("button", { name: "添加" });
    expect((addButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("名称*"), { target: { value: "KEH Camera" } });
    fireEvent.change(screen.getByPlaceholderText("地址*"), { target: { value: "4900 Highlands Pkwy" } });
    fireEvent.change(screen.getByPlaceholderText("城市"), { target: { value: "Smyrna" } });
    fireEvent.change(screen.getByPlaceholderText("州"), { target: { value: "GA" } });
    expect((addButton as HTMLButtonElement).disabled).toBe(false);
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(addButton);
    expect(await screen.findByText("已添加草稿:KEH Camera")).toBeTruthy();
    const createCall = apiFetchMock.mock.calls.find(
      (call) => String(call[0]) === "/api/admin/vkpi/dealers" && String((call[1] as RequestInit)?.method).toUpperCase() === "POST",
    );
    expect(JSON.parse(String((createCall![1] as RequestInit).body))).toEqual({ name: "KEH Camera", address: "4900 Highlands Pkwy", city: "Smyrna", state: "GA", brands: [] });
    // 成功清空(旧页同款)+ 重拉
    expect((screen.getByPlaceholderText("名称*") as HTMLInputElement).value).toBe("");
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
      expect(after).toBeGreaterThan(dealersCallsBefore);
    });
  });

  it("手动新增可登记其他厂商，并在明确勾选后二步发布到本地地图", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("名称*"), { target: { value: "Precision Camera" } });
    fireEvent.change(screen.getByPlaceholderText("地址*"), { target: { value: "2438 W Anderson Ln" } });
    fireEvent.change(screen.getByPlaceholderText("纬度（可选）"), { target: { value: "30.356" } });
    fireEvent.change(screen.getByPlaceholderText("经度（可选）"), { target: { value: "-97.735" } });
    fireEvent.change(screen.getByPlaceholderText("官网 / 门店 URL"), { target: { value: "https://precision-camera.com" } });
    fireEvent.click(screen.getByLabelText("NIKON"));
    fireEvent.click(screen.getByLabelText("SONY"));
    fireEvent.change(screen.getByPlaceholderText("其他厂商（逗号分隔）"), { target: { value: "Pentax" } });
    fireEvent.click(screen.getByLabelText(/创建后立即发布到本地地图/));
    fireEvent.click(screen.getByRole("button", { name: "添加" }));

    expect(await screen.findByText("已添加并发布到地图:Precision Camera")).toBeTruthy();
    const createCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === "/api/admin/vkpi/dealers" && String((call[1] as RequestInit)?.method).toUpperCase() === "POST");
    expect(JSON.parse(String((createCall![1] as RequestInit).body))).toMatchObject({
      name: "Precision Camera",
      brands: ["NIKON", "SONY", "PENTAX"],
      lat: 30.356,
      lng: -97.735,
      website_url: "https://precision-camera.com",
    });
    expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/dealers/9/publish" && String((call[1] as RequestInit)?.method).toUpperCase() === "POST")).toBe(true);
  });

  it("待补定位:缺经纬度行在列,徽「待定位」", async () => {
    renderBoard();
    // Samy's 同时住待补定位 + 名录两个模块 → findAll
    expect((await screen.findAllByText("Samy's Camera")).length).toBeGreaterThan(1);
    expect(screen.getAllByText("待定位").length).toBeGreaterThan(0);
  });

  it("待补定位:全部已定位时如实空行(不装数据也不装空)", async () => {
    routeApi({ dealers: { dealers: DEALERS_OK.dealers.slice(0, 2) } });
    renderBoard();
    expect(await screen.findByText("全部已定位。")).toBeTruthy();
  });
});

/* ============ 全量 + 连续翻(经销商名录) ============ */
describe("DealersBoardPage 行模块弹窗", () => {
  it("单店可修改地址、联系、品牌与活动字段，并可显式撤下地图", async () => {
    renderBoard();
    const matches = await screen.findAllByText("B&H Photo");
    const rosterRow = matches.map((match) => match.closest('[role="button"]')).find(Boolean);
    expect(rosterRow).toBeTruthy();
    fireEvent.click(rosterRow as Element);
    const editor = await screen.findByLabelText("编辑 Dealer 记录");
    expect(screen.getByRole("link", { name: "Instagram" }).getAttribute("href")).toBe("https://instagram.com/bhphoto");
    fireEvent.change(within(editor).getByPlaceholderText("电话"), { target: { value: "212-555-9999" } });
    fireEvent.change(within(editor).getByLabelText("活动观测状态"), { target: { value: "active" } });
    fireEvent.change(within(editor).getByLabelText("下一场时间"), { target: { value: "2026-08-15T14:30" } });
    fireEvent.change(within(editor).getByLabelText("Instagram URL"), { target: { value: "https://instagram.com/bhphoto" } });
    fireEvent.click(within(editor).getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/dealers/1" && String((call[1] as RequestInit)?.method).toUpperCase() === "PATCH")).toBe(true));
    const patchCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === "/api/admin/vkpi/dealers/1" && String((call[1] as RequestInit)?.method).toUpperCase() === "PATCH");
    expect(JSON.parse(String((patchCall![1] as RequestInit).body))).toMatchObject({
      phone: "212-555-9999",
      website_url: "https://www.bhphotovideo.com",
      social_links: [{ platform: "Instagram", url: "https://instagram.com/bhphoto" }],
      brands: ["VILTROX", "NIKON", "CANON"],
      activity: { status: "active" },
    });
    expect(JSON.parse(String((patchCall![1] as RequestInit).body))).not.toHaveProperty("viltrox_deployment");
    expect(JSON.parse(String((patchCall![1] as RequestInit).body)).activity.next_event_at).toMatch(/Z$/);

    fireEvent.click(within(editor).getByRole("button", { name: "从地图撤下" }));
    await waitFor(() => expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/dealers/1/unpublish" && String((call[1] as RequestInit)?.method).toUpperCase() === "POST")).toBe(true));
  });

  it("旧形状原始证据行在详情中保持产品、门店、核验与 provenance 同一口径", async () => {
    renderBoard();
    expect((await screen.findAllByText("Adorama")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText("Adorama")[0]);

    expect((await screen.findByRole("link", { name: "打开零售商站点" })).getAttribute("href"))
      .toBe("https://www.adorama.com");
    expect(screen.getAllByText("公开门店页 + 完整地址已核验").length).toBeGreaterThan(0);
    expect(screen.getAllByText("零售商产品页已核验，当前时效待服务端确认；不代表实时库存 / 成交").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/候选:否 · 公开门店:已核验 · 产品页已核验/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/public_listing:verified/).length).toBeGreaterThan(0);
    expect(screen.queryByText("未收集结构化 provenance")).toBeNull();
  });

  it("名录:点行 → 详情 ‹#n/N› + 绝对入库时间 + 库记录 id;↓ 连续翻", async () => {
    renderBoard();
    expect((await screen.findAllByText("B&H Photo")).length).toBeGreaterThan(0);
    const rosterRow = screen.getAllByText("B&H Photo").map((match) => match.closest('[role="button"]')).find(Boolean);
    expect(rosterRow).toBeTruthy();
    fireEvent.click(rosterRow as Element);
    expect(await screen.findByText("#1/3")).toBeTruthy();
    expect(screen.getAllByText(/vkpi_dealers #1/).length).toBeGreaterThan(0);
    // 绝对时间戳口径(存 UTC · 按浏览器时区显示)在详情行如实标注
    expect(screen.getAllByText(/UTC 存 · 按浏览器时区显示/).length).toBeGreaterThan(0);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2/3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    // 第 3 条是待定位行:定位字段如实「待补经纬度」
    expect(await screen.findByText("#3/3")).toBeTruthy();
    expect(screen.getAllByText(/待补经纬度/).length).toBeGreaterThan(0);
  });

  it("名录端点失败 → KPI pending 带原因 + 模块 ErrorCard(绝不编数)", async () => {
    routeApi({ dealers: new Error("boom") });
    renderBoard();
    expect((await screen.findAllByText(/读取失败[::]boom/)).length).toBeGreaterThan(0);
  });
});
