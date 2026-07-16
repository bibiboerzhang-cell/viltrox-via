import { describe, expect, it } from "vitest";

import {
  buildEventPins,
  buildMappedEvents,
  buildUpcomingEvents,
  filterUpcomingEvents,
} from "./CockpitApp.helpers";

describe("Dashboard upcoming Event truth boundary", () => {
  const asOfDate = "2026-07-16";

  it("uses end_date, keeps ongoing rows, and excludes closed or expired rows", () => {
    const mapped = buildMappedEvents([
      { id: "ongoing", title: "Ongoing", start_date: "2026-07-01", end_date: "2026-07-16", status: "active" },
      { id: "future", title: "Future", start_date: "2026-07-20", end_date: "2026-07-21", status: "planning" },
      { id: "expired", title: "Expired", start_date: "2026-07-01", end_date: "2026-07-15", status: "planning" },
      { id: "cancelled", title: "Cancelled", start_date: "2026-07-20", end_date: "2026-07-21", status: "cancelled" },
      { id: "closed", title: "Closed", start_date: "2026-07-20", end_date: "2026-07-21", status: "closed" },
      { id: "missing-end", title: "Missing end", start_date: "2026-07-20", status: "planning" },
    ]);

    expect(filterUpcomingEvents(mapped, asOfDate).map((event) => event.id)).toEqual(["ongoing", "future"]);
    expect(buildUpcomingEvents(mapped, asOfDate).map((event) => event.id)).toEqual(["ongoing", "future"]);
  });

  it("creates point pins only from explicit finite coordinates", () => {
    const mapped = filterUpcomingEvents(buildMappedEvents([
      {
        id: "exact",
        title: "Exact",
        start_date: "2026-07-20",
        end_date: "2026-07-21",
        status: "planning",
        location_country: "US",
        location_lat: 40.75,
        location_lng: -73.99,
      },
      {
        id: "country-only",
        title: "Country aggregate only",
        start_date: "2026-07-20",
        end_date: "2026-07-21",
        status: "planning",
        location_country: "US",
      },
    ]), asOfDate);

    expect(buildEventPins(mapped).map((pin) => pin.id)).toEqual(["exact"]);
  });
});
