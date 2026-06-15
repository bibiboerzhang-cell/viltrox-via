import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// --- Test harness stubs -----------------------------------------------------
// EventsMockupPage -> EventsPage 会:
//   1) 调 useAuth() 取 token —— 真实里恒在 <AuthProvider> 内,测试里没 provider 会抛
//      "useAuth must be used inside AuthProvider",故这里桩掉返回固定 token。
//   2) 在 useEffect 里打网络:listEvents / listInventory。桩成 resolved 空数据,
//      让页面走「空列表」分支(稳定、可断言),不触网。
// 这些都是测试环境依赖(网络 / context),不是页面 bug。
vi.mock("../../../../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "authenticated",
    token: "test-token",
    user: { id: "1", name: "Jia" },
    signIn: vi.fn(),
    signOut: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock("../../../../services/vkpi/events-api", () => ({
  listEvents: vi.fn().mockResolvedValue({ items: [] }),
  createEvent: vi.fn().mockResolvedValue({}),
  updateEvent: vi.fn().mockResolvedValue({}),
  deleteEvent: vi.fn().mockResolvedValue({}),
  toUiEvent: (row: any) => row,
  fromUiCreate: (data: any) => data,
  fromUiUpdate: (data: any) => data,
  unwrapItem: (res: any) => res ?? null,
}));

vi.mock("../../../../services/vkpi/inventory-api", () => ({
  listInventory: vi.fn().mockResolvedValue({ items: [] }),
  toUiStock: (row: any) => row,
}));

import { EventsMockupPage } from "./EventsMockupPage";

describe("EventsMockupPage smoke", () => {
  it("mounts with the nav-supplied props without throwing and renders the events list shell", () => {
    render(
      React.createElement(EventsMockupPage, {
        userName: "X",
        staff: [],
        currentUser: { id: "1", role: "owner" } as any,
        initialEventId: null,
        onConsumeInitialEvent: () => {},
      })
    );

    // ① 真实文案 —— 列表壳的标题 + 主操作按钮 + 空态。
    expect(screen.getByText("Events")).toBeTruthy();
    expect(screen.getByText("新建 Event")).toBeTruthy();
    // 空数据路径:没有 event 时渲染空态文案。
    expect(screen.getByText("没有匹配的 event")).toBeTruthy();
    // KPI 区其中一个标签,确认页面主体而非降级/错误屏。
    expect(screen.getByText("我参与的进行中")).toBeTruthy();
  });
});
