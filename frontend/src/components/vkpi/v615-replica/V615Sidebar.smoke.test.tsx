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

import { V615Sidebar } from "./V615Sidebar";

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

describe("V615Sidebar 员工可见区域过滤", () => {
  beforeEach(() => canViewBoard.mockReset());

  it("成员把某板块设为「无」→ 侧栏不渲染该板块,其余照常", () => {
    canViewBoard.mockImplementation((key: string) => key !== "shopify" && key !== "agents");
    render(React.createElement(V615Sidebar, baseProps));

    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByText("Dealers")).toBeTruthy();
    expect(screen.getByText("KOL Pool")).toBeTruthy();
    // 被设「无」的两块:不在 DOM
    expect(screen.queryByText("Shopify")).toBeNull();
    expect(screen.queryByText("Agents")).toBeNull();
  });

  it("owner / 全可见 → 所有板块都在", () => {
    canViewBoard.mockReturnValue(true);
    render(React.createElement(V615Sidebar, baseProps));

    expect(screen.getByText("Shopify")).toBeTruthy();
    expect(screen.getByText("Agents")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
  });
});
