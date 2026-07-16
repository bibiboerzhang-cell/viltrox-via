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
import { MANAGER_NAV_ITEMS } from "./vkpiLayoutConstants";

// P2-6: 路由门禁纯逻辑(零 mock)。覆盖 page-key 识别、候选清洗、归一化、访问门禁、越权回退、
// 以及触 window 的 getInitial/writeHash。manager 全量、employee 仅 EMPLOYEE_ALLOWED_PAGES。

describe("isVkpiPageKey 页面键识别", () => {
  it("已知键 → true", () => {
    expect(isVkpiPageKey("projects")).toBe(true);
    expect(isVkpiPageKey("cockpit")).toBe(true);
    expect(isVkpiPageKey("settings")).toBe(true);
    expect(isVkpiPageKey("dataQuery")).toBe(true);
    expect(isVkpiPageKey("marketTrends")).toBe(true);
    expect(isVkpiPageKey("skillStudio")).toBe(true);
  });
  it("未知串 → false", () => {
    expect(isVkpiPageKey("not-a-page")).toBe(false);
    expect(isVkpiPageKey("")).toBe(false);
  });
  it("管理侧栏中的每个真页面都已注册", () => {
    expect(MANAGER_NAV_ITEMS.filter((item) => !isVkpiPageKey(item.key))).toEqual([]);
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
  it("manager:command/dashboard → cockpit(默认管理页)", () => {
    expect(normalizeVkpiPage("command", "manager")).toBe("cockpit");
    expect(normalizeVkpiPage("dashboard", "manager")).toBe("cockpit");
  });
  it("manager:已知页原样返回", () => {
    expect(normalizeVkpiPage("projects", "manager")).toBe("projects");
    expect(normalizeVkpiPage("#/costs?x=1", "manager")).toBe("costs");
  });
  it("manager:未知串 → 默认管理页 cockpit", () => {
    expect(normalizeVkpiPage("garbage", "manager")).toBe("cockpit");
  });
  it("manager:遗留 repairCenter/ops → 真实 dataQuality 页面", () => {
    expect(normalizeVkpiPage("repairCenter", "manager")).toBe("dataQuality");
    expect(normalizeVkpiPage("#/ops?from=legacy", "manager")).toBe("dataQuality");
  });
  it("employee:遗留运维目标仍受页面门禁保护", () => {
    expect(normalizeVkpiPage("repairCenter", "employee")).toBe("cockpit");
    expect(normalizeVkpiPage("ops", "employee")).toBe("cockpit");
  });
  it("employee:管理层专属页(costs/audit)→ 回退 cockpit", () => {
    expect(normalizeVkpiPage("costs", "employee")).toBe("cockpit");
    expect(normalizeVkpiPage("audit", "employee")).toBe("cockpit");
  });
  it("employee:允许页原样返回", () => {
    expect(normalizeVkpiPage("projects", "employee")).toBe("projects");
    expect(normalizeVkpiPage("links", "employee")).toBe("links");
  });
  it("employee:dashboard → 默认员工页", () => {
    expect(normalizeVkpiPage("dashboard", "employee")).toBe("cockpit");
  });
  it("employee:未知串 → 默认员工页", () => {
    expect(normalizeVkpiPage("garbage", "employee")).toBe("cockpit");
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
    expect(EMPLOYEE_ALLOWED_PAGES.has("cockpit")).toBe(true);
  });
});

describe("enforcePageAccess 越权回退", () => {
  it("可访问 → 原样返回", () => {
    expect(enforcePageAccess("projects", "employee")).toBe("projects");
    expect(enforcePageAccess("costs", "manager")).toBe("costs");
  });
  it("employee 越权(costs)→ 回退默认员工页", () => {
    expect(enforcePageAccess("costs", "employee")).toBe("cockpit");
  });
  it("manager 未知页 → 回退默认管理页", () => {
    expect(enforcePageAccess("garbage", "manager")).toBe("cockpit");
  });
});

describe("getInitialVkpiPage / writeVkpiHash 触 window", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });
  it("无 hash/query → 默认页", () => {
    expect(getInitialVkpiPage("manager")).toBe("cockpit");
    expect(getInitialVkpiPage("employee")).toBe("cockpit");
  });
  it("hash 命中已知页 → 归一化结果", () => {
    window.history.replaceState(null, "", "#projects");
    expect(getInitialVkpiPage("manager")).toBe("projects");
  });
  it("遗留 ops hash → dataQuality，不回落首页", () => {
    window.history.replaceState(null, "", "#ops");
    expect(getInitialVkpiPage("manager")).toBe("dataQuality");
  });
  it("employee + hash=costs → 越权回退默认员工页", () => {
    window.history.replaceState(null, "", "#costs");
    expect(getInitialVkpiPage("employee")).toBe("cockpit");
  });
  it("writeVkpiHash 写入 location.hash", () => {
    writeVkpiHash("projects");
    expect(window.location.hash).toBe("#projects");
  });
  it("writeVkpiHash 只替换 hash，保留 pathname 和 Cockpit 深链 query", () => {
    window.history.replaceState(null, "", "/workspace?cockpit=dealers&source=release#cockpit");

    expect(writeVkpiHash("projects")).toBe(true);
    expect(window.location.pathname).toBe("/workspace");
    expect(window.location.search).toBe("?cockpit=dealers&source=release");
    expect(window.location.hash).toBe("#projects");
  });
  it("writeVkpiHash 对相同 hash 幂等且只派发一次事件", () => {
    let events = 0;
    const onHashChange = () => { events += 1; };
    window.addEventListener("hashchange", onHashChange);

    expect(writeVkpiHash("projects")).toBe(true);
    expect(writeVkpiHash("projects")).toBe(false);
    expect(events).toBe(1);

    window.removeEventListener("hashchange", onHashChange);
  });
});
