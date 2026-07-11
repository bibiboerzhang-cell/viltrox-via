import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";

// MY KOL 改版 M1 骨架 + M3 库弹窗化冒烟(金样板 MarketVoicePage.smoke 同构):
// - 页壳:pagehead(MY KOL + 视角/KOL 数药丸徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡(K1 在库 KOL / K2 合作推进中 / K3 内容播放实测 / K4 官号粉丝):
//   现值全真(aggregate.kpi_summary / 主控 evidence_metrics 注入 / official-matrix Σ followers),
//   无历史快照 → sparkline 一律 demo .spempty 纯虚线、环比药丸一律不渲染(诚实);
// - 注册表 manager vs employee 差异(裁决②A):risk/rollup 员工注册表直接不出现,
//   连专属端点都不该被请求(不是 403 卡);
// - 布局键 vkpi-my-kol-layout-v1 生效 + 不传 apiToken → 绝不写账户级 dashboard_layout_v1;
// - 诚实空态:aggregate 失败 = 错误卡;K3 主控指标未注入 = pending 不编数;
// - 【M3】KOL 库真身:V 筛选 chip(计数=board-ext v_content.v_kol_count 真值 + 诚实判据注)/
//   全量弹窗(搜索 + 批量工具条)/ 详情弹窗(视频三档徽 · 仅V开关 · NULL 剔除注明 ·
//   ‹#n/N›+↑↓ 连续翻 · 空视频诚实空态)。
// mock seam:services/http.apiFetch(全页唯一网络出口——kol-api / channel-api / myKolBoard-api /
// kolPool-api / DailyDigestCard / RiskIndexPanel 全都收敛到它),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MyKolBoardPage } from "./MyKolBoardPage";

// 【M3】库行(pool_favorites 形状对齐 my_kol_aggregate._pool_favorites):
// Alpha=收藏+挂项目(合作/进行中)/ Beta=共享行 / Gamma=收藏+认领桥(claims 平台+名称)。
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

// 【M3】board-ext 七组聚合(本刀只消费 v_content;v_kol_count=387 全库口径真值)
const EXT_OK = {
  status: "ready",
  days: 30,
  v_content: { status: "ready", total_evidence: 2134, v_kol_count: 387, tiers: { cooperation: 806, title_mention: 376, title_mention_only: 353, overlap_both: 23, undetermined: 975 } },
};

// 【M3】单 KOL evidence 视频(/kol-pool/{id}/videos):Alpha 三档齐(合作/标题提及/未判定,
// 其中标题提及条 view_count=NULL 验证「未实测剔除注明」);Beta/Gamma 零视频 → 诚实空态。
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

// CockpitApp dashboardRuntime.metrics 注入形状(normalizeDashboardMetrics 产物切片):
// K3 = exposure.all.value(SUM view_count 点时实测,≈20.5 亿)
const METRICS_PROP = [
  {
    id: "exposure",
    label: "Total Exposure",
    data: { all: { value: 2_052_179_053, trend: "实时 · evidence 覆盖 77%", source: "real" } },
  },
];

const DATA: any = { projects: [], staffMembers: [], kolOptions: [] };

function routeApi(overrides: { aggregate?: unknown } = {}) {
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
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext")) return EXT_OK;
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

beforeEach(() => {
  window.localStorage.clear();
  routeApi();
});

describe("MyKolBoardPage smoke (M1 骨架:页壳 + KPI 带 + 注册表 + 布局键 v1)", () => {
  it("页壳 + KPI 带四卡:现值全真,series 诚实 spempty、delta 不渲染,口径进 SrcChip", async () => {
    expect(() => renderBoard()).not.toThrow();

    // 现值先落位(等 aggregate/matrix 解析):K1=719 / K2=87 / K3=20.5亿 / K4=25.1万
    expect(await screen.findByText("20.5亿")).toBeTruthy();
    expect(await screen.findByText("25.1万")).toBeTruthy();
    expect(screen.getAllByText("719").length).toBeGreaterThan(0);
    expect(screen.getAllByText("87").length).toBeGreaterThan(0);
    // 四卡设计单语义(K1-K4;标签同时是 SrcChip 口径行键 → 取 ≥1)
    expect(screen.getAllByText("在库 KOL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("合作推进中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("内容播放实测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官号粉丝").length).toBeGreaterThan(0);
    // 无历史快照 → 四卡全部 demo .spempty 纯虚线;环比药丸/真 sparkline 一枚都不许有
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);

    // pagehead:视角徽 + KOL 数徽(aggregate 真数)+ 编辑布局钮
    expect(screen.getByText("管理层视角")).toBeTruthy();
    expect(await screen.findByText("719 KOL")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();

    // K3 播放口径「点时实测 · 非时序」写进 SrcChip 溯源行(设计单硬要求)
    expect(screen.getAllByText(/点时实测 · 非时序/).length).toBeGreaterThan(0);
    // 真表名溯源行在场
    expect(screen.getAllByText(/vkpi_kol_pool_favorites/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi_channel_metrics/).length).toBeGreaterThan(0);

    // 现成端点确实被调(本刀零新端点)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.startsWith("/api/admin/vkpi/my-kol/aggregate"))).toBe(true);
    expect(calledPaths.some((p) => p.startsWith("/api/marketing/channels/official-matrix"))).toBe(true);
  });

  it("默认布局六行(manager):内嵌模块 + 待接线模块在场;palette 备选不进默认", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    // 内嵌现有组件的模块(digest/team/official/risk/rollup)
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("团队矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 风险指数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("贡献度聚合").length).toBeGreaterThan(0);
    // 待接线模块 = PendingCard 诚实说明(不摆假数据)
    expect(screen.getAllByText("四环漏斗").length).toBeGreaterThan(0);
    expect(screen.getAllByText("KOL 库").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fit 分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("平台分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/本刀诚实待接不摆假漏斗/).length).toBeGreaterThan(0);
    // palette 备选不进默认布局
    expect(screen.queryByText("播放 Top 视频")).toBeNull();
    expect(screen.queryByText("联系方式覆盖")).toBeNull();
    expect(screen.queryByText("粉丝趋势")).toBeNull();
    expect(screen.queryByText("认领状态")).toBeNull();
    expect(screen.queryByText("共享池")).toBeNull();
    expect(screen.queryByText("数据覆盖")).toBeNull();
  });

  it("employee 注册表差异(裁决②A):risk/rollup 直接不出现,专属端点零请求,不是 403 卡", async () => {
    renderBoard({ viewMode: "employee", userName: "Momo" });
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    // own-only 视角徽
    expect(screen.getByText("Momo · own-only")).toBeTruthy();
    // 管理层专属模块在员工注册表里根本不存在(默认布局自动少两块)
    expect(screen.queryByText("KOL 风险指数")).toBeNull();
    expect(screen.queryByText("贡献度聚合")).toBeNull();
    // 专属端点连请求都不该发(不是渲染出来再 403)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("risk-index"))).toBe(false);
    expect(calledPaths.some((p) => p.includes("contribution-rollup"))).toBe(false);
    // 共同模块照常在场
    expect(screen.getAllByText("每日学习摘要").length).toBeGreaterThan(0);
    expect(screen.getAllByText("官方账号矩阵").length).toBeGreaterThan(0);
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出六个备选(全部诚实待接)", async () => {
    renderBoard();
    expect(await screen.findByText("KOL 指标带")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(await screen.findByText("播放 Top 视频")).toBeTruthy();
    expect(screen.getByText("联系方式覆盖")).toBeTruthy();
    expect(screen.getByText("粉丝趋势")).toBeTruthy();
    expect(screen.getByText("认领状态")).toBeTruthy();
    expect(screen.getByText("共享池")).toBeTruthy();
    expect(screen.getByText("数据覆盖")).toBeTruthy();
  });

  it("布局键 vkpi-my-kol-layout-v1 生效;不传 apiToken → 绝不写账户级 dashboard 布局", async () => {
    // 预置本机布局只留 kpiM → 其余默认模块不渲染(storageKey 被真实读取)
    window.localStorage.setItem("vkpi-my-kol-layout-v1", JSON.stringify([{ moduleKey: "kpiM", span: 12 }]));
    renderBoard();
    expect((await screen.findAllByText("在库 KOL")).length).toBeGreaterThan(0);
    expect(screen.queryByText("每日学习摘要")).toBeNull();
    expect(screen.queryByText("四环漏斗")).toBeNull();
    expect(screen.queryByText("官方账号矩阵")).toBeNull();
    // 金样板同注释:不给 EditableDashboardBoard 传 apiToken → 账户级偏好接口零调用
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("preference"))).toBe(false);
  });

  it("aggregate 失败 → 诚实错误卡(绝不假数据)", async () => {
    routeApi({ aggregate: new Error("HTTP 500") });
    renderBoard();
    expect((await screen.findAllByText("my-kol/aggregate 读取失败")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/HTTP 500/)).length).toBeGreaterThan(0);
    // KPI 现值不许出现(不编数)
    expect(screen.queryByText("719")).toBeNull();
  });

  it("K3 主控指标未注入 → 诚实 pending(—,带说明),其余三卡照常", async () => {
    renderBoard({ metrics: undefined });
    expect((await screen.findAllByText("719")).length).toBeGreaterThan(0);
    expect(screen.getByText("主控 evidence_metrics 未注入 · M2 board-ext 自取")).toBeTruthy();
    expect(screen.queryByText("20.5亿")).toBeNull();
  });
});

describe("MyKolBoardPage M3(KOL 库真身:V 筛选 chip + 全量弹窗 + 详情连续翻)", () => {
  it("库卡面:V chip 计数=board-ext 真值(387)+ KOL 行状态徽(收藏/共享/已认领/进行中)+ 查看全量入口", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    expect(screen.getByText("Gamma")).toBeTruthy();
    // V chip 计数来自 board-ext v_content.v_kol_count(387 全库口径,真值非编数)
    expect(screen.getByText("有 V 视频 (387)")).toBeTruthy();
    // 状态徽:共享(Beta)/ 已认领(Gamma 认领桥)/ 进行中(Alpha 挂项目)/ 收藏(非共享行)
    expect(screen.getAllByText("共享").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已认领").length).toBeGreaterThan(0);
    expect(screen.getAllByText("进行中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("收藏").length).toBeGreaterThan(0);
    expect(screen.getByText(/查看全量 3 条/)).toBeTruthy();
    // board-ext 端点确实被调(M3 新数据源)
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.startsWith("/api/admin/vkpi/my-kol/board-ext"))).toBe(true);
  });

  it("V 筛选 chip:点「有 V 视频」→ 只留可判据行(已挂项目)+ 诚实判据注;「全部」切回", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    fireEvent.click(screen.getByText("有 V 视频 (387)"));
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.queryByText("Beta Vlog")).toBeNull();
    expect(screen.queryByText("Gamma")).toBeNull();
    // 诚实口径:行级判据 ≠ 全库计数,注在筛选行(不装同口径)
    expect(screen.getByText(/筛选判据=已进项目的合作 KOL/)).toBeTruthy();
    fireEvent.click(screen.getByText("全部"));
    expect(await screen.findByText("Beta Vlog")).toBeTruthy();
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
    // 全选可见 → 已选 3;清空 → 已选 0(批量动作按钮随选空自动 disabled)
    fireEvent.click(screen.getByText("全选可见"));
    expect(screen.getByText("已选 3")).toBeTruthy();
    fireEvent.click(screen.getByText("清空"));
    expect(screen.getByText("已选 0")).toBeTruthy();
    // 搜索过滤(卡面与弹窗共享同一筛选状态)
    fireEvent.change(screen.getByLabelText("搜索 KOL"), { target: { value: "beta" } });
    expect(screen.getAllByText("Beta Vlog").length).toBeGreaterThan(0);
    expect(screen.queryByText("Alpha Cam")).toBeNull();
  });

  it("详情弹窗:视频三档徽(合作产出/标题提及V/未判定)+ 诚实小结(NULL 剔除注明)+ 仅V开关 + 记录预览", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Alpha Cam"));
    // 视频区取数(/kol-pool/101/videos)后三档徽齐(派生规则前端同构)
    expect(await screen.findByText("合作产出")).toBeTruthy();
    expect(screen.getByText("标题提及V")).toBeTruthy();
    expect(screen.getByText("未判定")).toBeTruthy();
    // 小结:播放合计只算实测(1000+500),NULL 条剔除并注明;V 相关/已深析计数真值
    expect(screen.getByText(/3 条视频/)).toBeTruthy();
    expect(screen.getByText(new RegExp(`实测播放合计 ${(1500).toLocaleString()}`))).toBeTruthy();
    expect(screen.getByText(/1 条未实测已剔除/)).toBeTruthy();
    expect(screen.getByText(/V 相关 2 条 · 已深析 1 条/)).toBeTruthy();
    // 已深析标 + 未判定行的一键深析按钮在场
    expect(screen.getAllByText("已深析").length).toBeGreaterThan(0);
    expect(screen.getByText("深析")).toBeTruthy();
    // 仅 V 相关开关:未判定隐藏 → 再点恢复
    fireEvent.click(screen.getByText("仅 V 相关"));
    expect(screen.queryByText("未判定")).toBeNull();
    expect(screen.getByText("合作产出")).toBeTruthy();
    fireEvent.click(screen.getByText("仅 V 相关"));
    expect(await screen.findByText("未判定")).toBeTruthy();
    // 溯源:evidence #id → 记录预览(NULL 口径如实;派生判据只进溯源不进卡面)
    fireEvent.click(screen.getByText("#9002"));
    expect(await screen.findByText("NULL(未实测 ≠ 0 播放)")).toBeTruthy();
  });

  it("连续翻:‹#n/N› + 下一条 + ↑↓ 方向键;零视频 KOL = 诚实空态(可发起深爬)", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Alpha Cam"));
    expect(await screen.findByText("#1 / 3")).toBeTruthy();
    fireEvent.click(screen.getByText("下一条 ›"));
    expect(await screen.findByText("#2 / 3")).toBeTruthy();
    // Beta(102)零视频 → 诚实空态,深爬入口在场(不假装有数据)
    expect(await screen.findByText(/暂无采集视频——可发起深爬/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#3 / 3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByText("#2 / 3")).toBeTruthy();
  });
});
