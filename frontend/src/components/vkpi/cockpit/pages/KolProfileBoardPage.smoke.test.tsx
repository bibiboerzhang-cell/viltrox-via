import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// KOL 档案改版冒烟(金样板 MarketVoicePage/MyKolBoardPage.smoke 同构):
// - 页壳:pagehead(KOL 档案 + 名号/平台药丸徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡全点时快照:无逐 KOL 时序端点 → 4 卡 spempty 诚实虚线、零 delta 药丸;
// - 默认布局十模块在场(identity/credit/signature/deep/videos/comments/coop/leadtime/audience),
//   gear=palette 备选不进默认;
// - 溯源:真实表名只进 SrcChip rows(vkpi_kol_pool / vkpi_kol_llm_deep_analysis_results /
//   vkpi_comments / vkpi_kol_cooperation_events),卡面零术语;
// - 诚实空态:深析 0 记录=「未分析」;competing-activity 失败 → /competitors 兜底 +
//   「兜底口径」如实标注;
// - 动作:「收藏 / 联系 / 补全 →」= localStorage pending id + vkpi:open-kol-pool-item
//   事件 + onNavigate("kol-pool")(既有抽屉管道,零重造);
// - 布局键 vkpi-kol-profile-layout-v1 本机记忆;不传 apiToken → 绝不写账户级
//   dashboard_layout_v1;
// - 引导态:未选择 KOL = 输入 ID → 打开档案。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { KolProfileBoardPage } from "./KolProfileBoardPage";

const ITEM = {
  id: 101,
  pool_uid: "yt:alpha",
  platform: "youtube",
  handle: "@alpha",
  display_name: "Alpha Cam",
  followers: 120000,
  avg_views: 45000,
  engagement_rate: 0.043,
  viltrox_fit_score: 82,
  bio: "Cinematic lens reviews",
  country: "US",
  email: "alpha@example.com",
  profile_url: "https://youtube.com/@alpha",
  language: "en",
  source_type: "apify",
  posts_count: 321,
  created_at: "2026-06-01T00:00:00Z",
  last_seen_at: "2026-07-10T00:00:00Z",
  updated_at: "2026-07-10T08:00:00Z",
  primary_topic: "镜头评测",
  content_style: "实拍",
  production_quality: "高",
  real_er: 0.021,
  real_er_sample_n: 40,
  suspect_inflation: false,
  inflation_reason: "",
  is_verified: true,
  device_primary: "Sony A7 IV",
  device_lenses: ["Viltrox", "Sigma"],
  device_uses_viltrox: true,
  upgrade_window: "low",
  audience_estimated: null,
  audience_languages: { languages: [{ lang: "en", pct: 72 }], sample_size: 50 },
  video_evidence: [{ evidence_id: 9001, id: 9001, title: "Lens test", view_count: 1000, content_url: "https://youtu.be/a1" }],
};

const BUNDLE_OK = {
  status: "ready",
  kol_pool_id: 101,
  item: ITEM,
  video_analysis: { items: [], summary: { evidence_count: 12, ready_count: 5 } },
};

const COOP_OK = {
  status: "active",
  status_label: "合作中",
  events: [{ action_type: "renew", action_label: "续约", status_after: "active", created_at: "2026-07-01T00:00:00Z" }],
};

const DEEP_OK = {
  status: "ready",
  count: 3,
  summary: { headline: "以镜头实拍见长" },
  primary_result: {
    llm_v6_fit: 7.4,
    confidence: 0.61,
    analysis_kind: "profile_llm",
    provider: "gemini",
    created_at: "2026-07-09T00:00:00Z",
    llm_dimensions_11: {
      strengths: ["运镜稳", "开箱节奏好"],
      weaknesses: ["曝光过度"],
      recommendations: ["先送 85mm 定焦评测"],
      account_judgment: "账号以镜头实拍横评见长,适合定焦新品首发。",
    },
  },
};

const CARD_OK = {
  mode: "cache",
  kol_pool_id: 101,
  item: {},
  comment_intelligence: {
    status: "ready",
    cached_comment_count: 34,
    counts: {
      questions: 5,
      opportunities: 2,
      issues: 1,
      sentiment: { positive: 20, negative: 4, neutral: 10 },
      brand_attitude: { praise: 6, neutral: 20 },
    },
  },
};

const COMPETING_OK = {
  status: "ok",
  window_days: 90,
  items: [
    {
      brand: "Sigma",
      when: "2026-07-01T00:00:00Z",
      scene: "对比评测",
      evidence_ref: { content_url: "https://youtu.be/x", title: "85mm shootout" },
    },
  ],
  undated_items: [],
  brands: [{ brand: "Sigma", count: 2 }],
  scanned_videos: 12,
  brand_signal_rows: 0,
};

const COMPETITORS_FALLBACK = {
  relations: [{ competitor_brand: "Sigma", risk_tier: "caution", risk_score: 0.42 }],
};

const LEADTIME_OK = {
  status: "ok",
  median_days: 9.5,
  sample_count: 2,
  samples: [{ delivered_at: "2026-06-01T00:00:00Z", published_at: "2026-06-10T12:00:00Z", days: 9.5, content_url: "https://youtu.be/p" }],
};

const SIGNATURE_OK = {
  status: "ready",
  coverage: { evidence_count: 12, deep_analyzed_count: 5 },
  shooting_styles: {
    status: "ready",
    sample_size: 5,
    modes: [{ key: "review", label: "评测", video_count: 4, avg_views: 50000 }],
    openings: [],
    professional_level: {},
    brand_exposure: { sufficient: 2, insufficient: 1 },
  },
  top_videos: {
    status: "ready",
    items: [{ evidence_id: 9001, title: "Lens test", content_url: "https://youtu.be/a1", view_count: 1000, comment_count: 2, engagement_rate: 0.03, verdict: "很稳" }],
    total_with_views: 8,
  },
  feedback: { status: "empty", reason: "暂无反馈聚合" },
};

const GEO_OK = {
  status: "ready",
  confidence: "medium",
  countries: [{ code: "US", name: "美国", pct: 40.0 }],
  signals: [{ key: "comment_language", label: "评论语言", status: "ready", sample_size: 34 }],
  comment_rows: 34,
  note: "多信号估算口径",
};

function routeApi(overrides: { deep?: unknown; competing?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.includes("/detail-bundle")) return BUNDLE_OK;
    if (p.includes("/cooperation")) return COOP_OK;
    if (p.includes("/llm-deep-analysis")) {
      const value = overrides.deep ?? DEEP_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.includes("/intelligence-card")) return CARD_OK;
    if (p.includes("/competing-activity")) {
      const value = overrides.competing ?? COMPETING_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.includes("/competitors")) return COMPETITORS_FALLBACK;
    if (p.includes("/production-leadtime")) return LEADTIME_OK;
    if (p.includes("/signature")) return SIGNATURE_OK;
    if (p.includes("/audience-geo")) return GEO_OK;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<KolProfileBoardPage apiToken="t" kolId={101} {...(props as any)} />);

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("KolProfileBoardPage smoke(页壳 + KPI 带 + 注册表 + 诚实态 + 动作管道)", () => {
  it("KPI 带四卡全真值;点时快照口径 → 4 卡诚实虚线零药丸;pagehead 名号/平台徽", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 现值:粉丝 12.0万 / 均播放 4.5万 / 互动率 4.3% / 已深析 5/12
    expect(await screen.findByText("12.0万")).toBeTruthy();
    expect(screen.getByText("4.5万")).toBeTruthy();
    expect(screen.getAllByText("粉丝").length).toBeGreaterThan(0);
    expect(screen.getAllByText("均播放").length).toBeGreaterThan(0);
    expect(screen.getAllByText("互动率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已深析视频").length).toBeGreaterThan(0);

    // 点时快照:4 卡全 spempty 虚线,零 sparkline 零环比药丸(绝不编时序)
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead:名号 + 平台徽 + 编辑布局钮
    expect(screen.getAllByText("Alpha Cam").length).toBeGreaterThan(0);
    expect(screen.getAllByText("youtube").length).toBeGreaterThan(0);
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // 六路 page 层端点全被调
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    for (const seg of ["/detail-bundle", "/cooperation", "/llm-deep-analysis", "/intelligence-card", "/competing-activity", "/production-leadtime"]) {
      expect(calledPaths.some((p) => p.includes(seg))).toBe(true);
    }
  });

  it("默认布局十模块在场;gear=palette 备选不进默认;真表名只进 SrcChip;Fit 只读徽", async () => {
    renderBoard();
    expect(await screen.findByText("档案指标带")).toBeTruthy();
    for (const title of ["身份档案", "信用与真实度", "招牌内容", "深析摘要", "视频深析", "评论声音", "合作动作", "制作周期", "受众画像"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.queryByText("创作装备")).toBeNull();

    // 三秒读懂 + Fit 只读徽 + 深析结论
    expect(await screen.findByText("他是谁")).toBeTruthy();
    expect(screen.getByText("什么最灵")).toBeTruthy();
    expect(screen.getByText("值多少钱")).toBeTruthy();
    expect(screen.getByText("Fit 82")).toBeTruthy();
    expect(screen.getAllByText(/账号以镜头实拍横评见长/).length).toBeGreaterThan(0);
    expect(screen.getByText("运镜稳")).toBeTruthy();

    // 评论声音真分布 + 信用真实度行
    expect(screen.getByText("样本 34")).toBeTruthy();
    expect(screen.getByText("提问 5")).toBeTruthy();
    expect(screen.getByText("无虚高嫌疑")).toBeTruthy();

    // 制作周期真中位
    expect(screen.getByText("签收 → 发布中位")).toBeTruthy();

    // 真表名住 SrcChip rows(卡面零术语)
    expect(screen.getAllByText(/vkpi_kol_pool\(1,237/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_kol_llm_deep_analysis_results\(817 行/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_comments\(本地 875 行/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_kol_cooperation_events\(0 行/).length).toBeGreaterThan(0);
  });

  it("诚实态:深析 0 记录=「未分析」;competing-activity 失败 → competitors 兜底 + 口径如实标注", async () => {
    routeApi({
      deep: { status: "ready", count: 0, summary: null, primary_result: null },
      competing: new Error("not found"),
    });
    renderBoard();
    expect(await screen.findByText(/未分析 —— 无深析记录/)).toBeTruthy();
    // 兜底源真身:已落库关系行 + 兜底口径进 SrcChip
    expect(await screen.findByText(/谨慎 · 0.42/)).toBeTruthy();
    expect(screen.getAllByText(/主源不可用 → 已落库品牌关系兜底/).length).toBeGreaterThan(0);
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("/competitors") && !p.includes("competing"))).toBe(true);
  });

  it("动作管道:「收藏 / 联系 / 补全 →」= pending id + 事件 + onNavigate(kol-pool),既有抽屉零重造", async () => {
    const onNavigate = vi.fn();
    const eventSpy = vi.fn();
    window.addEventListener("vkpi:open-kol-pool-item", eventSpy);
    renderBoard({ onNavigate });
    const btn = await screen.findByText("收藏 / 联系 / 补全 →");
    fireEvent.click(btn);
    expect(window.localStorage.getItem("vkpi:pending-kolpool-open-id")).toBe("101");
    expect(eventSpy).toHaveBeenCalled();
    expect(onNavigate).toHaveBeenCalledWith("kol-pool");
    window.removeEventListener("vkpi:open-kol-pool-item", eventSpy);
  });

  it("布局只走本机键 vkpi-kol-profile-layout-v1;绝不写账户级 dashboard_layout_v1", async () => {
    renderBoard();
    expect(await screen.findByText("档案指标带")).toBeTruthy();
    expect(window.localStorage.getItem("vkpi-kol-profile-layout-v1")).toBeTruthy();
    expect(window.localStorage.getItem("dashboard_layout_v1")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("dashboard/layout"))).toBe(false);
  });

  it("引导态:未选择 KOL → 输入 ID 打开档案;事件管道 vkpi:open-kol-profile 也能进档案", async () => {
    render(<KolProfileBoardPage apiToken="t" />);
    expect(screen.getByText(/未选择 KOL/)).toBeTruthy();
    // 零请求(诚实引导,不预取)
    expect(apiFetchMock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("KOL 池 ID"), { target: { value: "101" } });
    fireEvent.click(screen.getByText("打开档案"));
    expect(await screen.findByText("档案指标带")).toBeTruthy();
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
  });
});
