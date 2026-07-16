import React from "react";
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DashboardModuleDefinition } from "../components/EditableDashboardBoard";

let capturedModules: DashboardModuleDefinition[] = [];

vi.mock("../components/EditableDashboardBoard", () => ({
  EditableDashboardBoard: ({ modules }: { modules: DashboardModuleDefinition[] }) => {
    capturedModules = modules;
    return <div data-testid="editable-dashboard-board" />;
  },
}));

import { DashboardEditablePage } from "../DashboardEditablePage";
import { CROSS_BOARD_ENTRIES } from "./crossBoardModules";

const baseProps = {
  metrics: [],
  signals: [],
  topMovers: [],
  campaigns: [],
  campaignsMeta: {},
  calendarDays: [],
  calendarMeta: {},
  upcomingEvents: [],
  apiToken: "token",
};

beforeEach(() => {
  capturedModules = [];
});

function moduleKeys(category: DashboardModuleDefinition["category"]) {
  return capturedModules.filter((module) => module.category === category).map((module) => module.key);
}

describe("Dashboard 模块 registry 权限", () => {
  it("缺少权限上下文时不暴露业务入口或跨页模块", () => {
    render(<DashboardEditablePage {...baseProps} />);
    expect(moduleKeys("业务板块")).toEqual([]);
    expect(moduleKeys("跨板块模块")).toEqual([]);
  });

  it("只授权 Projects 时注册 Projects 入口和完整来源模块", () => {
    render(<DashboardEditablePage {...baseProps} canViewBoard={(navKey: string) => navKey === "projects"} />);
    expect(moduleKeys("业务板块")).toEqual(["board-projects"]);
    expect(moduleKeys("跨板块模块")).toHaveLength(8);
    expect(moduleKeys("跨板块模块")).toContain("xb-projects-due");
    expect(moduleKeys("跨板块模块")).toContain("xb-full-projects-kpi-p");
  });

  it("全量授权时注册全部业务入口和当前已实现业务/Ops 跨页模块", () => {
    render(<DashboardEditablePage {...baseProps} canViewBoard={() => true} />);
    expect(moduleKeys("业务板块")).toHaveLength(16);
    expect(moduleKeys("跨板块模块")).toHaveLength(CROSS_BOARD_ENTRIES.length);
    expect(moduleKeys("跨板块模块")).toContain("xb-pool-smart-search");
    expect(moduleKeys("跨板块模块")).toContain("xb-pool-search-history");
    expect(moduleKeys("跨板块模块")).toContain("xb-full-kol-profile-history");
    expect(moduleKeys("跨板块模块")).toEqual(expect.arrayContaining([
      "xb-full-triage-overview",
      "xb-full-data-query-overview",
      "xb-full-market-trends-overview",
      "xb-full-skill-studio-overview",
    ]));
    expect(new Set(capturedModules.map((module) => module.key)).size).toBe(capturedModules.length);
  });
});
