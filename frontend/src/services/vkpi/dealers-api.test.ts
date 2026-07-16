import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  createDealer,
  getDealerActivities,
  getDealerLocations,
  listAllDealers,
  listDealers,
  normalizeDealer,
  publishDealer,
  unpublishDealer,
  updateDealer,
} from "./dealers-api";

const RAW = {
  id: 1,
  name: "Adorama",
  address: "42 W 18th St",
  city: "New York",
  state: "NY",
  country: "US",
  lat: 40.74,
  lng: -73.99,
  source_status: "public_listing_verified",
  authorization_status: "needs_viltrox_confirmation",
  brand_listing_url: "https://www.adorama.com/brands/Viltrox",
  location_source_url: "https://www.adorama.com/g/nyc-store",
  source_checked_at: "2026-07-13T12:00:00Z",
  phone: "212-741-0063",
};

beforeEach(() => apiFetch.mockReset());

describe("dealers-api evidence normalization", () => {
  it("maps raw evidence fields into a consistent display contract without inventing stock or authorization", () => {
    const dealer = normalizeDealer(RAW);

    expect(dealer.website_url).toBe("https://www.adorama.com");
    expect(dealer.truth_status).toMatchObject({
      candidate: false,
      public_listing: "verified",
      product_evidence: "verified_public_url",
      viltrox_authorization: "pending",
      current_inventory: "unknown",
    });
    expect(dealer.channel_evidence).toMatchObject({
      offline_location: "public_listing_verified",
      online_product_page: "verified_public_url",
      online_sales: "unknown",
      current_inventory: "unknown",
    });
    expect(dealer.last_verified_at).toBe("2026-07-13T12:00:00Z");
    expect(dealer.provenance?.product?.source_url).toBe(RAW.brand_listing_url);
    expect(dealer.social_links).toEqual([]);
    expect(dealer.social_status).toBe("not_collected");
    expect(dealer.authorization_evidence?.official_viltrox_source_url).toBeNull();
  });

  it("normalizes both directory rows and map pins returned by legacy/raw endpoints", async () => {
    apiFetch
      .mockResolvedValueOnce({ dealers: [RAW] })
      .mockResolvedValueOnce({ pins: [RAW] });

    const directory = await listDealers("tok", {
      limit: 25,
      state: "NY",
      city: "New York",
      channel: "both",
      evidenceStatus: "public_listing_verified",
      productEvidence: "available",
      authorization: "pending",
    });
    const locations = await getDealerLocations("tok", {
      state: "NY",
      evidenceStatus: "candidate",
      productEvidence: "available",
      brand: "Sony",
      publishedOnly: true,
      bbox: [-74.2, 40.4, -73.6, 41],
    });

    expect(directory.dealers?.[0].truth_status?.product_evidence).toBe("verified_public_url");
    expect(locations.pins?.[0].truth_status?.public_listing).toBe("verified");
    const directoryUrl = new URL(`http://local${apiFetch.mock.calls[0][0]}`);
    expect(Object.fromEntries(directoryUrl.searchParams)).toMatchObject({
      limit: "25",
      state: "NY",
      city: "New York",
      channel: "both",
      evidence_status: "public_listing_verified",
      product_evidence: "available",
      authorization: "pending",
    });
    const mapUrl = new URL(`http://local${apiFetch.mock.calls[1][0]}`);
    expect(Object.fromEntries(mapUrl.searchParams)).toEqual({
      state: "NY",
      evidence_status: "candidate",
      product_evidence: "available",
      brand: "SONY",
      published_only: "true",
      bbox: "-74.2,40.4,-73.6,41",
    });
  });

  it("keeps multi-brand, publication, Viltrox deployment and activity as separate facts", () => {
    const dealer = normalizeDealer({
      ...RAW,
      brand_codes: ["sony", "Nikon"],
      brand_relationships: [{ brand_key: "CANON", authorization_status: "official_locator" }],
      publication_status: "published",
      published_at: "2026-07-14T12:00:00Z",
      viltrox_deployment: { status: "deployed", deployed_at: "2026-07-14T12:00:00Z", note: "local map" },
      activity: { status: "unknown", page_url: null },
    });

    expect(dealer.brand_codes).toEqual(["CANON", "SONY", "NIKON"]);
    expect(dealer.publication_status).toBe("published");
    expect(dealer.viltrox_deployment?.status).toBe("deployed");
    expect(dealer.activity?.status).toBe("unknown");
    expect(dealer.truth_status?.viltrox_authorization).toBe("pending");
  });

  it("uses explicit draft update and publish/unpublish endpoints", async () => {
    apiFetch
      .mockResolvedValueOnce({ ...RAW, id: 9, publication_status: "draft", brand_codes: ["NIKON"] })
      .mockResolvedValueOnce({ ...RAW, id: 9, publication_status: "draft", brand_codes: ["NIKON", "SONY"] })
      .mockResolvedValueOnce({ ...RAW, id: 9, publication_status: "published" })
      .mockResolvedValueOnce({ ...RAW, id: 9, publication_status: "draft" });

    await createDealer("tok", { name: "Store", address: "1 Main", brands: ["NIKON"] });
    await updateDealer("tok", 9, { brands: ["NIKON", "SONY"], viltrox_deployment: { status: "planned" } });
    await publishDealer("tok", 9);
    await unpublishDealer("tok", 9);

    expect(apiFetch.mock.calls.map((call) => [call[0], (call[1] as RequestInit)?.method])).toEqual([
      ["/api/admin/vkpi/dealers", "POST"],
      ["/api/admin/vkpi/dealers/9", "PATCH"],
      ["/api/admin/vkpi/dealers/9/publish", "POST"],
      ["/api/admin/vkpi/dealers/9/unpublish", "POST"],
    ]);
    expect(JSON.parse(String((apiFetch.mock.calls[1][1] as RequestInit).body))).toMatchObject({
      brands: ["NIKON", "SONY"],
      viltrox_deployment: { status: "planned" },
    });
  });

  it("reads Event Radar activities through the exact Dealer relation endpoint", async () => {
    apiFetch.mockResolvedValueOnce({
      status: "ready",
      dealer_id: 9,
      activities: [{ id: "opp-1", title: "Workshop", association: "exact_dealer_id" }],
      count: 1,
      association_policy: "exact_dealer_id_only",
    });

    const response = await getDealerActivities("tok", 9, 8);

    expect(response.count).toBe(1);
    expect(response.activities[0].association).toBe("exact_dealer_id");
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/dealers/9/activities?limit=8",
      { cache: "no-store" },
      "tok",
    );
  });

  it("preserves an explicit server projection instead of replacing it with a guess", () => {
    const dealer = normalizeDealer({
      ...RAW,
      truth_status: {
        candidate: false,
        public_listing: "verified",
        product_evidence: "unavailable",
        viltrox_authorization: "pending",
        current_inventory: "unknown",
      },
    });

    expect(dealer.truth_status?.product_evidence).toBe("unavailable");
  });

  it("downgrades a status-only authorization and confirms only official URL plus verified time", () => {
    const statusOnly = normalizeDealer({ ...RAW, authorization_status: "authorized_confirmed" });
    const complete = normalizeDealer({
      ...RAW,
      authorization_status: "authorized_confirmed",
      authorization_evidence: {
        status: "authorized_confirmed",
        official_viltrox_source_url: "https://www.viltrox.com/dealers/adorama",
        verified_at: "2026-07-14T12:00:00Z",
      },
    });

    expect(statusOnly.authorization_status).toBe("needs_viltrox_confirmation");
    expect(statusOnly.truth_status?.viltrox_authorization).toBe("pending");
    expect(complete.authorization_status).toBe("authorized_confirmed");
    expect(complete.truth_status?.viltrox_authorization).toBe("confirmed");
  });

  it("follows total_count pagination instead of silently treating 500 rows as the full directory", async () => {
    const first = Array.from({ length: 500 }, (_, index) => ({ ...RAW, id: index }));
    const second = Array.from({ length: 2 }, (_, index) => ({ ...RAW, id: 500 + index }));
    apiFetch
      .mockResolvedValueOnce({
        dealers: first,
        count: 500,
        total_count: 502,
        page: { limit: 500, offset: 0, returned: 500, next_offset: 500, has_more: true },
      })
      .mockResolvedValueOnce({
        dealers: second,
        count: 2,
        total_count: 502,
        page: { limit: 500, offset: 500, returned: 2, next_offset: null, has_more: false },
      });

    const response = await listAllDealers("tok");

    expect(response.dealers).toHaveLength(502);
    expect(response.total_count).toBe(502);
    expect(apiFetch.mock.calls.map((call) => String(call[0]))).toEqual([
      "/api/admin/vkpi/dealers?limit=500",
      "/api/admin/vkpi/dealers?limit=500&offset=500",
    ]);
  });

  it("fails closed when a legacy server returns a full 500-row page without total_count", async () => {
    apiFetch.mockResolvedValueOnce({
      dealers: Array.from({ length: 500 }, (_, index) => ({ ...RAW, id: index })),
    });

    await expect(listAllDealers("tok")).rejects.toThrow("may be truncated");
  });
});
