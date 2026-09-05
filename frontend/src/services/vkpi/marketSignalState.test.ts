import { describe, expect, it } from "vitest";
import { normalizeMarketBrainSummary } from "./gtmCommand-api";
import { completeSignalCount, marketSignalReadState } from "./marketSignalState";

describe("market signal read coverage", () => {
  it.each([
    ["ok", false, "empty"],
    ["ready", true, "ok"],
    ["empty", false, "empty"],
    ["empty", true, "partial"],
    ["error", false, "error"],
    ["partial", true, "partial"],
    ["partial", false, "partial"],
    ["stale", true, "partial"],
    ["unavailable", false, "error"],
    ["", false, "unknown"],
    ["new_state", true, "unknown"],
  ])("%s / items=%s => %s", (status, hasItems, expected) => {
    const section = { status: String(status), items: hasItems ? [{}] : [] };
    expect(marketSignalReadState(section)).toBe(expected);
    expect(completeSignalCount(section)).toBe(["ok", "empty"].includes(String(expected)) ? section.items.length : null);
  });

  it("retains only known source read states, without exception payloads", () => {
    const section = normalizeMarketBrainSummary({ weekly_signals: {
      status: "ok", items: [{ signal: "fixture" }],
      sources: {
        brand_pulse: { status: "error", reason: "internal fixture detail" },
        category_tracks: { status: "ready" }, market_voice: { status: "empty" },
        other_source: { status: "ready" },
      },
    } }).weekly_signals;
    expect(section.sources).toEqual({ brand_pulse: { status: "error" }, category_tracks: { status: "ready" }, market_voice: { status: "empty" } });
    expect(marketSignalReadState(section)).toBe("partial");
    expect(completeSignalCount(section)).toBeNull();
  });

  it("catches the legacy empty response with all sources failed", () => {
    const section = normalizeMarketBrainSummary({ weekly_signals: {
      status: "empty", sources: {
        brand_pulse: { status: "error" }, category_tracks: { status: "error" }, market_voice: { status: "error" },
      },
    } }).weekly_signals;
    expect(marketSignalReadState(section)).toBe("error");
    expect(completeSignalCount(section)).toBeNull();
  });

  it("does not infer completeness from malformed or missing expected states", () => {
    const section = normalizeMarketBrainSummary({ weekly_signals: {
      status: "empty", sources: { brand_pulse: null, market_voice: { status: "empty" } },
    } }).weekly_signals;
    expect(marketSignalReadState(section)).toBe("partial");
  });

  it("keeps a genuinely empty completed window at zero", () => {
    const section = normalizeMarketBrainSummary({ weekly_signals: {
      status: "empty", sources: {
        brand_pulse: { status: "no_data_in_window" }, category_tracks: { status: "empty" }, market_voice: { status: "empty" },
      },
    } }).weekly_signals;
    expect(marketSignalReadState(section)).toBe("empty");
    expect(completeSignalCount(section)).toBe(0);
  });
});
