import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// 创意资产库板块页改版冒烟(金样板 MarketVoicePage.smoke / MyKolBoardPage.smoke 同构):
// - 页壳:pagehead(标题 + 段数药丸徽 + 编辑布局钮)+ 可编辑看板(布局键
//   vkpi-creative-library-layout-v1;不传 apiToken → 绝不写账户级 dashboard_layout_v1);
// - KPI 带四卡全真值(段级条目/已深析视频/覆盖 KOL/当前命中);端点无按日时序 →
//   四卡全 spempty 诚实虚线,零假 sparkline 零假环比药丸;
// - 段级条目流:三路筛选(旧页零丢失)+ 卡面 6 条 + 「≡ 查看全量」→ 全量弹窗 →
//   单条详情 ‹ #n/N › + ↑↓ 连续翻;端点单次封顶如实标注(零假「载入更多」);
// - 图表联动:拍法条形点行 = style 精确过滤(真请求参数);段型环图点图例 =
//   已加载条目内过滤(如实标注端点无此参数);
// - 缩略图 = 毒缓存自愈链读端:cached_thumbnail_url(本地缓存)有 → 真 img;
//   三路皆无 → 诚实 ▶ 占位(绝不编图);
// - 诚实态:status:empty(深析库空带 reason)/ status:error(检索暂不可用)/
//   端点抛错(读取失败卡)三轨;
// - 溯源:SrcChip 口径行含真表名;详情溯源链每跳在场;「打开 KOL 档案 →」=
//   sessionStorage 传 kol_pool_id + onNavigate("kolProfile")(CockpitApp 既有管道)。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { CreativeLibraryBoardPage } from "./CreativeLibraryBoardPage";

// 段级条目(形状对齐 creative_segments._decompose_video 出参;video 含缩略图三件套):
// v1 = 有本地缓存缩略图(自愈后真图);v2 = 三路皆无(诚实占位)。
const V1 = {
  evidence_id: 1603,
  title: "VILTROX 85mm f1.4 review",
  content_url: "https://youtu.be/abc12345678",
  platform: "youtube",
  view_count: 5_200_000,
  posted_at: "2026-05-01T00:00:00+00:00",
  thumbnail_url: "https://i.ytimg.com/vi/abc12345678/hqdefault.jpg",
  cached_thumbnail_url: "/api/vkpi-media/image-cache/deadbeef",
  youtube_thumbnail_url: "https://img.youtube.com/vi/abc12345678/hqdefault.jpg",
  best_thumbnail: "/api/vkpi-media/image-cache/deadbeef",
};
const V2 = {
  evidence_id: 1720,
  title: "Street photography POV",
  content_url: "https://instagram.com/p/xyz",
  platform: "instagram",
  view_count: 88_000,
  posted_at: null,
  thumbnail_url: null,
  cached_thumbnail_url: null,
  youtube_thumbnail_url: null,
  best_thumbnail: null,
};
const KOL1 = { kol_pool_id: 101, handle: "@alpha", display_name: "Alpha Cam" };
const KOL2 = { kol_pool_id: null, handle: "", display_name: "" };

function seg(i: number, type: string, video: typeof V1, kol: typeof KOL1, extra: Record<string, unknown> = {}) {
  return {
    segment_id: `${video.evidence_id}:layer1_visual_content.scene_timeline[${i}]`,
    segment_type: type,
    description: `segment-desc-${i} bokeh night test`,
    timestamp: `00:${String(i).padStart(2, "0")}`,
    opening_types: [] as Array<{ key: string; label: string }>,
    styles: [{ key: "cinematic_broll", label: "电影感B-roll" }],
    video_styles: [{ key: "talking_review", label: "口播评测" }],
    focals: ["85mm"],
    source: { evidence_id: video.evidence_id, layer: `layer1_visual_content.scene_timeline[${i}]`, derive_method: "video_analysis_final_v1" },
    video,
    kol,
    ...extra,
  };
}

// 8 条:opening ×2 / scene ×5 / product_exposure ×1(卡面 6 条 + 全量弹窗断言用)
const ITEMS = [
  seg(0, "opening", V1, KOL1, { opening_types: [{ key: "hook_question", label: "悬念提问" }] }),
  seg(1, "scene", V1, KOL1),
  seg(2, "scene", V1, KOL1),
  seg(3, "scene", V1, KOL1),
  seg(4, "product_exposure", V1, KOL1, { timestamp: null }),
  seg(0, "opening", V2, KOL2, { opening_types: [{ key: "scene_atmosphere", label: "场景氛围" }], focals: [] }),
  seg(1, "scene", V2, KOL2, { focals: [] }),
  seg(2, "scene", V2, KOL2, { focals: [] }),
];

const READY = {
  status: "ready",
  method: "lexicon_segments_v1",
  filters: { query: "", style: "", focal: "" },
  scanned_videos: 2,
  segment_count: 8,
  kol_count: 1,
  matched: 8,
  returned: 8,
  items: ITEMS,
  facets: {
    styles: [
      { key: "cinematic_broll", label: "电影感B-roll", segment_count: 5 },
      { key: "pov", label: "POV第一视角", segment_count: 2 },
    ],
    openings: [
      { key: "hook_question", label: "悬念提问", segment_count: 1 },
      { key: "scene_atmosphere", label: "场景氛围", segment_count: 1 },
    ],
    focals: [
      { focal: "85mm", segment_count: 5 },
      { focal: "35mm", segment_count: 2 },
    ],
    segment_types: [
      { key: "scene", label: "分镜段", segment_count: 5 },
      { key: "opening", label: "开头段", segment_count: 2 },
      { key: "product_exposure", label: "产品露出段", segment_count: 1 },
    ],
  },
  generated_at: "2026-07-12T02:00:00+00:00",
  note: "段级条目=纯已有 final_v1 分析文本的索引。独立展示信号,不参与 V6 Fit 评分。",
};

// board-series?board=creative 真形状(对照 backend board_series._creative_board 出参):
// 段级新增/深析新增双序列;缺省用例走 Error(端点失败 → 卡面照旧诚实虚线的回归锁)。
const BOARD_SERIES_READY = {
  status: "ready",
  board: "creative",
  days: 30,
  window: { since: "2026-06-13", until: "2026-07-12", prev_since: "2026-05-14", prev_until: "2026-06-12" },
  series: {
    deep_videos_new: [
      { date: "2026-07-10", count: 0 },
      { date: "2026-07-11", count: 3 },
      { date: "2026-07-12", count: 17 },
    ],
    segments_new: [
      { date: "2026-07-10", count: 0 },
      { date: "2026-07-11", count: 20 },
      { date: "2026-07-12", count: 99 },
    ],
  },
  metrics: {
    deep_videos_new: { status: "ready", current: 20, previous: 417, delta_pct: -95.2, table: "vkpi_analysis_cache", unit: "rows" },
    segments_new: { status: "ready", current: 119, previous: 3825, delta_pct: -96.9, table: "vkpi_analysis_cache", unit: "rows" },
  },
  basis: { deep_videos_new: "vkpi_analysis_cache final_v1 ready 按日", segments_new: "同窗 result 拆段按日" },
  method: "board_series_v1",
  generated_at: "2026-07-12T02:00:00+00:00",
};

function routeApi(response: unknown = READY, boardSeries: unknown = new Error("board-series 未接通")) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/creative-segments/search")) {
      if (response instanceof Error) throw response;
      return response;
    }
    if (p.startsWith("/api/admin/vkpi/board-series")) {
      if (boardSeries instanceof Error) throw boardSeries;
      return boardSeries;
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<CreativeLibraryBoardPage apiToken="t" {...(props as any)} />);

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("CreativeLibraryBoardPage smoke(页壳 + KPI 带真值 + 布局键)", () => {
  it("KPI 带四卡全真值;端点无时序 → 四卡 spempty 诚实虚线零假药丸;pagehead 徽 + SrcChip 真表名口径", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 四卡现值:段级条目 8 / 已深析视频 2 / 覆盖 KOL 1 / 当前命中 8
    expect(await screen.findByText("段级条目")).toBeTruthy();
    expect(screen.getByText("已深析视频")).toBeTruthy();
    expect(screen.getByText("覆盖 KOL")).toBeTruthy();
    expect(screen.getByText("当前命中")).toBeTruthy();
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    // board-series 读取失败(缺省 mock)→ 全部诚实虚线;零 sparkline 零环比药丸(不编趋势)
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead:段数药丸 + 可编辑看板徽 + 编辑布局钮
    expect(screen.getByText("8 段")).toBeTruthy();
    expect(screen.getByText("可编辑看板")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // 口径进 SrcChip(真表名 + 旧页头长句收编:方法/使用权/独立性)
    expect(screen.getAllByText(/vkpi_analysis_cache/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_kol_video_evidence/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/白名单使用权请求进外联模板/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/不参与 V6 Fit 评分/).length).toBeGreaterThan(0);

    // 布局:本机 storageKey 落盘;绝不写账户级偏好端点(不传 apiToken 给看板)
    await waitFor(() => expect(window.localStorage.getItem("vkpi-creative-library-layout-v1")).toBeTruthy());
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(
      calledPaths.every(
        (p) => p.startsWith("/api/admin/vkpi/creative-segments/search") || p.startsWith("/api/admin/vkpi/board-series"),
      ),
    ).toBe(true);
  });

  it("board-series 就绪 → 段级条目/已深析视频两卡点亮真 sparkline(关联指标零环比药丸);另两卡虚线如实", async () => {
    routeApi(READY, BOARD_SERIES_READY);
    renderBoard();
    expect(await screen.findByText("段级条目")).toBeTruthy();
    // 两卡真 sparkline + 两卡诚实虚线(覆盖 KOL / 当前命中无按日序列)
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(2));
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(2);
    // 卡面大数是全库存量,序列是关联流量指标 → 环比药丸诚实不渲染
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    // 序列请求真发向 board-series?board=creative
    const bsCalls = apiFetchMock.mock.calls.map((call) => String(call[0])).filter((p) => p.startsWith("/api/admin/vkpi/board-series"));
    expect(bsCalls.length).toBeGreaterThan(0);
    expect(bsCalls[0]).toContain("board=creative");
  });

  it("段级条目流:卡面 6 条 + meta 行;全量弹窗 → 详情 ‹ #n/N › + ↑↓ 连续翻;溯源链每跳在场", async () => {
    renderBoard();
    // 卡面收敛 6 条(8 条命中 → 溢出走弹窗)
    expect(await screen.findByText(/≡ 查看全量 8 段/)).toBeTruthy();
    expect(screen.getAllByText(/segment-desc-/).length).toBe(6);
    expect(screen.getByText(/扫描 2 条深析视频 · 索引 8 段 · 命中 8 段/)).toBeTruthy();

    // 全量弹窗:8 条全在;命中=已加载 → 零封顶注
    fireEvent.click(screen.getByText(/≡ 查看全量 8 段/));
    expect(await screen.findByText("段级条目 · 全量")).toBeTruthy();
    expect(screen.getAllByText(/segment-desc-/).length).toBe(6 + 8);
    expect(screen.queryByText(/长尾未加载/)).toBeNull();

    // 点第一条 → 详情 #1/8;↓ 连续翻到 #2/8
    const listModal = screen.getByText("段级条目 · 全量").closest('[role="dialog"]') as HTMLElement;
    fireEvent.click(within(listModal).getAllByText(/segment-desc-0/)[0]);
    expect(await screen.findByText("#1 / 8")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2 / 8")).toBeTruthy();

    // 溯源链每跳在场(真表名节点 + 段级索引节点);白名单后续刀如实注脚
    expect(screen.getAllByText(/库记录 vkpi_kol_video_evidence/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/深析缓存 vkpi_analysis_cache/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/段级索引 · 实时词表拆解/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/外联模板接线 = 后续刀/).length).toBeGreaterThan(0);
  });

  it("详情「打开 KOL 档案 →」= sessionStorage 传 kol_pool_id + onNavigate(kolProfile)", async () => {
    const onNavigate = vi.fn();
    renderBoard({ onNavigate });
    fireEvent.click((await screen.findAllByText(/segment-desc-0/))[0]);
    const btn = await screen.findByText("打开 KOL 档案 →");
    fireEvent.click(btn);
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(onNavigate).toHaveBeenCalledWith("kolProfile");
  });

  it("缩略图毒缓存自愈链读端:本地缓存 → 真 img;三路皆无 → 诚实 ▶ 占位", async () => {
    renderBoard();
    await screen.findByText(/≡ 查看全量 8 段/);
    // v1 行:cached_thumbnail_url(本地 /api/vkpi-media/image-cache/)直出 img
    expect(document.querySelector('img[src*="/api/vkpi-media/image-cache/deadbeef"]')).toBeTruthy();
    // v2 行(卡面第 6 条 = V2 开头段):三路皆无 → 占位 + 诚实 title
    expect(screen.getAllByTitle(/该视频无可用缩略图/).length).toBeGreaterThan(0);
  });

  it("拍法条形点行 = style 真请求参数;段型环图点图例 = 已加载内过滤(如实标注)", async () => {
    renderBoard();
    await screen.findByText(/≡ 查看全量 8 段/);

    // 拍法构成模块内点「电影感B-roll」行 → 防抖后带 style= 精确重查
    const stylesCard = screen.getByText("拍法构成").closest("section") as HTMLElement;
    fireEvent.click(within(stylesCard).getByText("电影感B-roll"));
    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some((call) => String(call[0]).includes(`style=${encodeURIComponent("电影感B-roll")}`)),
      ).toBe(true),
    );

    // 段型环图点图例「开头段」→ 客户端段型过滤:chip 出现 + 卡面只剩 2 条 + 口径如实
    // (SrcChip 口径行里也有「开头段」字样 —— 必须限定到可点的图例行 role=button)
    const typesCard = screen.getByText("段型构成").closest("section") as HTMLElement;
    const legendRow = within(typesCard)
      .getAllByText("开头段")
      .map((el) => el.closest('[role="button"]') as HTMLElement | null)
      .find((el) => el && !el.closest("header")) as HTMLElement;
    fireEvent.click(legendRow);
    expect(await screen.findByText("开头段 ✕")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/段型过滤后 2 段/)).toBeTruthy());
    // 清除 chip → 恢复
    fireEvent.click(screen.getByText("开头段 ✕"));
    await waitFor(() => expect(screen.queryByText("开头段 ✕")).toBeNull());
  });

  it("三筛选旧功能零丢失:自由词防抖重查 / 拍法下拉 facets 真计数 / 焦段库内最多提示", async () => {
    renderBoard();
    await screen.findByText(/≡ 查看全量 8 段/);

    // 拍法下拉:facets 真计数选项
    const select = document.querySelector("select") as HTMLSelectElement;
    expect(select).toBeTruthy();
    // option 文本为「标签 +(计数 段)」两个文本节点 —— 按整体 textContent 断言
    const optionTexts = [...select.querySelectorAll("option")].map((opt) => opt.textContent || "");
    expect(optionTexts.some((text) => text.includes("电影感B-roll(5 段)"))).toBe(true);
    // 焦段输入:placeholder 带库里最多提示(facets 真值)
    expect(document.querySelector('input[placeholder*="库里最多:85mm / 35mm"]')).toBeTruthy();

    // 自由词输入 → 300ms 防抖后 query= 重查
    const queryInput = document.querySelector('input[placeholder^="自由词"]') as HTMLInputElement;
    fireEvent.change(queryInput, { target: { value: "bokeh" } });
    await waitFor(() => expect(apiFetchMock.mock.calls.some((call) => String(call[0]).includes("query=bokeh"))).toBe(true));
  });

  it("诚实态三轨:深析库空(status:empty 带 reason)/ 检索暂不可用(status:error)/ 端点抛错", async () => {
    routeApi({ status: "empty", reason: "深析库为空(vkpi_analysis_cache 无 ready 的 final_v1 视频)", items: [] });
    const first = renderBoard();
    expect((await screen.findAllByText(/深析库为空/)).length).toBeGreaterThan(0);
    first.unmount();

    routeApi({ status: "error", reason: "db down", items: [] });
    const second = renderBoard();
    expect((await screen.findAllByText("检索暂不可用")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/db down/).length).toBeGreaterThan(0);
    second.unmount();

    routeApi(new Error("boom"));
    renderBoard();
    expect((await screen.findAllByText("creative-segments 读取失败")).length).toBeGreaterThan(0);
  });

  it("端点封顶如实:matched > 已加载 → 全量弹窗封顶注(零假「载入更多」);当前命中读 matched 真值", async () => {
    routeApi({ ...READY, matched: 300 });
    renderBoard();
    expect(await screen.findByText(/命中 300 段/)).toBeTruthy();
    fireEvent.click(screen.getByText(/≡ 查看全量 8 段/));
    expect(await screen.findByText(/长尾未加载/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /载入更多/ })).toBeNull();
  });

  it("无 token:诚实 pending 卡,零请求", async () => {
    renderBoard({ apiToken: "" });
    expect((await screen.findAllByText(/未登录 \/ 无 token/)).length).toBeGreaterThan(0);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
