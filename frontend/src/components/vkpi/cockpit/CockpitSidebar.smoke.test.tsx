import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// 控制板块可见性的桩(vi.hoisted 以便在 vi.mock 工厂里引用)。
const { canViewBoard } = vi.hoisted(() => ({ canViewBoard: vi.fn() }));
vi.mock("../../../hooks/usePermissions", () => ({
  usePermissions: () => ({ canViewBoard }),
}));
// TaskProgressBoard 会拉任务队列,渲染冒烟里桩成空。
vi.mock("./components/TaskProgressBoard", () => ({ TaskProgressBoard: () => null }));

import { CockpitSidebar } from "./CockpitSidebar";

const baseProps = {
  collapsed: false,
  setCollapsed: () => {},
  activeNav: "dashboard",
  setActiveNav: () => {},
  theme: "dark",
  setTheme: () => {},
  versionBadge: null,
  apiToken: "t",
};

describe("CockpitSidebar 员工可见区域过滤", () => {
  beforeEach(() => canViewBoard.mockReset());

  // 注:Agents/Reports 等 v2:true 项被收进默认折叠的「V2」组,缺省态不在 DOM。
  // 冒烟只断言缺省可见的主导航(primaryItems,非 v2)板块,避免误判 V2 折叠。
  it("成员把某板块设为「无」→ 侧栏不渲染该板块,其余照常", () => {
    canViewBoard.mockImplementation((key: string) => key !== "shopify" && key !== "projects");
    render(React.createElement(CockpitSidebar, baseProps));

    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByText("Dealers")).toBeTruthy();
    expect(screen.getByText("KOL Pool")).toBeTruthy();
    // 被设「无」的两块:不在 DOM
    expect(screen.queryByText("Shopify")).toBeNull();
    expect(screen.queryByText("Projects")).toBeNull();
  });

  it("owner / 全可见 → 所有主导航板块都在", () => {
    canViewBoard.mockReturnValue(true);
    render(React.createElement(CockpitSidebar, baseProps));

    expect(screen.getByText("Shopify")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
  });
});
