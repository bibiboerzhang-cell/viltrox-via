import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

// 控制板块可见性的桩(vi.hoisted 以便在 vi.mock 工厂里引用)。
const { canViewBoard } = vi.hoisted(() => ({ canViewBoard: vi.fn() }));
vi.mock("../../../hooks/usePermissions", () => ({
  usePermissions: () => ({ canViewBoard }),
}));

import { CockpitMobileNav } from "./CockpitMobileNav";

describe("CockpitMobileNav 移动端导航", () => {
  beforeEach(() => canViewBoard.mockReset());

  it("汉堡按钮存在;owner 全可见时主导航项都在;点选调 setActiveNav(同一套 key)", () => {
    canViewBoard.mockReturnValue(true);
    const setActiveNav = vi.fn();
    render(React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav }));

    // 汉堡入口在(修复「移动端无导航入口」的核心)。
    expect(screen.getByLabelText("打开导航菜单")).toBeTruthy();
    // 抽屉复用侧边栏同一套主导航项。
    expect(screen.getByText("KOL Pool")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    // 点导航项 → 切页用与侧边栏一致的 board key。
    fireEvent.click(screen.getByText("KOL Pool"));
    expect(setActiveNav).toHaveBeenCalledWith("kol-pool");
  });

  it("成员把某板块设为「无」→ 抽屉不渲染该板块", () => {
    canViewBoard.mockImplementation((key: string) => key !== "shopify");
    render(
      React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav: vi.fn() }),
    );
    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.queryByText("Shopify")).toBeNull();
  });

  it("点汉堡切换 aria-expanded(关→开)", () => {
    canViewBoard.mockReturnValue(true);
    render(
      React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav: vi.fn() }),
    );
    const burger = screen.getByLabelText("打开导航菜单");
    expect(burger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(burger);
    expect(burger.getAttribute("aria-expanded")).toBe("true");
  });
});
