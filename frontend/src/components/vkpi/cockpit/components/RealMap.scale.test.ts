import { describe, expect, it } from "vitest";

import {
  MAX_DOM_MARKER_PIN_COUNT,
  MAX_NETWORK_LINE_PIN_COUNT,
  escapeMapHtml,
  nearestPinEdges,
  safeMapColor,
  shouldUseCanvasMarkers,
} from "./RealMap";

describe("RealMap large pin sets", () => {
  it("keeps nearest-neighbour lines for bounded maps", () => {
    expect(
      nearestPinEdges([
        { lat: 34.05, lng: -118.24 },
        { lat: 40.71, lng: -74.0 },
      ]),
    ).toHaveLength(1);
  });

  it("skips decorative O(n squared) lines before an all-US map can freeze", () => {
    const pins = Array.from({ length: MAX_NETWORK_LINE_PIN_COUNT + 1 }, (_, index) => ({
      lat: 25 + index * 0.01,
      lng: -124 + index * 0.01,
    }));

    expect(nearestPinEdges(pins)).toEqual([]);
  });

  it("switches national-scale marker sets away from thousands of DOM nodes", () => {
    const pins = Array.from({ length: MAX_DOM_MARKER_PIN_COUNT + 1 }, (_, index) => ({
      lat: 25 + index * 0.001,
      lng: -124 + index * 0.001,
    }));

    expect(shouldUseCanvasMarkers(pins)).toBe(true);
    expect(shouldUseCanvasMarkers(pins.slice(0, MAX_DOM_MARKER_PIN_COUNT))).toBe(false);
  });

  it("does not trust externally collected labels or colors as tooltip markup", () => {
    expect(escapeMapHtml('<img src=x onerror="alert(1)">')).toBe(
      "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
    );
    expect(safeMapColor("red; background:url(javascript:alert(1))")).toBe("#a855f7");
    expect(safeMapColor("rgb(16 185 129 / 82%)")).toBe("rgb(16 185 129 / 82%)");
  });
});
