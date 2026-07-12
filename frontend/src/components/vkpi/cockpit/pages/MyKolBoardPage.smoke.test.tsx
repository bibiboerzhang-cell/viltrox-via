import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// MY KOL 改版 M1 骨架 + M3 库弹窗化 + M4 图形真身冒烟(金样板 MarketVoicePage.smoke 同构):
// - 页壳:pagehead(MY KOL + 视角/KOL 数药丸徽 + 编辑布局钮)+ 可编辑看板;
// - 【M4】KPI 带四卡 series 接线:K1/K2/K4 真 sparkline + delta 药丸(board-ext
//   kpi_series;K1 pool_followers 缺快照日 null 断点 = 多段 line path 如实断线);
//   K3 保持点时实测口径(board-ext kol_views 自取,主控注入兜底,无 series 诚实虚线);
// - 【M4】funnel 合作漏斗 8 段条形(点段=过滤 library,筛选状态 page 层共享)/
//   fitdist 直方(10 桶 + 未评分诚实桶)/ platdist 条形(点行=库平台过滤)全真身;
// - 【M4】palette 六备选真身:viewsTop/contacts/followerTrend/claims/shares/cover
//   (cover=board-ext 无此组 → 静态盘点标日期);
// - 【M3→M4】库「有 V 视频」= board-ext v_content.v_kol_ids 名单精确过滤(Set 查找;
//   truncated / 名单缺席均如实降级标注,绝不悄悄装精确);
// - 注册表 manager vs employee 差异(裁决②A)+ 布局键 vkpi-my-kol-layout-v1 +
//   不传 apiToken → 绝不写账户级 dashboard_layout_v1;
// - 诚实空态:aggregate 失败 = 错误卡;board-ext 失败 = 图形卡 ErrorCard + KPI 带
//   时序/药丸缺席不编数;
// - 【M5】详情档案卡「打开 KOL 档案 →」= sessionStorage 传 kol_pool_id +
//   vkpi:open-kol-profile 事件(CockpitApp 既有管道,MarketVoice jumpIdentity 同口径);
// - 【M6】五内嵌模块 embeds 包装收编:卡头真短计数(team 负责人数/official 账号数/
//   rollup 窗口徽=包装层持窗)+ data-embed 收编容器;旧组件文件零改动(标题 CSS 隐藏仍在 DOM)。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MyKolBoardPage } from "./MyKolBoardPage";

// 【M3】库行(pool_favorites 形状对齐 my_kol_aggregate._pool_favorites):
// Alpha=收藏+挂项目(stage=shipped,漏斗「已寄样」段联动判据)/ Beta=共享行(零项目,
// 但在 v_kol_ids 名单内 —— 精确过滤必须留它,近似过滤会漏它)/ Gamma=收藏+认领桥。
const FAVS = [
  { kol_pool_id: 101, display_name: "Alpha Cam", handle: "@alpha", platform: "youtube", followers: 120000, viltrox_fit_score: 82, avatar_url: "", profile_url: "https://youtube.com/@alpha", country: "US", is_shared: false, shared_by_name: "", created_at: "2026-06-01T00:00:00Z", projects: [{ project_id: 7, project_name: "AF 85mm 铺量", stage: "shipped" }], contacts: [{ contact_type: "email", contact_value: "alpha@example.com" }] },
  { kol_pool_id: 102, display_name: "Beta Vlog", handle: "@beta", platform: "instagram", followers: 5600, viltrox_fit_score: null, avatar_url: "", profile_url: "", country: "", is_shared: true, shared_by_name: "Alice", created_at: "2026-06-02T00:00:00Z", projects: [], contacts: [] },
  { kol_pool_id: 103, display_name: "Gamma", handle: "@gamma", platform: "tiktok", followers: null, viltrox_fit_score: 55, avatar_url: "", profile_url: "", country: "", is_shared: false, shared_by_name: "", created_at: "2026-06-03T00:00:00Z", projects: [], contacts: [] },
];
const CLAIMS = [{ id: 55, kol_id: 9, status: "active", expires_at: "2026-08-01T00:00:00Z", kol_name: "Gamma", kol_platform: "tiktok" }];

// aggregate 现值(kpi_summary 形状对齐 my_kol_aggregate._kpi_summary)
const AGG_OK = {
  staff: { id: 3, name: "Boss", email: "boss@viltrox.com" },
  window_days: 30,
  official_matrix: { platforms: [], account_count: 0, total_followers: 0, total_posts: 0, total_views: 0 },
  pool_favorites: FAVS,
  projects: [],
  claims: CLAIMS,
  kpi_summary: { favorites_count: 719, claimed_count: 12, in_project_count: 87, published_count: 45, projects_count: 40 },
};

// 【M4】board-ext 七组聚合(形状对齐 my_kol_board_ext.py 各构建器;
// pool_followers 第 3 日 null = 快照缺日断点;v_kol_ids=[101,102] 精确名单)
const EXT_OK = {
  status: "ready",
  days: 30,
  kpi_series: {
    status: "ready",
    granularity: "day",
    series: {
      pool_followers: [
        { date: "2026-07-07", value: 1_000_000 },
        { date: "2026-07-08", value: 1_010_000 },
        { date: "2026-07-09", value: null },
        { date: "2026-07-10", value: 1_030_000 },
        { date: "2026-07-11", value: 1_040_000 },
      ],
      new_videos: [
        { date: "2026-07-07", count: 3 },
        { date: "2026-07-08", count: 0 },
        { date: "2026-07-09", count: 5 },
        { date: "2026-07-10", count: 2 },
        { date: "2026-07-11", count: 4 },
      ],
      official_followers: [
        { date: "2026-07-07", value: 250_600 },
        { date: "2026-07-08", value: 250_700 },
        { date: "2026-07-09", value: 250_800 },
        { date: "2026-07-10", value: 250_900 },
        { date: "2026-07-11", value: 251_000 },
      ],
      official_views: [{ date: "2026-07-11", value: 98_000_000 }],
    },
    metrics: {
      pool_followers: { current: 1_040_000, previous: 990_000, delta_pct: 5.1, table: "vkpi_kol_fit_snapshot" },
      new_videos: { current: 14, previous: 10, delta_pct: 40.0, table: "vkpi_kol_video_evidence" },
      official_followers: { current: 251_000, previous: 249_000, delta_pct: 0.8, table: "vkpi_channel_metrics" },
      official_views: { current: 98_000_000, previous: 97_000_000, delta_pct: 1.0, table: "vkpi_channel_metrics" },
    },
    kol_views: { status: "point_in_time", views_total: 2_052_179_053, measured: 1645, evidence_total: 2134, fill_rate: 0.771, basis: "点时实测" },
  },
  funnel: {
    status: "ready",
    total: 2094,
    items: [
      { stage: "discovered", label: "发现", count: 193, raw_stages: ["discovered", "discovery"] },
      { stage: "contacted", label: "已联系", count: 242, raw_stages: ["contacted", "replied"] },
      { stage: "agreed", label: "已同意", count: 39, raw_stages: ["agreed"] },
      { stage: "shipped", label: "已寄样", count: 850, raw_stages: ["device_sent", "shipped"] },
      { stage: "delivered", label: "已签收", count: 2, raw_stages: ["received"] },
      { stage: "content_published", label: "已发布", count: 695, raw_stages: ["content_posted"] },
      { stage: "retrospective_ready", label: "待复盘", count: 1, raw_stages: ["measured"] },
      { stage: "closed", label: "已关闭", count: 166, raw_stages: ["cancelled", "churned"] },
    ],
    other: [],
    basis: "assignments × 收藏集",
  },
  platform_dist: {
    status: "ready",
    items: [
      { platform: "youtube", count: 520 },
      { platform: "instagram", count: 150 },
      { platform: "tiktok", count: 47 },
    ],
    total: 717,
    basis: "收藏集 GROUP BY platform",
  },
  fit_dist: {
    status: "ready",
    buckets: [
      { bucket: "0-9", min: 0, max: 9, count: 0 },
      { bucket: "10-19", min: 10, max: 19, count: 4 },
      { bucket: "20-29", min: 20, max: 29, count: 12 },
      { bucket: "30-39", min: 30, max: 39, count: 40 },
      { bucket: "40-49", min: 40, max: 49, count: 96 },
      { bucket: "50-59", min: 50, max: 59, count: 180 },
      { bucket: "60-69", min: 60, max: 69, count: 220 },
      { bucket: "70-79", min: 70, max: 79, count: 120 },
      { bucket: "80-89", min: 80, max: 89, count: 120 },
      { bucket: "90-100", min: 90, max: 100, count: 8 },
    ],
    unscored: 410,
    scored_total: 800,
    total: 1210,
    basis: "全池十分位分桶",
  },
  contact_coverage: {
    status: "ready",
    types: [
      { contact_type: "email", count: 830 },
      { contact_type: "instagram", count: 210 },
    ],
    covered: 247,
    total: 719,
    coverage: 0.344,
    basis: "类型=全池 · 覆盖率=收藏集",
  },
  views_top: {
    status: "ready",
    items: [
      { kol_pool_id: 101, display_name: "Alpha Cam", handle: "@alpha", platform: "youtube", total_views: 120_000_000, video_count: 42 },
      { kol_pool_id: 999, display_name: "Zeta Films", handle: "@zeta", platform: "tiktok", total_views: 80_000_000, video_count: 17 },
    ],
    basis: "view_count 点时实测 Top12",
  },
  v_content: {
    status: "ready",
    total_evidence: 2134,
    v_kol_count: 387,
    v_kol_ids: [101, 102],
    v_kol_ids_truncated: false,
    tiers: { cooperation: 806, title_mention: 376, title_mention_only: 353, overlap_both: 23, undetermined: 975 },
    tiers_by_kol: { cooperation_kols: 210, title_mention_kols: 240 },
  },
};

// 【M3】单 KOL evidence 视频(/kol-pool/{id}/videos):Alpha 三档齐;Beta/Gamma 零视频。
const VIDEOS: Record<string, unknown[]> = {
  "101": [
    { evidence_id: 9001, id: 9001, kol_pool_id: 101, project_id: 7, title: "On set with the new lens", view_count: 1000, like_count: 10, comment_count: 2, has_final_v1_cache: true, publish_date: "2026-07-01", content_url: "https://youtu.be/a1" },
    { evidence_id: 9002, id: 9002, kol_pool_id: 101, project_id: null, title: "VILTROX 85mm field test", view_count: null, like_count: 5, comment_count: 1, has_final_v1_cache: false, publish_date: "2026-06-20", content_url: "https://youtu.be/a2" },
    { evidence_id: 9003, id: 9003, kol_pool_id: 101, project_id: null, title: "daily vlog", view_count: 500, like_count: 3, comment_count: 0, has_final_v1_cache: false, publish_date: "2026-06-10", content_url: "https://youtu.be/a3" },
  ],
};

// official-matrix(mapPlatform/mapAccount 读 snake_case)
const MATRIX_OK = {
  platforms: [
    {
      platform: "youtube",
      label: "YouTube",
      total_followers: 251000,
      total_posts: 1200,
      total_views: 98000000,
      accounts: [
        {
          id: 11,
          staff_id: 5,
          staff_name: "Alice",
          staff_email: "alice@viltrox.com",
          platform: "youtube",
          handle: "@viltrox",
          display_name: "VILTROX",
          sync_status: "synced",
          followers: 251000,
          posts_count: 1200,
          total_views: 98000000,
          posts: [],
        },
      ],
    },
  ],
  account_count: 1,
  post_count: 1200,
  total_views: 98000000,
  staff_managed: [],
};

// daily-digest:整体 ready + kol 块诚实 empty(带 reason),official 块 empty
const DIGEST_OK = {
  status: "ready",
  window: { days: 1, since: "2026-07-10", until: "2026-07-11" },
  scope: { staff_id: 3, mode: "all", kol_count: 0 },
  kol: { status: "empty", reason: "窗口内无收藏 KOL 变化。" },
  official: { status: "empty", reason: "无官号快照。" },
};

const RISK_OK = {
  items: [],
  summary: {
    kol_count: 0,
    analyzed_kol_count: 0,
    unanalyzed_kol_count: 0,
    coverage_ratio: 0,
    high_risk_count: 0,
    weights: {},
    deep_table_available: true,
    notes: {},
  },
  staff_id: 3,
};

const ROLLUP_OK = { items: [], window_days: 90, scope: {}, data_notes: {} };

// CockpitApp dashboardRuntime.metrics 注入形状(K3 兜底;board-ext kol_views 优先)
const METRICS_PROP = [
  {
    id: "exposure",
    label: "Total Exposure",
    data: { all: { value: 2_052_179_053, trend: "实时 · evidence 覆盖 77%", source: "real" } },
  },
];

const DATA: any = { projects: [], staffMembers: [], kolOptions: [] };

function routeApi(overrides: { aggregate?: unknown; boardExt?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/my-kol/aggregate")) {
      const value = overrides.aggregate ?? AGG_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/marketing/channels/official-matrix")) return MATRIX_OK;
    if (p.startsWith("/api/marketing/channels/") && p.includes("/posts")) {
      return { account: {}, posts: [], pagination: { page: 1, pages: 1, total: 0 } };
    }
    if (p.startsWith("/api/admin/vkpi/my-kol/daily-digest")) return DIGEST_OK;
    if (p.startsWith("/api/admin/vkpi/my-kol/risk-index")) return RISK_OK;
    if (p.startsWith("/api/admin/vkpi/my-kol/contribution-rollup")) return ROLLUP_OK;
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext")) {
      const value = overrides.boardExt ?? EXT_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    const videosMatch = p.match(/\/api\/admin\/vkpi\/kol-pool\/(\d+)\/videos/);
    if (videosMatch) {
      const items = VIDEOS[videosMatch[1]] || [];
      return { items, total: items.length, kol_pool_id: Number(videosMatch[1]) };
    }
    if (/\/api\/admin\/vkpi\/my-kol\/\d+\/viewer-context/.test(p)) return { kol_pool_id: 0, share_origin: null, claim: null };
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(
    <MyKolBoardPage
      apiToken="t"
      viewMode="manager"
      data={DATA}
      metrics={METRICS_PROP as any}
      {...(props as any)}
    />,
  );

// palette 六备选一次全上布局(storageKey 预置;kpiM 等默认模块刻意不含)
const PALETTE_LAYOUT = [
  { moduleKey: "viewsTop", span: 8 },
  { moduleKey: "contacts", span: 4 },
  { moduleKey: "followerTrend", span: 8 },
  { moduleKey: "claims", span: 4 },
  { moduleKey: "shares", span: 4 },
  { moduleKey: "cover", span: 4 },
];

beforeEach(() => {
  window.localStorage.clear();
  routeApi();
});

describe("MyKolBoardPage smoke (M1 页壳 + M4 KPI 带 series + 注册表 + 布局键 v1)", () => {
  it("KPI 带四卡:现值全真;K1/K2/K4 真 sparkline + delta 药丸,K1 缺快照日断点(两段线),K3 点时口径诚实虚线", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 现值:K1=719 / K2=87 / K3=20.5亿(board-ext kol_views 自取)/ K4=25.1万
    expect(await screen.findByText("20.5亿")).toBeTruthy();
    expect(await screen.findByText("25.1万")).toBeTruthy();
    expect(screen.getAllByText("719").length).toBeGreaterThan(0);
    expect(screen.getAllByText("87").length).toBeGreaterThan(0);
    expect(screen.getAllByText("在库 KOL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("合作推进中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("内容播放实测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官号粉丝").length).toBeGreaterThan(0);

    // M4:K1/K2/K4 真 sparkline(3 枚)+ K3 诚实 spempty(1 枚);delta 药丸恰 3 枚
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(3);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(1);
    const deltas = [...document.querySelectorAll(".ds-kpi__delta")].map((el) => el.textContent);
    expect(deltas.length).toBe(3);
    expect(deltas).toContain("▲5.1%");
    expect(deltas).toContain("▲40.0%");
    expect(deltas).toContain("▲0.8%");
    // K1 断点:pool_followers 第 3 日 null → sparkline 拆成两段 line path(如实断线)
    expect(kpis[0].querySelectorAll("path.ds-sparkline__line").length).toBe(2);
    // K3 卡(第 3 枚)零 sparkline 零药丸
    expect(kpis[2].querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(kpis[2].querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead:视角徽 + KOL 数徽 + 编辑布局钮
    expect(screen.getByText("管理层视角")).toBeTruthy();
    expect(await screen.findByText("719 KOL")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // 口径进 SrcChip:点时实测/真表名/K1 趋势线断点口径
    expect(screen.getAllByText(/点时实测 · 非时序/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_kol_pool_favorites/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_kol_fit_snapshot/).length).toBeGreaterThan(0);

    // 三个真端点全被调
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.startsWith("/api/admin/vkpi/my-kol/aggregate"))).toBe(true);
    expect(calledPaths.some((p) => p.startsWith("/api/marketing/channels/official-matrix"))).toBe(true);
    expect(calledPaths.some((p) => p.startsWith("/api/admin/vkpi/my-kol/board-ext"))).toBe(true);
  });

  it("默认布局六行(manager):漏斗/Fit 直方/平台分布真身在场;palette 备选不进默认", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    // 内嵌现有组件的模块
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("团队矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 风险指数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("贡献度聚合").length).toBeGreaterThan(0);
    // 【M4】图形真身:漏斗 8 段(已寄样段在场)/ fitdist 未评分桶 / platdist 平台行
    expect(screen.getAllByText("合作漏斗").length).toBeGreaterThan(0);
    expect(await screen.findByText("已寄样")).toBeTruthy();
    expect(screen.getAllByText("Fit 分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/未评分/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("平台分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("youtube").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 库").length).toBeGreaterThan(0);
    // palette 备选不进默认布局
    expect(screen.queryByText("播放 Top 视频")).toBeNull();
    expect(screen.queryByText("联系方式覆盖")).toBeNull();
    expect(screen.queryByText("粉丝趋势")).toBeNull();
    expect(screen.queryByText("我的认领")).toBeNull();
    expect(screen.queryByText("共享池")).toBeNull();
    expect(screen.queryByText("数据覆盖")).toBeNull();
  });

  it("employee 注册表差异(裁决②A):risk/rollup 直接不出现,专属端点零请求,不是 403 卡", async () => {
    renderBoard({ viewMode: "employee", userName: "Momo", data: { ...DATA, staffMembers: [{ id: "5", name: "Alice" }] } });
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    expect(screen.getByText("Momo · own-only")).toBeTruthy();
    expect(screen.queryByText("KOL 风险指数")).toBeNull();
    expect(screen.queryByText("贡献度聚合")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("risk-index"))).toBe(false);
    expect(calledPaths.some((p) => p.includes("contribution-rollup"))).toBe(false);
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
    // 【M5/M6 收尾】library own-only 口径再断言:员工 aggregate 不带 staff_id(服务端裁剪,
    // 零本地猜),负责人筛选 select 即使 staff 目录有人也不渲染(管理层专属)
    expect(calledPaths.filter((p) => p.startsWith("/api/admin/vkpi/my-kol/aggregate")).every((p) => !p.includes("staff_id"))).toBe(true);
    expect(screen.queryByLabelText("负责人筛选")).toBeNull();
    // library 行本体仍是 aggregate 下发的本人集合(后端已裁,前端如实渲染)
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出六个备选(M4 全真身)", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(await screen.findByText("播放 Top 视频")).toBeTruthy();
    expect(screen.getByText("联系方式覆盖")).toBeTruthy();
    expect(screen.getByText("粉丝趋势")).toBeTruthy();
    expect(screen.getByText("我的认领")).toBeTruthy();
    expect(screen.getByText("共享池")).toBeTruthy();
    expect(screen.getByText("数据覆盖")).toBeTruthy();
  });

  it("布局键 vkpi-my-kol-layout-v1 生效;不传 apiToken → 绝不写账户级 dashboard 布局", async () => {
    window.localStorage.setItem("vkpi-my-kol-layout-v1", JSON.stringify([{ moduleKey: "kpiM", span: 12 }]));
    renderBoard();
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    expect(screen.queryByText("每日学习摘要")).toBeNull();
    expect(screen.queryByText("合作漏斗")).toBeNull();
    expect(screen.queryByText("官方账号矩阵")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("preference"))).toBe(false);
  });

  it("aggregate 失败 → 诚实错误卡(绝不假数据)", async () => {
    routeApi({ aggregate: new Error("HTTP 500") });
    renderBoard();
    expect((await screen.findAllByText("my-kol/aggregate 读取失败")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/HTTP 500/)).length).toBeGreaterThan(0);
    expect(screen.queryByText("719")).toBeNull();
  });

  it("board-ext 失败 → 图形卡 ErrorCard;KPI 带时序/药丸缺席不编数,K3 主控注入兜底", async () => {
    routeApi({ boardExt: new Error("HTTP 503") });
    renderBoard();
    // 默认布局的三张图形卡(漏斗/fitdist/platdist)全部诚实错误卡
    expect((await screen.findAllByText("board-ext 读取失败")).length).toBe(3);
    expect(screen.getAllByText(/HTTP 503/).length).toBeGreaterThan(0);
    // 四卡全 spempty,零 sparkline 零药丸(时序缺席不编)
    expect(await screen.findByText("719 KOL")).toBeTruthy();
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    // K3 由主控兜底出真值(非 pending)→ 四卡全部 spempty 虚线
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    // K3 兜底:主控 evidence_metrics 注入仍给出点时读数(不因 board-ext 挂而丢真数)
    expect(screen.getByText("20.5亿")).toBeTruthy();
  });

  it("board-ext 失败且主控指标未注入 → K3 诚实 pending 带说明", async () => {
    routeApi({ boardExt: new Error("HTTP 503") });
    renderBoard({ metrics: undefined });
    expect((await screen.findAllByText("719")).length).toBeGreaterThan(0);
    expect(screen.getByText(/board-ext 读取失败且主控指标未注入/)).toBeTruthy();
    expect(screen.queryByText("20.5亿")).toBeNull();
  });
});

describe("MyKolBoardPage M4(图形联动:漏斗点段 / 平台点行 / fitdist 未评分桶)", () => {
  it("合作漏斗点段过滤:点「已寄样」→ library 只剩挂该段 raw 阶段的行 + 阶段 chip;再点 chip 清除", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    // 漏斗段行(count · 占比)在场
    fireEvent.click(await screen.findByText("已寄样"));
    // Alpha(项目 stage=shipped ∈ raw_stages)留,Beta/Gamma 无该阶段项目 → 隐藏
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.queryByText("Beta Vlog")).toBeNull();
    expect(screen.queryByText("Gamma")).toBeNull();
    // 阶段 chip(page 层共享筛选状态)+ 口径注
    expect(screen.getByText(/漏斗阶段:已寄样/)).toBeTruthy();
    expect(screen.getAllByText(/段计数=指派行/).length).toBeGreaterThan(0);
    // 点 chip 移除过滤 → 全量恢复
    fireEvent.click(screen.getByText(/漏斗阶段:已寄样/));
    expect(await screen.findByText("Beta Vlog")).toBeTruthy();
    expect(screen.queryByText(/漏斗阶段:已寄样/)).toBeNull();
  });

  it("平台分布点行过滤:点 youtube 行 → library 只剩 youtube 行;再点同行取消", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(await screen.findByText("youtube"));
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.queryByText("Beta Vlog")).toBeNull();
    expect(screen.queryByText("Gamma")).toBeNull();
    fireEvent.click(screen.getByText("youtube"));
    expect(await screen.findByText("Beta Vlog")).toBeTruthy();
  });

  it("fitdist 未评分桶:410 · 33.9% 灰桶如实在场(绝不并进 0 分桶)", async () => {
    renderBoard();
    expect(await screen.findByText("未评分")).toBeTruthy();
    expect(screen.getByText("410 · 33.9%")).toBeTruthy();
    // 有分桶(80-89)也在场
    expect(screen.getByText("80-89 分")).toBeTruthy();
  });

  it("palette 六模块真身(预置布局):播放榜/覆盖/双线/认领/共享/静态盘点全真渲染", async () => {
    window.localStorage.setItem("vkpi-my-kol-layout-v1", JSON.stringify(PALETTE_LAYOUT));
    renderBoard();
    // viewsTop:实测播放条形榜(NULL 剔除口径注在 SrcChip/ProvNote)
    expect(await screen.findByText("播放 Top 视频")).toBeTruthy();
    expect(await screen.findByText("Zeta Films")).toBeTruthy();
    expect(screen.getByText("1.2亿 · 42条")).toBeTruthy();
    // contacts:覆盖率 34.4% 大数 + 类型条形
    expect(screen.getAllByText("联系方式覆盖").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/34\.4/).length).toBeGreaterThan(0);
    expect(screen.getByText("email")).toBeTruthy();
    // followerTrend:双线 legend(收藏集 vs 官号)
    expect(screen.getAllByText("粉丝趋势").length).toBeGreaterThan(0);
    expect(screen.getByText("收藏集 KOL 粉丝")).toBeTruthy();
    // claims:aggregate.claims 真行(Gamma active · 到期时间)
    expect(screen.getAllByText("我的认领").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Gamma").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/active/).length).toBeGreaterThan(0);
    // shares:共享行(Beta 来自 Alice)
    expect(screen.getAllByText("共享池").length).toBeGreaterThan(0);
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    expect(screen.getByText("来自 Alice")).toBeTruthy();
    // cover:board-ext 无此组 → 静态盘点标日期 + 触点 2 条真读数 + 盲区如实标
    expect(screen.getAllByText(/静态盘点 2026-07-11/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("触点").length).toBeGreaterThan(0);
    expect(screen.getByText("2 条")).toBeTruthy();
    expect(screen.getAllByText("0 条 · 盲区").length).toBe(5);
  });
});

describe("MyKolBoardPage M3/M4(KOL 库:V 名单精确过滤 + 弹窗族)", () => {
  it("库卡面:V chip 计数=board-ext 真值(387)+ 状态徽 + 查看全量入口", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    expect(screen.getByText("Gamma")).toBeTruthy();
    expect(screen.getByText("有 V 视频 (387)")).toBeTruthy();
    expect(screen.getAllByText("共享").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已认领").length).toBeGreaterThan(0);
    expect(screen.getAllByText("进行中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("收藏").length).toBeGreaterThan(0);
    expect(screen.getByText(/查看全量 3 条/)).toBeTruthy();
  });

  it("【M4】V 精确过滤:v_kol_ids Set 查找 —— Beta(零项目但在名单)留下,Gamma 隐藏;精确口径注取代近似判据文案", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(screen.getByText("有 V 视频 (387)"));
    // 名单=[101,102]:Alpha + Beta 留(近似口径会漏 Beta —— 精确过滤的判别点),Gamma 隐藏
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    expect(screen.queryByText("Gamma")).toBeNull();
    // 精确口径注在场;M3 旧近似判据文案退役
    expect(screen.getByText(/按全库 V 信号名单精确过滤/)).toBeTruthy();
    expect(screen.queryByText(/筛选判据=已进项目的合作 KOL/)).toBeNull();
    // 名单未截断 → 零降级提示(近似降级注 / 截断注都不该出现)
    expect(screen.queryByText(/暂按「已挂项目」近似过滤/)).toBeNull();
    expect(screen.queryByText(/超过名单封顶/)).toBeNull();
    fireEvent.click(screen.getByText("全部"));
    expect(await screen.findByText("Gamma")).toBeTruthy();
  });

  it("【M4】truncated=true → 名单截断如实降级提示(后端原话透出)", async () => {
    routeApi({
      boardExt: {
        ...EXT_OK,
        v_content: {
          ...EXT_OK.v_content,
          v_kol_ids_truncated: true,
          v_kol_ids_note: "V 信号 KOL 超过名单封顶 2000,v_kol_ids 只含 id 升序前 2000 个;全量以 v_kol_count 为准。",
        },
      },
    });
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(screen.getByText("有 V 视频 (387)"));
    expect(screen.getByText(/超过名单封顶 2000/)).toBeTruthy();
  });

  it("【M4】名单缺席(旧后端/组失败)→ 降级为已挂项目近似 + 如实标注(绝不悄悄装精确)", async () => {
    const { v_kol_ids: _ids, v_kol_ids_truncated: _tr, ...vContentNoIds } = EXT_OK.v_content as Record<string, unknown>;
    routeApi({ boardExt: { ...EXT_OK, v_content: vContentNoIds } });
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(screen.getByText("有 V 视频 (387)"));
    // 近似口径:只有挂项目的 Alpha 留(Beta 在真名单内但名单缺席 → 如实降级漏掉)
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.queryByText("Beta Vlog")).toBeNull();
    expect(screen.getByText(/暂按「已挂项目」近似过滤,如实降级/)).toBeTruthy();
  });

  it("全量弹窗:搜索框 + 同款 chips + 批量工具条(全选可见/清空/批量入项目/导出 CSV/批量受众画像)", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(screen.getByText(/查看全量 3 条/));
    expect(await screen.findByText("KOL 库 · 全量")).toBeTruthy();
    expect(screen.getByLabelText("搜索 KOL")).toBeTruthy();
    const toolbar = screen.getByLabelText("批量工具条");
    expect(toolbar.textContent).toContain("批量入项目");
    expect(toolbar.textContent).toContain("导出 CSV");
    expect(toolbar.textContent).toContain("批量受众画像");
    expect(toolbar.textContent).toContain("清空");
    fireEvent.click(screen.getByText("全选可见"));
    expect(screen.getByText("已选 3")).toBeTruthy();
    fireEvent.click(screen.getByText("清空"));
    expect(screen.getByText("已选 0")).toBeTruthy();
    // 搜索过滤(卡面与弹窗共享同一筛选状态 —— page 层同一份)
    fireEvent.change(screen.getByLabelText("搜索 KOL"), { target: { value: "beta" } });
    expect(screen.getAllByText("Beta Vlog").length).toBeGreaterThan(0);
    expect(screen.queryByText("Alpha Cam")).toBeNull();
  });

  it("详情弹窗:视频三档徽 + 诚实小结(NULL 剔除注明)+ 仅V开关 + 记录预览", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Alpha Cam"));
    expect(await screen.findByText("合作产出")).toBeTruthy();
    expect(screen.getByText("标题提及V")).toBeTruthy();
    expect(screen.getByText("未判定")).toBeTruthy();
    expect(screen.getByText(/3 条视频/)).toBeTruthy();
    expect(screen.getByText(new RegExp(`实测播放合计 ${(1500).toLocaleString()}`))).toBeTruthy();
    expect(screen.getByText(/1 条未实测已剔除/)).toBeTruthy();
    expect(screen.getByText(/V 相关 2 条 · 已深析 1 条/)).toBeTruthy();
    expect(screen.getAllByText("已深析").length).toBeGreaterThan(0);
    expect(screen.getByText("深析")).toBeTruthy();
    fireEvent.click(screen.getByText("仅 V 相关"));
    expect(screen.queryByText("未判定")).toBeNull();
    expect(screen.getByText("合作产出")).toBeTruthy();
    fireEvent.click(screen.getByText("仅 V 相关"));
    expect(await screen.findByText("未判定")).toBeTruthy();
    fireEvent.click(screen.getByText("#9002"));
    expect(await screen.findByText("NULL(未实测 ≠ 0 播放)")).toBeTruthy();
  });

  it("连续翻:‹#n/N› + 下一条 + ↑↓ 方向键;零视频 KOL = 诚实空态(可发起深爬)", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Alpha Cam"));
    expect(await screen.findByText("#1 / 3")).toBeTruthy();
    fireEvent.click(screen.getByText("下一条 ›"));
    expect(await screen.findByText("#2 / 3")).toBeTruthy();
    expect(await screen.findByText(/暂无采集视频——可发起深爬/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#3 / 3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByText("#2 / 3")).toBeTruthy();
  });
});

describe("MyKolBoardPage M5/M6(溯源身份跳 + 内嵌模块卡头收编)", () => {
  it("【M5】详情档案卡「打开 KOL 档案 →」:sessionStorage 传 kol_pool_id + 派发 vkpi:open-kol-profile(CockpitApp 既有管道)", async () => {
    const onProfile = vi.fn();
    window.addEventListener("vkpi:open-kol-profile", onProfile);
    window.sessionStorage.clear();
    renderBoard();
    fireEvent.click(await screen.findByText("Alpha Cam"));
    fireEvent.click(await screen.findByText("打开 KOL 档案 →"));
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(onProfile).toHaveBeenCalledTimes(1);
    window.removeEventListener("vkpi:open-kol-profile", onProfile);
  });

  it("【M6】卡头真短计数:team=负责人数 / official=账号数 / rollup=窗口徽;五收编容器在场,旧标题仍在 DOM(CSS 隐藏非删改)", async () => {
    renderBoard();
    // team cnt = 已知展示元数据(Jianbo 壳)+ 矩阵真 staff(Alice)= 2,与旧头「负责人」chip 同源同数
    expect((await screen.findAllByText("2 负责人")).length).toBeGreaterThan(0);
    // official cnt = official-matrix account_count 真值
    expect(screen.getAllByText("1 账号").length).toBeGreaterThan(0);
    // rollup cnt = 包装层持窗默认 90 天(徽 + 窗口 chips 同数)
    expect(screen.getAllByText("90 天").length).toBeGreaterThan(0);
    // 五内嵌收编容器全在场(digest/team/official/risk/rollup)
    const embeds = [...document.querySelectorAll("[data-embed]")].map((el) => el.getAttribute("data-embed")).sort();
    expect(embeds).toEqual(["digest", "official", "risk", "rollup", "team"]);
    // 旧组件自带大标题仍在 DOM(包装容器 CSS 隐藏 —— 非侵入收编,旧组件文件零改动)
    expect(screen.getByText("每日学习")).toBeTruthy();
    expect(screen.getByText("KOL 贡献度聚合")).toBeTruthy();
    // digest 功能控件保留:窗口切换钮(内部 state,卡头不摆假窗口徽)
    expect(screen.getByText("昨天")).toBeTruthy();
    expect(screen.getByText("近30天")).toBeTruthy();
  });

  it("【M6】rollup 包装层持窗:点 30 天 → key 重挂载按 window_days=30 真重取(徽数=真实取数窗口)", async () => {
    renderBoard();
    expect((await screen.findAllByText("贡献度聚合")).length).toBeGreaterThan(0);
    const calls = () => apiFetchMock.mock.calls.map((call) => String(call[0]));
    await waitFor(() => expect(calls().some((p) => p.includes("contribution-rollup?window_days=90"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    await waitFor(() => expect(calls().some((p) => p.includes("contribution-rollup?window_days=30"))).toBe(true));
    expect(screen.getAllByText("30 天").length).toBeGreaterThan(0);
  });
});
