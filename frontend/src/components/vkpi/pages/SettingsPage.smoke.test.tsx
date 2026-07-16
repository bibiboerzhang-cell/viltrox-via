import { afterEach, describe, it, expect, vi } from "vitest";
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
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
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("manager 视图挂载不抛异常,并渲染出 PageShell 标题", async () => {
    await act(async () => {
      render(
        React.createElement(SettingsPage, {
          data: emptyData,
          viewMode: "manager",
          // apiToken 故意不传:守卫 effect 不触发,纯渲染冒烟。
          onInviteStaff: () => {},
          onUpsertProductCost: () => {},
          onRefreshData: () => {},
        }),
      );
    });

    expect(screen.getByText("系统设置")).toBeTruthy();
  });

  it("卸载时中止仍在等待的版本探针,不让异步结果回写已卸载页面", async () => {
    const abortSeen = vi.fn();
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        abortSeen();
        reject(new DOMException("aborted", "AbortError"));
      }, { once: true });
    })));

    const view = render(
      React.createElement(SettingsPage, {
        data: emptyData,
        viewMode: "manager",
        onInviteStaff: () => {},
        onUpsertProductCost: () => {},
        onRefreshData: () => {},
      }),
    );
    view.unmount();

    await waitFor(() => expect(abortSeen).toHaveBeenCalledOnce());
  });
});
