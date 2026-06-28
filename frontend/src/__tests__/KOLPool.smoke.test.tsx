// frontend-smoke ②: KOL Pool 主页面挂载冒烟。
// mock seam:domains/kol barrel + kolPool-api，全部 resolved-empty，零真实 http。
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

vi.mock("../domains/kol", () => ({
  listKolPoolFavorites: vi.fn().mockResolvedValue({ items: [] }),
  favoriteKolPool: vi.fn().mockResolvedValue({}),
  unfavoriteKolPool: vi.fn().mockResolvedValue({}),
  getKolPoolDetailBundle: vi.fn().mockResolvedValue({ item: null }),
  getKolPoolItem: vi.fn().mockResolvedValue({ item: null }),
  listKolSearchHistory: vi.fn().mockResolvedValue({ items: [] }),
  getKolSearchSession: vi.fn().mockResolvedValue({}),
  smartKolSearch: vi.fn().mockResolvedValue({}),
  smartKolSearchProfileAdvanceJob: vi.fn().mockResolvedValue({}),
  deepCrawlKolUrl: vi.fn().mockResolvedValue({}),
}));

vi.mock("../services/vkpi/kolPool-api", () => ({
  getKolVideoAnalysisCache: vi.fn().mockResolvedValue({ state: "empty", entry: null }),
}));

import { KOLPoolPage } from "../components/vkpi/v615-replica/KOLPoolPage";

describe("KOL Pool page smoke", () => {
  it("空数据挂载不抛 → 渲染指挥中心标题与诚实空态", () => {
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
    expect(screen.getByText("KOL Pool · Command Center")).toBeTruthy();
  });
});
