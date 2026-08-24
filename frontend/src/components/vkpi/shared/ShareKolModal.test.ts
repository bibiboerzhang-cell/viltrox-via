import { describe, expect, it } from "vitest";

import { isShareStaffPickable, shareKolErrorMessage } from "./ShareKolModal";

describe("shareKolErrorMessage", () => {
  it("explains row ownership instead of claiming every 403 only lacks VKPI write", () => {
    const message = shareKolErrorMessage({ status: 403, detail: "my_kol_share_write_forbidden" });

    expect(message).toContain("本人收藏者");
    expect(message).toContain("当前负责人");
    expect(message).toContain("不能转分享");
  });

  it.each([
    ["share_recipient_self", "不能把 KOL 共享给自己"],
    ["share_recipient_pending", "待批准"],
    ["share_recipient_inactive", "已停用或被暂停"],
    ["share_recipient_not_found", "不存在或已删除"],
  ])("surfaces the truthful recipient reason for %s", (detail, expected) => {
    expect(shareKolErrorMessage({ status: 422, detail })).toContain(expected);
  });

  it("keeps an unknown 403 honest about both tab and row-level requirements", () => {
    const message = shareKolErrorMessage({ status: 403, message: "Forbidden" });

    expect(message).toContain("VKPI 写权限");
    expect(message).toContain("本人收藏者");
  });

  it("hides self, existing shares, pending and inactive recipients before submit", () => {
    const members = new Set(["2"]);
    expect(isShareStaffPickable({ id: "1", active: true, verificationStatus: "activated" }, members, 1)).toBe(false);
    expect(isShareStaffPickable({ id: "2", active: true, verificationStatus: "activated" }, members, 1)).toBe(false);
    expect(isShareStaffPickable({ id: "3", active: true, verificationStatus: "pending" }, members, 1)).toBe(false);
    expect(isShareStaffPickable({ id: "4", active: false, verificationStatus: "activated" }, members, 1)).toBe(false);
    expect(isShareStaffPickable({ id: "5", active: true, verificationStatus: "verified" }, members, 1)).toBe(true);
  });
});
