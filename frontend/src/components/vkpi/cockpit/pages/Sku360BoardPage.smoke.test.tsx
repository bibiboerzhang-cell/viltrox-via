import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// SKU 360° 改版冒烟(金样板 MarketVoicePage/KolProfileBoardPage.smoke 同构):
// - 页壳:pagehead(SKU 360° + 型号/类目药丸徽 + 编辑布局钮)+ 控件行选择器 + 可编辑看板;
// - KPI 带四卡全真值(profile aggregate 请求时实算):无逐 SKU 时序端点 → 4 卡
//   spempty 诚实虚线、零 delta 药丸;
// - 默认布局九模块在场(kpiS/catalog/knowledge/contents/angles/voice/creators/fit/bh),
//   candidates=palette 备选不进默认;
// - 溯源:真实表名只进 SrcChip rows(vkpi_products / vkpi_product_persona /
//   vkpi_comments / vkpi_bh_reviews),卡面零术语;
// - 知识库/推广方向 = LLM 离线批产 → 卡面「AI 生成」徽 + 模型/生成时间标注;
//   该表无置信列 → 如实不摆置信徽;
// - 诚实空态:persona=null =「知识库未生成」;bh 0 行 =「待喂数」;
// - 全量 + 连续翻:提及内容默认 6 条 → 全量弹窗 → 单条详情 ‹#n/N› 连续翻;
// - 跨板块下钻:详情「KOL 档案 →」= sessionStorage + vkpi:open-kol-profile 事件 +
//   onNavigate("kolProfile")(既有管道,零重造);
// - sku 三通道:引导态 → sessionStorage + vkpi:open-sku360 事件进档案;
// - 布局键 vkpi-sku360-layout-v1 本机记忆;不传 apiToken → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { Sku360BoardPage } from "./Sku360BoardPage";

const contentItem = (i: number, kolId: number) => ({
  evidence_id: 9000 + i,
  title: `85mm 实拍评测 ${i}`,
  content_url: `https://youtu.be/v${i}`,
  platform: "youtube",
  posted_at: "2026-07-01",
  view_count: 10000 - i * 100,
  like_count: 500 - i,
  comment_count: 50,
  engagement: 550 - i,
  kol: { kol_pool_id: kolId, handle: `@creator${kolId}`, display_name: `Creator ${kolId}`, platform: "youtube" },
  has_deep_analysis: i % 2 === 0,
  marketing_value_score: i % 2 === 0 ? 7.5 : null,
  match: { alias: "85mm f1.4 pro", field: i % 2 === 0 ? "final_v1_products" : "evidence_title", confidence: 0.9 },
});

const PROFILE_OK = {
  status: "ok",
  product: {
    sku: "AF-85MM-F14-PRO-FE",
    model_name: "AF 85mm F1.4 Pro FE",
    marketing_name: "85mm Pro",
    category_main: "Lens",
    category_detail: "Prime",
    series: "Pro",
    mount: "Sony E",
    price_usd: 549,
    status: "official",
    description: "大光圈人像定焦,支持眼部对焦。",
    product_url: "https://viltrox.com/85mm",
    specs: { 焦段: "85mm", 光圈: "F1.4" },
    fit_tags: ["portrait", "video"],
    catalog_updated_at: "2026-06-20",
  },
  aliases_sample: ["85mm f1.4 pro"],
  content: {
    items: Array.from({ length: 8 }, (_, i) => contentItem(i + 1, i < 5 ? 101 : 202)),
    aggregate: {
      content_count: 8,
      creator_count: 2,
      total_views: 123456,
      total_likes: 3000,
      total_comments: 400,
      avg_engagement_rate: 0.0345,
      top_creators: [
        { kol_pool_id: 101, handle: "@creator101", display_name: "Creator 101", platform: "youtube", content_count: 5, total_views: 80000, total_engagement: 2000 },
        { kol_pool_id: 202, handle: "@creator202", display_name: "Creator 202", platform: "tiktok", content_count: 3, total_views: 43456, total_engagement: 950 },
      ],
    },
    content_fit_matches: [
      {
        kol_pool_id: 101,
        handle: "@creator101",
        display_name: "Creator 101",
        platform: "youtube",
        fit_verdict: "partial_fit",
        confidence: 0.7,
        fit_reasons: ["人像内容占比高", "已有同焦段评测"],
        updated_at: "2026-07-01T00:00:00Z",
      },
    ],
    diagnostics: { aliases_used: 12, deep_rows_scanned: 497, title_rows_scanned: 2175 },
    note: "",
  },
  comments: {
    matched_total: 2,
    scanned: 875,
    sample: [
      {
        comment_id: 1,
        platform: "youtube",
        comment_text: "the 85mm f1.4 pro is insanely sharp",
        author_handle: "userA",
        likes_count: 12,
        language_detected: "en",
        created_at: "2026-06-15",
        matched_alias: "85mm f1.4 pro",
      },
      {
        comment_id: 2,
        platform: "tiktok",
        comment_text: "85mm pro bokeh is unreal",
        author_handle: "userB",
        likes_count: 3,
        language_detected: "en",
        created_at: "2026-06-18",
        matched_alias: "85mm pro",
      },
    ],
  },
  bh_reviews: { table_present: true, matched_total: 0, avg_rating: null, sample: [] },
  generated_at: "2026-07-12T08:00:00Z",
};

const PERSONA_OK = {
  status: "ok",
  product: { sku: "AF-85MM-F14-PRO-FE" },
  persona: {
    product_sku: "AF-85MM-F14-PRO-FE",
    category: "Lens",
    what_is: "一款 85mm F1.4 大光圈人像定焦镜头,主打奶油虚化与锐利眼对焦。",
    key_specs_json: { 焦段: "85mm", 光圈: "F1.4" },
    ideal_persona: "适合以人像/婚礼为主业、需要大光圈氛围感的进阶创作者。",
    ideal_creator_types_json: ["人像摄影师", "婚礼跟拍"],
    verticals_json: ["portrait", "wedding"],
    promotion_angles_json: ["奶油虚化对比横评", "同价位大光圈性价比"],
    avoid_types_json: ["纯风光博主"],
    model: "gpt-5.4-mini-2026-03-17",
    source: "llm_persona_v1",
    generated_at: "2026-06-14T09:14:21Z",
  },
  generated_at: "2026-07-12T08:00:00Z",
};

const CARD_OK = {
  mode: "p5_70_product_campaign_card",
  kol_candidates: [
    { kol_pool_id: 101, display_name: "Creator 101", score: 62.5, confidence: 0.61, risk_flags: [], evidence: [{ type: "alias_match" }] },
  ],
  market_risk: { risk_tier: "low", risk_score: 6, top_competitor_brands: [{ brand: "sigma", count: 3 }], signal_types: {}, related_signals: [] },
  policy: { read_only: true, human_approval_required: true },
};

const SKU_LIST_OK = {
  status: "ok",
  count: 1,
  items: [
    {
      sku: "AF-85MM-F14-PRO-FE",
      model_name: "AF 85mm F1.4 Pro FE",
      marketing_name: "85mm Pro",
      category_main: "Lens",
      category_detail: "Prime",
      series: "Pro",
      mount: "Sony E",
      price_usd: 549,
      status: "official",
    },
  ],
};

function routeApi(overrides: { profile?: unknown; persona?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.includes("/sku/list")) return SKU_LIST_OK;
    if (p.includes("/persona")) {
      const value = overrides.persona ?? PERSONA_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.includes("/profile")) {
      const value = overrides.profile ?? PROFILE_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.includes("/product-campaign-card")) return CARD_OK;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<Sku360BoardPage apiToken="t" sku="AF-85MM-F14-PRO-FE" {...(props as any)} />);

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("Sku360BoardPage smoke(页壳 + KPI 带 + 注册表 + 诚实态 + 全量连续翻)", () => {
  it("KPI 带四卡全真值;实算口径 → 4 卡诚实虚线零药丸;pagehead 型号/类目徽;三路端点全被调", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 真值:提及内容 8 / 覆盖创作者 2 / 总曝光 12.3万 / 平均互动率 3.45%
    expect(await screen.findByText("12.3万")).toBeTruthy();
    expect(screen.getAllByText("提及内容").length).toBeGreaterThan(0);
    expect(screen.getAllByText("覆盖创作者").length).toBeGreaterThan(0);
    expect(screen.getAllByText("内容总曝光").length).toBeGreaterThan(0);
    expect(screen.getAllByText("平均互动率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3.45").length).toBeGreaterThan(0);

    // 实算聚合:4 卡全 spempty 虚线,零 sparkline 零环比药丸(绝不编时序)
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead:型号徽 + 类目徽 + 编辑布局钮
    expect(screen.getAllByText("AF 85mm F1.4 Pro FE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Lens").length).toBeGreaterThan(0);
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // 三路 page 层端点全被调(profile / persona / campaign-card)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    for (const seg of ["/profile", "/persona", "/product-campaign-card"]) {
      expect(calledPaths.some((p) => p.includes(seg))).toBe(true);
    }
  });

  it("默认布局九模块在场;candidates=palette 备选不进默认;真表名只进 SrcChip;AI 生成标注 + 置信原样", async () => {
    renderBoard();
    expect(await screen.findByText("声量指标带")).toBeTruthy();
    for (const title of ["产品档案", "知识库画像", "提及内容", "推广方向", "评论声量", "关联创作者", "内容契合", "电商口碑"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("推广候选")).toBeNull();

    // 知识库/推广方向 = AI 生成徽 ×2 + 真内容;无置信列 → 不摆置信徽
    expect(screen.getAllByText("AI 生成").length).toBe(2);
    expect(screen.getAllByText(/大光圈人像定焦镜头/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/奶油虚化对比横评/).length).toBeGreaterThan(0);
    expect(screen.getByText("人像摄影师")).toBeTruthy();
    expect(screen.getByText("纯风光博主")).toBeTruthy();

    // 内容契合:AI 判断缓存只读,判定徽 + 行级置信原样
    expect(screen.getByText("部分契合")).toBeTruthy();
    expect(screen.getByText("置信 70%")).toBeTruthy();

    // 关联创作者 + 评论声量真值
    expect(screen.getAllByText("Creator 101").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/insanely sharp/).length).toBeGreaterThan(0);

    // 真表名住 SrcChip rows(卡面零术语)
    expect(screen.getAllByText(/vkpi_products/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/353\/369 SKU 已生成/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_comments.本地 875 行/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/表已建 · 本地 0 行/).length).toBeGreaterThan(0);
  });

  it("诚实态:persona=null =「知识库未生成」;bh 已接 0 行 =「待喂数」", async () => {
    routeApi({ persona: { status: "ok", product: {}, persona: null, generated_at: "2026-07-12T08:00:00Z" } });
    renderBoard();
    expect((await screen.findAllByText(/知识库未生成该 SKU 画像/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/数据源已接,等采集任务喂数/).length).toBeGreaterThan(0);
  });

  it("全量 + 连续翻:默认 6 条 → 全量弹窗 → 详情 ‹#n/N› 连续翻;「KOL 档案 →」走既有事件管道", async () => {
    const onNavigate = vi.fn();
    const eventSpy = vi.fn();
    window.addEventListener("vkpi:open-kol-profile", eventSpy);
    renderBoard({ onNavigate });

    // 默认 6 条 + 查看全量入口(共 8 条)
    const moreBtn = await screen.findByText(/≡ 查看全量.8 条./);
    fireEvent.click(moreBtn);
    expect(await screen.findByText("提及内容 · 全量")).toBeTruthy();
    // 全量弹窗内 8 行都在(标题行含卡面 6 行 → 用行总数断言)
    expect(screen.getAllByText(/85mm 实拍评测/).length).toBeGreaterThanOrEqual(8);

    // 点第一条 → 详情 #1/8;下一条 → #2/8
    fireEvent.click(screen.getAllByText("85mm 实拍评测 1")[0]);
    expect(await screen.findByText("#1 / 8")).toBeTruthy();
    fireEvent.click(screen.getByText("下一条 ›"));
    expect(await screen.findByText("#2 / 8")).toBeTruthy();

    // 跨板块下钻:KOL 档案 → = sessionStorage + 事件 + onNavigate("kolProfile")
    fireEvent.click(screen.getByText("KOL 档案 →"));
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(eventSpy).toHaveBeenCalled();
    expect(onNavigate).toHaveBeenCalledWith("kolProfile");
    window.removeEventListener("vkpi:open-kol-profile", eventSpy);
  });

  it("布局只走本机键 vkpi-sku360-layout-v1;绝不写账户级 dashboard_layout_v1", async () => {
    renderBoard();
    expect(await screen.findByText("声量指标带")).toBeTruthy();
    expect(window.localStorage.getItem("vkpi-sku360-layout-v1")).toBeTruthy();
    expect(window.localStorage.getItem("dashboard_layout_v1")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("dashboard/layout"))).toBe(false);
  });

  it("引导态:未选择 SKU → 选择器引导;sessionStorage + vkpi:open-sku360 事件管道能进档案", async () => {
    render(<Sku360BoardPage apiToken="t" />);
    expect(screen.getByText(/选一个 SKU,看谁的内容提到\/评测了它/)).toBeTruthy();
    // 未选择 → 零档案请求(选择器 /sku/list 防抖后才发,且不属档案请求)
    expect(apiFetchMock.mock.calls.map((c) => String(c[0])).some((p) => p.includes("/profile"))).toBe(false);

    window.sessionStorage.setItem("vkpi:sku360-sku", "AF-85MM-F14-PRO-FE");
    fireEvent(window, new Event("vkpi:open-sku360"));
    expect(await screen.findByText("声量指标带")).toBeTruthy();
  });
});
