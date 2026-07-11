import { describe, it, expect, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { LazyMotion, domAnimation } from "framer-motion";
import type { VkpiDashboardData, VkpiStaffMember } from "../../vkpiTypes";
import { AuthorizationOverlay } from "./AuthorizationOverlay";
import { CockpitOverlays } from "../../cockpit/CockpitApp.Sections";

const mk = (over: Partial<VkpiStaffMember>): VkpiStaffMember => ({
  id: "0",
  name: "成员",
  email: "",
  role: "employee",
  active: true,
  vkpiPermission: "read",
  ...over,
} as VkpiStaffMember);

const members: VkpiStaffMember[] = [
  mk({ id: "1", name: "Jianbo", email: "jianbo@viltrox.com", role: "admin", isOwner: true, vkpiPermission: "admin", verificationStatus: "verified" }),
  mk({ id: "2", name: "Daniel", email: "daniel@viltrox.com", role: "manager", vkpiPermission: "write", verificationStatus: "activated" }),
];

// apiToken 故意不传:富名单直拉 / 邀请能力探测两个 effect 都被 !apiToken 守卫拦下,
// 纯渲染冒烟不发网络(与 SettingsPage.smoke 同款手法)。
const emptyData = {
  staffMembers: members,
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
  projects: [],
} as unknown as VkpiDashboardData;

describe("授权页 V1.1 · AuthorizationOverlay 独立浮层", () => {
  it("只渲染 staff 内容:页头(标题+OWNER 徽+成员数药丸)+ 邀请卡 + 关系视图;没有设置页侧栏/其他分区", () => {
    const { container } = render(
      React.createElement(AuthorizationOverlay, { data: emptyData, onClose: () => {} }),
    );
    // 页头:标题 + OWNER 徽(页头 1 枚 + 关系视图 owner 大卡 1 枚)+ 成员数药丸
    expect(screen.getByText("成员与授权")).toBeTruthy();
    expect(screen.getAllByText("OWNER").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("2 成员 · 1 Owner")).toBeTruthy();
    // staff 零件原样复用:邀请卡 + 关系视图两栏(--staff 修饰类)
    expect(screen.getByText("授权账户")).toBeTruthy();
    expect(container.querySelector(".vkpi-settings-two-column--staff")).toBeTruthy();
    // 容器机制照系统设置浮层:cockpit-settings-dark 作用域(global.css 按钮重置 + 暗色 color-scheme)
    expect(container.querySelector(".cockpit-settings-dark.vkpi-settings-surface")).toBeTruthy();
    // 独立页判据:不带设置页的分区骨架与标题
    expect(screen.queryByText("系统设置")).toBeNull();
    expect(container.querySelector(".vkpi-settings-clean")).toBeNull();
    expect(screen.queryByText("数据健康哨兵")).toBeNull();
  });

  it("点成员卡 = 打开权限抽屉;关闭钮回主界面(onClose)", () => {
    const onClose = vi.fn();
    render(React.createElement(AuthorizationOverlay, { data: emptyData, onClose }));
    // 点 Daniel 卡 → StaffPermissionDrawer(板块选择器等内部逻辑不在本测范围)
    fireEvent.click(screen.getByTitle(/daniel@viltrox\.com/));
    expect(screen.getByText("深度授权")).toBeTruthy();
    expect(screen.getByText("板块可见 · 侧栏")).toBeTruthy();
    // 关闭钮
    fireEvent.click(screen.getByLabelText("关闭成员与授权"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("入口改线冒烟:头像菜单「成员与授权」→ 独立浮层,不再走设置页 initialSection", () => {
  const baseProps = {
    t: (s: string) => s,
    currentUser: { id: "1", role: "owner", name: "Jianbo", email: "jianbo@viltrox.com", avatar: "J", avatarGradient: "var(--ds-accent)" },
    uiStaff: [],
    viewingAs: null,
    theme: "dark",
    lang: "zh",
    userMenuBtnRef: { current: null },
    dashboardData: emptyData,
  };

  it("点击菜单项:setShowMembersAuth(true),不碰 setShowSettingsModal / setSettingsInitialSection", () => {
    const setShowMembersAuth = vi.fn();
    const setShowSettingsModal = vi.fn();
    const setSettingsInitialSection = vi.fn();
    render(
      React.createElement(LazyMotion, { features: domAnimation },
        CockpitOverlays({
          ...baseProps,
          showUserMenu: true,
          setShowUserMenu: vi.fn(),
          setTheme: vi.fn(), setLang: vi.fn(), setViewingAs: vi.fn(),
          setShowProfile: vi.fn(), setShowTeam: vi.fn(), onSignOut: vi.fn(),
          isOwnerUser: true,
          setShowMembersAuth, setShowSettingsModal, setSettingsInitialSection,
        })),
    );
    fireEvent.click(screen.getByText("成员与授权"));
    expect(setShowMembersAuth).toHaveBeenCalledWith(true);
    expect(setShowSettingsModal).not.toHaveBeenCalled();
    expect(setSettingsInitialSection).not.toHaveBeenCalled();
    // 「系统设置」菜单项照旧走设置页(双入口并存)
    fireEvent.click(screen.getByText("系统设置"));
    expect(setShowSettingsModal).toHaveBeenCalledWith(true);
  });

  it("showMembersAuth=true 时挂载的是 AuthorizationOverlay,而非 SettingsPage", () => {
    render(
      React.createElement(LazyMotion, { features: domAnimation },
        CockpitOverlays({
          ...baseProps,
          showMembersAuth: true,
          setShowMembersAuth: vi.fn(),
          onRefreshData: vi.fn(),
        })),
    );
    expect(screen.getByText("成员与授权")).toBeTruthy();
    expect(screen.queryByText("系统设置")).toBeNull();
  });
});
