// frontend-smoke ②: Projects 主页面挂载冒烟。
// mock seam:useAuth(无 Provider 会抛) + projects-api/domains 全部 resolved-empty。
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import type { VkpiDashboardData } from "../components/vkpi/vkpiTypes";

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "authenticated",
    token: "t",
    user: null,
    signIn: vi.fn(),
    signOut: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock("../services/vkpi/projects-api", () => ({
  getProjectsDueList: vi.fn().mockResolvedValue({ items: [], count: 0, note: "" }),
}));

vi.mock("../domains/projects", () => ({
  addKolsToProject: vi.fn().mockResolvedValue({ inserted: 0, skipped_existing: 0 }),
  advanceProjectKol: vi.fn().mockResolvedValue({}),
  getAvailableProjectKols: vi.fn().mockResolvedValue({ kols: [] }),
  getProjectDetail: vi.fn().mockResolvedValue({}),
  submitProjectKolActionStub: vi.fn().mockResolvedValue({}),
  updateProjectFollowStatus: vi.fn().mockResolvedValue({}),
  updateProjectKolShipping: vi.fn().mockResolvedValue({}),
  updateProjectStar: vi.fn().mockResolvedValue({}),
}));

vi.mock("../domains/kol", () => ({
  buildKolOptions: vi.fn().mockReturnValue([]),
}));

import { ProjectsPage } from "../components/vkpi/pages/ProjectsPage";

const data = {
  staffMembers: [],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
} as unknown as VkpiDashboardData;

describe("Projects page smoke", () => {
  it("空筛选挂载不抛 → 渲染列表视图主标题与新建按钮", () => {
    expect(() =>
      render(
        React.createElement(ProjectsPage, {
          data,
          filteredProjects: [],
          selectedProject: undefined,
          viewMode: "manager",
          apiToken: "t",
          onSelectProject: () => {},
        }),
      ),
    ).not.toThrow();
    expect(screen.getByText("把「项目跟进」从表格变成每日决策台。")).toBeTruthy();
    expect(screen.getByText("✦ 新建推广")).toBeTruthy();
  });
});
