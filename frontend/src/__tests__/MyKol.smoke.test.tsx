// frontend-smoke ②: MY KOL 主页面挂载冒烟。
// mock seam:channels 官方矩阵 + kol-api 聚合 + dashboard-api 导出，全部 resolved-empty。
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import type { VkpiDashboardData } from "../components/vkpi/vkpiTypes";

vi.mock("../domains/channels", () => ({
  getOfficialChannelMatrix: vi
    .fn()
    .mockResolvedValue({ platforms: [], account_count: 0, post_count: 0, total_views: 0 }),
}));

vi.mock("../services/vkpi/kol-api", () => ({
  getMyKolAggregate: vi.fn().mockResolvedValue({ pool_favorites: [] }),
  fetchContributionRollup: vi.fn().mockResolvedValue({ rows: [], window_days: 90 }),
}));

vi.mock("../services/vkpi/dashboard-api", () => ({
  exportVkpiReport: vi.fn().mockResolvedValue({ downloadUrl: "" }),
}));

import { MyKolPage } from "../components/vkpi/pages/myKol/MyKolPage";

const emptyData = {
  projects: [],
  staffMembers: [],
  kolOptions: [],
} as unknown as VkpiDashboardData;

describe("MY KOL page smoke", () => {
  it("manager 视图空数据挂载不抛 → 渲染团队矩阵 / 官方账号矩阵", () => {
    expect(() =>
      render(
        React.createElement(MyKolPage, {
          apiToken: "t",
          viewMode: "manager",
          data: emptyData,
          userName: "X",
          userRole: "owner",
          onRefreshData: () => {},
          onSelectPage: () => {},
        }),
      ),
    ).not.toThrow();
    expect(screen.getByText("团队矩阵")).toBeTruthy();
    expect(screen.getByText("官方账号矩阵")).toBeTruthy();
  });
});
