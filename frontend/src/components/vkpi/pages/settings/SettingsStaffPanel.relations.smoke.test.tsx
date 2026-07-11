import { describe, it, expect, vi } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import type { VkpiStaffMember } from "../../vkpiTypes";
import { StaffRelationView, memberHoverTitle, staffRelationGroups } from "./SettingsStaffPanel.relations";
import { SettingsStaffPanel } from "./SettingsStaffPanel";

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
  // 生产真形状:owner 的 role 是 admin,靠 staff.is_owner 标记(不能只看 role)。
  mk({ id: "1", name: "Jianbo", email: "jianbo@viltrox.com", role: "admin", isOwner: true, vkpiPermission: "admin", verificationStatus: "verified", online: true }),
  mk({ id: "2", name: "Daniel", email: "daniel@viltrox.com", role: "manager", vkpiPermission: "write", verificationStatus: "activated" }),
  mk({ id: "3", name: "Adrian", email: "adrian@viltrox.com", role: "employee", vkpiPermission: "write", verificationStatus: "activated", online: true }),
  mk({ id: "4", name: "Lummo", email: "lummo@viltrox.com", role: "employee", vkpiPermission: "write", verificationStatus: "pending" }),
  mk({ id: "5", name: "电影部门", email: "film@viltrox.com", role: "readonly", vkpiPermission: "read", verificationStatus: "activated" }),
];

describe("授权页 V1 · 关系视图冒烟", () => {
  it("分组:owner 大卡 + 管理层/运营/只读 三组,题注计数为真", () => {
    const groups = staffRelationGroups(members);
    expect(groups.owners.map((m) => m.id)).toEqual(["1"]);
    expect(groups.managers.map((m) => m.id)).toEqual(["2"]);
    expect(groups.ops.map((m) => m.id)).toEqual(["3", "4"]);
    expect(groups.readonly.map((m) => m.id)).toEqual(["5"]);

    const { container } = render(React.createElement(StaffRelationView, { members, selectedStaffId: null, onSelectStaff: () => {} }));
    expect(screen.getByText("OWNER")).toBeTruthy();
    expect(screen.getByText("Jianbo")).toBeTruthy();
    expect(screen.getByText("管理层")).toBeTruthy();
    expect(screen.getByText("运营")).toBeTruthy();
    expect(screen.getByText("只读 / 外部")).toBeTruthy();
    // 统计行:总数 5 / 在线 2 / 待激活 1(真数,不编)
    const stats = container.querySelector(".vkpi-staff-relations__stats") as HTMLElement;
    expect(stats.textContent).toContain("成员总数 5");
    expect(stats.textContent).toContain("当前在线 2");
    expect(stats.textContent).toContain("待激活 1");
  });

  it("卡片:在线点(user_online 真字段)/ 待激活徽 / 选中高亮 / hover title 不丢 6 列信息", () => {
    render(React.createElement(StaffRelationView, { members, selectedStaffId: "3", onSelectStaff: () => {} }));
    // 在线:Adrian 的头像 wrapper 带 is-online
    const adrianCard = screen.getByTitle(/adrian@viltrox\.com/) as HTMLElement;
    expect(adrianCard.querySelector(".vkpi-staff-member-card__avatar.is-online")).toBeTruthy();
    expect(adrianCard.className).toContain("is-selected");
    // 待激活徽:Lummo(pending)
    const lummoCard = screen.getByTitle(/lummo@viltrox\.com/) as HTMLElement;
    expect(lummoCard.querySelector(".vkpi-staff-pending-badge")?.textContent).toBe("待激活");
    // hover title 收编原表格 6 列:成员/邮箱/角色/V-KPI/状态(+在线)
    const title = memberHoverTitle(members[2]);
    expect(title).toContain("Adrian");
    expect(title).toContain("adrian@viltrox.com");
    expect(title).toContain("employee");
    expect(title).toContain("V-KPI write");
    expect(title).toContain("已激活");
    expect(title).toContain("在线");
  });

  it("点卡 = 打开右侧权限面板:onSelectStaff(id, member) 被调", () => {
    const onSelectStaff = vi.fn();
    render(React.createElement(StaffRelationView, { members, selectedStaffId: null, onSelectStaff }));
    fireEvent.click(screen.getByTitle(/adrian@viltrox\.com/));
    expect(onSelectStaff).toHaveBeenCalledWith("3", expect.objectContaining({ id: "3", name: "Adrian" }));
    fireEvent.click(screen.getByTitle(/jianbo@viltrox\.com/));
    expect(onSelectStaff).toHaveBeenCalledWith("1", expect.objectContaining({ isOwner: true }));
  });

  it("空成员 → 诚实空态,不编数据", () => {
    render(React.createElement(StaffRelationView, { members: [], selectedStaffId: null, onSelectStaff: () => {} }));
    expect(screen.getByText(/暂无成员访问记录/)).toBeTruthy();
  });
});

describe("SettingsStaffPanel · 关系视图为默认,表格保留为备选(功能零删减)", () => {
  const panelProps = {
    members,
    selectedStaffId: null,
    email: "",
    name: "",
    role: "employee",
    permission: "write" as const,
    permissionTemplate: "employee_workspace",
    busy: false,
    canInvite: false,
    inviteMode: "manual_link" as const,
    inviteCapabilities: null,
    inviteCapabilitiesError: "",
    activationLink: null,
    activationCopied: false,
    onEmailChange: () => {},
    onNameChange: () => {},
    onRoleChange: () => {},
    onPermissionChange: () => {},
    onPermissionTemplateChange: () => {},
    onCopyActivationLink: () => {},
    onSubmitInvite: (e: React.FormEvent) => e.preventDefault(),
    onSelectStaff: () => {},
  };

  it("默认关系视图;切「表格」回到 6 列 StaffTable;邀请卡(StaffInviteCard)仍在", () => {
    const { container } = render(React.createElement(SettingsStaffPanel, panelProps));
    // 布局修锚点:staff 区两栏带 --staff 修饰类(traffic css 按它把 align-items 覆回 start,
    // 左表单卡贴内容,不吃 settings-dark 的全局 stretch)。
    expect(container.querySelector(".vkpi-settings-two-column.vkpi-settings-two-column--staff")).toBeTruthy();
    // 邀请入口保留
    expect(screen.getByText("授权账户")).toBeTruthy();
    // 默认 = 关系视图
    expect(screen.getByText("OWNER")).toBeTruthy();
    expect(screen.queryByText("邮箱")).toBeNull();
    // 切表格 → 原 6 列全在
    fireEvent.click(screen.getByRole("button", { name: "表格" }));
    for (const col of ["成员", "邮箱", "角色", "V-KPI", "状态", "操作"]) {
      expect(screen.getByText(col)).toBeTruthy();
    }
  });
});
