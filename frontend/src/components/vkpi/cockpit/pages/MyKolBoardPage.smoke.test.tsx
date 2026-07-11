import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// MY KOL 改版 M1 骨架冒烟(金样板 MarketVoicePage.smoke 同构):
// - 页壳:pagehead(MY KOL + 视角/KOL 数药丸徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡(K1 在库 KOL / K2 合作推进中 / K3 内容播放实测 / K4 官号粉丝):
//   现值全真(aggregate.kpi_summary / 主控 evidence_metrics 注入 / official-matrix Σ followers),
//   无历史快照 → sparkline 一律 demo .spempty 纯虚线、环比药丸一律不渲染(诚实);
// - 注册表 manager vs employee 差异(裁决②A):risk/rollup 员工注册表直接不出现,
//   连专属端点都不该被请求(不是 403 卡);
// - 布局键 vkpi-my-kol-layout-v1 生效 + 不传 apiToken → 绝不写账户级 dashboard_layout_v1;
// - 诚实空态:aggregate 失败 = 错误卡;K3 主控指标未注入 = pending 不编数。
// mock seam:services/http.apiFetch(全页唯一网络出口——kol-api / channel-api /
// DailyDigestCard / RiskIndexPanel 全都收敛到它),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MyKolBoardPage } from "./MyKolBoardPage";

// aggregate 现值(kpi_summary 形状对齐 my_kol_aggregate._kpi_summary)
const AGG_OK = {
  staff: { id: 3, name: "Boss", email: "boss@viltrox.com" },
  window_days: 30,
  official_matrix: { platforms: [], account_count: 0, total_followers: 0, total_posts: 0, total_views: 0 },
  pool_favorites: [],
  projects: [],
  claims: [],
  kpi_summary: { favorites_count: 719, claimed_count: 12, in_project_count: 87, published_count: 45, projects_count: 40 },
};

// official-matrix(mapPlatform/mapAccount 读 snake_case)
const MATRIX_OK = {
  platforms: [
    {
      platform: "youtube",
      label: "YouTube",
      total_followers: 251000,
      total_posts: 1200,
      total_views: 98000000,
      accounts: [
        {
          id: 11,
          staff_id: 5,
          staff_name: "Alice",
          staff_email: "alice@viltrox.com",
          platform: "youtube",
          handle: "@viltrox",
          display_name: "VILTROX",
          sync_status: "synced",
          followers: 251000,
          posts_count: 1200,
          total_views: 98000000,
          posts: [],
        },
      ],
    },
  ],
  account_count: 1,
  post_count: 1200,
  total_views: 98000000,
  staff_managed: [],
};

// daily-digest:整体 ready + kol 块诚实 empty(带 reason),official 块 empty
const DIGEST_OK = {
  status: "ready",
  window: { days: 1, since: "2026-07-10", until: "2026-07-11" },
  scope: { staff_id: 3, mode: "all", kol_count: 0 },
  kol: { status: "empty", reason: "窗口内无收藏 KOL 变化。" },
  official: { status: "empty", reason: "无官号快照。" },
};

const RISK_OK = {
  items: [],
  summary: {
    kol_count: 0,
    analyzed_kol_count: 0,
    unanalyzed_kol_count: 0,
    coverage_ratio: 0,
    high_risk_count: 0,
    weights: {},
    deep_table_available: true,
    notes: {},
  },
  staff_id: 3,
};

const ROLLUP_OK = { items: [], window_days: 90, scope: {}, data_notes: {} };

// CockpitApp dashboardRuntime.metrics 注入形状(normalizeDashboardMetrics 产物切片):
// K3 = exposure.all.value(SUM view_count 点时实测,≈20.5 亿)
const METRICS_PROP = [
  {
    id: "exposure",
    label: "Total Exposure",
    data: { all: { value: 2_052_179_053, trend: "实时 · evidence 覆盖 77%", source: "real" } },
  },
];

const DATA: any = { projects: [], staffMembers: [], kolOptions: [] };

function routeApi(overrides: { aggregate?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/my-kol/aggregate")) {
      const value = overrides.aggregate ?? AGG_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/marketing/channels/official-matrix")) return MATRIX_OK;
    if (p.startsWith("/api/marketing/channels/") && p.includes("/posts")) {
      return { account: {}, posts: [], pagination: { page: 1, pages: 1, total: 0 } };
    }
    if (p.startsWith("/api/admin/vkpi/my-kol/daily-digest")) return DIGEST_OK;
    if (p.startsWith("/api/admin/vkpi/my-kol/risk-index")) return RISK_OK;
    if (p.startsWith("/api/admin/vkpi/my-kol/contribution-rollup")) return ROLLUP_OK;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(
    <MyKolBoardPage
      apiToken="t"
      viewMode="manager"
      data={DATA}
      metrics={METRICS_PROP as any}
      {...(props as any)}
    />,
  );

beforeEach(() => {
  window.localStorage.clear();
  routeApi();
});

describe("MyKolBoardPage smoke (M1 骨架:页壳 + KPI 带 + 注册表 + 布局键 v1)", () => {
  it("页壳 + KPI 带四卡:现值全真,series 诚实 spempty、delta 不渲染,口径进 SrcChip", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 现值先落位(等 aggregate/matrix 解析):K1=719 / K2=87 / K3=20.5亿 / K4=25.1万
    expect(await screen.findByText("20.5亿")).toBeTruthy();
    expect(await screen.findByText("25.1万")).toBeTruthy();
    expect(screen.getAllByText("719").length).toBeGreaterThan(0);
    expect(screen.getAllByText("87").length).toBeGreaterThan(0);
    // 四卡设计单语义(K1-K4;标签同时是 SrcChip 口径行键 → 取 ≥1)
    expect(screen.getAllByText("在库 KOL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("合作推进中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("内容播放实测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官号粉丝").length).toBeGreaterThan(0);
    // 无历史快照 → 四卡全部 demo .spempty 纯虚线;环比药丸/真 sparkline 一枚都不许有
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);

    // pagehead:视角徽 + KOL 数徽(aggregate 真数)+ 编辑布局钮
    expect(screen.getByText("管理层视角")).toBeTruthy();
    expect(await screen.findByText("719 KOL")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // K3 播放口径「点时实测 · 非时序」写进 SrcChip 溯源行(设计单硬要求)
    expect(screen.getAllByText(/点时实测 · 非时序/).length).toBeGreaterThan(0);
    // 真表名溯源行在场
    expect(screen.getAllByText(/vkpi_kol_pool_favorites/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_channel_metrics/).length).toBeGreaterThan(0);

    // 现成端点确实被调(本刀零新端点)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.startsWith("/api/admin/vkpi/my-kol/aggregate"))).toBe(true);
    expect(calledPaths.some((p) => p.startsWith("/api/marketing/channels/official-matrix"))).toBe(true);
  });

  it("默认布局六行(manager):内嵌模块 + 待接线模块在场;palette 备选不进默认", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    // 内嵌现有组件的模块(digest/team/official/risk/rollup)
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("团队矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 风险指数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("贡献度聚合").length).toBeGreaterThan(0);
    // 待接线模块 = PendingCard 诚实说明(不摆假数据)
    expect(screen.getAllByText("四环漏斗").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 库").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fit 分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("平台分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/本刀诚实待接不摆假漏斗/).length).toBeGreaterThan(0);
    // palette 备选不进默认布局
    expect(screen.queryByText("播放 Top 视频")).toBeNull();
    expect(screen.queryByText("联系方式覆盖")).toBeNull();
    expect(screen.queryByText("粉丝趋势")).toBeNull();
    expect(screen.queryByText("认领状态")).toBeNull();
    expect(screen.queryByText("共享池")).toBeNull();
    expect(screen.queryByText("数据覆盖")).toBeNull();
  });

  it("employee 注册表差异(裁决②A):risk/rollup 直接不出现,专属端点零请求,不是 403 卡", async () => {
    renderBoard({ viewMode: "employee", userName: "Momo" });
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    // own-only 视角徽
    expect(screen.getByText("Momo · own-only")).toBeTruthy();
    // 管理层专属模块在员工注册表里根本不存在(默认布局自动少两块)
    expect(screen.queryByText("KOL 风险指数")).toBeNull();
    expect(screen.queryByText("贡献度聚合")).toBeNull();
    // 专属端点连请求都不该发(不是渲染出来再 403)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("risk-index"))).toBe(false);
    expect(calledPaths.some((p) => p.includes("contribution-rollup"))).toBe(false);
    // 共同模块照常在场
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出六个备选(全部诚实待接)", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(await screen.findByText("播放 Top 视频")).toBeTruthy();
    expect(screen.getByText("联系方式覆盖")).toBeTruthy();
    expect(screen.getByText("粉丝趋势")).toBeTruthy();
    expect(screen.getByText("认领状态")).toBeTruthy();
    expect(screen.getByText("共享池")).toBeTruthy();
    expect(screen.getByText("数据覆盖")).toBeTruthy();
  });

  it("布局键 vkpi-my-kol-layout-v1 生效;不传 apiToken → 绝不写账户级 dashboard 布局", async () => {
    // 预置本机布局只留 kpiM → 其余默认模块不渲染(storageKey 被真实读取)
    window.localStorage.setItem("vkpi-my-kol-layout-v1", JSON.stringify([{ moduleKey: "kpiM", span: 12 }]));
    renderBoard();
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    expect(screen.queryByText("每日学习摘要")).toBeNull();
    expect(screen.queryByText("四环漏斗")).toBeNull();
    expect(screen.queryByText("官方账号矩阵")).toBeNull();
    // 金样板同注释:不给 EditableDashboardBoard 传 apiToken → 账户级偏好接口零调用
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("preference"))).toBe(false);
  });

  it("aggregate 失败 → 诚实错误卡(绝不假数据)", async () => {
    routeApi({ aggregate: new Error("HTTP 500") });
    renderBoard();
    expect((await screen.findAllByText("my-kol/aggregate 读取失败")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/HTTP 500/)).length).toBeGreaterThan(0);
    // KPI 现值不许出现(不编数)
    expect(screen.queryByText("719")).toBeNull();
  });

  it("K3 主控指标未注入 → 诚实 pending(—,带说明),其余三卡照常", async () => {
    renderBoard({ metrics: undefined });
    expect((await screen.findAllByText("719")).length).toBeGreaterThan(0);
    expect(screen.getByText("主控 evidence_metrics 未注入 · M2 board-ext 自取")).toBeTruthy();
    expect(screen.queryByText("20.5亿")).toBeNull();
  });
});
