import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { listEvents, listUpcomingEvents } from "./events-api";

beforeEach(() => apiFetch.mockReset());

describe("events API list contracts", () => {
  it("forwards offset, status and owner_id to the bounded server list", async () => {
    apiFetch.mockResolvedValueOnce({
      items: [],
      count: 0,
      total_count: 0,
      page: { limit: 25, offset: 50, returned: 0, next_offset: null, has_more: false },
    });

    const response = await listEvents("token", {
      limit: 25,
      offset: 50,
      status: "planning",
      owner_id: "17",
    });

    expect(response.page?.offset).toBe(50);
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/events?limit=25&offset=50&status=planning&owner_id=17",
      {},
      "token",
    );
  });

  it("binds upcoming reads to an explicit UTC as_of_date", async () => {
    apiFetch.mockResolvedValueOnce({ items: [], count: 0 });

    await listUpcomingEvents("token", 200, "2026-07-16");

    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/events/upcoming?limit=200&as_of_date=2026-07-16",
      {},
      "token",
    );
  });
});
