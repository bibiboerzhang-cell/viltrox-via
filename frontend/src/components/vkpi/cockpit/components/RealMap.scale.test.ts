import { describe, expect, it } from "vitest";

import {
  LOW_ZOOM_CLUSTER_MAX,
  MAX_DOM_MARKER_PIN_COUNT,
  MAX_NETWORK_LINE_PIN_COUNT,
  NETWORK_LINE_MIN_ZOOM,
  clusterClickAction,
  clusterPinsForZoom,
  escapeMapHtml,
  nearestPinEdges,
  safeMapColor,
  shouldShowNetworkLines,
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

describe("RealMap deterministic display clusters", () => {
  const project = (pin: any) => ({ x: Number(pin.lng) * 10, y: Number(pin.lat) * 10 });

  it("groups nearby projected pins at low zoom and separates them after the threshold", () => {
    const pins = [
      // Projected x=35 and x=37 straddle a 36px grid boundary.  A plain
      // bucket-only algorithm would leave these visually overlapping.
      { id: "a", lat: 1, lng: 3.5 },
      { id: "b", lat: 1.2, lng: 3.7 },
    ];

    const lowZoom = clusterPinsForZoom(pins, project, LOW_ZOOM_CLUSTER_MAX);
    const highZoom = clusterPinsForZoom(pins, project, LOW_ZOOM_CLUSTER_MAX + 1);

    expect(lowZoom).toHaveLength(1);
    expect(lowZoom[0]).toMatchObject({ count: 2, exactCoordinates: false });
    expect(clusterClickAction(lowZoom[0])).toBe("zoom");
    expect(highZoom).toHaveLength(2);
  });

  it("retains every exact-coordinate member at high zoom for member selection", () => {
    const pins = [
      { id: "bh", name: "B&H", lat: 40.7128, lng: -74.006 },
      { id: "adorama", name: "Adorama", lat: 40.7128, lng: -74.006 },
    ];

    const [cluster] = clusterPinsForZoom(pins, project, 18);

    expect(cluster).toMatchObject({ count: 2, exactCoordinates: true });
    expect(cluster.members.map((member) => member.id)).toEqual(["adorama", "bh"]);
    expect(clusterClickAction(cluster)).toBe("members");
  });

  it("is deterministic under input reorder and never mutates source coordinates", () => {
    const pins = [
      { id: "b", lat: 1.2, lng: 1.2 },
      { id: "a", lat: 1, lng: 1 },
      { id: "c", lat: 9, lng: 9 },
    ];
    const before = structuredClone(pins);
    const forward = clusterPinsForZoom(pins, project, 2);
    const reversed = clusterPinsForZoom(pins.slice().reverse(), project, 2);

    const signature = (clusters: typeof forward) => clusters.map((cluster) => ({
      key: cluster.key,
      count: cluster.count,
      members: cluster.members.map((member) => member.id),
    }));

    expect(signature(forward)).toEqual(signature(reversed));
    expect(pins).toEqual(before);
    expect(forward.reduce((sum, cluster) => sum + cluster.count, 0)).toBe(pins.length);
  });

  it("suppresses decorative network lines at overview zoom", () => {
    const pins = [
      { lat: 34.05, lng: -118.24 },
      { lat: 40.71, lng: -74 },
    ];

    expect(shouldShowNetworkLines(NETWORK_LINE_MIN_ZOOM - 1, pins)).toBe(false);
    expect(shouldShowNetworkLines(NETWORK_LINE_MIN_ZOOM, pins)).toBe(true);
  });
});
