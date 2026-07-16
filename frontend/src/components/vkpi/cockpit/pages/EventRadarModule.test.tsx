import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const listEventRadar = vi.fn();
const getEventRadarSummary = vi.fn();
const getEventCandidateStagingSummary = vi.fn();
const getEventUsSourceRegistry = vi.fn();
const previewEventRadarRefresh = vi.fn();
const refreshEventRadar = vi.fn();
const setEventRadarDecision = vi.fn();
const promoteEventRadarOpportunity = vi.fn();

vi.mock("../../../../services/vkpi/eventRadar-api", () => ({
  listEventRadar: (...args: unknown[]) => listEventRadar(...args),
  getEventRadarSummary: (...args: unknown[]) => getEventRadarSummary(...args),
  getEventCandidateStagingSummary: (...args: unknown[]) => getEventCandidateStagingSummary(...args),
  getEventUsSourceRegistry: (...args: unknown[]) => getEventUsSourceRegistry(...args),
  previewEventRadarRefresh: (...args: unknown[]) => previewEventRadarRefresh(...args),
  refreshEventRadar: (...args: unknown[]) => refreshEventRadar(...args),
  setEventRadarDecision: (...args: unknown[]) => setEventRadarDecision(...args),
  promoteEventRadarOpportunity: (...args: unknown[]) => promoteEventRadarOpportunity(...args),
}));

import { EventRadarModule, buildUsOpportunityMapAggregate, hasCurrentApprovalTruth } from "./EventRadarModule";

const DEALER_EVENT = {
  id: "opp-1",
  title: "Dealer Camera Demo Day",
  lane: "dealer_event",
  source_kind: "school_calendar",
  organizer: "Dealer One",
  start_date: "2026-09-10",
  end_date: "2026-09-10",
  timezone: "America/New_York",
  local_time_text: "10:00–16:00",
  venue: "Dealer One NYC",
  city: "New York",
  region: "NY",
  country_code: "US",
  official_url: "https://dealer.example/events/demo-day",
  registration_url: "https://dealer.example/events/demo-day/register",
  event_status: "scheduled",
  decision_status: "new",
  evidence_grade: "A2",
  verification_status: "conflict",
  freshness_status: "current",
  source_status: "active",
  source_enabled: true,
  confidence: 0.82,
  relevance_score: 88,
  relevance_basis: "官方门店活动页含摄影器材现场体验",
  last_verified_at: "2026-07-13T12:00:00Z",
  change_count: 2,
};

const EXPO = {
  ...DEALER_EVENT,
  id: "opp-2",
  title: "Major Imaging Expo",
  lane: "major_expo",
  source_kind: "major_expo",
  organizer: "Expo Organizer",
  city: "Amsterdam",
  country_code: "NL",
  decision_status: "approved",
  verification_status: "verified",
  evidence_grade: "official",
  official_url: "https://expo.example/official",
  registration_url: null,
  change_count: 0,
};

beforeEach(() => {
  listEventRadar.mockReset();
  getEventRadarSummary.mockReset();
  getEventCandidateStagingSummary.mockReset();
  getEventUsSourceRegistry.mockReset();
  previewEventRadarRefresh.mockReset();
  refreshEventRadar.mockReset();
  setEventRadarDecision.mockReset();
  promoteEventRadarOpportunity.mockReset();

  listEventRadar.mockResolvedValue({
    items: [DEALER_EVENT, EXPO],
    count: 2,
    coverage_claim: "registered_publisher_owned_public_entries_only",
  });
  getEventRadarSummary.mockResolvedValue({
    total: 2,
    lane_counts: { dealer_event: 1, major_expo: 1 },
    verification_counts: { stale: 3, conflict: 1 },
    decision_counts: { new: 1, approved: 1 },
    us_jurisdiction_matrix: {
      covered_states: ["CA", "NY"],
      missing_states: [],
      covered_count: 2,
      jurisdiction_count: 51,
      opportunity_counts_by_state_dc: { CA: 3, NY: 2 },
      verification_marked_counts_by_state_dc: { CA: 2 },
      opportunity_entity_count: 5,
      map_precision: "state_dc_aggregate_not_venue_coordinates",
      authoritative_market_denominator: null,
      coverage_rate: null,
      claim_status: "descriptive_only",
    },
    last_refresh_at: "2026-07-13T12:00:00Z",
  });
  getEventCandidateStagingSummary.mockResolvedValue({
    status: "ready",
    candidate_type: "event_opportunity",
    total: 7,
    review_status: { pending: 5, approved: 2 },
    promotion_gate_status: { blocked: 7 },
    linked_field_evidence: 11,
    claim_status: "descriptive_only",
    automatic_promotion: false,
    business_rows_written: 0,
  });
  getEventUsSourceRegistry.mockResolvedValue({
    ok: true,
    country_code: "US",
    coverage_claim: "registered_publisher_owned_public_entries_only",
    full_us_coverage: false,
    claim_status: "descriptive_only",
    event_sources: [
      {
        id: "source-ca-ny",
        name: "Published source CA NY",
        source_kind: "dealer_event",
        publisher: "Publisher A",
        canonical_url: "https://publisher.example/events",
        state_codes: ["CA", "NY"],
      },
      {
        id: "source-tx",
        name: "Photography workshop calendar TX",
        source_kind: "school_calendar",
        publisher: "Publisher B",
        canonical_url: "https://school.example/events",
        state_codes: ["TX"],
      },
    ],
    counts: {
      event_sources: 59,
      event_source_kinds: {
        association_directory: 2,
        brand_event: 10,
        community_calendar: 6,
        dealer_event: 16,
        major_expo: 11,
        photo_club: 3,
        school_calendar: 8,
        university_calendar: 3,
      },
      enabled: 0,
      direct_import_allowed: 0,
    },
    source_jurisdiction_matrix: {
      event_sources: {
        scope: "registered_source_discovery_jurisdictions_only",
        covered_states_dc: ["CA", "NY", "TX"],
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
  });
  previewEventRadarRefresh.mockResolvedValue({
    discovered: 4,
    conflicted: 1,
    import_allowed: true,
    quality_status: "partial_descriptive",
  });
  refreshEventRadar.mockResolvedValue({ inserted: 1, updated: 2 });
  setEventRadarDecision.mockImplementation((_token, _id, status) => Promise.resolve({ item: { ...DEALER_EVENT, verification_status: "verified", decision_status: status } }));
  promoteEventRadarOpportunity.mockResolvedValue({ ok: true, event_id: "event-77" });
});

describe("EventRadarModule", () => {
  it("mirrors the backend approval truth gate without guessing missing fields", () => {
    const current = { ...DEALER_EVENT, verification_status: "verified" };
    expect(hasCurrentApprovalTruth(current, "2026-07-15")).toBe(true);
    expect(hasCurrentApprovalTruth({ ...current, source_status: "hold" }, "2026-07-15")).toBe(false);
    expect(hasCurrentApprovalTruth({ ...current, source_enabled: false }, "2026-07-15")).toBe(false);
    expect(hasCurrentApprovalTruth({ ...current, event_status: "postponed" }, "2026-07-15")).toBe(false);
    expect(hasCurrentApprovalTruth({ ...current, freshness_status: "stale" }, "2026-07-15")).toBe(false);
    expect(hasCurrentApprovalTruth({ ...current, end_date: "2026-07-14" }, "2026-07-15")).toBe(false);
    expect(hasCurrentApprovalTruth({ ...current, start_date: null }, "2026-07-15")).toBe(false);
  });

  it("builds exact US state entity and verification counts without accepting non-US regions", () => {
    expect(buildUsOpportunityMapAggregate([
      { ...DEALER_EVENT, id: "ca-1", region: "CA", verification_status: "verified" },
      { ...DEALER_EVENT, id: "ca-2", region: "CA", verification_status: "pending" },
      { ...DEALER_EVENT, id: "ny-1", region: "NY", verification_status: "current" },
      { ...DEALER_EVENT, id: "on-1", region: "ON", verification_status: "verified" },
      { ...DEALER_EVENT, id: "nl-1", country_code: "NL", region: "NH", verification_status: "verified" },
    ])).toEqual({
      opportunity_counts_by_state_dc: { CA: 2, NY: 1 },
      verification_marked_counts_by_state_dc: { CA: 1, NY: 1 },
      opportunity_entity_count: 3,
      map_precision: "state_dc_aggregate_not_venue_coordinates",
    });
  });

  it("separates local/dealer and major-expo coverage and exposes source quality", async () => {
    render(<EventRadarModule apiToken="tok" />);

    expect(await screen.findByText("Dealer Camera Demo Day")).toBeTruthy();
    expect(screen.getByText("Major Imaging Expo")).toBeTruthy();
    expect(screen.getAllByText("来源冲突").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("A2 · 登记日历页")).toBeTruthy();
    expect(screen.getByText(/当前仅覆盖数据库已登记的发布者自有公开入口与已入库活动记录/)).toBeTruthy();
    expect(screen.getAllByText(/登记门店活动页（发布者身份待核验）含摄影器材现场体验/).length).toBe(2);
    expect(screen.getByText("登记活动页")).toBeTruthy();
    expect(screen.getByTestId("event-us-jurisdiction-matrix").textContent).toContain("2 / 51");
    expect(screen.getByTestId("event-us-source-coverage").textContent).toContain("美国记录部分覆盖");
    expect(screen.getByTestId("event-us-source-coverage").textContent).toContain("来源发现州 / DC51");
    expect(screen.getByTestId("event-us-source-coverage").textContent).toContain("已入库活动州 / DC2");
    expect(screen.getByTestId("event-us-source-coverage").textContent).toContain("候选机会7");
    expect(screen.getByTestId("event-us-source-coverage").textContent).toContain("待人工复核5");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("Dealer / 门店活动入口16");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("摄影学校入口8");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("高校入口3");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("社区 / 社群入口11");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("工作坊 / 课程线索入口1");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("大展会入口11");
    expect(screen.getByTestId("event-priority-source-groups").textContent).toContain("品牌活动入口10");
    expect(screen.getByText(/会与 Dealer、学校、社群或品牌分类重叠/)).toBeTruthy();
    expect(screen.getByTestId("event-source-import-gate").textContent).toContain("来源启用 0/59 · 直接导入 0");
    expect(screen.getByText("须重查发布者身份与来源后再行动")).toBeTruthy();
    expect(screen.getByText("经销商 / 线下")).toBeTruthy();
    expect(screen.getAllByText("Dealer 活动").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("摄影学校").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("大展会记录").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: "筛选全部美国记录" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开 Dealer Camera Demo Day 登记来源" }).getAttribute("href"))
      .toBe("https://dealer.example/events/demo-day");
    expect(screen.getByTestId("event-evidence-fields-opp-1").textContent).toContain("外部机会候选 · 未转 Event");
    expect(screen.getByTestId("event-evidence-fields-opp-1").textContent).toContain("来源链接 已记录");
  });

  it("renders a state aggregate map and uses a source-only zero-entity state as a list filter", async () => {
    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");

    fireEvent.click(screen.getByRole("button", { name: /州级地图/ }));

    const map = await screen.findByTestId("event-us-state-tile-map");
    expect(map.textContent).toContain("州级聚合 / 非场地坐标");
    expect(screen.getAllByTestId(/^event-map-state-/)).toHaveLength(51);
    expect(screen.getByTestId("event-map-state-CA").getAttribute("aria-label")).toContain("已入库机会实体 3");
    expect(screen.getByTestId("event-map-state-CA").getAttribute("aria-label")).toContain("核验标记 2");
    expect(screen.getByTestId("event-map-state-TX").getAttribute("aria-label")).toContain("来源入口 1 · 已入库机会实体 0");
    expect(screen.getByTestId("event-map-state-TX").textContent).toContain("实体 0");

    fireEvent.click(screen.getByTestId("event-map-state-TX"));

    await waitFor(() => expect(listEventRadar).toHaveBeenLastCalledWith("tok", expect.objectContaining({
      country: "US",
      region: "TX",
      time_window: "upcoming",
    })));
    expect(screen.getByRole("button", { name: /列表/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("recovers exact state counts from the complete US opportunity list when a legacy summary omits aggregate fields", async () => {
    getEventRadarSummary.mockResolvedValue({
      total: 3,
      lane_counts: { dealer_event: 3 },
      us_jurisdiction_matrix: {
        covered_states: ["CA", "NY"],
        covered_count: 2,
        jurisdiction_count: 51,
        claim_status: "descriptive_only",
      },
    });
    listEventRadar.mockImplementation((_token, params) => {
      if (params?.include_past === true && params?.country === "US") {
        return Promise.resolve({
          items: [
            { ...DEALER_EVENT, id: "ca-1", region: "CA", verification_status: "verified" },
            { ...DEALER_EVENT, id: "ca-2", region: "CA", verification_status: "pending" },
            { ...DEALER_EVENT, id: "ny-1", region: "NY", verification_status: "current" },
          ],
          count: 3,
          page: { limit: 500, offset: 0, returned: 3, next_offset: null, has_more: false },
        });
      }
      return Promise.resolve({
        items: [DEALER_EVENT],
        count: 1,
        coverage_claim: "registered_publisher_owned_public_entries_only",
      });
    });

    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");
    fireEvent.click(screen.getByRole("button", { name: /州级地图/ }));

    await waitFor(() => {
      expect(screen.getByTestId("event-map-state-CA").getAttribute("aria-label")).toContain("已入库机会实体 2");
    });
    expect(screen.getByTestId("event-map-state-CA").getAttribute("aria-label")).toContain("核验标记 1");
    expect(screen.getByTestId("event-map-state-NY").getAttribute("aria-label")).toContain("已入库机会实体 1");
    expect(screen.getByTestId("event-map-state-TX").getAttribute("aria-label")).toContain("已入库机会实体 0");
    expect(listEventRadar).toHaveBeenCalledWith("tok", {
      limit: 500,
      offset: 0,
      country: "US",
      include_past: true,
    });
  });

  it("passes filters to the list endpoint", async () => {
    render(<EventRadarModule apiToken="tok" limit={25} />);
    await screen.findByText("Dealer Camera Demo Day");

    fireEvent.change(screen.getByLabelText("活动层级"), { target: { value: "major_expo" } });
    fireEvent.change(screen.getByLabelText("国家代码"), { target: { value: "nl" } });

    await waitFor(() => {
      expect(listEventRadar).toHaveBeenLastCalledWith("tok", {
        limit: 25,
        offset: 0,
        lane: "major_expo",
        source_kind: undefined,
        decision_status: undefined,
        evidence_status: undefined,
        time_window: "upcoming",
        country: "NL",
        region: undefined,
      });
    });
  });

  it("keeps brand events as a source kind and never exposes them as a lane", async () => {
    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");

    const laneOptions = Array.from((screen.getByLabelText("活动层级") as HTMLSelectElement).options);
    const sourceOptions = Array.from((screen.getByLabelText("活动类型") as HTMLSelectElement).options);
    expect(laneOptions.map((option) => option.value)).not.toContain("brand_event");
    expect(sourceOptions.map((option) => option.value)).toContain("brand_event");
  });

  it("does not request or clear results for an incomplete country code", async () => {
    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");
    const callsBefore = listEventRadar.mock.calls.length;

    fireEvent.change(screen.getByLabelText("国家代码"), { target: { value: "u" } });

    expect(await screen.findByText("请输入完整两位国家代码；当前保留上一批结果。")).toBeTruthy();
    expect(screen.getByText("Dealer Camera Demo Day")).toBeTruthy();
    expect(listEventRadar.mock.calls.length).toBe(callsBefore);

    fireEvent.change(screen.getByLabelText("国家代码"), { target: { value: "us" } });
    await waitFor(() => expect(listEventRadar).toHaveBeenLastCalledWith("tok", expect.objectContaining({ country: "US" })));
  });

  it("keeps all write actions hidden for a read-only viewer", async () => {
    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");

    expect(screen.getByRole("button", { name: "预检目录快照" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "导入目录快照" })).toBeNull();
    expect(screen.queryByRole("button", { name: "关注 Dealer Camera Demo Day" })).toBeNull();
    expect(screen.queryByRole("button", { name: "忽略 Dealer Camera Demo Day" })).toBeNull();
    expect(screen.queryByRole("button", { name: "将 Major Imaging Expo 转为 Event" })).toBeNull();
    expect(screen.getByText(/本操作不联网抓取/)).toBeTruthy();
    expect(screen.queryByText(/执行同步|最近同步/)).toBeNull();
  });

  it("shows approve and promote only when backend truth fields are current", async () => {
    const validWatching = { ...DEALER_EVENT, id: "valid-watch", title: "Valid Watching", verification_status: "verified", decision_status: "watching" };
    const staleWatching = { ...validWatching, id: "stale-watch", title: "Stale Watching", freshness_status: "stale" };
    const heldWatching = { ...validWatching, id: "held-watch", title: "Held Watching", source_status: "hold" };
    const postponedWatching = { ...validWatching, id: "postponed-watch", title: "Postponed Watching", event_status: "postponed" };
    const validApproved = { ...validWatching, id: "valid-approved", title: "Valid Approved", decision_status: "approved" };
    const staleApproved = { ...validApproved, id: "stale-approved", title: "Stale Approved", freshness_status: "stale" };
    listEventRadar.mockResolvedValue({
      items: [validWatching, staleWatching, heldWatching, postponedWatching, validApproved, staleApproved],
      count: 6,
      coverage_claim: "registered_publisher_owned_public_entries_only",
    });

    render(<EventRadarModule apiToken="tok" canManage />);
    await screen.findByText("Valid Watching");

    expect(screen.getByRole("button", { name: "批准 Valid Watching" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "将 Valid Approved 转为 Event" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "批准 Stale Watching" })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准 Held Watching" })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准 Postponed Watching" })).toBeNull();
    expect(screen.queryByRole("button", { name: "将 Stale Approved 转为 Event" })).toBeNull();
  });

  it("reloads a server-filtered decision page after mutation", async () => {
    let current = { ...DEALER_EVENT };
    listEventRadar.mockImplementation((_token, params) => {
      const visible = !params?.decision_status || params.decision_status === current.decision_status;
      return Promise.resolve({
        items: visible ? [current] : [],
        count: visible ? 1 : 0,
        coverage_claim: "registered_publisher_owned_public_entries_only",
      });
    });
    setEventRadarDecision.mockImplementation((_token, _id, status) => {
      current = { ...current, decision_status: status };
      return Promise.resolve({ item: current });
    });
    render(<EventRadarModule apiToken="tok" canManage />);
    await screen.findByText("Dealer Camera Demo Day");

    fireEvent.change(screen.getByLabelText("人工判断"), { target: { value: "new" } });
    await waitFor(() => expect(listEventRadar).toHaveBeenLastCalledWith("tok", expect.objectContaining({ decision_status: "new" })));
    fireEvent.click(screen.getByRole("button", { name: "关注 Dealer Camera Demo Day" }));

    await waitFor(() => expect(screen.queryByText("Dealer Camera Demo Day")).toBeNull());
    expect(await screen.findByText(/当前筛选下没有已入库机会/)).toBeTruthy();
    expect(listEventRadar).toHaveBeenLastCalledWith("tok", expect.objectContaining({ decision_status: "new" }));
  });

  it("filters US state, source type, time and evidence without promoting candidates", async () => {
    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");

    fireEvent.change(screen.getByLabelText("国家代码"), { target: { value: "US" } });
    fireEvent.change(screen.getByLabelText("州 / DC"), { target: { value: "NY" } });
    fireEvent.change(screen.getByLabelText("活动类型"), { target: { value: "school_calendar" } });
    fireEvent.change(screen.getByLabelText("证据状态"), { target: { value: "conflict" } });

    expect(await screen.findByText("Dealer Camera Demo Day")).toBeTruthy();
    expect(screen.queryByText("Major Imaging Expo")).toBeNull();
    await waitFor(() => expect(listEventRadar).toHaveBeenLastCalledWith("tok", expect.objectContaining({
      country: "US",
      region: "NY",
      source_kind: "school_calendar",
      evidence_status: "conflict",
      time_window: "upcoming",
    })));
    expect(getEventCandidateStagingSummary).toHaveBeenCalledWith("tok");
    expect(getEventUsSourceRegistry).toHaveBeenCalledWith("tok");
    expect(screen.queryByRole("button", { name: /7.*转为 Event/ })).toBeNull();
  });

  it("requires attention before promotion, reloads server truth, and records the returned Event id", async () => {
    const onPromoted = vi.fn();
    let current = { ...DEALER_EVENT, verification_status: "verified" };
    listEventRadar.mockImplementation(() => Promise.resolve({
      items: [current],
      count: 1,
      coverage_claim: "registered_publisher_owned_public_entries_only",
    }));
    setEventRadarDecision.mockImplementation((_token, _id, status) => {
      current = { ...current, decision_status: status };
      return Promise.resolve({ item: current });
    });
    promoteEventRadarOpportunity.mockImplementation(() => {
      current = { ...current, decision_status: "promoted", converted_event_id: "event-77" };
      return Promise.resolve({ ok: true, event_id: "event-77", item: current });
    });
    render(<EventRadarModule apiToken="tok" canManage onPromoted={onPromoted} />);
    await screen.findByText("Dealer Camera Demo Day");

    fireEvent.click(screen.getByRole("button", { name: "关注 Dealer Camera Demo Day" }));
    await waitFor(() => expect(setEventRadarDecision).toHaveBeenCalledWith("tok", "opp-1", "watching"));

    fireEvent.click(await screen.findByRole("button", { name: "批准 Dealer Camera Demo Day" }));
    await waitFor(() => expect(setEventRadarDecision).toHaveBeenCalledWith("tok", "opp-1", "approved"));

    const promote = await screen.findByRole("button", { name: "将 Dealer Camera Demo Day 转为 Event" });
    fireEvent.click(promote);
    await waitFor(() => expect(promoteEventRadarOpportunity).toHaveBeenCalledWith("tok", "opp-1"));
    expect(await screen.findByText("已转为内部 Event · event-77")).toBeTruthy();
    expect(onPromoted).toHaveBeenCalledWith("event-77", expect.objectContaining({ id: "opp-1" }));
  });

  it("keeps preview read-only and reports an honest empty state", async () => {
    listEventRadar.mockResolvedValueOnce({ items: [], count: 0, coverage_claim: null });
    render(<EventRadarModule apiToken="tok" canManage />);
    expect(await screen.findByText(/当前筛选下没有已入库机会/)).toBeTruthy();

    const execute = screen.getByRole("button", { name: /导入目录快照/ }) as HTMLButtonElement;
    expect(execute.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /预检目录快照/ }));
    await waitFor(() => expect(previewEventRadarRefresh).toHaveBeenCalledWith("tok"));
    expect(await screen.findByText("内置目录快照预检完成（未写入） · 发现 4 · 冲突 1")).toBeTruthy();
    expect(await screen.findByText(
      "质量门禁通过 · import_allowed=true · quality_status=partial_descriptive",
    )).toBeTruthy();
    expect(execute.disabled).toBe(false);
    expect(refreshEventRadar).not.toHaveBeenCalled();
  });

  it("keeps write execution locked when preview quality blocks import", async () => {
    previewEventRadarRefresh
      .mockResolvedValueOnce({
        discovered: 4,
        import_allowed: true,
        quality_status: "partial_descriptive",
      })
      .mockResolvedValueOnce({
        discovered: 4,
        import_allowed: false,
        quality_status: "blocked_for_import",
      });
    render(<EventRadarModule apiToken="tok" canManage />);
    await screen.findByText("Dealer Camera Demo Day");

    const execute = screen.getByRole("button", { name: /导入目录快照/ }) as HTMLButtonElement;
    expect(execute.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /预检目录快照/ }));

    expect(await screen.findByText(
      "质量门禁已阻断 · import_allowed=false · quality_status=blocked_for_import",
    )).toBeTruthy();
    expect(execute.disabled).toBe(true);
    fireEvent.click(execute);
    expect(refreshEventRadar).not.toHaveBeenCalled();
  });

  it("shows bundled US opportunities only as blocked read-only discovery previews", async () => {
    listEventRadar.mockResolvedValueOnce({ items: [], count: 0, coverage_claim: null });
    previewEventRadarRefresh.mockResolvedValue({
      import_allowed: false,
      quality_status: "blocked_for_import",
      preview_item_count: 1,
      preview_items: [{
        ...EXPO,
        id: "catalog-preview:opp-siggraph-2026",
        catalog_item_id: "opp-siggraph-2026",
        title: "SIGGRAPH 2026",
        city: "Los Angeles",
        region: "CA",
        country_code: "US",
        start_date: "2026-07-19",
        end_date: "2026-07-23",
        decision_status: "needs_review",
        verification_status: "needs_review",
        freshness_status: "unverified",
        source_status: "active",
        source_enabled: false,
        source_checked_at: null,
        preview_only: true,
      }],
    });

    render(<EventRadarModule apiToken="tok" canManage />);

    expect(await screen.findByTestId("event-discovery-preview")).toHaveTextContent(
      "未写入数据库、未通过当前发布者身份与新鲜度护照",
    );
    expect(screen.getByText("SIGGRAPH 2026")).toBeTruthy();
    expect(screen.getByText("目录只读候选")).toBeTruthy();
    expect(screen.getByText("来源非 enabled + active，决策与转 Event 已阻断")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /关注 SIGGRAPH 2026/ })).toBeNull();
  });

  it("pages beyond the first 100 server-filtered opportunities", async () => {
    listEventRadar.mockImplementation((_token, params) => Promise.resolve(
      params?.offset === 100
        ? {
          items: [{ ...EXPO, id: "opp-101", title: "Page Two Expo" }],
          count: 101,
          page: { limit: 100, offset: 100, returned: 1, next_offset: null, has_more: false },
          coverage_claim: "registered_publisher_owned_public_entries_only",
        }
        : {
          items: [DEALER_EVENT],
          count: 101,
          page: { limit: 100, offset: 0, returned: 100, next_offset: 100, has_more: true },
          coverage_claim: "registered_publisher_owned_public_entries_only",
        },
    ));

    render(<EventRadarModule apiToken="tok" />);
    await screen.findByText("Dealer Camera Demo Day");
    expect(screen.getByTestId("event-radar-pagination").textContent).toContain("1-1 / 101");

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("Page Two Expo")).toBeTruthy();
    await waitFor(() => expect(listEventRadar).toHaveBeenLastCalledWith(
      "tok",
      expect.objectContaining({ offset: 100, time_window: "upcoming" }),
    ));
    expect(screen.getByTestId("event-radar-pagination").textContent).toContain("101-101 / 101");
  });
});
