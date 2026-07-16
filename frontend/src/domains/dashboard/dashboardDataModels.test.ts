import { describe, it, expect } from "vitest";

// W5 recon: dashboardDataModels 纯函数(无网络)。断言 cents→USD、metric 映射、
// funnel 优先级、leaderboard fallback、platform share 归一、alert severity 归并、scope context。
import {
  buildDashboardMetrics,
  buildDashboardRevenueTrend,
  buildDashboardFunnel,
  buildDashboardLeaderboard,
  buildDashboardProductRoi,
  buildDashboardPlatformShare,
  buildDashboardAlerts,
  buildDashboardKpiLedger,
  buildScopeContext,
  emptyEvidence,
  hasAnyDashboardData,
  buildWeeklySummary,
} from "./dashboardDataModels";

describe("buildDashboardMetrics", () => {
  it("空输入返回 6 张卡且键稳定", () => {
    const cards = buildDashboardMetrics([]);
    expect(cards.map((c) => c.key)).toEqual([
      "views",
      "cost",
      "gmv",
      "new_kol",
      "published_content",
      "active_projects",
    ]);
  });

  it("gmv/cost 按 cents→USD 折算并格式化为货币", () => {
    const cards = buildDashboardMetrics([
      { metric_key: "gmv", value_numeric: 123400, source_count: 3 },
      { metric_key: "cost", value_numeric: 50000 },
    ]);
    const gmv = cards.find((c) => c.key === "gmv")!;
    const cost = cards.find((c) => c.key === "cost")!;
    expect(gmv.value).toBe("$1,234");
    expect(cost.value).toBe("$500");
    expect(gmv.deltaLabel).toBe("来源 3 条");
  });

  it("无对应行的 metric 给「未生成快照」delta", () => {
    const cards = buildDashboardMetrics([{ metric_key: "views", value_numeric: 1000 }]);
    expect(cards.find((c) => c.key === "gmv")!.deltaLabel).toBe("未生成快照");
  });
});

describe("buildDashboardRevenueTrend", () => {
  it("空数组返回空", () => {
    expect(buildDashboardRevenueTrend([])).toEqual([]);
  });
  it("date 切片为 MM/DD 标签,sales 按 cents 折算", () => {
    const pts = buildDashboardRevenueTrend([
      { date: "2026-06-15", views: 200, sales_cents: 99900, cost_cents: 1000 },
    ]);
    expect(pts[0].label).toBe("06/15");
    expect(pts[0].views).toBe(200);
    expect(pts[0].sales).toBe(999);
    expect(pts[0].cost).toBe(10);
  });
});

describe("buildDashboardFunnel", () => {
  it("固定 8 阶段优先级 + rateLabel 相对 max 归一", () => {
    const funnel = buildDashboardFunnel([
      { stage: "claimed", count: 10 },
      { stage: "contacted", count: 5 },
    ]);
    expect(funnel).toHaveLength(8);
    expect(funnel[0].label).toBe("已认领");
    expect(funnel[0].value).toBe(10);
    expect(funnel[0].rateLabel).toBe("100.0%");
    expect(funnel[1].rateLabel).toBe("50.0%");
  });
});

describe("buildDashboardLeaderboard", () => {
  it("有 staffRows 时优先用 staffRows,首位 isTop", () => {
    const rows = buildDashboardLeaderboard(
      [{ staff_id: 1, staff_name: "A", gmv_cents: 200000 }],
      [{ staff_id: 9, staff_name: "Z" }],
    );
    expect(rows[0].name).toBe("A");
    expect(rows[0].gmv).toBe(2000);
    expect(rows[0].isTop).toBe(true);
  });
  it("staffRows 为空时回退到 fallbackRows", () => {
    const rows = buildDashboardLeaderboard([], [{ id: 3, name: "Fb" }]);
    expect(rows[0].name).toBe("Fb");
  });
});

describe("buildDashboardProductRoi", () => {
  it("roi = revenue/cost 保留两位", () => {
    const items = buildDashboardProductRoi([
      { product_name: "镜头", sales_cents: 30000, cost_cents: 10000 },
    ]);
    expect(items[0].product).toBe("镜头");
    expect(items[0].roi).toBe(3);
  });
  it("缺少成本来源时 roi=null,不把未知冒充 0", () => {
    const items = buildDashboardProductRoi([{ product_name: "X", sales_cents: 100 }]);
    expect(items[0].roi).toBeNull();
  });
  it("明确存在零成本来源时 roi=0", () => {
    const items = buildDashboardProductRoi([
      { product_name: "X", sales_cents: 100, cost_cents: 0, cost_source_count: 1 },
    ]);
    expect(items[0].roi).toBe(0);
  });
  it("空输入返回占位行", () => {
    expect(buildDashboardProductRoi([])[0].product).toBe("暂无项目数据");
    expect(buildDashboardProductRoi([])[0].roi).toBeNull();
  });
});

describe("buildDashboardPlatformShare", () => {
  it("按平台聚合并算百分比(总和≈100)", () => {
    const share = buildDashboardPlatformShare([
      { platform: "youtube", revenue_cents: 7500 },
      { platform: "tiktok", revenue_cents: 2500 },
    ]);
    const total = share.reduce((s, x) => s + x.value, 0);
    expect(Math.round(total)).toBe(100);
    const yt = share.find((x) => x.value === 75);
    expect(yt).toBeTruthy();
  });
  it("无归因金额时返回占位 100", () => {
    const share = buildDashboardPlatformShare([{ platform: "youtube", revenue_cents: 0 }]);
    expect(share).toEqual([{ label: "暂无归因", value: 100 }]);
  });
});

describe("buildDashboardAlerts(severity 归并 + triageGroup)", () => {
  it("critical/high → danger;warning → warning;其它 → info", () => {
    const alerts = buildDashboardAlerts([
      { id: 1, severity: "critical", title: "A" },
      { id: 2, severity: "warning", title: "B" },
      { id: 3, severity: "info", title: "C" },
    ]);
    expect(alerts[0].severity).toBe("danger");
    expect(alerts[1].severity).toBe("warning");
    expect(alerts[2].severity).toBe("info");
  });
  it("comment_intelligence 规则归到 comment_intelligence 组", () => {
    const alerts = buildDashboardAlerts([
      { id: 4, rule_key: "comment_intelligence.negative", title: "neg" },
    ]);
    expect(alerts[0].triageGroup).toBe("comment_intelligence");
  });
  it("metadata_json 字符串可解析 flagged_comments", () => {
    const alerts = buildDashboardAlerts([
      { id: 5, title: "x", metadata_json: '{"flagged_comments":7}' },
    ]);
    expect(alerts[0].count).toBe(7);
  });
});

describe("buildDashboardKpiLedger", () => {
  it("过滤无 id 的行并映射字段", () => {
    const rows = buildDashboardKpiLedger([
      { id: 11, metric_key: "views", metric_value: 9, ledger_date: "2026-06-15" },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].metricKey).toBe("views");
    expect(rows[0].metricValue).toBe(9);
  });
});

describe("buildScopeContext", () => {
  it("无 scope_mode → undefined", () => {
    expect(buildScopeContext({})).toBeUndefined();
    expect(buildScopeContext(undefined)).toBeUndefined();
  });
  it("有 scope_mode → 映射 id 字符串与布尔", () => {
    const ctx = buildScopeContext({
      scope_mode: "own",
      effective_staff_id: 42,
      can_view_all: true,
      is_owner: false,
    });
    expect(ctx?.scopeMode).toBe("own");
    expect(ctx?.effectiveStaffId).toBe("42");
    expect(ctx?.canViewAll).toBe(true);
    expect(ctx?.isOwner).toBe(false);
  });
});

describe("emptyEvidence / hasAnyDashboardData", () => {
  it("emptyEvidence 全是空数组", () => {
    const ev = emptyEvidence();
    expect(Object.values(ev).every((v) => Array.isArray(v) && v.length === 0)).toBe(true);
  });
  it("全空 → false", () => {
    expect(hasAnyDashboardData({}, [], [], [], [], [], [])).toBe(false);
  });
  it("有 metric source_count → true", () => {
    expect(hasAnyDashboardData({}, [], [], [], [], [], [{ source_count: 2 }])).toBe(true);
  });
  it("有 projects → true", () => {
    expect(hasAnyDashboardData({}, [{}], [], [], [], [], [])).toBe(true);
  });
});

describe("buildWeeklySummary", () => {
  it("含成员数与未处理提醒数,金额按 cents 折算", () => {
    const text = buildWeeklySummary(
      { revenue_cents: 100000, cost_cents: 50000, total_views: 1000 },
      [{}, {}],
      [{}],
    );
    expect(text).toContain("$1,000");
    expect(text).toContain("$500");
    expect(text).toContain("2 名成员");
    expect(text).toContain("1 条未处理提醒");
  });
});
