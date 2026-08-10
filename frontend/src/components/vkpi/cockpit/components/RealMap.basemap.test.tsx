import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const leaflet = vi.hoisted(() => ({
  map: vi.fn(),
  tileLayer: vi.fn(),
  geoJSON: vi.fn(),
  mapApi: null as any,
  localLayer: null as any,
  lastTileLayer: null as any,
  lastTileError: null as null | (() => void),
}));
const themeState = vi.hoisted(() => ({ value: "light" as "light" | "dark" }));

vi.mock("leaflet", () => ({
  default: {
    map: leaflet.map,
    tileLayer: leaflet.tileLayer,
    geoJSON: leaflet.geoJSON,
  },
}));

vi.mock("../../../../app/providers/ThemeProvider", () => ({
  useTheme: () => ({ theme: themeState.value }),
}));

import {
  DEFAULT_REAL_MAP_BASEMAP_MODE,
  LOCAL_WORLD_TOPOLOGY_URL,
  RealMap,
  basemapModeAfterTileError,
  cartoTileUrl,
  shouldRequestExternalBasemap,
} from "./RealMap";

const EMPTY_WORLD_TOPOLOGY = {
  type: "Topology",
  objects: {
    countries: {
      type: "GeometryCollection",
      geometries: [],
    },
  },
  arcs: [],
};

describe("RealMap basemap policy", () => {
  beforeEach(() => {
    themeState.value = "light";
    const activeLayers = new Set<any>();
    const mapApi: any = {};
    mapApi.setView = vi.fn(() => mapApi);
    mapApi.invalidateSize = vi.fn();
    mapApi.remove = vi.fn(() => activeLayers.clear());
    mapApi.removeLayer = vi.fn((layer: any) => {
      activeLayers.delete(layer);
      return mapApi;
    });
    mapApi.hasLayer = vi.fn((layer: any) => activeLayers.has(layer));
    mapApi.getZoom = vi.fn(() => 4);
    mapApi.on = vi.fn(() => mapApi);
    mapApi.off = vi.fn(() => mapApi);

    const localLayer: any = {};
    localLayer.addTo = vi.fn(() => {
      activeLayers.add(localLayer);
      return localLayer;
    });
    localLayer.setStyle = vi.fn(() => localLayer);

    leaflet.mapApi = mapApi;
    leaflet.localLayer = localLayer;
    leaflet.lastTileLayer = null;
    leaflet.lastTileError = null;
    leaflet.map.mockReset().mockReturnValue(mapApi);
    leaflet.geoJSON.mockReset().mockReturnValue(localLayer);
    leaflet.tileLayer.mockReset().mockImplementation(() => {
      const listeners = new Map<string, () => void>();
      const layer: any = {};
      layer.addTo = vi.fn(() => {
        activeLayers.add(layer);
        return layer;
      });
      layer.on = vi.fn((event: string, listener: () => void) => {
        listeners.set(event, listener);
        if (event === "tileerror") leaflet.lastTileError = listener;
        return layer;
      });
      layer.off = vi.fn((event: string, listener: () => void) => {
        if (listeners.get(event) === listener) listeners.delete(event);
        return layer;
      });
      leaflet.lastTileLayer = layer;
      return layer;
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue(EMPTY_WORLD_TOPOLOGY),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("loads the same-origin world geometry by default without creating a CARTO tile layer", async () => {
    render(<RealMap pins={[]} defaultZoom={4} />);

    await waitFor(() => expect(leaflet.geoJSON).toHaveBeenCalledTimes(1));

    expect(fetch).toHaveBeenCalledWith(
      LOCAL_WORLD_TOPOLOGY_URL,
      expect.objectContaining({ cache: "force-cache", signal: expect.any(AbortSignal) }),
    );
    expect(DEFAULT_REAL_MAP_BASEMAP_MODE).toBe("local");
    expect(shouldRequestExternalBasemap(DEFAULT_REAL_MAP_BASEMAP_MODE)).toBe(false);
    expect(leaflet.geoJSON).toHaveBeenCalledWith(
      expect.objectContaining({ type: "FeatureCollection", features: [] }),
      expect.objectContaining({ interactive: false, pane: "tilePane" }),
    );
    expect(leaflet.tileLayer).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "在线街道底图" })).toHaveAttribute("aria-pressed", "false");
  });

  it("creates the reviewed CARTO URL only after an explicit click and falls back on tileerror", async () => {
    render(<RealMap pins={[]} defaultZoom={4} />);
    await waitFor(() => expect(leaflet.geoJSON).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "在线街道底图" }));

    await waitFor(() => expect(leaflet.tileLayer).toHaveBeenCalledTimes(1));
    expect(leaflet.tileLayer).toHaveBeenCalledWith(
      cartoTileUrl("light"),
      expect.objectContaining({ attribution: "© CARTO", subdomains: "abcd", maxZoom: 19 }),
    );
    expect(shouldRequestExternalBasemap("online")).toBe(true);
    expect(basemapModeAfterTileError("online")).toBe("local");

    const failedLayer = leaflet.lastTileLayer;
    const tileError = leaflet.lastTileError;
    expect(tileError).toBeTypeOf("function");
    act(() => tileError?.());

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "在线街道底图" })).toHaveAttribute("aria-pressed", "false");
    });
    expect(leaflet.mapApi.removeLayer).toHaveBeenCalledWith(failedLayer);
    expect(leaflet.localLayer.addTo).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent(
      "在线街道底图加载失败，已回退到本地世界底图；真实点位与筛选不受影响。",
    );
    expect(leaflet.tileLayer).toHaveBeenCalledTimes(1);
  });

  it("replaces an enabled online layer when the theme changes and ignores its stale error handler", async () => {
    const view = render(<RealMap pins={[]} defaultZoom={4} />);
    await waitFor(() => expect(leaflet.geoJSON).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "在线街道底图" }));
    await waitFor(() => expect(leaflet.tileLayer).toHaveBeenCalledTimes(1));

    const lightLayer = leaflet.lastTileLayer;
    const staleLightError = leaflet.lastTileError;
    themeState.value = "dark";
    view.rerender(<RealMap pins={[]} defaultZoom={4} />);

    await waitFor(() => expect(leaflet.tileLayer).toHaveBeenCalledTimes(2));
    expect(leaflet.tileLayer).toHaveBeenLastCalledWith(
      cartoTileUrl("dark"),
      expect.objectContaining({ attribution: "© CARTO" }),
    );
    expect(lightLayer.off).toHaveBeenCalledWith("tileerror", staleLightError);
    expect(leaflet.mapApi.removeLayer).toHaveBeenCalledWith(lightLayer);

    act(() => staleLightError?.());
    expect(screen.getByRole("button", { name: "本地世界底图" })).toHaveAttribute("aria-pressed", "true");
    expect(leaflet.tileLayer).toHaveBeenCalledTimes(2);
  });

  it("aborts the same-origin world fetch when the map unmounts", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.mocked(fetch).mockImplementation((_url, options) => {
      requestSignal = options?.signal as AbortSignal;
      return new Promise<Response>(() => {});
    });

    const view = render(<RealMap pins={[]} defaultZoom={4} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(requestSignal?.aborted).toBe(false);

    view.unmount();

    expect(requestSignal?.aborted).toBe(true);
    expect(leaflet.mapApi.remove).toHaveBeenCalledTimes(1);
    expect(leaflet.tileLayer).not.toHaveBeenCalled();
  });
});
