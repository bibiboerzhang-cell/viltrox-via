// frontend-smoke ②: Dashboard 主页面挂载冒烟。
// 只验「能渲染不抛」。mock seam:framer-motion / RealMap(leaflet) /
// http.apiFetch / actionInbox-api —— 与既有 DashboardReplicaPage.test 同源,
// 但本文件独立放在 src/__tests__/,不触碰他人文件。
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

vi.mock("framer-motion", () => ({
  motion: new Proxy(
    {},
    {
      get: () => {
        const R = require("react");
        return R.forwardRef((props: Record<string, unknown>, _ref: unknown) =>
          R.createElement("div", props, props.children as React.ReactNode),
        );
      },
    },
  ),
  AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("../components/vkpi/v615-replica/components/RealMap", () => ({
  RealMap: () => null,
}));

const apiFetch = vi.fn();
vi.mock("../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
}));

const listActionInbox = vi.fn();
vi.mock("../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...a: unknown[]) => listActionInbox(...a),
  approveAction: vi.fn(),
  dismissAction: vi.fn(),
  snoozeAction: vi.fn(),
}));

import { DashboardReplicaPage } from "../components/vkpi/v615-replica/DashboardReplicaPage";

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({});
  listActionInbox.mockReset().mockResolvedValue({ items: [], available: false });
});

// 最小但完整的 props bag（无默认值字段不补会崩：breadcrumb.length / topListData.items）。
const baseProps = {
  t: (s: string) => s,
  kpiScope: "all",
  setKpiScope: () => {},
  setSelectedKpi: () => {},
  apiToken: "",
  breadcrumb: [],
  goBack: () => {},
  topListData: { title: "", items: [] },
  revenueBySource: [],
  currentMode: { color: "#fff", isEvents: false },
  globeContainerRef: { current: null },
  isAvailable: false,
  viewMode: null,
  setViewMode: () => {},
  countryOptions: [],
  cityOptions: [],
  itemOptions: [],
  venueOptions: [],
  metrics: [],
  campaigns: [],
  campaignsMeta: {},
  calendarDays: [],
  calendarMeta: {},
  signals: [],
  topMovers: [],
  kolFunnel: null,
  upcomingEvents: [],
};

describe("Dashboard page smoke", () => {
  it("挂载不抛 → 渲染出 Performance Overview，且无错误边界文案", async () => {
    expect(() =>
      render(React.createElement(DashboardReplicaPage, baseProps as any)),
    ).not.toThrow();
    expect(await screen.findByText("Performance Overview")).toBeTruthy();
    expect(screen.queryByText(/出错了|Something went wrong/)).toBeNull();
  });
});
