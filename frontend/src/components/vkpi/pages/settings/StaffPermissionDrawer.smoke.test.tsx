import { describe, it, expect, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { StaffPermissionDrawer } from "./StaffPermissionDrawer";
import { BOARD_PERMISSION_MODULES } from "./staffPermissionTemplates";

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

describe("StaffPermissionDrawer 授权页冒烟(C7 两态·默认显示 + V1 板块可见)", () => {
  const renderDrawer = (onSavePermissions = vi.fn().mockResolvedValue(undefined)) => {
    render(
      React.createElement(StaffPermissionDrawer, {
        member,
        busy: false,
        onClose: () => {},
        onSavePermissions,
        onCreateActivationLink: vi.fn().mockResolvedValue(null),
        onCreatePasswordResetLink: vi.fn().mockResolvedValue(null),
      }),
    );
    return onSavePermissions;
  };

  it("挂载不抛异常:深度授权 + 板块可见选择器都在,旧「导航板块授权」行矩阵不复活", () => {
    expect(() => renderDrawer()).not.toThrow();
    expect(screen.getByText("深度授权")).toBeTruthy();
    // V1:板块层以「板块可见 · 侧栏」选择器形式回归(激活 boardLevelFor)
    expect(screen.getByText("板块可见 · 侧栏")).toBeTruthy();
    // 旧的逐行三态「导航板块授权」矩阵不回来
    expect(screen.queryByText("导航板块授权")).toBeNull();
    expect(screen.queryByText("板块 · 核心")).toBeNull();
    // 代表模块仍在
    expect(screen.getByText("V-KPI 工作台")).toBeTruthy();
    expect(screen.getByText("API Key")).toBeTruthy();
    // 两态/三态图例
    expect(screen.getByText(/业务模块 · 两态/)).toBeTruthy();
    expect(screen.getByText(/系统与敏感 · 三态/)).toBeTruthy();
  });

  it("业务模块两态(显示/可使用),敏感模块保留「无」+ OWNER 锁徽", () => {
    renderDrawer();
    expect(screen.getAllByText("显示").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可使用").length).toBeGreaterThan(0);
    // 「无」出现在 owner 按需授权模块:4 个敏感 + 系统设置/用量/运行诊断 = 7
    expect(screen.getAllByText("无").length).toBe(7);
    // 4 个敏感行都有 OWNER 锁徽
    expect(screen.getAllByText("OWNER").length).toBe(4);
    // 旧「只读/可写/管理」文案已彻底移除
    expect(screen.queryByText("只读")).toBeNull();
    expect(screen.queryByText("可写")).toBeNull();
  });

  it("未设置的业务模块默认兜底为「显示」(read),非「无」", () => {
    renderDrawer();
    const row = screen.getByText("overview").closest(".vkpi-staff-permission-row") as HTMLElement;
    expect(row).toBeTruthy();
    const active = within(row).getByText("显示");
    expect(active.className).toContain("is-active");
  });

  it("模板选中态:初始按当前权限反推高亮,微调后落「自定义」", () => {
    renderDrawer();
    // member 的权限恰好 = 成员工作台模板 floor 后的结果 → 初始即高亮
    const tpl = screen.getByRole("button", { name: /成员工作台/ });
    expect(tpl).toHaveAttribute("aria-pressed", "true");
    // 换套 KOL 外联 → 高亮跟着走
    fireEvent.click(screen.getByRole("button", { name: /KOL 外联/ }));
    expect(screen.getByRole("button", { name: /KOL 外联/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /成员工作台/ })).toHaveAttribute("aria-pressed", "false");
    // 逐项微调 → 模板高亮清空,「自定义」高亮
    const row = screen.getByText("overview").closest(".vkpi-staff-permission-row") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "可使用" }));
    expect(screen.getByRole("button", { name: /KOL 外联/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("自定义").className).toContain("is-active");
  });

  it("改动即在顶部亮「未保存」黄条,保存后消失", async () => {
    const onSave = renderDrawer();
    expect(screen.queryByText("未保存")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /成员工作台/ }));
    expect(screen.getByText("未保存")).toBeTruthy();
    expect(screen.getByText(/权限已改但未保存/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /保存权限/ }));
    await screen.findByText(/权限已保存/);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("未保存")).toBeNull();
  });

  it("板块可见选择器:默认全见 17/17,勾掉写 board.<navKey>=none 并随保存下发", async () => {
    const onSave = renderDrawer();
    // 未存过 board 权限 = 全见(向后兼容口径)
    expect(screen.getByText(/已开/).textContent).toContain(`/ ${BOARD_PERMISSION_MODULES.length}`);
    expect(screen.getByText("17")).toBeTruthy();
    // 展开 17 板块 chips
    fireEvent.click(screen.getByRole("button", { name: "选择板块" }));
    const shopifyChip = screen.getByRole("button", { name: /Shopify/ });
    expect(shopifyChip).toHaveAttribute("aria-pressed", "true");
    // 勾掉 Shopify → 计数 16 + 未保存
    fireEvent.click(shopifyChip);
    expect(screen.getByRole("button", { name: /Shopify/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("16")).toBeTruthy();
    expect(screen.getByText("未保存")).toBeTruthy();
    // 保存:board.shopify=none 进 permissions 整包
    fireEvent.click(screen.getByRole("button", { name: /保存权限/ }));
    await screen.findByText(/权限已保存/);
    const saved = onSave.mock.calls[0][1] as Record<string, string>;
    expect(saved["board.shopify"]).toBe("none");
    // 模块权限原样带上,没有被板块操作洗掉
    expect(saved.vkpi).toBe("write");
  });
});
