import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// KOL 池改版冒烟(金样板 MarketVoicePage/MyKolBoardPage/KolProfileBoardPage.smoke 同构):
// - 页壳:pagehead(KOL 池 + KOL 数/数据截至真值徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡真值:在池总数 / 本周新发现(created_at 7 天窗)/ 已深析
//   (llm_deep_analysis_count>0 的 KOL 数)/ 低触达暂不推荐(kol-pool/summary
//   low_reach_hidden_count);四指标全点时快照 → 4 卡 spempty 诚实虚线、零 delta 药丸;
//   summary 键缺席 → 第 4 卡诚实 pending(绝不编 0);
// - 默认布局七模块在场(kpiK/smart/recs/kinds/lanes/needs/coverage),
//   table=palette 备选不进默认;
// - 零丢失:找达人(SmartKolInputPanel 内嵌,触达二段闸行为零改动)/ 分类速览
//   (KPIBar 内嵌,总数卡开全量大窗)/ 待深析(needs-analysis 清单 + 批量入队)/
//   任务进度(TaskProgressBoard 内嵌)/ 海外市场覆盖(MarketCoverageCard 内嵌)/
//   推荐卡片流 + 筛选次级展开 / 详情抽屉 · 联系弹窗 · 全量大窗页级 overlay;
// - 跨页事件:vkpi:open-kol-pool-search 消费 pending 关键词 → 填筛选并展开;
//   vkpi:open-kol-pool-item 消费 pending id → 开抽屉(拉 detail-bundle);
// - 溯源:真实表名只进 SrcChip rows(vkpi_kol_pool / vkpi_kol_llm_deep_analysis_results
//   / kol-pool/summary),卡面零术语;
// - 布局键 vkpi-kol-pool-layout-v1 本机记忆;不传 apiToken → 绝不写账户级
//   dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { KolPoolBoardPage } from "./KolPoolBoardPage";

const now = Date.now();
const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
const DAY = 24 * 3600 * 1000;

// 池行(kolPoolRows 形状:toCockpitKolPoolRows 产物的消费键子集):
// Alpha=本周新发现 + 已深析;Beta=老行未深析;Gamma=已深析老行。
const ITEMS = [
  {
    id: 101, handle: "@alpha", display_name: "Alpha Cam", platform: "youtube", country: "US",
    followers: 120000, avg_views: 45000, v6_fit: 82, candidate_kind: "existing",
    created_at: iso(2 * DAY), last_seen_at: "2026-07-11T00:00:00Z",
    llm_deep_analysis_count: 3, video_evidence_count: 5,
    geo_distribution: [{ country: "US", share: 1 }],
    estimated_country_reach: { US: 45000 },
    devices: { camera_body: "Sony A7 IV", lenses: [], has_viltrox: true, competitor_brands: [] },
  },
  {
    id: 102, handle: "@beta", display_name: "Beta Vlog", platform: "instagram", country: "DE",
    followers: 5600, avg_views: 900, v6_fit: null, candidate_kind: "new_discovered",
    created_at: iso(40 * DAY), last_seen_at: "2026-07-10T00:00:00Z",
    llm_deep_analysis_count: 0, video_evidence_count: 0,
    geo_distribution: [{ country: "DE", share: 1 }],
    estimated_country_reach: { DE: 900 },
    devices: { camera_body: "", lenses: [], has_viltrox: false, competitor_brands: [] },
  },
  {
    id: 103, handle: "@gamma", display_name: "Gamma", platform: "tiktok", country: "",
    followers: null, avg_views: null, v6_fit: 55, candidate_kind: "existing",
    created_at: iso(60 * DAY), last_seen_at: "2026-07-09T00:00:00Z",
    llm_deep_analysis_count: 1, video_evidence_count: 2,
    geo_distribution: [],
    estimated_country_reach: null,
    devices: { camera_body: "", lenses: [], has_viltrox: false, competitor_brands: ["Sigma"] },
  },
];

const NEEDS_OK = {
  items: [{ kol_pool_id: 103, handle: "@gamma", platform: "tiktok", evidence_id: 77, evidence_count: 2 }],
  count: 1,
};

const SUMMARY_OK = { total: 1237, low_reach_hidden_count: 81 };

const BUNDLE_OK = {
  status: "ready",
  kol_pool_id: 101,
  item: ITEMS[0],
  video_analysis: { items: [], summary: { evidence_count: 5, ready_count: 3 } },
};

function routeApi(overrides: { summary?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.includes("/kol-pool/favorites")) return { items: [] };
    if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
    if (p.includes("/kol-pool/summary")) return overrides.summary ?? SUMMARY_OK;
    if (p.includes("/detail-bundle")) return BUNDLE_OK;
    if (p.includes("/kol-search-history")) return { items: [] };
    if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
    if (p.includes("/signature")) return { status: "empty" };
    if (p.includes("/cooperation")) return { status: "none", events: [] };
    if (p.includes("/audience-geo")) return { status: "empty", countries: [], signals: [] };
    // 抽屉/内嵌组件的其余读端点:空形状兜底(冒烟只验挂载与接线,不验其内容)
    return {};
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(
    <KolPoolBoardPage
      items={ITEMS as any}
      loading={false}
      error=""
      apiToken="t"
      staff={[]}
      {...(props as any)}
    />,
  );

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("KolPoolBoardPage smoke(页壳 + KPI 带真值 + 注册表 + 零丢失接线 + 事件管道)", () => {
  it("KPI 带四卡真值(总数 3 / 本周新 1 / 已深析 2 / 低触达 81);点时快照 → 4 卡诚实虚线零药丸", async () => {
    expect(() => renderBoard()).not.toThrow();

    expect(screen.getAllByText("在池总数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("本周新发现").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已深析").length).toBeGreaterThan(0);
    expect(screen.getAllByText("低触达 · 暂不推荐").length).toBeGreaterThan(0);

    // 真值:3 行池 / created_at 2 天前 ×1 / llm_deep_analysis_count>0 ×2(共 4 条结果)
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    expect(kpis[0].textContent).toContain("3");
    expect(kpis[1].textContent).toContain("1");
    expect(kpis[2].textContent).toContain("2");
    expect(kpis[2].textContent).toContain("4 份结果");
    // 低触达 = kol-pool/summary 真值 81
    await waitFor(() => expect(kpis[3].textContent).toContain("81"));

    // 点时快照:4 卡全 spempty 虚线,零 sparkline 零环比药丸(绝不编时序)
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead 真值徽
    expect(screen.getByText("3 KOL")).toBeTruthy();
    expect(screen.getByText("数据截至 2026-07-11")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
  });

  it("summary 键缺席 → 低触达卡诚实 pending(绝不编 0)", async () => {
    routeApi({ summary: { total: 1237 } });
    renderBoard();
    await waitFor(() => {
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/kol-pool/summary"))).toBe(true);
    });
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis[3].textContent).toContain("计数暂不可用");
    expect(kpis[3].textContent).not.toContain("81");
  });

  it("默认布局七模块在场;table=palette 备选不进默认;零丢失:找达人/分类速览/待深析/任务进度/覆盖", async () => {
    renderBoard();
    for (const title of ["池子指标带", "找达人", "推荐 · 卡片流", "分类速览", "任务进度", "待深析", "海外市场覆盖"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("表格视图")).toBeNull();

    // 找达人内嵌真身(SmartKolInputPanel 输入框 + 触达闸所在结果区容器,行为零改动)
    expect(screen.getByTestId("smart-kol-input-panel")).toBeTruthy();
    expect(screen.getByTestId("smart-kol-input")).toBeTruthy();

    // 分类速览 = KPIBar 内嵌真身(旧卡文案原样;推导口径进 SrcChip)
    expect(screen.getByText("Pool 总数")).toBeTruthy();

    // 待深析清单真值(needs-analysis 1 行 + 一键全部分析)
    expect(await screen.findByText("全部分析 (1)")).toBeTruthy();
    expect(screen.getAllByText("@gamma").length).toBeGreaterThan(0);

    // 卡片流:结果计数 + 筛选次级展开钮
    expect(screen.getByText("筛选 · 排序")).toBeTruthy();

    // needs-analysis / favorites / summary 三读端点都被调
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    for (const seg of ["/kol-pool/needs-analysis", "/kol-pool/favorites", "/kol-pool/summary"]) {
      expect(calledPaths.some((p) => p.includes(seg))).toBe(true);
    }
  });

  it("分类速览总数卡 → 全量大窗;点卡片流卡片 → 详情抽屉(拉 detail-bundle)", async () => {
    renderBoard();
    // KPIBar「Pool 总数」卡开 KolPoolAllModal
    fireEvent.click(screen.getByText("Pool 总数"));
    expect(await screen.findByPlaceholderText(/搜索/)).toBeTruthy();

    // 大窗点行(行内「打开详情」冒泡到行 onClick)→ openItem 拉 detail-bundle 开抽屉
    const row = screen.getAllByText("打开详情")[0];
    fireEvent.click(row);
    await waitFor(() => {
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/detail-bundle"))).toBe(true);
    });
  });

  it("跨页事件:vkpi:open-kol-pool-search 消费 pending 关键词 → 填筛选并展开;-item 消费 id → 开抽屉", async () => {
    window.localStorage.setItem("vkpi:pending-kolpool-search", "alpha");
    renderBoard();
    // 消费后清 pending + 筛选条展开(FilterBar 在场)
    expect(window.localStorage.getItem("vkpi:pending-kolpool-search")).toBeNull();
    expect(await screen.findByText("收起筛选 · 排序")).toBeTruthy();

    window.localStorage.setItem("vkpi:pending-kolpool-open-id", "101");
    window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item"));
    await waitFor(() => {
      expect(window.localStorage.getItem("vkpi:pending-kolpool-open-id")).toBeNull();
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/detail-bundle"))).toBe(true);
    });
  });

  it("布局只走本机键 vkpi-kol-pool-layout-v1;绝不写账户级 dashboard_layout_v1", async () => {
    renderBoard();
    expect(await screen.findByText("池子指标带")).toBeTruthy();
    expect(window.localStorage.getItem("vkpi-kol-pool-layout-v1")).toBeTruthy();
    expect(window.localStorage.getItem("dashboard_layout_v1")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("dashboard/layout"))).toBe(false);
  });

  it("无 token → 诚实 pending 卡,零读端点请求;空池 → 诚实空态文案", () => {
    render(<KolPoolBoardPage items={[]} loading={false} error="" apiToken="" staff={[]} />);
    expect(screen.getAllByText(/未登录 \/ 无 token/).length).toBeGreaterThan(0);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
