import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

// KOL 池改版冒烟(金样板 MarketVoicePage/MyKolBoardPage/KolProfileBoardPage.smoke 同构):
// - 页壳:pagehead(KOL 池 + KOL 数/数据截至真值徽 + 编辑布局钮)+ 可编辑看板;
// - KPI 带四卡真值:在池总数 / 本周新发现(created_at 7 天窗)/ 已深析
//   (llm_deep_analysis_count>0 的 KOL 数)/ 低触达暂不推荐(kol-pool/summary
//   low_reach_hidden_count);四指标全点时快照 → 4 卡 spempty 诚实虚线、零 delta 药丸;
//   summary 键缺席 → 第 4 卡诚实 pending(绝不编 0);
// - 图形三件真值(2026-07-12 图形密度波):Fit 分布(poolItems 前端十分位分桶 +
//   未评分诚实桶)/ 平台分布(前端聚合)/ 发现转化近 30 天四段
//   (summary.discovery_funnel_30d;键缺席 → 诚实空态,绝不编 0);
// - 默认布局九模块在场(kpiK/smart/fitDist/platDist/funnel/recs/needs/coverage/lanes),
//   kinds(经典指标条)/table=palette 备选不进默认;
// - 去重复接棒:kinds 撤出默认后,总数大窗入口=卡片流工具行「全部 N」钮
//   (aria-label 打开全量池表大窗);分类点击筛选在 FilterBar kindFilter 保留;
// - 行内快捷动作:卡片流每卡收藏(favorite 既有端点)/ 入项目(projects 列表 +
//   addKolsToProject 既有端点,快捷小窗)一键可达,不用先开抽屉;
// - 零丢失:找达人(SmartKolInputPanel 内嵌,触达二段闸行为零改动)/ 待深析
//   (needs-analysis 清单 + 批量入队)/ 任务进度(TaskProgressBoard 内嵌)/
//   估算受众覆盖(MarketCoverageCard 内嵌)/ 推荐卡片流 + 筛选次级展开 /
//   详情抽屉 · 联系弹窗 · 全量大窗页级 overlay;
// - 跨页事件:vkpi:open-kol-pool-search 消费 pending 关键词 → 填筛选并展开;
//   vkpi:open-kol-pool-item 消费 pending id → 开抽屉(拉 detail-bundle);
// - 溯源:真实表名只进 SrcChip rows(vkpi_kol_pool / vkpi_kol_llm_deep_analysis_results
//   / kol-pool/summary),卡面零术语;
// - 布局键 vkpi-kol-pool-layout-v2 本机记忆(v1→v2:kinds 撤默认 + 图形三件进默认,
//   bump 盖本机残留);不传 apiToken → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { KolPoolBoardPage } from "./KolPoolBoardPage";

const now = Date.now();
const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
const DAY = 24 * 3600 * 1000;

// 池行(kolPoolRows 形状:toCockpitKolPoolRows 产物的消费键子集):
// Alpha=本周新发现 + 已深析;Beta=老行未深析;Gamma=已深析老行。
const ITEMS = [
  {
    id: 101, handle: "@alpha", display_name: "Alpha Cam", platform: "youtube", country: "US",
    followers: 120000, avg_views: 45000, v6_fit: 82, candidate_kind: "existing",
    created_at: iso(2 * DAY), last_seen_at: "2026-07-11T00:00:00Z",
    llm_deep_analysis_count: 3, video_evidence_count: 5,
    geo_distribution: [{ country: "US", share: 1 }],
    estimated_country_reach: { US: 45000 },
    devices: { camera_body: "Sony A7 IV", lenses: [], has_viltrox: true, competitor_brands: [] },
  },
  {
    id: 102, handle: "@beta", display_name: "Beta Vlog", platform: "instagram", country: "DE",
    followers: 5600, avg_views: 900, v6_fit: null, candidate_kind: "new_discovered",
    created_at: iso(40 * DAY), last_seen_at: "2026-07-10T00:00:00Z",
    llm_deep_analysis_count: 0, video_evidence_count: 0,
    geo_distribution: [{ country: "DE", share: 1 }],
    estimated_country_reach: { DE: 900 },
    devices: { camera_body: "", lenses: [], has_viltrox: false, competitor_brands: [] },
  },
  {
    id: 103, handle: "@gamma", display_name: "Gamma", platform: "tiktok", country: "",
    followers: null, avg_views: null, v6_fit: 55, candidate_kind: "existing",
    created_at: iso(60 * DAY), last_seen_at: "2026-07-09T00:00:00Z",
    llm_deep_analysis_count: 1, video_evidence_count: 2,
    geo_distribution: [],
    estimated_country_reach: null,
    devices: { camera_body: "", lenses: [], has_viltrox: false, competitor_brands: ["Sigma"] },
  },
];

const NEEDS_OK = {
  items: [{ kol_pool_id: 103, handle: "@gamma", platform: "tiktok", evidence_id: 77, evidence_count: 2 }],
  count: 1,
};

const SUMMARY_OK = {
  total: 1237,
  low_reach_hidden_count: 81,
  discovery_funnel_30d: { window_days: 30, discovered: 42, enrolled: 18, deep_analyzed: 7, favorited: 5 },
};

const BUNDLE_OK = {
  status: "ready",
  kol_pool_id: 101,
  item: ITEMS[0],
  video_analysis: { items: [], summary: { evidence_count: 5, ready_count: 3 } },
};

function routeApi(overrides: { summary?: unknown; history?: unknown; favorites?: unknown; projects?: unknown; addProject?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    if (p.includes("/kol-pool/favorites")) return overrides.favorites ?? { items: [] };
    if (p.includes("/vkpi/projects?limit=200")) return overrides.projects ?? { projects: [] };
    if (p.includes("/projects/") && p.endsWith("/kols") && init?.method === "POST") {
      return overrides.addProject ?? { project_id: 1, requested: 1, inserted: 1, skipped_existing: 0, missing_kol_pool_ids: [] };
    }
    if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
    if (p.includes("/kol-pool/summary")) return overrides.summary ?? SUMMARY_OK;
    if (p.includes("/detail-bundle")) return BUNDLE_OK;
    if (p.includes("/kol-search-history")) return overrides.history ?? { items: [] };
    if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
    if (p.includes("/signature")) return { status: "empty" };
    if (p.includes("/cooperation")) return { status: "none", events: [] };
    if (p.includes("/audience-geo")) return { status: "empty", countries: [], signals: [] };
    // 抽屉/内嵌组件的其余读端点:空形状兜底(冒烟只验挂载与接线,不验其内容)
    return {};
  });
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(
    <KolPoolBoardPage
      items={ITEMS as any}
      loading={false}
      error=""
      apiToken="t"
      staff={[]}
      {...(props as any)}
    />,
  );

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("KolPoolBoardPage smoke(页壳 + KPI 带真值 + 注册表 + 零丢失接线 + 事件管道)", () => {
  it("KPI 带四卡真值(总数 3 / 本周新 1 / 已深析 2 / 低触达 81);点时快照 → 4 卡诚实虚线零药丸", async () => {
    expect(() => renderBoard()).not.toThrow();

    expect(screen.getAllByText("在池总数").length).toBeGreaterThan(0);
    expect(screen.getAllByText("本周新发现").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已深析").length).toBeGreaterThan(0);
    expect(screen.getAllByText("低触达 · 暂不推荐").length).toBeGreaterThan(0);

    // 真值:3 行池 / created_at 2 天前 ×1 / llm_deep_analysis_count>0 ×2(共 4 条结果)
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    expect(kpis[0].textContent).toContain("3");
    expect(kpis[1].textContent).toContain("1");
    expect(kpis[2].textContent).toContain("2");
    expect(kpis[2].textContent).toContain("4 份结果");
    // 低触达 = kol-pool/summary 真值 81
    await waitFor(() => expect(kpis[3].textContent).toContain("81"));

    // 点时快照:4 卡全 spempty 虚线,零 sparkline 零环比药丸(绝不编时序)
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead 真值徽
    expect(screen.getByText("3 KOL")).toBeTruthy();
    expect(screen.getByText("数据截至 2026-07-11")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
  });

  it("summary 键缺席 → 低触达卡诚实 pending + 漏斗诚实空态(绝不编 0)", async () => {
    routeApi({ summary: { total: 1237 } });
    renderBoard();
    await waitFor(() => {
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/kol-pool/summary"))).toBe(true);
    });
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis[3].textContent).toContain("计数暂不可用");
    expect(kpis[3].textContent).not.toContain("81");
    // discovery_funnel_30d 缺席 → 漏斗模块诚实空态(不画四段条形;
    // 「自动入库」等词在 SrcChip 溯源行仍在,故按段行 title 断言零条形)
    expect(await screen.findByText(/漏斗计数暂不可用/)).toBeTruthy();
    expect(document.querySelector('[title*="搜到自动落池"]')).toBeNull();
  });

  it("默认布局九模块在场;kinds/table=palette 备选不进默认;图形三件真值;零丢失接线", async () => {
    renderBoard();
    for (const title of ["池子指标带", "找达人", "推荐 · 卡片流", "Fit 分布", "平台分布", "发现转化 · 近30天", "任务进度", "待深析", "估算受众覆盖"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    // kinds(经典指标条)与 table 撤出默认布局(palette 备选);KPIBar 旧卡不再默认挂载
    expect(screen.queryByText("经典指标条")).toBeNull();
    expect(screen.queryByText("表格视图")).toBeNull();
    expect(screen.queryByText("Pool 总数")).toBeNull();

    // Fit 直方真值:82→80-89 桶 / 55→50-59 桶 / null→未评分桶(前端只读分桶;
    // 「未评分」在 SrcChip 溯源行也出现 → getAllByText)
    expect(screen.getByText("80-89 分")).toBeTruthy();
    expect(screen.getByText("50-59 分")).toBeTruthy();
    expect(screen.getAllByText("未评分").length).toBeGreaterThan(0);
    // null 必须进入未评分桶；Number(null) 会变成 0，曾导致全池错误堆入 0-9。
    expect(document.querySelector('[title*="匹配分 0-9 区间"]')?.textContent).toContain("0 · 0%");
    expect(document.querySelector('[title*="匹配分为空的诚实桶"]')?.textContent).toContain("1 · 33.3%");

    // 平台分布真值:三行三平台(前端聚合)
    for (const label of ["YouTube", "Instagram", "TikTok"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }

    // 发现转化四段真值(summary.discovery_funnel_30d:42→18→7→5)
    // SrcChip 的静态口径也包含这些标签，不能拿它当 summary 已完成的
    // 异步信号。等待带精确业务 title 的真实漏斗行，避免快 runner 上
    // 在请求 resolve 前读取数值形成时序 flake。
    await waitFor(() => {
      const discovered = screen.getByTitle("近 30 天找达人产出条目(含已在库命中,未去重到人)");
      const enrolled = screen.getByTitle("近 30 天新入池 KOL(搜到自动落池 · 非重复行)");
      const deepAnalyzed = screen.getByTitle("近 30 天出深析结果的 KOL 数(完成态结果覆盖)");
      const favorited = screen.getByTitle("近 30 天被收藏的 KOL 数(收藏=归我,进 MY KOL)");
      expect(discovered.textContent).toContain("发现");
      expect(discovered.textContent).toContain("42");
      expect(enrolled.textContent).toContain("自动入库");
      expect(enrolled.textContent).toContain("18");
      expect(deepAnalyzed.textContent).toContain("已深析");
      expect(deepAnalyzed.textContent).toContain("7");
      expect(favorited.textContent).toContain("已收藏");
      expect(favorited.textContent).toContain("5");
    });

    // 找达人内嵌真身(SmartKolInputPanel 输入框 + 触达闸所在结果区容器,行为零改动)
    expect(screen.getByTestId("smart-kol-input-panel")).toBeTruthy();
    expect(screen.getByTestId("smart-kol-input")).toBeTruthy();

    // 待深析清单真值(needs-analysis 1 行 + 一键全部分析)
    expect(await screen.findByText("全部分析 (1)")).toBeTruthy();
    expect(screen.getAllByText("@gamma").length).toBeGreaterThan(0);

    // 卡片流:结果计数 + 筛选次级展开钮 + 数据密度 chips(粉丝/均播 mono 显眼位)
    expect(screen.getByText("筛选 · 排序")).toBeTruthy();
    expect(screen.getAllByText("粉丝").length).toBeGreaterThan(0);
    expect(screen.getAllByText("均播").length).toBeGreaterThan(0);

    // needs-analysis / favorites / summary 三读端点都被调
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    for (const seg of ["/kol-pool/needs-analysis", "/kol-pool/favorites", "/kol-pool/summary"]) {
      expect(calledPaths.some((p) => p.includes(seg))).toBe(true);
    }
  });

  it("找达人只允许点击查找触发;回车不执行;服务端历史读取 50 条并可回溯会话", async () => {
    routeApi({
      history: {
        items: [{
          id: 778,
          query_text: "35mm 低光人像 YouTube 摄影师",
          query_type: "text_recall",
          status: "ready",
          item_count: 12,
          created_at: "2026-07-12T12:00:00Z",
          updated_at: "2026-07-12T12:05:00Z",
        }],
      },
    });
    renderBoard();

    const input = screen.getByTestId("smart-kol-input");
    fireEvent.change(input, { target: { value: "85mm portrait creator" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search")).toBe(false);

    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search")).toBe(true);
    });
    const searchCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search");
    const searchBody = JSON.parse(String((searchCall?.[1] as RequestInit | undefined)?.body || "{}"));
    expect(searchBody).toMatchObject({
      result_limit: 30,
      limit: 30,
      search_strategy: "balanced",
      filters: { platforms: ["youtube", "instagram", "tiktok"] },
      bucket_policy: { core_vertical: 18, expansion: 9, exploration: 3 },
    });

    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some((call) => String(call[0]).includes("/kol-search-history?limit=50"))).toBe(true);
    });
    fireEvent.click(await screen.findByText("历史记录"));
    expect(await screen.findByText("35mm 低光人像 YouTube 摄影师")).toBeTruthy();
    expect(screen.getByText(/会话 #778 · 12 个结果/)).toBeTruthy();
  });

  it("找达人搜索前筛选会进入真实请求合同，并与旧页搜索模式保持同步", async () => {
    routeApi();
    renderBoard();

    fireEvent.click(screen.getByRole("button", { name: /搜索前筛选/ }));
    fireEvent.click(screen.getByRole("button", { name: /垂直优先/ }));
    fireEvent.change(screen.getByLabelText("国家或地区"), { target: { value: "JP" } });
    fireEvent.change(screen.getByLabelText("内容语言"), { target: { value: "ja" } });
    fireEvent.change(screen.getByLabelText("内容垂类"), { target: { value: "lens_review" } });
    fireEvent.change(screen.getByLabelText("最低粉丝数"), { target: { value: "10000" } });
    fireEvent.change(screen.getByLabelText("最高粉丝数"), { target: { value: "500000" } });
    fireEvent.click(screen.getByRole("button", { name: "发布过镜头/器材" }));
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "26mm EVO 街拍创作者" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));

    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search")).toBe(true);
    });
    const searchCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search");
    const body = JSON.parse(String((searchCall?.[1] as RequestInit | undefined)?.body || "{}"));
    expect(body).toMatchObject({
      result_limit: 30,
      search_strategy: "vertical",
      languages: ["ja"],
      local_qualification_spec: { languages: ["ja"] },
      filters: {
        platforms: ["youtube", "instagram", "tiktok"],
        countries: ["JP"],
        languages: ["ja"],
        followers_min: 10000,
        followers_max: 500000,
        verticals: ["lens_review"],
        gear_content: "yes",
      },
      bucket_policy: { core_vertical: 24, expansion: 5, exploration: 1 },
    });
    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search/profile-advance-job")).toBe(true);
    });
    const advanceCall = apiFetchMock.mock.calls.find((call) => String(call[0]) === "/api/admin/vkpi/kol-smart-search/profile-advance-job");
    const advanceBody = JSON.parse(String((advanceCall?.[1] as RequestInit | undefined)?.body || "{}"));
    expect(advanceBody).toMatchObject({
      languages: ["ja"],
      local_qualification_spec: { languages: ["ja"] },
      filters: { countries: ["JP"], languages: ["ja"] },
    });
    fireEvent.click(screen.getByRole("button", { name: "筛选 · 排序" }));
    expect(screen.getAllByRole("button", { name: /垂直优先/ }).some((node) => node.getAttribute("aria-pressed") === "true" || node.getAttribute("style")?.includes("background"))).toBe(true);
  });

  it("卡片流工具行「全部 N」钮 → 全量大窗(kinds 撤默认后的接棒入口);点卡片 → 详情抽屉", async () => {
    renderBoard();
    // 工具行「全部 N」钮开 KolPoolAllModal(原 KPIBar 总数卡的等价入口)
    fireEvent.click(screen.getByLabelText("打开全量池表大窗"));
    expect(await screen.findByPlaceholderText(/搜索/)).toBeTruthy();

    // 大窗点行(行内「打开详情」冒泡到行 onClick)→ openItem 拉 detail-bundle 开抽屉
    const row = screen.getAllByText("打开详情")[0];
    fireEvent.click(row);
    await waitFor(() => {
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/detail-bundle"))).toBe(true);
    });
  });

  it("行内快捷动作:收藏走 favorite 既有端点;入项目开快捷小窗(projects 列表端点 + 诚实空态)", async () => {
    renderBoard();
    // 每卡一键收藏(不用开抽屉):点第一张卡的收藏钮 → favorite 端点被调
    const favButtons = screen.getAllByLabelText("收藏");
    expect(favButtons.length).toBeGreaterThan(0);
    fireEvent.click(favButtons[0]);
    await waitFor(() => {
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/favorite"))).toBe(true);
    });
    // 收藏动作不开抽屉(detail-bundle 未被调)
    expect(apiFetchMock.mock.calls.map((call) => String(call[0])).some((p) => p.includes("/detail-bundle"))).toBe(false);

    // 每卡一键入项目:开快捷小窗 → projects 列表端点被调;mock 空列表 → 诚实空态
    const projButtons = screen.getAllByLabelText("入项目");
    expect(projButtons.length).toBeGreaterThan(0);
    fireEvent.click(projButtons[0]);
    expect(await screen.findByText(/暂无可选项目/)).toBeTruthy();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("/vkpi/projects?limit=200"))).toBe(true);
  });

  it("快捷入项目读取真实回执:inserted=0/skipped=1 显示已存在,不冒充新增成功", async () => {
    routeApi({
      projects: { projects: [{ id: 900, project_name: "Z1 Launch" }] },
      addProject: { project_id: 900, requested: 1, inserted: 0, skipped_existing: 1, missing_kol_pool_ids: [] },
    });
    renderBoard();

    fireEvent.click(screen.getAllByLabelText("入项目")[0]);
    fireEvent.change(await screen.findByLabelText("目标项目"), { target: { value: "900" } });
    fireEvent.click(screen.getByRole("button", { name: "确认入项目" }));

    expect(await screen.findByText(/已在目标项目中/)).toBeTruthy();
    expect(screen.queryByText(/已新增 1 人到项目/)).toBeNull();
    const call = apiFetchMock.mock.calls.find((args) => String(args[0]).endsWith("/projects/900/kols"));
    expect(call?.[1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(String(call?.[1]?.body || "{}"))).toMatchObject({ kol_pool_ids: [101] });
    await waitFor(() => {
      const favoriteReads = apiFetchMock.mock.calls.filter((args) => String(args[0]).includes("/kol-pool/favorites"));
      expect(favoriteReads.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("身份 token 变化立即清旧收藏,空收藏响应不会遗留上一身份星标", async () => {
    routeApi({ favorites: { items: [{ kol_pool_id: 101 }] } });
    const view = renderBoard();
    expect((await screen.findAllByLabelText("取消收藏")).length).toBeGreaterThan(0);

    apiFetchMock.mockImplementation(async (path: unknown) => {
      const p = String(path);
      if (p.includes("/kol-pool/favorites")) return { items: [] };
      if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
      if (p.includes("/kol-pool/summary")) return SUMMARY_OK;
      if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
      return {};
    });
    view.rerender(<KolPoolBoardPage items={ITEMS as any} loading={false} error="" apiToken="another-user" staff={[]} />);

    await waitFor(() => expect(screen.queryAllByLabelText("取消收藏")).toHaveLength(0));
    expect(screen.getAllByLabelText("收藏").length).toBeGreaterThan(0);
  });

  it("旧身份收藏成功回调不会触发新身份回读,finally 也不会释放新身份同 KOL 的写锁", async () => {
    let resolveOld!: (value: unknown) => void;
    let resolveCurrent!: (value: unknown) => void;
    const oldMutation = new Promise((resolve) => { resolveOld = resolve; });
    const currentMutation = new Promise((resolve) => { resolveCurrent = resolve; });
    let favoriteReads = 0;
    let mutationCalls = 0;
    apiFetchMock.mockImplementation(async (path: unknown, init?: RequestInit) => {
      const p = String(path);
      if (p.includes("/kol-pool/favorites")) {
        favoriteReads += 1;
        return { items: [] };
      }
      if (p.endsWith("/favorite") && (init?.method === "POST" || init?.method === "DELETE")) {
        mutationCalls += 1;
        if (mutationCalls === 1) return oldMutation;
        if (mutationCalls === 2) return currentMutation;
        return {};
      }
      if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
      if (p.includes("/kol-pool/summary")) return SUMMARY_OK;
      if (p.includes("/kol-search-history")) return { items: [] };
      if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
      return {};
    });
    const favoritesChanged = vi.fn();
    window.addEventListener("vkpi:favorites-changed", favoritesChanged);
    const view = renderBoard({ apiToken: "old-token" });
    await waitFor(() => expect(favoriteReads).toBe(1));

    fireEvent.click(screen.getAllByLabelText("收藏")[0]);
    await waitFor(() => expect(mutationCalls).toBe(1));

    view.rerender(<KolPoolBoardPage items={ITEMS as any} loading={false} error="" apiToken="new-token" staff={[]} />);
    await waitFor(() => expect(favoriteReads).toBe(2));
    await waitFor(() => expect(screen.queryAllByLabelText("取消收藏")).toHaveLength(0));
    fireEvent.click(screen.getAllByLabelText("收藏")[0]);
    await waitFor(() => expect(mutationCalls).toBe(2));

    await act(async () => {
      resolveOld({ status: "ok" });
      await oldMutation;
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(favoritesChanged).not.toHaveBeenCalled();
    expect(favoriteReads).toBe(2);

    // 新身份写仍在途；旧身份 finally 不得把它的锁删掉并放行重复 DELETE。
    fireEvent.click(screen.getAllByLabelText("取消收藏")[0]);
    expect(mutationCalls).toBe(2);

    window.removeEventListener("vkpi:favorites-changed", favoritesChanged);
    await act(async () => {
      resolveCurrent({ status: "ok" });
      await currentMutation;
    });
  });

  it("旧身份收藏失败回调不会回滚新身份已收藏星标或注入错误态", async () => {
    let rejectOld!: (reason?: unknown) => void;
    const oldMutation = new Promise((_resolve, reject) => { rejectOld = reject; });
    let mutationCalls = 0;
    apiFetchMock.mockImplementation(async (path: unknown, init?: RequestInit, token?: string) => {
      const p = String(path);
      if (p.includes("/kol-pool/favorites")) {
        return token === "new-token" ? { items: [{ kol_pool_id: 101 }] } : { items: [] };
      }
      if (p.endsWith("/favorite") && init?.method === "POST") {
        mutationCalls += 1;
        return oldMutation;
      }
      if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
      if (p.includes("/kol-pool/summary")) return SUMMARY_OK;
      if (p.includes("/kol-search-history")) return { items: [] };
      if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
      return {};
    });
    const view = renderBoard({ apiToken: "old-token" });
    await waitFor(() => expect(screen.queryAllByLabelText("取消收藏")).toHaveLength(0));
    fireEvent.click(screen.getAllByLabelText("收藏")[0]);
    await waitFor(() => expect(mutationCalls).toBe(1));

    view.rerender(<KolPoolBoardPage items={ITEMS as any} loading={false} error="" apiToken="new-token" staff={[]} />);
    expect((await screen.findAllByLabelText("取消收藏")).length).toBeGreaterThan(0);

    await act(async () => {
      rejectOld(new Error("old account write failed"));
      try { await oldMutation; } catch { /* expected */ }
      await Promise.resolve();
    });

    expect(screen.getAllByLabelText("取消收藏").length).toBeGreaterThan(0);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("已同步")).toBeTruthy();
  });

  it("同页收藏变更事件会回读服务端并刷新星标,不保留旧快照", async () => {
    let favoriteReads = 0;
    apiFetchMock.mockImplementation(async (path: unknown) => {
      const p = String(path);
      if (p.includes("/kol-pool/favorites")) {
        favoriteReads += 1;
        return favoriteReads === 1 ? { items: [] } : { items: [{ kol_pool_id: 101 }] };
      }
      if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
      if (p.includes("/kol-pool/summary")) return SUMMARY_OK;
      if (p.includes("/kol-search-history")) return { items: [] };
      if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
      return {};
    });
    renderBoard();
    await waitFor(() => expect(favoriteReads).toBe(1));

    act(() => window.dispatchEvent(new CustomEvent("vkpi:favorites-changed")));

    await waitFor(() => expect(favoriteReads).toBe(2));
    expect((await screen.findAllByLabelText("取消收藏")).length).toBeGreaterThan(0);
  });

  it("收藏读取失败显式标未知,不把空星标冒充服务端零收藏", async () => {
    apiFetchMock.mockImplementation(async (path: unknown) => {
      const p = String(path);
      if (p.includes("/kol-pool/favorites")) throw new Error("favorites unavailable");
      if (p.includes("/kol-pool/needs-analysis")) return NEEDS_OK;
      if (p.includes("/kol-pool/summary")) return SUMMARY_OK;
      if (p.includes("/kol-search-history")) return { items: [] };
      if (p.includes("/task-queue")) return { active: [], recent: [], counts: { active_total: 0, queued: 0 } };
      return {};
    });
    renderBoard();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("收藏状态暂无法确认");
    expect(alert.textContent).toContain("当前空星标不代表服务端没有收藏");
    expect(screen.getByText("部分未同步")).toBeTruthy();
  });

  it("跨页事件:vkpi:open-kol-pool-search 消费 pending 关键词 → 填筛选并展开;-item 消费 id → 开抽屉", async () => {
    window.localStorage.setItem("vkpi:pending-kolpool-search", "alpha");
    renderBoard();
    // 消费后清 pending + 筛选条展开(FilterBar 在场)
    expect(window.localStorage.getItem("vkpi:pending-kolpool-search")).toBeNull();
    expect(await screen.findByText("收起筛选 · 排序")).toBeTruthy();

    window.localStorage.setItem("vkpi:pending-kolpool-open-id", "101");
    act(() => {
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-pool-item"));
    });
    await waitFor(() => {
      expect(window.localStorage.getItem("vkpi:pending-kolpool-open-id")).toBeNull();
      const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(calledPaths.some((p) => p.includes("/detail-bundle"))).toBe(true);
    });
  });

  it("布局只走本机键 vkpi-kol-pool-layout-v2(v1→v2 bump 盖本机残留);绝不写账户级 dashboard_layout_v1", async () => {
    renderBoard();
    expect(await screen.findByText("池子指标带")).toBeTruthy();
    expect(window.localStorage.getItem("vkpi-kol-pool-layout-v2")).toBeTruthy();
    expect(window.localStorage.getItem("dashboard_layout_v1")).toBeNull();
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.some((p) => p.includes("dashboard/layout"))).toBe(false);
  });

  it("无 token → 诚实 pending 卡,零读端点请求;空池 → 诚实空态文案", () => {
    render(<KolPoolBoardPage items={[]} loading={false} error="" apiToken="" staff={[]} />);
    expect(screen.getAllByText(/未登录 \/ 无 token/).length).toBeGreaterThan(0);
    expect(screen.getByText("未连接")).toBeTruthy();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
