import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// RealMap 用 Leaflet,jsdom 里无地图运行时 → 桩成占位 div,避免假崩。
vi.mock("../cockpit/components/RealMap", () => ({
  RealMap: () => React.createElement("div", { "data-testid": "real-map-stub" }),
}));

// 经销商网络/抓取端点全部桩掉(apiToken="t" 时 effect 会调 → 给空形 resolved)。
vi.mock("../../../services/vkpi/dealers-api", () => ({
  getDealerLocations: vi.fn().mockResolvedValue({ pins: [] }),
  listDealers: vi.fn().mockResolvedValue({ dealers: [] }),
  scrapeDealersEnqueue: vi.fn().mockResolvedValue({ record_only: true }),
}));

import { DealerMapPage } from "./DealerMapPage";

describe("DealerMapPage smoke", () => {
  it("挂载不抛异常,并渲染出该页真实文案", () => {
    expect(() =>
      render(React.createElement(DealerMapPage, { apiToken: "t" })),
    ).not.toThrow();

    // 页头标题 + 右侧待补 geocode 段是必现真实元素。
    expect(screen.getByText("经销商地图 · Dealer Map")).toBeTruthy();
    // "待补 geocode" 在副标题和右栏标签各出现一次 → 用 getAllByText。
    expect(screen.getAllByText(/待补 geocode/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("real-map-stub")).toBeTruthy();
  });
});
