// frontend-smoke ②: Settings 主页面挂载冒烟。
// apiToken 不传 → 守卫 effect 不发网络，纯渲染整棵面板树。
import { describe, it, expect } from "vitest";
import React from "react";
import { act, render, screen } from "@testing-library/react";
import type { VkpiDashboardData } from "../components/vkpi/vkpiTypes";

import { SettingsPage } from "../components/vkpi/pages/SettingsPage";

const emptyData = {
  staffMembers: [],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
  projects: [],
} as unknown as VkpiDashboardData;

describe("Settings page smoke", () => {
  it("manager 视图挂载不抛 → 渲染系统设置标题", async () => {
    await act(async () => {
      render(
        React.createElement(SettingsPage, {
          data: emptyData,
          viewMode: "manager",
          onInviteStaff: () => {},
          onUpsertProductCost: () => {},
          onRefreshData: () => {},
        }),
      );
    });
    expect(screen.getByText("系统设置")).toBeTruthy();
  });
});
