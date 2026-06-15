import { describe, it, expect } from "vitest";
import {
  buildDashboardAccountKpiCards,
  accountDisplayName,
  type DashboardAccountKpi,
  type DashboardAccountRow,
} from "./accountPicker";
import { compact } from "./dashboardModel";

// P2-6: Dashboard 账号选择器前端模型(零 mock,compact 真实 import)。
// 覆盖 null → 6 张待接入;真实 payload → Active Roster=compact、Engagement realOrEmpty、
// gmvCard/roiCard 三态(真实 / awaiting_data 文案 / 待接入);accountDisplayName 别名兜底链。

function basePayload(overrides: Partial<DashboardAccountKpi> = {}): DashboardAccountKpi {
  return {
    account_type: "all",
    active_roster: 12,
    total_roster: 40,
    active_30d: 8,
    total_exposure: 0,
    total_followers: 0,
    engagement_rate: 0,
    attributed_gmv: 0,
    avg_roi: null,
    ...overrides,
  };
}

function findCard(cards: ReturnType<typeof buildDashboardAccountKpiCards>, label: string) {
  return cards.find((c) => c.label === label)!;
}

describe("buildDashboardAccountKpiCards(null)", () => {
  const cards = buildDashboardAccountKpiCards(null);
  it("返回 6 张卡", () => {
    expect(cards).toHaveLength(6);
  });
  it("全部 pending=true + status=待接入", () => {
    expect(cards.every((c) => c.pending === true && c.status === "待接入")).toBe(true);
  });
});

describe("buildDashboardAccountKpiCards 真实 payload", () => {
  it("Active Roster = compact(active),含 total total 标注", () => {
    const cards = buildDashboardAccountKpiCards(basePayload({ active_roster: 12, total_roster: 40 }));
    const card = findCard(cards, "Active Roster");
    expect(card.value).toBe(compact(12));
    expect(card.status).toBe("真实");
    expect(card.meta).toContain(compact(40));
  });

  it("Active 30D = compact(active_30d)", () => {
    const cards = buildDashboardAccountKpiCards(basePayload({ active_30d: 8 }));
    expect(findCard(cards, "Active 30D").value).toBe(compact(8));
  });

  it("Engagement R. > 0 → 真实(realOrEmpty),百分比格式", () => {
    const cards = buildDashboardAccountKpiCards(basePayload({ engagement_rate: 3.5 }));
    const card = findCard(cards, "Engagement R.");
    expect(card.value).toBe("3.50%");
    expect(card.status).toBe("真实");
    expect(card.pending).toBeUndefined();
  });

  it("Engagement R. = 0 → 待接入 pending", () => {
    const cards = buildDashboardAccountKpiCards(basePayload({ engagement_rate: 0 }));
    const card = findCard(cards, "Engagement R.");
    expect(card.value).toBe("--");
    expect(card.pending).toBe(true);
  });

  it("Total Exposure > 0 → 真实;= 0 → 待接入", () => {
    const real = findCard(buildDashboardAccountKpiCards(basePayload({ total_exposure: 50000 })), "Total Exposure");
    expect(real.status).toBe("真实");
    const empty = findCard(buildDashboardAccountKpiCards(basePayload({ total_exposure: 0 })), "Total Exposure");
    expect(empty.value).toBe("--");
    expect(empty.pending).toBe(true);
  });
});

describe("gmvCard 三态", () => {
  it("gmv > 0 → 真实,带金额", () => {
    const card = findCard(buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 12000 })), "Attributed GMV");
    expect(card.status).toBe("真实");
    expect(card.value).toContain("$");
  });
  it("gmv=0 且 metric_source.gmv=awaiting_data → 「已连接 · 等待确认归因订单」文案", () => {
    const card = findCard(
      buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 0, metric_source: { gmv: "awaiting_data" } })),
      "Attributed GMV",
    );
    expect(card.pending).toBe(true);
    expect(card.meta).toContain("已连接");
  });
  it("gmv=0 无 awaiting → 「待 Shopify 归因接入」", () => {
    const card = findCard(buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 0 })), "Attributed GMV");
    expect(card.meta).toContain("待 Shopify");
  });
});

describe("roiCard 三态", () => {
  it("gmv>0 且 avg_roi 非空 → Nx 真实", () => {
    const card = findCard(
      buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 12000, avg_roi: 3.2 })),
      "Avg ROI",
    );
    expect(card.status).toBe("真实");
    expect(card.value).toBe("3.2x");
  });
  it("gmv>0 但 avg_roi=null → 待接入", () => {
    const card = findCard(
      buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 12000, avg_roi: null })),
      "Avg ROI",
    );
    expect(card.pending).toBe(true);
  });
  it("gmv=0 且 metric_source.roi=awaiting_data → 已连接文案", () => {
    const card = findCard(
      buildDashboardAccountKpiCards(basePayload({ attributed_gmv: 0, avg_roi: 5, metric_source: { roi: "awaiting_data" } })),
      "Avg ROI",
    );
    expect(card.meta).toContain("已连接");
  });
});

describe("accountDisplayName 别名兜底链", () => {
  function row(overrides: Partial<DashboardAccountRow>): DashboardAccountRow {
    return {
      id: "x",
      dashboard_id: "d",
      source_id: 0,
      source_table: "t",
      handle: "",
      name: "",
      platform: "",
      country: "",
      tier: "",
      account_type: "all",
      followers: 0,
      posts_count: 0,
      total_views: 0,
      total_likes: 0,
      total_comments: 0,
      total_shares: 0,
      engagement_rate: 0,
      profile_url: "",
      avatar_url: "",
      is_active: true,
      interactions: 0,
      ...overrides,
    };
  }
  it("有 name → name", () => {
    expect(accountDisplayName(row({ name: "Alice", handle: "@a" }))).toBe("Alice");
  });
  it("无 name 有 handle → handle", () => {
    expect(accountDisplayName(row({ handle: "@a" }))).toBe("@a");
  });
  it("name/handle 皆空 → Account {source_id}", () => {
    expect(accountDisplayName(row({ source_id: 7 }))).toBe("Account 7");
  });
});
