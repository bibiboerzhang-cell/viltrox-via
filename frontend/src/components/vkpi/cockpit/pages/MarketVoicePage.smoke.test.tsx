import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// V0a 金样板冒烟:MarketVoicePage(可编辑板块页)挂载后渲染出
// KPI 带 + 模块标题 + 反馈流真数据行 + 情感 pending 诚实卡。
// mock seam:services/vkpi/marketVoice-api(getVoiceReport / getVoiceFeed)——
// 页面唯一的网络出口,mock 后零真实 HTTP;布局走 localStorage(每测清空)。

const getVoiceReport = vi.fn();
const getVoiceFeed = vi.fn();
vi.mock("../../../../services/vkpi/marketVoice-api", () => ({
  getVoiceReport: (...args: unknown[]) => getVoiceReport(...args),
  getVoiceFeed: (...args: unknown[]) => getVoiceFeed(...args),
}));

import { MarketVoicePage } from "./MarketVoicePage";

beforeEach(() => {
  window.localStorage.clear();
  getVoiceReport.mockReset().mockResolvedValue({
    status: "ready",
    window: { since: "2026-06-11", until: "2026-07-11", label: "近30天" },
    sample_size: 777,
    dedup_removed: 0,
    sources: {
      comments: { table: "vkpi_comments", status: "ready", count: 777 },
      intent_queue: { table: "vkpi_reply_queue", status: "ready", count: 132 },
      bh_reviews: { table: "vkpi_bh_reviews", status: "empty", count: 0 },
      brand_signal: { table: "vkpi_brand_signal", status: "empty", count: 0 },
      sentiment: { table: "vkpi_sentiment_results", status: "empty", count: 0 },
    },
    complaints: {
      status: "ready",
      total_matched: 3,
      categories: [{ key: "af", label: "对焦", count: 3, quotes: [] }],
    },
    wishlist: { status: "empty", reason: "无愿望命中。" },
    gaps: { status: "empty", reason: "本窗口无目录空白焦段声量。" },
    suggestions: { status: "empty", reason: "无过阈值建议。" },
    buckets: { product_lines: [], product_line_basis: "focal_matrix" },
    generated_at: "2026-07-11T00:00:00+00:00",
    note: "纯词表聚合",
  });
  getVoiceFeed.mockReset().mockResolvedValue({
    items: [
      {
        id: 1042,
        source_table: "vkpi_comments",
        platform: "youtube",
        text: "对焦很稳,呼吸效应控制超预期",
        language: "zh",
        identity: "kol",
        identity_ref: "@demo_kol",
        post_url: "https://www.youtube.com/watch?v=demo",
        likes: 3,
        created_at: "2026-07-10T02:41:00+00:00",
        prov: { fetched_at: "2026-07-10T03:00:00+00:00", post_table: "vkpi_kol_video_evidence", post_id: 9 },
      },
    ],
    total: 1,
    offset: 0,
    limit: 20,
  });
});

describe("MarketVoicePage smoke (V0a 可编辑板块页)", () => {
  it("renders the KPI band, module titles and real feed row from mocked endpoints", async () => {
    expect(() => render(<MarketVoicePage apiToken="t" />)).not.toThrow();

    // 页头(pagehead 行:标题 + 月份控件保留)
    expect(screen.getByText("市场之声 · 用户反馈雷达")).toBeTruthy();
    expect(screen.getByText("近30天")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // KPI 带(kpiV 模块)+ 情感 pending 诚实卡
    expect(await screen.findByText("反馈总览")).toBeTruthy();
    expect(await screen.findByText("窗口样本")).toBeTruthy();
    expect(await screen.findByText("情绪管线未点火")).toBeTruthy();

    // 月报模块标题 + 覆盖模块 + 反馈流模块
    expect(await screen.findByText("抱怨聚类")).toBeTruthy();
    expect(await screen.findByText("愿望清单")).toBeTruthy();
    expect(await screen.findByText("需求空白")).toBeTruthy();
    expect(await screen.findByText("给产品部的建议")).toBeTruthy();
    expect(await screen.findByText("监听覆盖")).toBeTruthy();
    expect(await screen.findByText("反馈流")).toBeTruthy();

    // 反馈流真数据行(mock 的 vkpi_comments 条目)
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();

    // 端点确实按契约被调用
    expect(getVoiceReport).toHaveBeenCalledWith("t", "");
    expect(getVoiceFeed).toHaveBeenCalledWith("t", { offset: 0, limit: 20 });
  });

  it("shows the honest error card when voice-feed fails (绝不假数据)", async () => {
    getVoiceFeed.mockReset().mockRejectedValue(new Error("HTTP 404"));
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("端点待接 · voice-feed")).toBeTruthy();
    expect(await screen.findByText(/HTTP 404/)).toBeTruthy();
  });
});
