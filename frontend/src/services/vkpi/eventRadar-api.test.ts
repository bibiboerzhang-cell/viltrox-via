import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  getEventRadarSummary,
  listEventRadar,
  previewEventRadarRefresh,
  promoteEventRadarOpportunity,
  refreshEventRadar,
  setEventRadarDecision,
} from "./eventRadar-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({});
});

describe("eventRadar-api", () => {
  it("lists opportunities with bounded filters and no-store", async () => {
    await listEventRadar("tok", {
      limit: 25,
      offset: 50,
      lane: "dealer_event",
      source_kind: "school_calendar",
      decision_status: "new",
      evidence_status: "review",
      time_window: "90d",
      country: "US",
      region: "CA",
    });
    const [path, init, token] = apiFetch.mock.calls[0];
    const url = new URL(`http://local${path}`);
    expect(url.pathname).toBe("/api/admin/vkpi/event-radar");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: "25",
      offset: "50",
      lane: "dealer_event",
      source_kind: "school_calendar",
      decision_status: "new",
      evidence_status: "review",
      time_window: "90d",
      country: "US",
      region: "CA",
    });
    expect(init).toEqual({ cache: "no-store" });
    expect(token).toBe("tok");
  });

  it("uses a default list limit and omits empty filters", async () => {
    await listEventRadar("tok");
    const [path] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/event-radar?limit=100");
  });

  it("reads the summary", async () => {
    await getEventRadarSummary("tok");
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/event-radar/summary",
      { cache: "no-store" },
      "tok",
    );
  });

  it("keeps preview refresh body-free", async () => {
    await previewEventRadarRefresh("tok");
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/event-radar/refresh-preview",
      { method: "POST", cache: "no-store" },
      "tok",
    );
  });

  it("records a refresh only through explicit record_only=false", async () => {
    await refreshEventRadar("tok");
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/event-radar/refresh");
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(init.body)).toEqual({ record_only: false });
    expect(token).toBe("tok");
  });

  it("patches an operator decision", async () => {
    await setEventRadarDecision("tok", "opp/7", "approved");
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/event-radar/opp%2F7/decision");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ decision_status: "approved" });
    expect(token).toBe("tok");
  });

  it("promotes an opportunity without inventing an Event payload", async () => {
    await promoteEventRadarOpportunity("tok", 9);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/event-radar/9/promote",
      { method: "POST", cache: "no-store" },
      "tok",
    );
  });
});
