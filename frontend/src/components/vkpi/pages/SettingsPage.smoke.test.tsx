import { describe, it, expect } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
import type { VkpiDashboardData } from "../vkpiTypes";

import { SettingsPage } from "./SettingsPage";

// apiToken 不传 → 所有 `if (!isManager || !apiToken) return` 守卫的 effect 都不发网络,
// 渲染等价于「登录后首帧、数据未回」的真实状态:能完整挂载整棵面板树(含恢复的
// Preference/Notification 面板),正好抓 object-as-child / undefined-import / render 崩。
const emptyData = {
  staffMembers: [],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
  projects: [],
} as unknown as VkpiDashboardData;

describe("SettingsPage smoke", () => {
  it("manager 视图挂载不抛异常,并渲染出 PageShell 标题", () => {
    expect(() =>
      render(
        React.createElement(SettingsPage, {
          data: emptyData,
          viewMode: "manager",
          // apiToken 故意不传:守卫 effect 不触发,纯渲染冒烟。
          onInviteStaff: () => {},
          onUpsertProductCost: () => {},
          onRefreshData: () => {},
        }),
      ),
    ).not.toThrow();

    expect(screen.getByText("系统设置")).toBeTruthy();
  });
});
