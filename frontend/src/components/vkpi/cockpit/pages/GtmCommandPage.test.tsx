import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

// GtmCommandPage 渲染逻辑单测(GTM-1 W3)。后端两端点 W1/W2 并行在建,
// 此处按规格合约 mock 响应:①全局五卡 ②SKU 预览五区块 ③显示层宪法(private 不落 DOM)
// ④人审按钮 disabled+GTM-3 接线 ⑤红线:绝不调用 marketing-brain/daily 与 market/trends。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { GtmCommandPage } from "./GtmCommandPage";

const e = React.createElement;

const SUMMARY_RAW = {
  weekly_signals: {
    items: [{ signal: "85mm 赛道热度上行", kind: "category", freshness: "7d", sample_size: 42, confidence: "medium" }],
    sources_note: "内部信号缓存;外部雷达 GTM-5 待接",
  },
  product_opportunities: { items: [{ sku: "AF-85MM-F14-PRO-FE", market: "US", persona: "人像摄影师", content_angle: "夜景人像", opportunity_score: 8.2, basis: "赛道位次#2" }] },
  recommended_actions: {
    items: [{ action: "补 3 个 85mm 候选", reason: "池子薄", evidence_summary: "近 30d 无新增", cost_note: "$0", risk: "低", expected_gain: "候选+3", ref: "inbox:12" }],
  },
  strategy_defaults: { simulate_entry: { sku_hint: "AF-85MM-F14-PRO-FE", budget_hint: 3000 }, note: "从这里试预算" },
  learning_digest: {
    validated: ["7d 预判命中 2/3"],
    effective_styles: ["before-after"],
    dropped_channels: [],
    next_change: "降低长尾权重",
    honesty_note: "样本仍小",
    score_details: { secret: "LEAK_SUMMARY_SCORE" },
  },
  generated_at: "2026-07-07T00:00:00Z",
};

const PREVIEW_RAW = {
  public_plan: {
    thesis: {
      go_nogo: "GO · 建议推",
      market: "US",
      persona: "人像摄影师",
      mainline: "转化主线",
      confidence: "medium",
      basis_summary: "赛道位次 + 内容表现",
      score_details: { secret: "LEAK_PLAN_SCORE" },
    },
    forecast: [
      { horizon_days: 14, statement: "美区有 14 天内容放大机会", signals_summary: "完播与意向评论上行", confidence: "medium", escalate_if: "48h 内 3 条视频完播进前 25%", retreat_if: "10 个 KOL 有效回复低于目标" },
      { horizon_days: 30, statement: "只有陈述没有条件" },
    ],
    roadmap: { w1: { channels: [{ channel: "KOL", play: "寄样 10 人" }], items: ["建短链"] }, "w2-4": ["两段式放大"], m2_3: { note: "视 W2-4 结果定" } },
    kol_candidates: { items: [{ handle: "@portrait_pro", risk_labels: ["合作历史短"], raw_comments: ["LEAK_RAW_COMMENT"] }] },
    dealer_targets: { status: "data_missing", note: "Dealer 表 0 行,GTM-2 导入后激活" },
    official_channel_actions: { items: [] },
    shopify_indie_site_actions: { items: [{ action: "建佣金短链", note: "本地无订单,诚实标注" }] },
    content_angles: { items: [{ angle: "夜景人像 before-after", basis: "persona.promotion_angles" }] },
    budget_mix: { items: [{ name: "平衡 50/50", split: "KOL 50% / 放大 50%" }] },
    risks: ["库存未知"],
    data_gaps: ["dealer_rows=0"],
    action_inbox_items: [
      { action: "接洽 @portrait_pro", reason: "契合人像人群", evidence_summary: "近 90d 两条同焦段爆款", cost_note: "$250 报价参考", risk: "档期未知", expected_gain: "预计 p50 80K 播放", ref: "preview:1" },
    ],
    success_metrics: [{ metric: "完播率", threshold: "40%", confidence: "low", basis: "规则库" }],
  },
  private_evidence: { raw_sources: ["LEAK_PRIVATE_RAW"] },
  meta: { generated_at: "2026-07-07T01:00:00Z", coverage: { kol: "18/20" }, data_gaps: ["dealer 未导入"] },
};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockImplementation((url: unknown) => {
    const u = String(url);
    if (u.startsWith("/api/admin/vkpi/market-brain/summary")) return Promise.resolve(SUMMARY_RAW);
    if (u.startsWith("/api/admin/vkpi/sku/list")) {
      return Promise.resolve({ items: [{ sku: "AF-85MM-F14-PRO-FE", model_name: "AF 85mm F1.4 Pro", price_usd: 549 }] });
    }
    if (u.startsWith("/api/admin/vkpi/market-brain/gtm-plan/preview")) return Promise.resolve(PREVIEW_RAW);
    return Promise.resolve({});
  });
});

function assertNoForbiddenCalls() {
  for (const call of apiFetch.mock.calls) {
    const u = String(call[0]);
    expect(u).not.toContain("marketing-brain/daily");
    expect(u).not.toContain("market/trends");
  }
}

describe("GtmCommandPage · 全局五卡(无 SKU)", () => {
  it("五卡全出 + 来源脚注诚实标注 + 人审 disabled(GTM-3 接线)", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    expect(await screen.findByText("① 本周信号")).toBeInTheDocument();
    expect(screen.getByText("② 产品机会")).toBeInTheDocument();
    expect(screen.getByText("③ 建议行动")).toBeInTheDocument();
    expect(screen.getByText("④ 策略模拟入口")).toBeInTheDocument();
    expect(screen.getByText("⑤ 复盘学习")).toBeInTheDocument();
    expect(screen.getByText("85mm 赛道热度上行")).toBeInTheDocument();
    expect(screen.getAllByText(/外部雷达 GTM-5 待接/).length).toBeGreaterThanOrEqual(1);
    // 六要素行 + 人审占位
    expect(screen.getByText("补 3 个 85mm 候选")).toBeInTheDocument();
    const reviewBtn = screen.getByText("人审执行") as HTMLButtonElement;
    expect(reviewBtn).toBeDisabled();
    expect(reviewBtn).toHaveAttribute("title", "GTM-3 接线");
    // 显示层宪法:后端多给的 score_details 绝不落 DOM
    expect(screen.queryByText(/LEAK_SUMMARY_SCORE/)).toBeNull();
    assertNoForbiddenCalls();
  });

  it("summary 端点未就绪 → 安静降级提示,不炸页面", async () => {
    apiFetch.mockImplementation((url: unknown) => {
      const u = String(url);
      if (u.startsWith("/api/admin/vkpi/market-brain/summary")) return Promise.reject(new Error("404"));
      return Promise.resolve({ items: [] });
    });
    render(e(GtmCommandPage, { apiToken: "tok" }));
    expect(await screen.findByText(/全局摘要暂不可用/)).toBeInTheDocument();
    assertNoForbiddenCalls();
  });

  it("无 token → 空态提示", () => {
    render(e(GtmCommandPage, { apiToken: "" }));
    expect(screen.getByText(/未登录 \/ 无 token/)).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe("GtmCommandPage · SKU 作战预览(五区块)", () => {
  it("选 SKU → 五区块渲染:条件化预判四段式 / roadmap 三段 / dealer 诚实占位 / 零 private 泄漏", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    const input = screen.getByPlaceholderText(/搜索 SKU/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "85" } });
    // 300ms 防抖后下拉出现,点第一项触发 preview
    const option = await screen.findByText("AF 85mm F1.4 Pro");
    fireEvent.click(option);

    expect(await screen.findByText(/① 主判断 · AF-85MM-F14-PRO-FE/)).toBeInTheDocument();
    expect(screen.getByText("GO · 建议推")).toBeInTheDocument();
    // ② 条件化预判:四段式 + 缺条件标不合规(视觉强调条件≠断言)
    expect(screen.getByText("条件 ≠ 断言")).toBeInTheDocument();
    expect(screen.getByText(/48h 内 3 条视频完播进前 25%/)).toBeInTheDocument();
    expect(screen.getByText(/10 个 KOL 有效回复低于目标/)).toBeInTheDocument();
    expect(screen.getAllByText(/条件缺失/).length).toBeGreaterThanOrEqual(2);
    // ③ 路线图三段 + 渠道段
    expect(screen.getByText(/W1 · 首周点火/)).toBeInTheDocument();
    expect(screen.getByText(/W2-4 · 放大验证/)).toBeInTheDocument();
    expect(screen.getByText(/M2-3 · 沉淀续推/)).toBeInTheDocument();
    expect(screen.getByText(/Dealer 表 0 行,GTM-2 导入后激活/)).toBeInTheDocument();
    // KOL 风险只出标签
    expect(screen.getByText("合作历史短")).toBeInTheDocument();
    // ④ 今日行动六要素 + 人审 disabled
    expect(screen.getByText("接洽 @portrait_pro")).toBeInTheDocument();
    const reviewBtn = screen.getByText("人审执行") as HTMLButtonElement;
    expect(reviewBtn).toBeDisabled();
    expect(reviewBtn).toHaveAttribute("title", "GTM-3 接线");
    // ⑤ 复盘学习(全局口径复用)+ 脚注
    expect(screen.getByText(/复用全局复盘账本/)).toBeInTheDocument();
    // 风险/缺口/成功指标脚注
    expect(screen.getByText("库存未知")).toBeInTheDocument();
    expect(screen.getByText("dealer_rows=0")).toBeInTheDocument();
    expect(screen.getByText(/完播率/)).toBeInTheDocument();
    // 显示层宪法:private_evidence / score_details / raw_comments 零落 DOM
    expect(screen.queryByText(/LEAK_PLAN_SCORE/)).toBeNull();
    expect(screen.queryByText(/LEAK_PRIVATE_RAW/)).toBeNull();
    expect(screen.queryByText(/LEAK_RAW_COMMENT/)).toBeNull();
    // 请求参数按合约拼装
    const previewCall = apiFetch.mock.calls.map((c) => String(c[0])).find((u) => u.includes("gtm-plan/preview"));
    expect(previewCall).toContain("sku=AF-85MM-F14-PRO-FE");
    expect(previewCall).toContain("country=US");
    expect(previewCall).toContain("budget_usd=3000");
    expect(previewCall).toContain("goal=conversion");
    expect(previewCall).toContain("window_days=30");
    assertNoForbiddenCalls();
  });
});
