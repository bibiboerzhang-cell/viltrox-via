import { describe, it, expect } from "vitest";
import {
  STAFF_PERMISSION_MODULES,
  STAFF_PERMISSION_TEMPLATES,
  STAFF_ASSIGNABLE_PERMISSION_TEMPLATES,
  permissionsForTemplate,
  vkpiPermissionFromTemplate,
} from "./staffPermissionTemplates";

// P2-6: 员工权限模板纯函数(零 mock)。覆盖模板查找/兜底、vkpi 级别映射、可分配模板过滤
// (admin ownerOnly 不可分配)、viewer 模板里 ownerOnly 模块=none、其余=read。

describe("permissionsForTemplate 模板查找", () => {
  it("已知模板返回其权限拷贝", () => {
    const perms = permissionsForTemplate("viewer");
    expect(perms.kol_ops).toBe("read");
  });
  it("返回的是新对象(不共享引用)", () => {
    const a = permissionsForTemplate("kol_outreach");
    a.vkpi = "none";
    const b = permissionsForTemplate("kol_outreach");
    expect(b.vkpi).toBe("write");
  });
  it("未知键 → 兜底到第一个模板(employee_workspace)", () => {
    const fallback = permissionsForTemplate("__nope__");
    expect(fallback).toEqual(permissionsForTemplate("employee_workspace"));
  });
});

describe("vkpiPermissionFromTemplate vkpi 级别映射", () => {
  it("admin → write", () => {
    expect(vkpiPermissionFromTemplate("admin")).toBe("write");
  });
  it("read 级模板(finance vkpi=read)→ read", () => {
    expect(vkpiPermissionFromTemplate("finance")).toBe("read");
  });
  it("employee_workspace(vkpi=write)→ write", () => {
    expect(vkpiPermissionFromTemplate("employee_workspace")).toBe("write");
  });
  it("viewer(vkpi=read)→ read", () => {
    expect(vkpiPermissionFromTemplate("viewer")).toBe("read");
  });
});

describe("STAFF_ASSIGNABLE_PERMISSION_TEMPLATES 过滤 ownerOnly", () => {
  it("不含 admin(ownerOnly)", () => {
    expect(STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.some((t) => t.key === "admin")).toBe(false);
  });
  it("含 employee_workspace / viewer 等可分配模板", () => {
    const keys = STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.map((t) => t.key);
    expect(keys).toContain("employee_workspace");
    expect(keys).toContain("viewer");
  });
  it("每个可分配模板均非 ownerOnly", () => {
    expect(STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.every((t) => !t.ownerOnly)).toBe(true);
  });
});

describe("viewer 模板:ownerOnly 模块=none、其余=read", () => {
  const viewerPerms = permissionsForTemplate("viewer");
  it("敏感模块(ownerOnly)= none", () => {
    const ownerOnlyKeys = STAFF_PERMISSION_MODULES.filter((m) => m.ownerOnly).map((m) => m.key);
    expect(ownerOnlyKeys.length).toBeGreaterThan(0);
    for (const key of ownerOnlyKeys) expect(viewerPerms[key]).toBe("none");
  });
  it("非敏感模块 = read", () => {
    const normalKeys = STAFF_PERMISSION_MODULES.filter((m) => !m.ownerOnly).map((m) => m.key);
    for (const key of normalKeys) expect(viewerPerms[key]).toBe("read");
  });
});

describe("admin 模板:非敏感=admin、ownerOnly=read", () => {
  const adminTpl = STAFF_PERMISSION_TEMPLATES.find((t) => t.key === "admin")!;
  it("ownerOnly 模块 = read", () => {
    const ownerOnlyKeys = STAFF_PERMISSION_MODULES.filter((m) => m.ownerOnly).map((m) => m.key);
    for (const key of ownerOnlyKeys) expect(adminTpl.permissions[key]).toBe("read");
  });
  it("非敏感模块 = admin", () => {
    const normalKeys = STAFF_PERMISSION_MODULES.filter((m) => !m.ownerOnly).map((m) => m.key);
    for (const key of normalKeys) expect(adminTpl.permissions[key]).toBe("admin");
  });
});
