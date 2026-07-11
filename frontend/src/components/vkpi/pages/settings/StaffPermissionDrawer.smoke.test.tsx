import { describe, it, expect, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { StaffPermissionDrawer } from "./StaffPermissionDrawer";

const member = {
  id: "staff-1",
  name: "测试成员",
  email: "staff-1@pending.viltrox.local",
  role: "employee",
  active: true,
  verificationStatus: "pending",
  // overview 未设置(应被兜底为「显示」),kol_ops=read,vkpi=write
  permissions: { vkpi: "write", kol_ops: "read" },
  vkpiPermission: "write",
} as unknown as Parameters<typeof StaffPermissionDrawer>[0]["member"];

describe("StaffPermissionDrawer 授权页冒烟(C7 两态·默认显示)", () => {
  const renderDrawer = () =>
    render(
      React.createElement(StaffPermissionDrawer, {
        member,
        busy: false,
        onClose: () => {},
        onSavePermissions: vi.fn().mockResolvedValue(undefined),
        onCreateActivationLink: vi.fn().mockResolvedValue(null),
        onCreatePasswordResetLink: vi.fn().mockResolvedValue(null),
      }),
    );

  it("挂载不抛异常,只剩「深度授权」段,板块授权段已下线", () => {
    expect(() => renderDrawer()).not.toThrow();
    expect(screen.getByText("深度授权")).toBeTruthy();
    // 导航板块授权段已移除
    expect(screen.queryByText("导航板块授权")).toBeNull();
    expect(screen.queryByText("板块 · 核心")).toBeNull();
    // 代表模块仍在
    expect(screen.getByText("V-KPI 工作台")).toBeTruthy();
    expect(screen.getByText("API Key")).toBeTruthy();
  });

  it("业务模块两态(显示/可使用),敏感模块保留「无」", () => {
    renderDrawer();
    // 显示 / 可使用 两态按钮存在
    expect(screen.getAllByText("显示").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可使用").length).toBeGreaterThan(0);
    // 「无」出现在 owner 按需授权模块:4 个敏感(api_keys/models/members/restart)
    // + 系统设置/用量/运行诊断 3 个 = 7(这 3 个不再默认显示,改 owner 按需)
    expect(screen.getAllByText("无").length).toBe(7);
    // 旧「只读/可写/管理」文案已彻底移除
    expect(screen.queryByText("只读")).toBeNull();
    expect(screen.queryByText("可写")).toBeNull();
  });

  it("未设置的业务模块默认兜底为「显示」(read),非「无」", () => {
    renderDrawer();
    // overview 未在 permissions 里 → 该行应高亮「显示」
    const row = screen.getByText("overview").closest(".vkpi-staff-permission-row") as HTMLElement;
    expect(row).toBeTruthy();
    const active = within(row).getByText("显示");
    expect(active.className).toContain("is-active");
  });

  it("套用模板会明确标记未保存，权限按钮暴露选中状态", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /成员工作台/ }));
    expect(screen.getByText(/权限已改但未保存/)).toBeTruthy();

    const row = screen.getByText("overview").closest(".vkpi-staff-permission-row") as HTMLElement;
    expect(within(row).getByRole("button", { name: "显示" })).toHaveAttribute("aria-pressed", "true");
  });
});
