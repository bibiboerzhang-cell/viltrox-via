// F1 · DashboardEditablePage 模块定义 useMemo 化:
//   CockpitApp 每次 modal/popover 状态变化都重渲染 Dashboard 并重传内联回调,此前每帧重建 ~40 个
//   模块定义 + render 闭包,EditableDashboardBoard 的 moduleMap/gridLayout/回调 memo 逐帧失效。
//   契约:① 数据/回调身份变化不重建模块数组;② render() 仍吃最新数据;③ 语言或板块可见性变化才重建。
import React from "react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const boardCalls: Array<{ modules: any[]; editing: boolean }> = [];

vi.mock("./components/EditableDashboardBoard", () => ({
  EditableDashboardBoard: (props: any) => {
    boardCalls.push({ modules: props.modules, editing: props.editing });
    return React.createElement("div", { "data-testid": "board" }, props.modules.length);
  },
}));
vi.mock("./components/MetricCard", () => ({
  MetricCard: ({ item }: any) => React.createElement("i", { "data-testid": "metric" }, item.id),
}));
vi.mock("./components/DashboardCommandCenter", () => ({ DashboardCommandCenter: () => null }));
vi.mock("./pages/crossBoardModules", () => ({
  CROSS_BOARD_SOURCES: [{ board: "projects", boardLabel: "Projects", entries: [] }],
  buildCrossBoardModules: (options: any) => (options.canViewBoard("projects")
    ? [{
      key: "xb-projects-probe", label: "探针", description: "Projects · 探针", category: "跨板块模块",
      sourceBoard: "projects", sourceLabel: "Projects", defaultSpan: 4,
      render: () => React.createElement("span", { "data-testid": "xb-page-props" }, JSON.stringify(options.pageProps.projects)),
    }]
    : []),
}));

import { DashboardEditablePage } from "./DashboardEditablePage";

function baseProps(overrides: Record<string, unknown> = {}) {
  return {
    apiToken: "token",
    kpiScope: "all",
    metrics: [{ id: "kol-count", data: { all: { value: 1200 } } }],
    signals: [],
    topMovers: [],
    campaigns: [],
    upcomingEvents: [],
    canViewBoard: (board: string) => board === "projects" || board === "my-kol",
    crossBoardPageProps: { projects: { selectedProjectId: 1 } },
    ...overrides,
  };
}

function renderModule(modules: any[], key: string) {
  const definition = modules.find((module) => module.key === key);
  expect(definition, `module ${key}`).toBeTruthy();
  return render(React.createElement(React.Fragment, null, definition.render()));
}

describe("DashboardEditablePage module definitions", () => {
  it("keeps the same modules array across re-renders that only change data or inline callbacks", () => {
    boardCalls.length = 0;
    const { rerender } = render(React.createElement(DashboardEditablePage, baseProps({ setSelectedKpi: () => undefined })));
    rerender(React.createElement(DashboardEditablePage, baseProps({
      setSelectedKpi: () => undefined,
      metrics: [{ id: "kol-count", data: { all: { value: 7 } } }, { id: "exposure", data: {} }],
      crossBoardPageProps: { projects: { selectedProjectId: 2 } },
      dashboardEditing: true,
    })));
    expect(boardCalls).toHaveLength(2);
    expect(boardCalls[1].modules).toBe(boardCalls[0].modules);
    expect(boardCalls[1].editing).toBe(true);
  });

  it("renders the latest data through stable render closures", () => {
    boardCalls.length = 0;
    const setSelectedKpi = vi.fn();
    const { rerender } = render(React.createElement(DashboardEditablePage, baseProps({ setSelectedKpi })));
    rerender(React.createElement(DashboardEditablePage, baseProps({
      setSelectedKpi,
      metrics: [{ id: "kol-count", data: { all: { value: 7 } } }, { id: "exposure", data: {} }],
      crossBoardPageProps: { projects: { selectedProjectId: 2 } },
    })));
    const modules = boardCalls[boardCalls.length - 1].modules;

    const kpi = renderModule(modules, "kpi");
    expect(kpi.getAllByTestId("metric").map((node) => node.textContent)).toEqual(["kol-count", "exposure"]);
    expect(kpi.container.querySelector(".vkpi-kpi-overview__count")?.textContent).toBe("2 指标");
    kpi.unmount();

    // zh 模式下 t() 把 "Projects" 译成「项目」;metric 为空时卡面显示「打开」。
    const projects = renderModule(modules, "board-projects");
    expect(projects.container.textContent).toContain("项目履约与复盘");
    projects.unmount();

    // 跨板块模块的 pageProps 走 live 视图:注册表拿到的是最新 CockpitApp 数据,不是首帧快照。
    const probe = renderModule(modules, "xb-projects-probe");
    expect(probe.getByTestId("xb-page-props").textContent).toBe(JSON.stringify({ selectedProjectId: 2 }));
    probe.unmount();
  });

  it("rebuilds modules when board visibility or the translator changes", () => {
    boardCalls.length = 0;
    const { rerender } = render(React.createElement(DashboardEditablePage, baseProps()));
    const first = boardCalls[0].modules;
    expect(first.some((module) => module.key === "board-my-kol")).toBe(true);
    expect(first.some((module) => module.key === "board-events")).toBe(false);

    rerender(React.createElement(DashboardEditablePage, baseProps({ canViewBoard: () => true })));
    const second = boardCalls[boardCalls.length - 1].modules;
    expect(second).not.toBe(first);
    expect(second.some((module) => module.key === "board-events")).toBe(true);

    rerender(React.createElement(DashboardEditablePage, baseProps({ canViewBoard: () => true, t: (key: string) => `en:${key}` })));
    const third = boardCalls[boardCalls.length - 1].modules;
    expect(third).not.toBe(second);
    expect(third.find((module) => module.key === "kpi")?.label).toBe("en:增长总览");
  });
});
