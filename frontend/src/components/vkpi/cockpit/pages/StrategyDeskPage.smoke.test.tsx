import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// 战略台 板块页范式改版冒烟(金样板 GtmCommandBoardPage.smoke 同构):
// - 页壳:pagehead(战略台 + 联动品牌徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡:现值全真(声量份额=benchmark / 机会赛道=tracks / 押注命中 +
//   待对答案=performance),战略域无时序端点 → 四卡 spempty 诚实虚线零药丸;
// - 默认十二模块 = 旧页四块(对照/赛道/模拟/表现)全功能零丢失;
// - 联动位三通道(SKU360 先例):prop / URL ?strategyBrand= / sessionStorage
//   (vkpi:strategy-brand)+ vkpi:open-strategy-desk 事件 → 排名条标记 + 对照行
//   自动展开 + 页头徽可清除;
// - 布局键 vkpi-strategy-desk-layout-v1 + 不传 apiToken → 绝不写账户级布局;
// - 【总脑纯读红线】全程绝不调用 marketing-brain/daily 与 market/trends(隐藏写入);
//   全程零 POST(本页无任何写路径);模拟器未点「模拟」绝不调 /strategy/simulate。
// mock seam:services/http.apiFetch(全页唯一网络出口,embeds 同缝)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { StrategyDeskPage, STRATEGY_BRAND_KEY, OPEN_STRATEGY_EVENT } from "./StrategyDeskPage";

const BENCH_OK = {
  status: "ok",
  window_days: 90,
  viltrox: {
    key: "viltrox", brand: "Viltrox", videos: 173, kol_count: 87, share_of_voice: 0.305, rank: 1,
    engagement: { avg_rate: 0.051, sample: 60, confidence: "medium" },
  },
  brand_count_ranked: 15,
  competitors: [
    {
      key: "sony", brand: "Sony", videos: 109, kol_count: 53, share_of_voice: 0.192, rank: 2,
      engagement: { avg_rate: 0.042, sample: 30, confidence: "medium" },
      top_examples: [{ evidence_id: 9, title: "Sony 85mm 实测", content_url: "https://example.com/v9", view_count: 42000, kol_name: "CamGuy" }],
    },
    { key: "canon", brand: "Canon", videos: 66, kol_count: 30, share_of_voice: 0.116, rank: 3, engagement: { avg_rate: null, sample: 0, confidence: "none" }, top_examples: [] },
  ],
  head_to_head: [
    {
      key: "sony", brand: "Sony",
      rows: [
        { metric: "voice_videos", label: "声量(提及视频)", viltrox: 173, competitor: 109 },
        { metric: "kol_count", label: "覆盖 KOL(独立人数)", viltrox: 87, competitor: 53 },
        { metric: "avg_views", label: "均播放(有播放数的提及视频)", viltrox: 27600, competitor: 19800 },
      ],
      verdict: "vs Sony:声量领先(173 vs 109)—— 对该品牌保持压制。",
    },
    { key: "canon", brand: "Canon", rows: [], verdict: "vs Canon:声量领先。" },
  ],
  focal_grid: {
    status: "ok",
    cells: [
      { focal: "85mm", competitor_videos: 12, viltrox_videos: 5, official_sku_count: 2, sku_weak: false, voice_weak: false },
      { focal: "300mm", competitor_videos: 7, viltrox_videos: 0, official_sku_count: 0, sku_weak: true, voice_weak: false, competitor_brands: ["Sony"] },
    ],
    opportunities: [{ focal: "300mm" }],
  },
  basis: { videos_scanned: 1462, brand_hit_videos: 568, deep_analyzed_in_window: 145 },
  confidence: { level: "high", reason: "命中 568≥150 且扫描 1462≥500" },
};

const TRACKS_OK = {
  status: "ready",
  sources: { voice_docs: 797, evidence_rows: 1644, catalog_skus: 369 },
  category_tracks: [
    {
      track_id: "cat:tele", dimension: "category", key: "tele", label: "远摄",
      demand: {
        total: 42, norm: 0.9, comment_recent: 10, comment_prev: 5, comment_trend: "rising", comment_mom_pct: 100,
        evidence_recent: 20, evidence_prev: 18, evidence_trend: "stable", evidence_mom_pct: 0,
        wish_count: 3, wish_quotes: [{ text: "please make a 300mm", author: "userA", platform: "youtube", at: "2026-07-01" }],
      },
      coverage: { sku_count: 0, our_voice_videos: 0, norm: 0.1 },
      competitors: { status: "ready", top_brand: "Sony", top_share: 0.4, total_mentions: 12, brand_count: 3, monopoly: false },
      opportunity: { score: 45, confidence: "high" },
    },
  ],
  focal_tracks: [
    {
      track_id: "focal:75mm", dimension: "focal", key: "75mm", label: "75mm 焦段",
      demand: { total: 30, norm: 0.86, comment_recent: 6, comment_prev: 6, comment_trend: "stable", comment_mom_pct: 0, evidence_recent: 9, evidence_prev: 4, evidence_trend: "rising", evidence_mom_pct: 125, wish_count: 2, wish_quotes: [{ text: "75mm please", author: "userB" }] },
      coverage: { sku_count: 0, our_voice_videos: 1, norm: 0.2 },
      competitors: { status: "empty" },
      opportunity: { score: 37, confidence: "high" },
    },
  ],
  opportunities: [
    { track_id: "focal:75mm", dimension: "focal", label: "75mm 焦段", opportunity: { score: 37, confidence: "high" }, demand: { wish_count: 2 } },
  ],
  no_go: [
    { track_id: "cat:monitor", dimension: "category", label: "监视器", reason: "竞品垄断:feelworld 占该赛道竞品声量 95%(样本 20 条)" },
  ],
  mount_signals: [{ mount: "L 卡口", wish_count: 1, quotes: [{ text: "L-mount please" }] }],
};

const PERF_OK = {
  status: "ok",
  generated_at: "2026-07-12T02:00:00Z",
  scoreboard: {
    bets: {
      status: "ok", won: 1, lost: 0, open: 1, void: 0, settled: 1, hit_rate: 1.0, confidence: "insufficient",
      oldest_open: { age_days: 12, review_at: "2026-07-20T00:00:00Z", review_overdue: false, probability: 0.7, hypothesis: "美国人像 KOL 实测能拉动 85mm 销量" },
    },
    predictions: {
      status: "ok",
      groups: [
        { action_type: "kol_discovery", label: "找 KOL", hit_rate: 0.72, sample_count: 50, confidence: "medium", pending_count: 200 },
        { action_type: "outreach", label: "催合作", hit_rate: 0.35, sample_count: 20, confidence: "low", pending_count: 0 },
      ],
      judged_total: 109, pending_total: 795,
      backlog_top: { action_type: "kol_discovery", label: "找 KOL", pending_count: 200 },
    },
    fulfillment: {
      status: "ok", loops_completed: 10,
      first_loop: { run_id: 5, created_at: "2026-06-20T00:00:00Z", steps: [{ step_name: "签收", status: "done" }, { step_name: "开窗", status: "done" }] },
      windows: { total: 102, matched: 40, scanning: 10 },
      posts: { confirmed: 23, candidates: 5 },
      plan_vs_actual: {
        samples: [{
          post_id: 1, project_id: 3, project_name: "85mm 首发", kol_pool_id: 77, post_status: "matched", content_url: "https://example.com/post1",
          planned: { window_id: 9, starts_at: "2026-06-01T00:00:00Z", ends_at: "2026-06-15T00:00:00Z", scan_count: 4 },
          actual: { published_at: "2026-06-05T00:00:00Z", view_count: 15000 },
          published_within_window: true,
        }],
      },
      confidence: "medium",
    },
  },
  lessons: { status: "ok", items: [{ text: "长测比开箱更能带动转化", source: "bet", ref: "bet#1", at: "2026-07-01T00:00:00Z" }] },
  honesty_note: { items: ["vkpi_prediction_evals 0 行(裁决走台账回填)", "Dealer 账本未导入"] },
};

const SKU_LIST_OK = { items: [{ sku: "VL-LEN072", model_name: "AF 85mm F1.4 Pro FE", marketing_name: "", category_main: "lens", mount: "FE", price_usd: 549 }] };

function routeApi(overrides: { bench?: unknown; tracks?: unknown; perf?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/strategy/industry-benchmark")) {
      const value = overrides.bench ?? BENCH_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/strategy/category-tracks")) {
      const value = overrides.tracks ?? TRACKS_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/strategy/performance")) {
      const value = overrides.perf ?? PERF_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/sku/list")) return SKU_LIST_OK;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderDesk = (props: Record<string, unknown> = {}) => render(<StrategyDeskPage apiToken="t1" {...(props as any)} />);

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/");
  routeApi();
});

describe("StrategyDeskPage smoke(页壳 + KPI 带真数 + 默认十二模块 + 联动三通道 + 纯读红线)", () => {
  it("KPI 带四卡现值全真;战略域无时序 → 四卡诚实虚线零药丸;总脑纯读红线全程守住(零 POST)", async () => {
    expect(() => renderDesk()).not.toThrow();
    // 「声量份额」双出现 = KPI 卡标签 + SrcChip 口径行(同名同源)
    expect((await screen.findAllByText("声量份额")).length).toBeGreaterThan(0);

    await waitFor(() => {
      const vals = [...document.querySelectorAll(".ds-kpi__val")].map((el) => (el.textContent || "").trim());
      expect(vals).toContain("30.5%"); // 声量份额 = viltrox.share_of_voice
      expect(vals).toContain("1条"); // 机会赛道 = opportunities 1 条
      expect(vals).toContain("100%"); // 押注命中 = hit_rate 1.0(已结算 1 注)
      expect(vals).toContain("795条"); // 待对答案 = pending_total
    });
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    // 无按日时序端点:四卡全 spempty 虚线,零 sparkline 零环比药丸(不编序列)
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead(按钮动词直说)
    expect(screen.getByText("战略台")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
    expect(screen.getByText("刷新数据")).toBeTruthy();

    // 【总脑纯读红线】marketing-brain/daily 与 market/trends 全程零调用;
    // 全页零 POST 写路径;模拟器未点「模拟」绝不调 /strategy/simulate
    const paths = calledPaths();
    expect(paths.some((pp) => pp.includes("marketing-brain/daily"))).toBe(false);
    expect(paths.some((pp) => pp.includes("market/trends"))).toBe(false);
    expect(paths.some((pp) => pp.includes("/strategy/simulate"))).toBe(false);
    const inits = apiFetchMock.mock.calls.map((call) => (call[1] || {}) as { method?: string; body?: unknown });
    expect(inits.some((init) => String(init.method || "GET").toUpperCase() !== "GET" || init.body != null)).toBe(false);
    expect(paths.some((pp) => pp.startsWith("/api/admin/vkpi/strategy/industry-benchmark"))).toBe(true);
    expect(paths.some((pp) => pp.startsWith("/api/admin/vkpi/strategy/category-tracks"))).toBe(true);
    expect(paths.some((pp) => pp.startsWith("/api/admin/vkpi/strategy/performance"))).toBe(true);
  });

  it("默认十二模块 = 旧页四块全功能零丢失(对照三块 / 赛道三块 / 模拟 / 表现四块)", async () => {
    renderDesk();

    // 对照:排名条(Viltrox 高亮行)+ 焦段格局(红格 tooltip 语义)+ 对照行
    expect(await screen.findByText("#1 Viltrox")).toBeTruthy();
    expect(screen.getByText("#2 Sony")).toBeTruthy();
    expect(screen.getByText("85mm 12v5")).toBeTruthy();
    expect(screen.getByText("300mm 7v0")).toBeTruthy();
    expect(screen.getByText("空档 ×1")).toBeTruthy();
    expect(screen.getAllByText(/vs Sony:声量领先/).length).toBeGreaterThan(0);

    // 赛道:机会矩阵(维度切换 + 赛道芯片)+ Top 机会 + 不进清单 + 卡口愿望
    expect(screen.getByText("品类维")).toBeTruthy();
    expect(screen.getByText("焦段维")).toBeTruthy();
    expect(screen.getByText("tele 45")).toBeTruthy();
    expect(screen.getByText("75mm 焦段")).toBeTruthy();
    expect(screen.getByText(/竞品垄断:feelworld/)).toBeTruthy();
    expect(screen.getByText("L 卡口 ×1")).toBeTruthy();

    // 模拟:embed 控件真身(旧组件零改动收编)
    expect(screen.getByPlaceholderText(/搜索 SKU/)).toBeTruthy();
    expect(screen.getByText("模拟")).toBeTruthy();

    // 表现:押注 / 预测 / 履约 / 教训 + 数据荒
    expect(screen.getByText("押对 1")).toBeTruthy();
    expect(screen.getByText("未结算 1")).toBeTruthy();
    expect(screen.getByText("美国人像 KOL 实测能拉动 85mm 销量")).toBeTruthy();
    expect(screen.getByText("找 KOL")).toBeTruthy();
    expect(screen.getByText(/已裁决合计/)).toBeTruthy();
    expect(screen.getByText(/完成闭环/)).toBeTruthy();
    expect(screen.getByText("签收 ✓")).toBeTruthy();
    expect(screen.getByText("85mm 首发")).toBeTruthy();
    expect(screen.getByText("发布落在计划窗口内")).toBeTruthy();
    expect(screen.getByText(/长测比开箱更能带动转化/)).toBeTruthy();
    expect(screen.getAllByText(/vkpi_prediction_evals 0 行/).length).toBeGreaterThan(0);

    // 十二模块卡头全在默认布局
    for (const title of ["战略总览", "声量份额排名", "焦段格局", "Viltrox vs 竞品", "机会矩阵", "Top 机会赛道", "策略模拟器", "不进清单", "预测命中率", "押注台账", "履约对账", "教训与数据荒"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
  });

  it("对照行点击展开:三行对比 + 质量侧写 + 结论句 + 例证外链", async () => {
    renderDesk();
    const row = await screen.findByText(/vs Sony:声量领先/);
    fireEvent.click(row);
    expect(await screen.findByText("声量(提及视频)")).toBeTruthy();
    expect(screen.getByText("覆盖 KOL(独立人数)")).toBeTruthy();
    expect(screen.getByText(/内容质量侧写/)).toBeTruthy();
    const link = screen.getByText("Sony 85mm 实测") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://example.com/v9");
  });

  it("Top 机会点行 → 机会矩阵切到焦段维并展开该赛道证据(原声入口 + 红格 + 竞品口径)", async () => {
    renderDesk();
    const row = await screen.findByText("75mm 焦段");
    fireEvent.click(row);
    // 详情块:机会分 + 需求/覆盖/竞品三信号 + 红格 + 原声折叠入口
    expect(await screen.findByText("机会分 37")).toBeTruthy();
    expect(screen.getByText("目录零 SKU(焦段红格)")).toBeTruthy();
    expect(screen.getByText("词表零命中,按未垄断处理(低置信)")).toBeTruthy();
    expect(screen.getByText(/原声 ×1/)).toBeTruthy();
    // 矩阵已切到焦段维(75mm 芯片在格内)
    expect(screen.getByText("75mm 37")).toBeTruthy();
  });

  it("联动三通道(SKU360 先例):sessionStorage+事件 → 页头徽 + 对照行自动展开;可一键清除", async () => {
    window.sessionStorage.setItem(STRATEGY_BRAND_KEY, "Sony");
    renderDesk();
    expect(await screen.findByText("联动 · Sony")).toBeTruthy();
    // 对照 Sony 行自动展开(无需点击)
    expect(await screen.findByText(/内容质量侧写/)).toBeTruthy();
    // 排名条联动标记
    expect(screen.getByText("#2 Sony ◈")).toBeTruthy();
    // 清除:徽消失 + sessionStorage 键清空
    fireEvent.click(screen.getByLabelText("清除联动品牌"));
    expect(screen.queryByText("联动 · Sony")).toBeNull();
    expect(window.sessionStorage.getItem(STRATEGY_BRAND_KEY)).toBeNull();
  });

  it("联动另两通道:URL ?strategyBrand= 与 prop brand;事件通道运行中点亮", async () => {
    window.history.replaceState({}, "", "/?strategyBrand=Canon");
    const first = renderDesk();
    expect(await screen.findByText("联动 · Canon")).toBeTruthy();
    first.unmount();
    window.history.replaceState({}, "", "/");

    const second = renderDesk({ brand: "Sony" });
    expect(await second.findByText("联动 · Sony")).toBeTruthy();
    second.unmount();

    renderDesk();
    expect(await screen.findByText("#1 Viltrox")).toBeTruthy();
    expect(screen.queryByText("联动 · Sony")).toBeNull();
    window.sessionStorage.setItem(STRATEGY_BRAND_KEY, "Sony");
    fireEvent(window, new CustomEvent(OPEN_STRATEGY_EVENT));
    expect(await screen.findByText("联动 · Sony")).toBeTruthy();
  });

  it("单路失败互不拖垮:对照失败 = 诚实错误卡;赛道空窗 = 后端 reason;表现照常真身", async () => {
    routeApi({ bench: new Error("boom"), tracks: { status: "empty", reason: "近 60 天无声量数据" } });
    renderDesk();
    expect((await screen.findAllByText("strategy/industry-benchmark 读取失败")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("近 60 天无声量数据")).length).toBeGreaterThan(0);
    // 表现族独立源照常真身
    expect(await screen.findByText("押对 1")).toBeTruthy();
    // KPI 带:对照/赛道两卡诚实 pending,表现两卡真值
    await waitFor(() => {
      const vals = [...document.querySelectorAll(".ds-kpi__val")].map((el) => (el.textContent || "").trim());
      expect(vals).toContain("100%");
      expect(vals).toContain("795条");
    });
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出全部十二模块(默认已在看板)", async () => {
    renderDesk();
    expect(await screen.findByText("#1 Viltrox")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    await waitFor(() => {
      for (const label of ["战略总览", "声量份额排名", "焦段格局", "Viltrox vs 竞品", "机会矩阵", "Top 机会赛道", "策略模拟器", "不进清单", "预测命中率", "押注台账", "履约对账", "教训与数据荒"]) {
        expect(screen.getAllByText(label).length).toBeGreaterThan(1); // 卡头 + palette 条目双出现
      }
    });
  });

  it("布局键 vkpi-strategy-desk-layout-v1 生效;不传 apiToken 给板组件 → 绝不写账户级布局", async () => {
    window.localStorage.setItem("vkpi-strategy-desk-layout-v1", JSON.stringify([{ moduleKey: "kpiS", span: 12 }]));
    renderDesk();
    expect((await screen.findAllByText("声量份额")).length).toBeGreaterThan(0);
    expect(screen.queryByText("机会矩阵")).toBeNull();
    expect(screen.queryByText("押注台账")).toBeNull();
    expect(screen.queryByText("履约对账")).toBeNull();
    expect(calledPaths().some((pp) => pp.includes("preference"))).toBe(false);
  });
});
