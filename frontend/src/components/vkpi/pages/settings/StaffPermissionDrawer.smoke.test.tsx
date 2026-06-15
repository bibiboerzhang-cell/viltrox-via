import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen, within } from "@testing-library/react";
import { StaffPermissionDrawer } from "./StaffPermissionDrawer";

const member = {
  id: "staff-1",
  name: "测试成员",
  email: "staff-1@pending.viltrox.local",
  role: "employee",
  active: true,
  verificationStatus: "pending",
  permissions: { vkpi: "write", kol_ops: "read", "board.shopify": "none" },
  vkpiPermission: "write",
} as unknown as Parameters<typeof StaffPermissionDrawer>[0]["member"];

describe("StaffPermissionDrawer 授权页冒烟", () => {
  it("挂载不抛异常,渲染出导航板块授权区 + 15 板块 + 四档", () => {
    expect(() =>
      render(
        React.createElement(StaffPermissionDrawer, {
          member,
          busy: false,
          onClose: () => {},
          onSavePermissions: vi.fn().mockResolvedValue(undefined),
          onCreateActivationLink: vi.fn().mockResolvedValue(null),
          onCreatePasswordResetLink: vi.fn().mockResolvedValue(null),
        }),
      ),
    ).not.toThrow();

    // 新「导航板块授权」区 + 分组 + 代表板块都在
    expect(screen.getByText("导航板块授权")).toBeTruthy();
    expect(screen.getByText("板块 · 核心")).toBeTruthy();
    expect(screen.getByText("板块 · 运营")).toBeTruthy();
    expect(screen.getByText("Shopify")).toBeTruthy();
    expect(screen.getByText("Dealers")).toBeTruthy();
    // 四档按钮存在(矩阵 + 板块都用,出现多次)
    expect(screen.getAllByText("无").length).toBeGreaterThan(0);
    expect(screen.getAllByText("只读").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可写").length).toBeGreaterThan(0);
    expect(screen.getAllByText("管理").length).toBeGreaterThan(0);
    // board.shopify=none 的行存在(键作为副标题渲染)
    expect(screen.getByText("board.shopify")).toBeTruthy();
  });
});
