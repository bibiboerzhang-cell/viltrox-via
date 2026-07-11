import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// P2-7 render smoke(jsdom):DashboardReplicaPage 顶层展示页。
// 该页纯展示但 props 无完整默认 → 传完整 props bag(否则 breadcrumb.length / topListData.items 崩)。
// mock seam:framer-motion(含 AnimatePresence)、RealMap(→null,隔离 leaflet)、
// services/http(apiFetch,SystemHealthBar 用)、actionInbox-api(listActionInbox,ActionInboxPanel 用)。

vi.mock("framer-motion", () => {
  // forwardRef stub:吞掉 motion 组件传入的 ref,消除良性 ref 警告。
  const motionProxy = new Proxy(
    {},
    {
      get: () => {
        const React = require("react");
        return React.forwardRef((props: Record<string, unknown>, _ref: unknown) =>
          React.createElement("div", props, props.children as React.ReactNode),
        );
      },
    },
  );
  // LazyMotion 迁移:代码用 m.*(等价 motion.*)+ LazyMotion/domMax,mock 全部补齐。
  return {
    motion: motionProxy,
    m: motionProxy,
    LazyMotion: ({ children }: { children: React.ReactNode }) => children,
    domMax: {},
    domAnimation: {},
    AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
  };
});

vi.mock("./components/RealMap", () => ({ RealMap: () => null }));

const apiFetch = vi.fn();
vi.mock("../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
  buildApiUrl: (p: string) => p,
}));

const listActionInbox = vi.fn();
vi.mock("../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...a: unknown[]) => listActionInbox(...a),
  approveAction: vi.fn(),
  dismissAction: vi.fn(),
  snoozeAction: vi.fn(),
}));

import { DashboardReplicaPage } from "./DashboardReplicaPage";

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({});
  listActionInbox.mockReset().mockResolvedValue({ items: [], available: false });
});

// 最小但完整的 props bag(避免无默认值字段崩)。
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

describe("DashboardReplicaPage render smoke", () => {
  it("挂载不抛错 → 绩效总览标题在", async () => {
    render(<DashboardReplicaPage {...baseProps} />);
    expect(await screen.findByText("绩效总览")).toBeInTheDocument();
  });

  it("无 error boundary 文案", async () => {
    render(<DashboardReplicaPage {...baseProps} />);
    await screen.findByText("绩效总览");
    expect(screen.queryByText(/出错了|Something went wrong/)).not.toBeInTheDocument();
  });

  it("dashboardLoading=true → 标题仍在,副标题显示读取中", async () => {
    render(<DashboardReplicaPage {...baseProps} dashboardLoading={true} />);
    expect(await screen.findByText("绩效总览")).toBeInTheDocument();
    expect(screen.getByText("读取中")).toBeInTheDocument();
  });

  it("dashboardError 非空 → 副标题显示无信号", async () => {
    render(<DashboardReplicaPage {...baseProps} dashboardError="boom" />);
    await screen.findByText("绩效总览");
    expect(screen.getByRole("status", { name: "KPI data status" })).toHaveTextContent("无信号");
  });

  it("展示接口在线数与待接能力，不把空值伪装成全部接入", async () => {
    render(<DashboardReplicaPage {...baseProps} sourceHealth={{
      available: true,
      label: "9/10 可用 · 2 待接",
      detail: "异常: 竞品雷达；待接能力: GMV、ROI",
      degraded: true,
    }} />);

    const status = await screen.findByRole("status", { name: "KPI data status" });
    expect(status).toHaveTextContent("9/10 可用 · 2 待接");
    expect(status).toHaveAttribute("title", "异常: 竞品雷达；待接能力: GMV、ROI");
  });
});
