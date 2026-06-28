// frontend-smoke ②: Events 主页面挂载冒烟。
// mock seam:useAuth(无 Provider 会抛) + events-api / inventory-api 空列表分支。
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "authenticated",
    token: "test-token",
    user: { id: "1", name: "Jia" },
    signIn: vi.fn(),
    signOut: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

vi.mock("../services/vkpi/events-api", () => ({
  listEvents: vi.fn().mockResolvedValue({ items: [] }),
  createEvent: vi.fn().mockResolvedValue({}),
  updateEvent: vi.fn().mockResolvedValue({}),
  deleteEvent: vi.fn().mockResolvedValue({}),
  toUiEvent: (row: any) => row,
  fromUiCreate: (d: any) => d,
  fromUiUpdate: (d: any) => d,
  unwrapItem: (res: any) => res ?? null,
}));

vi.mock("../services/vkpi/inventory-api", () => ({
  listInventory: vi.fn().mockResolvedValue({ items: [] }),
  toUiStock: (row: any) => row,
}));

import { EventsMockupPage } from "../components/vkpi/pages/events/EventsMockupPage";

describe("Events page smoke", () => {
  it("空列表挂载不抛 → 渲染 Events 标题与主操作按钮", () => {
    expect(() =>
      render(
        React.createElement(EventsMockupPage, {
          userName: "X",
          staff: [],
          currentUser: { id: "1", role: "owner" } as any,
          initialEventId: null,
          onConsumeInitialEvent: () => {},
        }),
      ),
    ).not.toThrow();
    expect(screen.getByText("Events")).toBeTruthy();
    expect(screen.getByText("新建 Event")).toBeTruthy();
  });
});
