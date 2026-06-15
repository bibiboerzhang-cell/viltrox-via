import { describe, it, expect } from "vitest";

// W5 recon: staffModel.buildStaffMembers 纯映射。覆盖 permissions 对象/JSON 两路、
// active 归一(0→false)、字段别名兜底、无 id 且无 email 的行被过滤。
import { buildStaffMembers } from "./staffModel";

describe("buildStaffMembers", () => {
  it("permissions 为对象时直接采用,vkpiPermission 取 permissions.vkpi", () => {
    const [m] = buildStaffMembers([
      { id: 1, user_name: "Amy", email: "a@x.com", permissions: { vkpi: "write" } },
    ]);
    expect(m.id).toBe("1");
    expect(m.name).toBe("Amy");
    expect(m.vkpiPermission).toBe("write");
    expect(m.permissions.vkpi).toBe("write");
  });

  it("permissions_json 字符串可解析", () => {
    const [m] = buildStaffMembers([
      { id: 2, email: "b@x.com", permissions_json: '{"vkpi":"read","kol":"none"}' },
    ]);
    expect(m.vkpiPermission).toBe("read");
    expect(m.permissions.kol).toBe("none");
  });

  it("坏 JSON 不抛错,降级为空权限 → vkpi 兜底 none", () => {
    const [m] = buildStaffMembers([{ id: 3, email: "c@x.com", permissions_json: "{bad" }]);
    expect(m.vkpiPermission).toBe("none");
    expect(m.permissions).toEqual({});
  });

  it("active=0 → false;缺省 → true", () => {
    const [a, b] = buildStaffMembers([
      { id: 4, email: "d@x.com", active: 0 },
      { id: 5, email: "e@x.com" },
    ]);
    expect(a.active).toBe(false);
    expect(b.active).toBe(true);
  });

  it("名字别名兜底链:无任何名字时回退邮箱再回退「未命名成员」", () => {
    const [withEmail] = buildStaffMembers([{ id: 6, email: "f@x.com" }]);
    expect(withEmail.name).toBe("f@x.com");
    const [noName] = buildStaffMembers([{ id: 7 }]);
    expect(noName.name).toBe("未命名成员");
  });

  it("id 兜底链 staff_id/staffId,role 默认 readonly", () => {
    const [m] = buildStaffMembers([{ staff_id: 88, email: "g@x.com" }]);
    expect(m.id).toBe("88");
    expect(m.role).toBe("readonly");
  });

  it("既无 id 也无 email 的行被过滤", () => {
    const rows = buildStaffMembers([{ name: "幽灵" }, { id: 9, email: "h@x.com" }]);
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe("9");
  });
});
