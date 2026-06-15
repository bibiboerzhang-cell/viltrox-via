import { describe, it, expect, beforeEach } from "vitest";
import {
  isVkpiPageKey,
  cleanVkpiPageCandidate,
  normalizeVkpiPage,
  canAccessPage,
  enforcePageAccess,
  getInitialVkpiPage,
  writeVkpiHash,
  EMPLOYEE_ALLOWED_PAGES,
} from "./vkpiDashboardRouting";

// P2-6: 路由门禁纯逻辑(零 mock)。覆盖 page-key 识别、候选清洗、归一化、访问门禁、越权回退、
// 以及触 window 的 getInitial/writeHash。manager 全量、employee 仅 EMPLOYEE_ALLOWED_PAGES。

describe("isVkpiPageKey 页面键识别", () => {
  it("已知键 → true", () => {
    expect(isVkpiPageKey("projects")).toBe(true);
    expect(isVkpiPageKey("v615Replica")).toBe(true);
    expect(isVkpiPageKey("settings")).toBe(true);
  });
  it("未知串 → false", () => {
    expect(isVkpiPageKey("not-a-page")).toBe(false);
    expect(isVkpiPageKey("")).toBe(false);
  });
});

describe("cleanVkpiPageCandidate 候选清洗", () => {
  it("剥离 hash 前缀与 query/锚点", () => {
    expect(cleanVkpiPageCandidate("#/projects?x=1")).toBe("projects");
    expect(cleanVkpiPageCandidate("#projects")).toBe("projects");
    expect(cleanVkpiPageCandidate("projects#frag")).toBe("projects");
    expect(cleanVkpiPageCandidate("  projects  ")).toBe("projects");
  });
  it("空串 → 空", () => {
    expect(cleanVkpiPageCandidate("")).toBe("");
  });
});

describe("normalizeVkpiPage 归一化分支", () => {
  it("manager:command/dashboard → v615Replica(默认管理页)", () => {
    expect(normalizeVkpiPage("command", "manager")).toBe("v615Replica");
    expect(normalizeVkpiPage("dashboard", "manager")).toBe("v615Replica");
  });
  it("manager:已知页原样返回", () => {
    expect(normalizeVkpiPage("projects", "manager")).toBe("projects");
    expect(normalizeVkpiPage("#/costs?x=1", "manager")).toBe("costs");
  });
  it("manager:未知串 → 默认管理页 v615Replica", () => {
    expect(normalizeVkpiPage("garbage", "manager")).toBe("v615Replica");
  });
  it("employee:管理层专属页(costs/audit)→ 回退 v615Replica", () => {
    expect(normalizeVkpiPage("costs", "employee")).toBe("v615Replica");
    expect(normalizeVkpiPage("audit", "employee")).toBe("v615Replica");
  });
  it("employee:允许页原样返回", () => {
    expect(normalizeVkpiPage("projects", "employee")).toBe("projects");
    expect(normalizeVkpiPage("links", "employee")).toBe("links");
  });
  it("employee:dashboard → 默认员工页", () => {
    expect(normalizeVkpiPage("dashboard", "employee")).toBe("v615Replica");
  });
  it("employee:未知串 → 默认员工页", () => {
    expect(normalizeVkpiPage("garbage", "employee")).toBe("v615Replica");
  });
});

describe("canAccessPage 访问门禁", () => {
  it("manager 可达全部已知页", () => {
    expect(canAccessPage("costs", "manager")).toBe(true);
    expect(canAccessPage("audit", "manager")).toBe(true);
    expect(canAccessPage("projects", "manager")).toBe(true);
  });
  it("employee 仅 EMPLOYEE_ALLOWED_PAGES", () => {
    expect(canAccessPage("projects", "employee")).toBe(true);
    expect(canAccessPage("settings", "employee")).toBe(true);
    expect(canAccessPage("costs", "employee")).toBe(false);
    expect(canAccessPage("audit", "employee")).toBe(false);
  });
  it("EMPLOYEE_ALLOWED_PAGES 不含管理层专属页", () => {
    expect(EMPLOYEE_ALLOWED_PAGES.has("costs")).toBe(false);
    expect(EMPLOYEE_ALLOWED_PAGES.has("audit")).toBe(false);
    expect(EMPLOYEE_ALLOWED_PAGES.has("v615Replica")).toBe(true);
  });
});

describe("enforcePageAccess 越权回退", () => {
  it("可访问 → 原样返回", () => {
    expect(enforcePageAccess("projects", "employee")).toBe("projects");
    expect(enforcePageAccess("costs", "manager")).toBe("costs");
  });
  it("employee 越权(costs)→ 回退默认员工页", () => {
    expect(enforcePageAccess("costs", "employee")).toBe("v615Replica");
  });
  it("manager 未知页 → 回退默认管理页", () => {
    expect(enforcePageAccess("garbage", "manager")).toBe("v615Replica");
  });
});

describe("getInitialVkpiPage / writeVkpiHash 触 window", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });
  it("无 hash/query → 默认页", () => {
    expect(getInitialVkpiPage("manager")).toBe("v615Replica");
    expect(getInitialVkpiPage("employee")).toBe("v615Replica");
  });
  it("hash 命中已知页 → 归一化结果", () => {
    window.history.replaceState(null, "", "#projects");
    expect(getInitialVkpiPage("manager")).toBe("projects");
  });
  it("employee + hash=costs → 越权回退默认员工页", () => {
    window.history.replaceState(null, "", "#costs");
    expect(getInitialVkpiPage("employee")).toBe("v615Replica");
  });
  it("writeVkpiHash 写入 location.hash", () => {
    writeVkpiHash("projects");
    expect(window.location.hash).toBe("#projects");
  });
});
