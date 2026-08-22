import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

// 控制板块可见性的桩(vi.hoisted 以便在 vi.mock 工厂里引用)。
const { canViewBoard } = vi.hoisted(() => ({ canViewBoard: vi.fn() }));
vi.mock("../../../hooks/usePermissions", () => ({
  usePermissions: () => ({ canViewBoard }),
}));

import { CockpitMobileNav } from "./CockpitMobileNav";
import { I18nContext, makeT } from "./lib/i18n";
import { I18N_EN } from "./data/i18nEn";

describe("CockpitMobileNav 移动端导航", () => {
  beforeEach(() => canViewBoard.mockReset());

  it("汉堡按钮存在;owner 全可见时主导航项都在;点选调 setActiveNav(同一套 key)", () => {
    canViewBoard.mockReturnValue(true);
    const setActiveNav = vi.fn();
    render(React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav }));

    // 汉堡入口在(修复「移动端无导航入口」的核心)。
    expect(screen.getByLabelText("打开导航菜单")).toBeTruthy();
    // 抽屉复用侧边栏同一套主导航项。
    expect(screen.getByText("KOL 人才库")).toBeTruthy();
    expect(screen.getByText("项目")).toBeTruthy();
    // 点导航项 → 切页用与侧边栏一致的 board key。
    fireEvent.click(screen.getByText("KOL 人才库"));
    expect(setActiveNav).toHaveBeenCalledWith("kol-pool");
  });

  it("成员把某板块设为「无」→ 抽屉不渲染该板块", () => {
    canViewBoard.mockImplementation((key: string) => key !== "shopify");
    render(
      React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav: vi.fn() }),
    );
    expect(screen.getByText("仪表盘")).toBeTruthy();
    expect(screen.queryByText("Shopify")).toBeNull();
  });

  it("英文模式保留英文标签和无障碍名称", () => {
    canViewBoard.mockReturnValue(true);
    render(
      <I18nContext.Provider value={{ t: makeT("en", I18N_EN), lang: "en", setLang: vi.fn() }}>
        <CockpitMobileNav activeNav="dashboard" setActiveNav={vi.fn()} />
      </I18nContext.Provider>,
    );

    expect(screen.getByLabelText("Open navigation menu")).toBeTruthy();
    expect(screen.getByLabelText("Primary navigation")).toBeTruthy();
    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
  });

  it("点汉堡切换 aria-expanded(关→开)", () => {
    canViewBoard.mockReturnValue(true);
    render(
      React.createElement(CockpitMobileNav, { activeNav: "dashboard", setActiveNav: vi.fn() }),
    );
    const burger = screen.getByLabelText("打开导航菜单");
    const drawer = screen.getByLabelText("主导航");
    expect(burger.getAttribute("aria-expanded")).toBe("false");
    expect(drawer).toHaveStyle({ transform: "translateX(-100%)" });
    expect(screen.getByAltText("Viltrox")).toBeTruthy();
    fireEvent.click(burger);
    expect(burger.getAttribute("aria-expanded")).toBe("true");
    expect(drawer).toHaveStyle({ transform: "translateX(0)" });
  });
});
