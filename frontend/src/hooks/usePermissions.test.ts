import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";

// 用可变 mockUser 驱动 useAuth(工厂在调用时读取 → 每个用例可改身份)。
let mockUser: Record<string, unknown> | null = null;
vi.mock("./useAuth", () => ({ useAuth: () => ({ user: mockUser }) }));

import { usePermissions } from "./usePermissions";

function tierFor(user: Record<string, unknown> | null): string {
  mockUser = user;
  const { result } = renderHook(() => usePermissions());
  return result.current.accountTier();
}

describe("usePermissions.accountTier(三层语义)", () => {
  it("未登录 → none", () => {
    expect(tierFor(null)).toBe("none");
  });
  it("owner → company", () => {
    expect(tierFor({ is_owner: true })).toBe("company");
  });
  it("vkpi=admin → company", () => {
    expect(tierFor({ id: 5, role: "staff", permissions: { vkpi: "admin" } })).toBe("company");
  });
  it("有身份的普通成员 → member", () => {
    expect(tierFor({ id: 5, role: "staff", permissions: { vkpi: "read" } })).toBe("member");
  });
});

describe("usePermissions.hasPermission / isManager", () => {
  it("owner 任何权限都放行", () => {
    mockUser = { is_owner: true };
    const { result } = renderHook(() => usePermissions());
    expect(result.current.hasPermission("vkpi", "admin")).toBe(true);
    expect(result.current.isManager()).toBe(true);
  });
  it("read 级成员:read 通过、write 拒绝", () => {
    mockUser = { id: 9, role: "staff", permissions: { vkpi: "read" } };
    const { result } = renderHook(() => usePermissions());
    expect(result.current.hasPermission("vkpi", "read")).toBe(true);
    expect(result.current.hasPermission("vkpi", "write")).toBe(false);
    expect(result.current.isManager()).toBe(false);
  });
});
