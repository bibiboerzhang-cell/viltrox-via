import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// Mock the KOL domain barrel that both KOLPoolPage and its child
// SmartKolInputPanel import from. Every network call resolves to an
// empty-shaped payload so nothing throws on mount and no real HTTP fires.
vi.mock("../../../domains/kol", () => ({
  // Used directly by KOLPoolPage
  listKolPoolFavorites: vi.fn().mockResolvedValue({ items: [] }),
  favoriteKolPool: vi.fn().mockResolvedValue({}),
  unfavoriteKolPool: vi.fn().mockResolvedValue({}),
  getKolPoolDetailBundle: vi.fn().mockResolvedValue({ item: null }),
  getKolPoolItem: vi.fn().mockResolvedValue({ item: null }),
  // Used by SmartKolInputPanel (rendered unconditionally by the page)
  listKolSearchHistory: vi.fn().mockResolvedValue({ items: [] }),
  getKolSearchSession: vi.fn().mockResolvedValue({}),
  smartKolSearch: vi.fn().mockResolvedValue({}),
  smartKolSearchProfileAdvanceJob: vi.fn().mockResolvedValue({}),
  deepCrawlKolUrl: vi.fn().mockResolvedValue({}),
}));

vi.mock("../../../services/vkpi/kolPool-api", () => ({
  getKolVideoAnalysisCache: vi.fn().mockResolvedValue({ state: "empty", entry: null }),
}));

import { KOLPoolPage } from "./KOLPoolPage";

describe("KOLPoolPage smoke", () => {
  it("mounts with the documented nav props without crashing and renders its real UI", () => {
    expect(() =>
      render(
        React.createElement(KOLPoolPage, {
          items: [],
          loading: false,
          error: null,
          apiToken: "t",
        }),
      ),
    ).not.toThrow();

    // Real, page-specific copy that must appear in the DOM.
    expect(screen.getByText("KOL Pool · Command Center")).toBeTruthy();
    // Empty-pool honest banner (items=[] → length 0 branch).
    expect(screen.getByText("暂无真实 KOL Pool 数据")).toBeTruthy();
  });
});
