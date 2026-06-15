import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// useAuth 在没有 AuthProvider 时会 throw —— 必须 mock,否则页面挂载即崩。
vi.mock("../../../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "authenticated",
    token: "t",
    user: null,
    signIn: vi.fn(),
    signOut: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

// ProjectDueListCard 在 mount 时(apiToken 非空)会拉 getProjectsDueList,mock 成空结果。
vi.mock("../../../services/vkpi/projects-api", () => ({
  getProjectsDueList: vi.fn().mockResolvedValue({ items: [], count: 0, note: "" }),
}));

// 页面直接 import 的 domains 层函数全部桩成 resolved-empty,杜绝任何真实 http 触发。
vi.mock("../../../domains/projects", () => ({
  addKolsToProject: vi.fn().mockResolvedValue({ inserted: 0, skipped_existing: 0 }),
  advanceProjectKol: vi.fn().mockResolvedValue({}),
  getAvailableProjectKols: vi.fn().mockResolvedValue({ kols: [] }),
  getProjectDetail: vi.fn().mockResolvedValue({}),
  submitProjectKolActionStub: vi.fn().mockResolvedValue({}),
  updateProjectFollowStatus: vi.fn().mockResolvedValue({}),
  updateProjectKolShipping: vi.fn().mockResolvedValue({}),
  updateProjectStar: vi.fn().mockResolvedValue({}),
}));

vi.mock("../../../domains/kol", () => ({
  buildKolOptions: vi.fn().mockReturnValue([]),
}));

import { ProjectsPage } from "./ProjectsPage";
import type { VkpiDashboardData } from "../vkpiTypes";

// 仅填页面读取的 4 个字段(staffMembers/productCosts/productLaunches/kolOptions),
// 其余 dashboard 字段页面不触碰,用 as 收口避免噪音。
const data = {
  staffMembers: [],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
} as unknown as VkpiDashboardData;

const noop = () => {};

describe("ProjectsPage", () => {
  it("renders the project follow-up list view without crashing", () => {
    render(
      React.createElement(ProjectsPage, {
        data,
        filteredProjects: [],
        selectedProject: undefined,
        viewMode: "manager",
        apiToken: "t",
        onSelectProject: noop,
      }),
    );

    // 真实文案:列表视图看板主标题 + 新建推广按钮 + 空态提示,均应出现在 DOM。
    // (PageShell 在本页以 hideHeading 渲染,故不断言 description,那段本就不渲染。)
    expect(
      screen.getByText("把「项目跟进」从表格变成每日决策台。"),
    ).toBeTruthy();
    expect(screen.getByText("✦ 新建推广")).toBeTruthy();
    expect(
      screen.getByText(
        "当前筛选下没有项目。请先创建真实项目，或调整搜索和状态筛选。",
      ),
    ).toBeTruthy();
  });
});
