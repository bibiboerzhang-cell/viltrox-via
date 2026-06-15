import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import type { VkpiDashboardData } from "../../vkpiTypes";

// 网络/service 层全部桩掉,返回空数组/空对象的 resolved promise。
// 1) 官方账号矩阵 hook 的数据源(domains/channels)。
vi.mock("../../../../domains/channels", () => ({
  getOfficialChannelMatrix: vi
    .fn()
    .mockResolvedValue({ platforms: [], account_count: 0, post_count: 0, total_views: 0 }),
}));

// 2) EmployeeKolLibrary 在 manager 视图下动态 import 的收藏聚合端点。
vi.mock("../../../../services/vkpi/kol-api", () => ({
  getMyKolAggregate: vi.fn().mockResolvedValue({ pool_favorites: [] }),
}));

// 3) 导出收藏名单按钮动态 import 的报表导出端点(点击才触发,桩着兜底)。
vi.mock("../../../../services/vkpi/dashboard-api", () => ({
  exportVkpiReport: vi.fn().mockResolvedValue({ downloadUrl: "" }),
}));

import { MyKolPage } from "./MyKolPage";

// 贴近真实的最小 props:页面读取 data.projects / data.staffMembers / data.kolOptions,
// 均给空数组(真实 app 永远传完整 VkpiDashboardData,这里给最小可渲染骨架)。
const emptyData = {
  projects: [],
  staffMembers: [],
  kolOptions: [],
} as unknown as VkpiDashboardData;

describe("MyKolPage smoke", () => {
  it("挂载 manager 视图不抛异常,并渲染出该页真实文案", () => {
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

    // 该页 hero 标题与团队矩阵段标题是 manager 视图必现的真实元素。
    expect(screen.getByText("团队矩阵")).toBeTruthy();
    expect(screen.getByText("官方账号矩阵")).toBeTruthy();
    expect(screen.getByText("刷新数据")).toBeTruthy();
  });
});
