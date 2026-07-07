import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Gate D · 增长指挥台四层单测(独立文件,零改既有 GtmCommandPage.test):
//   ①健康条读 /health + scheduler-tasks + runtime/metrics(全走 useCachedGet → apiFetch mock)
//   ②增长路线:全局态出 Top 机会 + 「生成路线」;preview 态出路线链 + Bet 生命周期
//   ③执行队列:actions/inbox 按类型聚合 + 跳转 Inbox
//   ④学习沉淀:learning_digest 复用;⑤红线:不调用 marketing-brain/daily 与 market/trends。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { GtmCommandPage } from "./GtmCommandPage";

const e = React.createElement;

const SUMMARY_RAW = {
  weekly_signals: { items: [], sources_note: "" },
  product_opportunities: {
    items: [
      { sku: "AF-85MM-F14-PRO-FE", market: "US", persona: "人像摄影师", opportunity_score: 8.2, basis: "赛道#2" },
    ],
  },
  recommended_actions: { items: [] },
  strategy_defaults: {},
  learning_digest: { validated: ["7d 预判命中 2/3"], effective_styles: ["before-after"], dropped_channels: [], next_change: "降低长尾权重", honesty_note: "样本仍小" },
  generated_at: "2026-07-07T00:00:00Z",
};

const PREVIEW_RAW = {
  public_plan: {
    thesis: { go_nogo: "GO · 建议推", market: "US", persona: "人像", mainline: "转化主线", confidence: "medium", basis_summary: "" },
    forecast: [],
    roadmap: {},
    kol_candidates: { items: [{ handle: "@p" }] },
    dealer_targets: { items: [] },
    official_channel_actions: { items: [] },
    shopify_indie_site_actions: { items: [{ action: "建短链" }] },
    content_angles: { items: [] },
    budget_mix: { items: [] },
    risks: ["库存未知"],
    data_gaps: [],
    action_inbox_items: [],
    success_metrics: [],
  },
  meta: { generated_at: "2026-07-07T01:00:00Z", coverage: {}, data_gaps: [] },
};

const HEALTH_RAW = {
  trust: { server_git_sha: "abcdef1234", client_git_sha: "abcdef1234", sha_aligned: true, worker_online: true, db_migration_max: "216" },
};
const SCHED_RAW = { status: { total: 5, enabled: 3 } };
const METRICS_RAW = { requests_persisted: { available: true } };
const INBOX_RAW = {
  available: true,
  items: [
    { id: 1, category: "kol_discovery", title: "找人 A" },
    { id: 2, category: "kol_discovery", title: "找人 B" },
    { id: 3, category: "outreach", title: "催合作 C" },
  ],
  today_summary: { today_executed_count: 2, today_approved_count: 1 },
};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockImplementation((url: unknown) => {
    const u = String(url);
    if (u.startsWith("/health")) return Promise.resolve(HEALTH_RAW);
    if (u.startsWith("/api/admin/vkpi/settings/scheduler-tasks")) return Promise.resolve(SCHED_RAW);
    if (u.startsWith("/api/admin/runtime/metrics")) return Promise.resolve(METRICS_RAW);
    if (u.startsWith("/api/admin/vkpi/actions/inbox")) return Promise.resolve(INBOX_RAW);
    if (u.startsWith("/api/admin/vkpi/market-brain/summary")) return Promise.resolve(SUMMARY_RAW);
    if (u.startsWith("/api/admin/vkpi/market-brain/gtm-plan/preview")) return Promise.resolve(PREVIEW_RAW);
    if (u.startsWith("/api/admin/vkpi/sku/list")) {
      return Promise.resolve({ items: [{ sku: "AF-85MM-F14-PRO-FE", model_name: "AF 85mm F1.4 Pro" }] });
    }
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

describe("GtmCommandPage · Gate D 指挥台(全局态)", () => {
  it("健康条五点 + 增长路线 Top 机会 + 执行队列聚合 + 学习沉淀", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    // 健康条
    expect(await screen.findByText("系统健康")).toBeInTheDocument();
    expect(await screen.findByText("3/5 启用")).toBeInTheDocument();
    expect(screen.getByText("已留痕")).toBeInTheDocument();
    // 增长路线(全局机会 + 生成路线按钮)
    expect(screen.getByText("今日增长路线 · 全局机会")).toBeInTheDocument();
    expect(screen.getByText("生成路线")).toBeInTheDocument();
    // 执行队列按类型聚合
    expect(await screen.findByText("找 KOL")).toBeInTheDocument();
    expect(screen.getByText("催合作")).toBeInTheDocument();
    expect(screen.getByText("去 Action Inbox 人审")).toBeInTheDocument();
    // 学习沉淀
    expect(screen.getByText("本周学习沉淀")).toBeInTheDocument();
    // Bet 生命周期只在 preview 态出
    expect(screen.queryByText("Bet 生命周期")).toBeNull();
    assertNoForbiddenCalls();
  });
});

describe("GtmCommandPage · Gate D 指挥台(preview 态)", () => {
  it("选 SKU → 路线链 + Bet 生命周期,风险合并不裸标签", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    const input = screen.getByPlaceholderText(/搜索 SKU/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "85" } });
    const option = await screen.findByText("AF 85mm F1.4 Pro");
    fireEvent.click(option);

    expect(await screen.findByText(/今日增长路线 · AF-85MM-F14-PRO-FE/)).toBeInTheDocument();
    // 决策带前缀(不与 ① 主判断卡的裸值「GO · 建议推」冲突)
    expect(screen.getByText(/决策 GO · 建议推/)).toBeInTheDocument();
    // 路线链段标签
    expect(screen.getByText("渠道组合")).toBeInTheDocument();
    // Bet 生命周期 preview 态点亮
    expect(await screen.findByText("Bet 生命周期")).toBeInTheDocument();
    // 风险合并为一句(裸「库存未知」仍归脚注区,仅一处)
    await waitFor(() => expect(screen.getByText(/风险 1 项 · 库存未知/)).toBeInTheDocument());
    assertNoForbiddenCalls();
  });
});
