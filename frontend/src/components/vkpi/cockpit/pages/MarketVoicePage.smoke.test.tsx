import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// V0a 金样板冒烟 + V0h-ab 波2 终棒:
// - KPI 带四卡 demo 语义(本月反馈/待处理/正面情绪占比/已转产品部)+ 真 sparkline +
//   环比药丸(delta null=诚实省略)+「情绪管线未点火」过期文案清除 + PRD 诚实 pending;
// - 图表模块族(alerts/cat/senti/line_voice/topics/geo/comp)接 voice-report-ext 九组契约,
//   逐组诚实降级(comp 空窗走后端 reason 原样透出);
// - 默认布局 v3 对齐 demo 六行:complaints/wishlist/gaps/buckets/plat 不进默认,palette 可选;
// - V0e 闭环「转回复队列」/「转产品部」诚实态原样保留。
// mock seam:services/vkpi/marketVoice-api(getVoiceReport / getVoiceFeed /
// fetchVoiceReportExt / enqueueReplyQueueComment)—— 页面唯一网络出口,mock 后零真实 HTTP;
// 布局走 localStorage(每测清空)。

const getVoiceReport = vi.fn();
const getVoiceFeed = vi.fn();
const fetchVoiceReportExt = vi.fn();
const enqueueReplyQueueComment = vi.fn();
vi.mock("../../../../services/vkpi/marketVoice-api", () => ({
  getVoiceReport: (...args: unknown[]) => getVoiceReport(...args),
  getVoiceFeed: (...args: unknown[]) => getVoiceFeed(...args),
  fetchVoiceReportExt: (...args: unknown[]) => fetchVoiceReportExt(...args),
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

// voice-report-ext 九组契约(与后端 voice_report_ext.py 形状一致;alerts 正常态=6 类全 0 触发)
const ALERT_CATS = [
  ["motor_noise", "镜头马达异响"],
  ["firmware", "固件与兼容"],
  ["build", "做工与手感"],
  ["price", "价格与价值"],
  ["af", "对焦表现"],
  ["battery", "配件与电池"],
] as const;

const EXT_OK = {
  status: "ready",
  window: { since: "2026-06-11", until: "2026-07-11", label: "近30天" },
  kpi_series: {
    status: "ready",
    granularity: "day",
    series: {
      comments: [
        { date: "2026-07-08", count: 10 },
        { date: "2026-07-09", count: 20 },
        { date: "2026-07-10", count: 30 },
      ],
      queue: [
        { date: "2026-07-08", count: 3 },
        { date: "2026-07-09", count: 2 },
        { date: "2026-07-10", count: 1 },
      ],
    },
  },
  kpi_prev: {
    status: "ready",
    metrics: {
      comments: { current: 769, previous: 625, delta_pct: 23.0, table: "vkpi_comments" },
      // 上窗 0 → delta_pct=null → 前端诚实省略环比药丸
      queue: { current: 132, previous: 0, delta_pct: null, table: "vkpi_reply_queue" },
    },
  },
  sentiment_summary: {
    status: "ready",
    done_total: 769,
    pos_total: 627,
    neu_total: 104,
    neg_total: 38,
    pos_share: 0.821,
    neg_share: 0.046,
    granularity: "day",
    trend: [
      { period_start: "2026-07-08", total: 10, pos_share: 0.8, neg_share: 0.1 },
      { period_start: "2026-07-09", total: 0, pos_share: null, neg_share: null },
      { period_start: "2026-07-10", total: 12, pos_share: 0.85, neg_share: 0.05 },
    ],
  },
  alerts_state: {
    status: "ok",
    method: "lexicon_v0_alerts",
    window_hours: 8,
    threshold: 3,
    scanned: 12,
    categories: ALERT_CATS.map(([category, label]) => ({
      category,
      label,
      negative_count: 0,
      score: 0,
      threshold: 3,
      triggered: false,
      window_hours: 8,
    })),
    recent_pushed: [],
  },
  platform_dist: {
    status: "ready",
    items: [
      { platform: "youtube", count: 700 },
      { platform: "instagram", count: 69 },
    ],
  },
  line_voice: {
    status: "ready",
    items: [
      { key: "af_lens", label: "AF 自动对焦镜头", count: 32, annotated: 30, pos_share: 0.82 },
      { key: "telecon", label: "增距镜", count: 4, annotated: 0, pos_share: null },
    ],
  },
  topics: {
    status: "ready",
    items: [
      { key: "wishlist", label: "愿望/求出", count: 14, delta_pct: -6.7 },
      { key: "firmware", label: "固件与兼容", count: 7, delta_pct: null },
    ],
    scanned: 769,
  },
  language_dist: {
    status: "ready",
    items: [
      { language: "en", count: 350 },
      { language: "und", count: 193 },
    ],
    total: 543,
  },
  competitor_voice: {
    status: "empty",
    reason: "窗口内无已深析(final_v1)视频——竞品声量诚实空,不编。",
    sample_videos: 0,
    viltrox_videos: 0,
    competitor_exposures: 0,
    items: [],
  },
  method: "lexicon_v0_ext",
  generated_at: "2026-07-11T00:00:00+00:00",
};

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
      sentiment: { table: "vkpi_sentiment_results", status: "ready", count: 769 },
    },
    complaints: {
      status: "ready",
      total_matched: 4,
      categories: [
        { key: "af", label: "对焦", count: 3, quotes: [{ text: "对焦拉风箱有点烦", author: "u1", platform: "youtube" }] },
        { key: "fw", label: "固件", count: 1, quotes: [] },
      ],
    },
    wishlist: { status: "empty", reason: "无愿望命中。" },
    gaps: { status: "empty", reason: "本窗口无目录空白焦段声量。" },
    suggestions: {
      status: "ready",
      items: [
        {
          kind: "improvement",
          title: "低温马达异响改进",
          evidence_count: 2,
          confidence: "high",
          detail: "低温环境异响集中在 -10°C 以下,建议核查润滑脂标号。",
          quotes: [{ text: "低温异响原声样本", author: "u9", platform: "reddit" }],
        },
      ],
    },
    buckets: { product_lines: [], product_line_basis: "focal_matrix" },
    generated_at: "2026-07-11T00:00:00+00:00",
    note: "纯词表聚合",
  });
  getVoiceFeed.mockReset().mockResolvedValue({ items: FEED_ITEMS, total: 7, offset: 0, limit: 20 });
  fetchVoiceReportExt.mockReset().mockResolvedValue(EXT_OK);
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

describe("MarketVoicePage smoke (V0h-ab 图形模块补齐 + KPI 带图形化)", () => {
  it("KPI 带四卡 demo 语义:真环比药丸 + delta null 诚实省略 + PRD 诚实 pending + 过期文案清除", async () => {
    expect(() => render(<MarketVoicePage apiToken="t" />)).not.toThrow();

    // 四卡新语义(demo kpiV 601-604 行同构)
    expect(await screen.findByText("本月反馈")).toBeTruthy();
    expect(screen.getByText("待处理")).toBeTruthy();
    expect(screen.getByText("正面情绪占比")).toBeTruthy();
    expect(screen.getByText("已转产品部")).toBeTruthy();
    // 数值来自 kpi_prev 真环比口径(769/132 也出现在 SrcChip 溯源行,取 ≥1)
    expect(screen.getAllByText("769").length).toBeGreaterThan(0);
    expect(screen.getAllByText("132").length).toBeGreaterThan(0);
    // 82.1 同时出现在 KPI 卡与 senti tstats(同源 pos_share,取 ≥1)
    expect(screen.getAllByText("82.1").length).toBeGreaterThan(0);
    // comments delta 23.0 → ▲ 药丸;queue delta null → 诚实省略(全页无 ▼ 环比药丸)
    expect(screen.getByText("▲23.0%")).toBeTruthy();
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(1);
    // PRD 无落库通路 → pending 药丸;「情绪管线未点火」过期文案全删(数据已 769 行)
    expect(screen.getByText("PRD 通路待建")).toBeTruthy();
    expect(screen.queryByText("情绪管线未点火")).toBeNull();
    // 旧口径四数不再占卡面(收进 SrcChip rows,以 hover 卡形式存在)
    expect(screen.queryByText("窗口样本")).toBeNull();
    // sparkline 真序列已画(kpi_series ≥2 点 → svg 而非纯虚线)
    expect(document.querySelectorAll(".ds-kpi .ds-kpi__spark").length).toBeGreaterThanOrEqual(2);

    // 端点确实按契约被调用
    expect(getVoiceReport).toHaveBeenCalledWith("t", "");
    expect(fetchVoiceReportExt).toHaveBeenCalledWith("t", "");
    expect(getVoiceFeed).toHaveBeenCalledWith("t", { offset: 0, limit: 20 });
  });

  it("默认布局 v3 六行:图表模块族在场,文字模块降为 palette 备选;旧 v2 布局键不干扰", async () => {
    // 旧 storageKey 残留不应影响 v3 新默认(bump 版本盖过本机旧布局)
    window.localStorage.setItem("vkpi-market-voice-layout-v2", JSON.stringify([{ moduleKey: "complaints", span: 8 }]));
    render(<MarketVoicePage apiToken="t" />);

    expect(await screen.findByText("声量告警")).toBeTruthy();
    expect(screen.getByText("类别构成")).toBeTruthy();
    expect(screen.getByText("情绪趋势")).toBeTruthy();
    expect(screen.getByText("产品线声音榜")).toBeTruthy();
    expect(screen.getByText("同话题竞品声量")).toBeTruthy();
    expect(screen.getByText("热点话题")).toBeTruthy();
    expect(screen.getByText("按语言 / 市场")).toBeTruthy();
    expect(screen.getByText("监听覆盖")).toBeTruthy();
    expect(screen.getByText("反馈流")).toBeTruthy();
    expect(screen.getByText("给产品部的建议")).toBeTruthy();
    // 降级模块不进默认布局
    expect(screen.queryByText("抱怨聚类")).toBeNull();
    expect(screen.queryByText("愿望清单")).toBeNull();
    expect(screen.queryByText("需求空白")).toBeNull();
    expect(screen.queryByText("平台分布")).toBeNull();
    expect(screen.queryByText("产品线声量分桶")).toBeNull();
    // 反馈流真数据行照常
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();
  });

  it("palette 含降级模块:编辑布局 → 添加模块弹层列出 complaints/wishlist/gaps/buckets/plat", async () => {
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("声量告警")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(await screen.findByText("抱怨聚类")).toBeTruthy();
    expect(screen.getByText("愿望清单")).toBeTruthy();
    expect(screen.getByText("需求空白")).toBeTruthy();
    expect(screen.getByText("平台分布")).toBeTruthy();
    expect(screen.getByText("产品线声量分桶")).toBeTruthy();
  });

  it("alerts 正常态(6 类全 0 触发):全绿聚合行 + 0 触发徽,绝不摆假告警/假推送", async () => {
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("0 触发")).toBeTruthy();
    expect(screen.getByText(/全部 6 类别/)).toBeTruthy();
    expect(screen.getByText("正常")).toBeTruthy();
    // 零触发 → 无「已推送 →」徽、无热行(SrcChip 溯源行的「已推送」口径键不算)
    expect(screen.queryByText("已推送 →")).toBeNull();
    expect(screen.queryByText(/8h 内 \d+ 条负面/)).toBeNull();
  });

  it("alerts 触发态:hot 行 + 阈值文案 +「已推送 →」徽(recent_pushed 对应行)", async () => {
    fetchVoiceReportExt.mockReset().mockResolvedValue({
      ...EXT_OK,
      alerts_state: {
        ...EXT_OK.alerts_state,
        categories: EXT_OK.alerts_state.categories.map((c) =>
          c.category === "firmware" ? { ...c, negative_count: 5, score: 7, triggered: true } : c,
        ),
        recent_pushed: [
          {
            id: 9,
            dedupe_key: "voice_alert:firmware:2026-07-11",
            title: "声量告警 · 「固件与兼容」负面提及 8h 加权 7 分(阈值 3)",
            priority: "high",
            status: "suggested",
            created_at: "2026-07-11T02:00:00+00:00",
          },
        ],
      },
    });
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("1 触发")).toBeTruthy();
    expect(screen.getByText(/8h 内 5 条负面/)).toBeTruthy();
    expect(screen.getByText("已推送 →")).toBeTruthy();
    expect(screen.getByText(/其余 5 类别/)).toBeTruthy();
  });

  it("cat 环图:complaints 类别构成(分段占比图例)+ senti 双线 tstats 三大数", async () => {
    render(<MarketVoicePage apiToken="t" />);
    // 环图图例:3/4=75%、1/4=25%(分段分母=类别命中合计;中心大数=total_matched)
    expect(await screen.findByText("75%")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    expect(screen.getByText("2 类")).toBeTruthy();
    // senti tstats:正/负占比 + 已批注样本(空期 null 断线不抛错)
    expect(screen.getByText("4.6")).toBeTruthy();
    expect(screen.getByText("已批注样本")).toBeTruthy();
    expect(screen.getByText("负面占比")).toBeTruthy();
  });

  it("line_voice 声音榜:pos_share 右注 + null=待批注;geo 待检桶;comp 诚实空;topics chips", async () => {
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("82% 正 · 32条")).toBeTruthy();
    expect(screen.getByText("待批注 · 4条")).toBeTruthy();
    // geo:und → 待检桶(语言分布非地理;「待检」也出现在 SrcChip 口径行,取 ≥1)
    expect(screen.getByText("英语")).toBeTruthy();
    expect(screen.getAllByText("待检").length).toBeGreaterThan(0);
    // comp:窗口零深析视频 → 后端 reason 原样透出(诚实空,不编份额)
    expect(screen.getByText(/竞品声量诚实空/)).toBeTruthy();
    // topics chips:词 + mono 计数
    expect(screen.getByText("愿望/求出")).toBeTruthy();
    expect(screen.getByText("14")).toBeTruthy();
  });

  it("voice-report-ext 整体失败:图表模块诚实错误卡,月报模块不受拖累", async () => {
    fetchVoiceReportExt.mockReset().mockRejectedValue(new Error("HTTP 500"));
    render(<MarketVoicePage apiToken="t" />);
    expect((await screen.findAllByText("voice-report-ext 读取失败")).length).toBeGreaterThan(0);
    // 月报驱动的 cat 环图照常渲染(数据来自 voice-report)
    expect(await screen.findByText("75%")).toBeTruthy();
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();
  });

  it("recs 卡面一行一条,detail 与引文进弹窗(卡面不铺长文)", async () => {
    render(<MarketVoicePage apiToken="t" />);
    const item = await screen.findByText("低温马达异响改进");
    expect(screen.queryByText(/低温异响原声样本/)).toBeNull();
    fireEvent.click(item);
    expect(await screen.findByText(/低温异响原声样本/)).toBeTruthy();
    expect(screen.getByText(/建议核查润滑脂标号/)).toBeTruthy();
  });

  it("feed 收敛:卡面只渲染前 6 条,「≡ 查看全量」开弹窗滚全量", async () => {
    render(<MarketVoicePage apiToken="t" />);
    expect(await screen.findByText("对焦很稳,呼吸效应控制超预期")).toBeTruthy();
    expect(screen.queryByText("第七条原声样本(只在全量弹窗)")).toBeNull();
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

    fireEvent.click(await screen.findByText("对焦很稳,呼吸效应控制超预期"));
    expect(await screen.findByText(/反馈详情/)).toBeTruthy();

    const prdBtn = screen.getByRole("button", { name: /转产品部/ });
    expect((prdBtn as HTMLButtonElement).disabled).toBe(true);
    expect(prdBtn.getAttribute("title") || "").toContain("PRD 通路待建");

    const rqBtn = screen.getByRole("button", { name: /转回复队列/ });
    expect((rqBtn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(rqBtn);
    expect(enqueueReplyQueueComment).toHaveBeenCalledWith("t", 1042);
    expect(await screen.findByText("✓ 已入队")).toBeTruthy();
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
