import { describe, it, expect } from "vitest";
import {
  BOARD_PERMISSION_MODULES,
  BOARD_PERMISSION_KEY_PREFIX,
  BOARD_NAV_KEYS,
  boardLevelFor,
} from "./staffPermissionTemplates";

describe("板块授权注册表", () => {
  it("覆盖侧栏 17 个主导航板块(对齐 navItems 主项,非 ops/v2),key 均以 board. 前缀", () => {
    expect(BOARD_PERMISSION_MODULES).toHaveLength(17);
    for (const m of BOARD_PERMISSION_MODULES) {
      expect(m.key).toBe(`${BOARD_PERMISSION_KEY_PREFIX}${m.navKey}`);
      expect(m.label.length).toBeGreaterThan(0);
      expect(m.group.startsWith("板块")).toBe(true);
    }
    // 关键导航键都在(17 主项抽样:核心 + 增长渠道 + 智能中枢 + 自动化)
    for (const k of ["dashboard", "my-kol", "kol-pool", "kolProfile", "shopify", "dealers", "events", "marketVoice", "replyQueue", "gtmCommand"]) {
      expect(BOARD_NAV_KEYS).toContain(k);
    }
    // 已归 V2 折叠组的旧 navKey 不再出现在选择器注册表里
    for (const k of ["campaigns", "intelligence", "attribution", "analytics", "reports", "signals", "agents", "p15"]) {
      expect(BOARD_NAV_KEYS).not.toContain(k);
    }
  });
});

describe("boardLevelFor —— 默认可见,显式可隐", () => {
  it("未设置该板块 → 默认 read(可见),避免现有成员侧栏空白", () => {
    expect(boardLevelFor({}, "shopify")).toBe("read");
    expect(boardLevelFor(null, "dashboard")).toBe("read");
    expect(boardLevelFor(undefined, "kol-pool")).toBe("read");
    // 设了别的板块,不影响未设的
    expect(boardLevelFor({ "board.dealers": "none" }, "shopify")).toBe("read");
  });

  it("显式 none → 隐藏;显式 read/write/admin 原样返回", () => {
    expect(boardLevelFor({ "board.shopify": "none" }, "shopify")).toBe("none");
    expect(boardLevelFor({ "board.shopify": "read" }, "shopify")).toBe("read");
    expect(boardLevelFor({ "board.shopify": "write" }, "shopify")).toBe("write");
    expect(boardLevelFor({ "board.shopify": "admin" }, "shopify")).toBe("admin");
  });

  it("读的是 board. 前缀键,不会和同名 tab(analytics)串", () => {
    // 只有裸 analytics(tab)被设 none,board.analytics 未设 → 仍默认可见
    expect(boardLevelFor({ analytics: "none" }, "analytics")).toBe("read");
    expect(boardLevelFor({ "board.analytics": "none" }, "analytics")).toBe("none");
  });

  it("脏值/大小写 → 归一,非法值回退默认 read", () => {
    expect(boardLevelFor({ "board.shopify": "ADMIN" }, "shopify")).toBe("admin");
    expect(boardLevelFor({ "board.shopify": "garbage" }, "shopify")).toBe("read");
  });
});
