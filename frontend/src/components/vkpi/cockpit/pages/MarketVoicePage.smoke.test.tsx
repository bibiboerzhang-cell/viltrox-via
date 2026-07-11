import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// V0a 金样板冒烟:MarketVoicePage(可编辑板块页)挂载后渲染出
// KPI 带 + 模块标题 + 反馈流真数据行 + 情感 pending 诚实卡。
// V0h-c chrome 新形态:卡头 cnt 短徽(长口径进 SrcChip)/ 引文卡面一行入口 → 中央弹窗 /
// feed 卡面只留 6 条 + 「≡ 查看全量」弹窗(分页逻辑随行为进弹窗)。
// V0e 闭环:详情弹窗「转回复队列」真调 enqueueReplyQueueComment → ✓ 已入队;
// 「转产品部」诚实 disabled(PRD 通路待建,不假装成功)。
// mock seam:services/vkpi/marketVoice-api(getVoiceReport / getVoiceFeed /
// enqueueReplyQueueComment)—— 页面唯一的网络出口,mock 后零真实 HTTP;
// 布局走 localStorage(每测清空)。

const getVoiceReport = vi.fn();
const getVoiceFeed = vi.fn();
const enqueueReplyQueueComment = vi.fn();
vi.mock("../../../../services/vkpi/marketVoice-api", () => ({
  getVoiceReport: (...args: unknown[]) => getVoiceReport(...args),
  getVoiceFeed: (...args: unknown[]) => getVoiceFeed(...args),
  enqueueReplyQueueComment: (...args: unknown[]) => enqueueReplyQueueComment(...args),
}));

import { MarketVoicePage } from "./MarketVoicePage";

// 7 条反馈:卡面按 demo 只渲染前 6 条,第 7 条只在「查看全量」弹窗可见
const FEED_ITEMS = Array.from({ length: 7 }, (_, i) => ({
  id: 1042 + i,
  source_table: "vkpi_comments",
  platform: "youtube",
  text: i === 0 ? "对焦很稳,呼吸效应控制超预期" : i === 6 ? "第七条原声样本(只在全量弹窗)" : `原声样本第${i + 1}条`,
  language: "zh",
  identity: "kol",
  identity_ref: "@demo_kol",
  post_url: "https://www.youtube.com/watch?v=demo",
  likes: i === 0 ? 3 : 0,
  created_at: "2026-07-10T02:41:00+00:00",
  prov: { fetched_at: "2026-07-10T03:00:00+00:00", post_table: "vkpi_kol_video_evidence", post_id: 9 },
}));

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
      categories: [{ key: "af", label: "对焦", count: 3, quotes: [{ text: "对焦拉风箱有点烦", author: "u1", platform: "youtube" }] }],
    },
    wishlist: { status: "empty", reason: "无愿望命中。" },
    gaps: { status: "empty", reason: "本窗口无目录空白焦段声量。" },
    suggestions: { status: "empty", reason: "无过阈值建议。" },
    buckets: { product_lines: [], product_line_basis: "focal_matrix" },
    generated_at: "2026-07-11T00:00:00+00:00",
    note: "纯词表聚合",
  });
  getVoiceFeed.mockReset().mockResolvedValue({
    items: FEED_ITEMS,
    total: 7,
    offset: 0,
    limit: 20,
  });
  enqueueReplyQueueComment.mockReset().mockResolvedValue({
    ok: true,
    already_queued: false,
    comment_id: 1042,
    item: {
      id: 501,
      platform: "youtube",
      comment_external_id: "yt-c-777",
      intent_tag: "manual",
      lang: "zh",
      status: "pending",
      created_at: "2026-07-11T00:00:00Z",
    },
  });
});

describe("MarketVoicePage smoke (V0a 可编辑板块页 + V0h-c chrome)", () => {
  it("renders pagehead badges, KPI band, module titles and real feed row from mocked endpoints", async () => {
    expect(() => render(<MarketVoicePage apiToken="t" />)).not.toThrow();

    // 页头(demo pagehead 范式):标题 + 方法论药丸徽(长句已删)+ 控件保留
    expect(screen.getByText("市场之声 · 用户反馈雷达")).toBeTruthy();
    expect(screen.getByText("lexicon_v0")).toBeTruthy();
    expect(screen.getByText("零 LLM")).toBeTruthy();
    expect(screen.getByRole("button", { name: "近30天" })).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // KPI 带(kpiV 模块)+ 情感 pending 诚实卡(.pt 药丸独立行)
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

    // 卡头 cnt 短徽化:抱怨聚类 = 纯计数「3」;监听覆盖 = 「2/5」(长口径句不再上卡头)
    expect(await screen.findByText("3")).toBeTruthy();
    expect(await screen.findByText("2/5")).toBeTruthy();
    expect(screen.queryByText(/命中 3 条 · 话题词/)).toBeNull();

    // 反馈流真数据行(mock 的 vkpi_comments 条目)
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();

    // 页脚「生成于…」行已删(口径进 kpiV SrcChip rows)
    expect(screen.queryByText(/生成于 2026-07-11/)).toBeNull();

    // 端点确实按契约被调用
    expect(getVoiceReport).toHaveBeenCalledWith("t", "");
    expect(getVoiceFeed).toHaveBeenCalledWith("t", { offset: 0, limit: 20 });
  });

  it("引文全进弹窗:卡面只留「▸ 原声 ×N(点开)」一行,点击开中央弹窗列原文", async () => {
    render(<MarketVoicePage apiToken="t" />);
    const fold = await screen.findByText("▸ 原声 ×1(点开)");
    // 卡面不内联铺原文
    expect(screen.queryByText(/对焦拉风箱有点烦/)).toBeNull();
    fireEvent.click(fold);
    expect(await screen.findByText(/对焦拉风箱有点烦/)).toBeTruthy();
    expect(screen.getByText(/抱怨原声 · 对焦/)).toBeTruthy();
  });

  it("feed 收敛:卡面只渲染前 6 条,「≡ 查看全量」开弹窗滚全量", async () => {
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();
    // 第 7 条不在卡面
    expect(screen.queryByText("第七条原声样本(只在全量弹窗)")).toBeNull();
    // 「已全量加载」状态行已删
    expect(screen.queryByText(/已全量加载/)).toBeNull();
    fireEvent.click(screen.getByText("≡ 查看全量 7 条 · 点单条连续翻"));
    expect(await screen.findByText("第七条原声样本(只在全量弹窗)")).toBeTruthy();
    expect(screen.getByText("反馈流 · 全量")).toBeTruthy();
  });

  it("shows the honest error card when voice-feed fails (绝不假数据)", async () => {
    getVoiceFeed.mockReset().mockRejectedValue(new Error("HTTP 404"));
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("端点待接 · voice-feed")).toBeTruthy();
    expect(await screen.findByText(/HTTP 404/)).toBeTruthy();
  });

  it("详情弹窗闭环动作(V0e):转回复队列真调端点→✓ 已入队;转产品部诚实 disabled", async () => {
    render(<MarketVoicePage apiToken="t" />);

    // 点开反馈流单条 → 详情弹窗
    fireEvent.click(await screen.findByText("对焦很稳,呼吸效应控制超预期"));
    expect(await screen.findByText(/反馈详情/)).toBeTruthy();

    // 转产品部:无 market→PRD 落库通路 → 按钮 disabled + title 说明(不假装成功)
    const prdBtn = screen.getByRole("button", { name: /转产品部/ });
    expect((prdBtn as HTMLButtonElement).disabled).toBe(true);
    expect(prdBtn.getAttribute("title") || "").toContain("PRD 通路待建");

    // 转回复队列:点击 → 端点按契约被调(token + vkpi_comments.id)→ 成功后变 ✓ 已入队
    const rqBtn = screen.getByRole("button", { name: /转回复队列/ });
    expect((rqBtn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(rqBtn);
    expect(enqueueReplyQueueComment).toHaveBeenCalledWith("t", 1042);
    expect(await screen.findByText("✓ 已入队")).toBeTruthy();
    // 该 fb 行加小徽(反馈流行内「已入队」徽)
    expect(await screen.findByText("💬 已入队")).toBeTruthy();
  });

  it("转回复队列失败时错误如实展示(不吞、不假绿)", async () => {
    enqueueReplyQueueComment.mockReset().mockRejectedValue(new Error("HTTP 403"));
    render(<MarketVoicePage apiToken="t" />);
    fireEvent.click(await screen.findByText("对焦很稳,呼吸效应控制超预期"));
    fireEvent.click(await screen.findByRole("button", { name: /转回复队列/ }));
    expect(await screen.findByText(/转回复队列失败/)).toBeTruthy();
    expect(await screen.findByText(/HTTP 403/)).toBeTruthy();
    expect(screen.queryByText("✓ 已入队")).toBeNull();
  });
});
