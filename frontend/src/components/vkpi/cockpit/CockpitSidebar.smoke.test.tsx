import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// 控制板块可见性的桩(vi.hoisted 以便在 vi.mock 工厂里引用)。
const { canViewBoard } = vi.hoisted(() => ({ canViewBoard: vi.fn() }));
vi.mock("../../../hooks/usePermissions", () => ({
  usePermissions: () => ({ canViewBoard }),
}));
// TaskProgressBoard 会拉任务队列,渲染冒烟里桩成空。
vi.mock("./components/TaskProgressBoard", () => ({ TaskProgressBoard: () => null }));

import { CockpitSidebar } from "./CockpitSidebar";
import { I18nContext, makeT } from "./lib/i18n";
import { I18N_EN } from "./data/i18nEn";

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

    expect(screen.getByRole("button", { name: "仪表盘" })).toBeTruthy();
    expect(screen.getByText("经销商")).toBeTruthy();
    expect(screen.getByText("KOL 人才库")).toBeTruthy();
    // 被设「无」的两块:不在 DOM
    expect(screen.queryByText("Shopify")).toBeNull();
    expect(screen.queryByText("项目")).toBeNull();
  });

  it("owner / 全可见 → 所有主导航板块都在", () => {
    canViewBoard.mockReturnValue(true);
    render(React.createElement(CockpitSidebar, baseProps));

    expect(screen.getByText("Shopify")).toBeTruthy();
    expect(screen.getByText("项目")).toBeTruthy();
    expect(screen.getByRole("button", { name: "仪表盘" })).toBeTruthy();
  });

  it("英文模式保留英文导航", () => {
    canViewBoard.mockReturnValue(true);
    render(
      <I18nContext.Provider value={{ t: makeT("en", I18N_EN), lang: "en", setLang: vi.fn() }}>
        <CockpitSidebar {...baseProps} />
      </I18nContext.Provider>,
    );

    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByText("Projects")).toBeTruthy();
    expect(screen.getByText("Dealers")).toBeTruthy();
    expect(screen.getByText("KOL Pool")).toBeTruthy();
  });

  it("收起控件固定在侧栏边缘且不再重复主题开关", () => {
    canViewBoard.mockReturnValue(true);
    const setCollapsed = vi.fn();
    render(React.createElement(CockpitSidebar, { ...baseProps, setCollapsed }));

    fireEvent.click(screen.getByRole("button", { name: "收起侧栏" }));
    expect(setCollapsed).toHaveBeenCalledWith(true);
    expect(screen.queryByText("Collapse")).toBeNull();
    expect(screen.queryByText("Dark")).toBeNull();
  });
});
