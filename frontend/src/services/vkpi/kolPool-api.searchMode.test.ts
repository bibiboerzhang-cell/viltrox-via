import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../http", () => ({ apiFetch: vi.fn(), jsonBody: (value: unknown) => JSON.stringify(value) }));
import { apiFetch } from "../http";
import { smartKolSearch, smartKolSearchProfileAdvanceJob } from "./kolPool-api.search";

const request = vi.mocked(apiFetch);

describe("explicit KOL search source wire contract", () => {
  beforeEach(() => { request.mockReset(); request.mockResolvedValue({} as never); });

  it("keeps legacy requests hybrid by omission", async () => {
    await smartKolSearch("token", "street photography");
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).not.toHaveProperty("search_mode");
  });

  it("forwards fresh_network to both explicit queue entrypoints", async () => {
    await smartKolSearch("token", "street photography", { searchMode: "fresh_network" });
    await smartKolSearchProfileAdvanceJob("token", "street photography", { searchMode: "fresh_network", sessionId: 41 });
    expect(JSON.parse(String(request.mock.calls[0][1]?.body)).search_mode).toBe("fresh_network");
    expect(JSON.parse(String(request.mock.calls[1][1]?.body))).toMatchObject({ search_mode: "fresh_network", session_id: 41, queue_pipeline: true });
  });

  it("saved mode disables discovery even if a caller supplies the old true flag", async () => {
    await smartKolSearchProfileAdvanceJob("token", "street photography", { searchMode: "saved", includeNewDiscovery: true });
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toMatchObject({ search_mode: "saved", include_new_discovery: false });
  });

  it("hybrid authorization and online limits are explicit on the single search POST", async () => {
    await smartKolSearch("token", "street photography", {
      searchMode: "hybrid", includeNewDiscovery: true, executeNewDiscovery: true,
      newDiscoveryPlatforms: ["youtube"], newDiscoveryLimit: 30, advanceLimit: 30,
      onlineQualificationSpec: { version: "online_net_new_30_v1", target_count: 30 },
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toMatchObject({
      search_mode: "hybrid", include_new_discovery: true, execute_new_discovery: true,
      new_discovery_platforms: ["youtube"], new_discovery_limit: 30, advance_limit: 30,
    });
  });

  it("a saved search never serializes true paid flags", async () => {
    await smartKolSearch("token", "street photography", {
      searchMode: "saved", includeNewDiscovery: true, executeNewDiscovery: true,
    });
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toMatchObject({
      search_mode: "saved", include_new_discovery: false, execute_new_discovery: false,
    });
  });
});
