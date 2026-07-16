import { describe, expect, it } from "vitest";

import { eventTiming, percentOf } from "./helpers";

describe("Events truth-safe display helpers", () => {
  const now = new Date(2026, 6, 13, 12, 0, 0);

  it("uses both dates so an expired draft is not presented as still running", () => {
    expect(eventTiming("2000-01-01", "2000-01-02", now)).toEqual({
      phase: "ended",
      days: 9689,
      label: "已结束 9689 天",
    });
  });

  it("distinguishes upcoming, opening-day, ongoing, and invalid ranges", () => {
    expect(eventTiming("2026-07-20", "2026-07-21", now)).toMatchObject({ phase: "upcoming", days: 7 });
    expect(eventTiming("2026-07-13", "2026-07-14", now)).toMatchObject({ phase: "starts_today", days: 0 });
    expect(eventTiming("2026-07-12", "2026-07-14", now)).toMatchObject({ phase: "ongoing", days: 2 });
    expect(eventTiming("2026-07-15", "2026-07-14", now)).toEqual({ phase: "invalid", days: 0, label: "日期待确认" });
  });

  it("returns unknown instead of NaN or Infinity when no budget is set", () => {
    expect(percentOf(0, 0)).toBeNull();
    expect(percentOf(50, 0)).toBeNull();
    expect(percentOf(Number.NaN, 100)).toBeNull();
    expect(percentOf(20, 100)).toBe(20);
  });
});
