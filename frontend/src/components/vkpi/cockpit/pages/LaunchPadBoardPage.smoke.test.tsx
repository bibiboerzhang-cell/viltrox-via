import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// 发射台改版冒烟(金样板 MarketVoicePage/MyKolBoardPage.smoke 同构):
// - 页壳:pagehead(发射台 + SKU 搜索 + 人数上限 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡 = 四源真计数(进行中计划/候选待核/已发内容/审批中),无按日序列端点 →
//   四卡全 spempty 虚线零药丸(诚实,不编时序);
// - 旧页零丢失:SKU 选择 → 全案六段(名单招牌拍法+焦段覆盖/预算 p50+不可估如实/排期
//   顺延徽/打法/预测)全真渲染;成员详情 = 六段一档 + ‹#n/N› + ↑↓ 连续翻 + 档案跳转;
// - 闭环动作:内容候选行内 ✓ 确认(PATCH 真端点,端点真实返回才置绿 + 重拉服务器)、
//   待审批行内 ✓ 通过(POST /publish/approve);
// - 诚实空态:审批表 0 行 = 已建未用;available=false = 后端未上线 pending;端点失败 =
//   ErrorCard + KPI 卡 pending 不编数;
// - 布局键 vkpi-launchpad-layout-v1 + 不传 apiToken → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { LaunchPadBoardPage } from "./LaunchPadBoardPage";

const SKUS = {
  items: [
    {
      sku: "AF-85MM-F14-PRO-FE",
      model_name: "AF 85mm F1.4 Pro",
      marketing_name: "",
      category_main: "镜头",
      category_detail: "定焦",
      series: "Pro",
      mount: "FE",
      price_usd: 549,
      status: "active",
    },
  ],
};

// 全案六段(形状对齐 launch_assembly.assemble_launch_plan;Beta = 缺深析/不可估/默认周期
// 的诚实降级样本,Alpha = 全段齐)
const PLAN_OK = {
  generated_at: "2026-07-12T03:00:00Z",
  llm_calls: false,
  sku_focal: "85mm",
  product: { sku: "AF-85MM-F14-PRO-FE", model_name: "AF 85mm F1.4 Pro", category_main: "镜头", category_detail: "定焦", series: "Pro", mount: "FE", price_usd: 549 },
  roster: {
    status: "ready",
    selection_basis: "家族证据 × 焦段覆盖打分",
    basis: { resolved_query: "AF 85mm", family: "85mm 家族" },
    members: [
      {
        kol_pool_id: 101, display_name: "Alpha Cam", handle: "@alpha", platform: "youtube", country: "US", score: 92,
        signature: { status: "ready", top_mode: { label: "实拍测评", avg_views: 120000 } },
        focal_coverage: { status: "ready", sku_focal: "85mm", sku_focal_covered: true, sku_focal_video_count: 3, covered_focal_count: 5 },
      },
      {
        kol_pool_id: 102, display_name: "Beta Vlog", handle: "@beta", platform: "instagram", country: "", score: 77,
        signature: { status: "empty", reason: "无深析样本" },
        focal_coverage: { status: "ready", sku_focal: "85mm", sku_focal_covered: false, covered_focal_count: 2 },
      },
    ],
  },
  budgets: {
    status: "ready",
    items: [
      { kol_pool_id: 101, display_name: "Alpha Cam", estimate: { status: "ok", estimated_usd_p50: 1200, estimated_usd_low: 800, estimated_usd_high: 1600, method: "个人报价历史", source_count: 4, confidence: "high" } },
      { kol_pool_id: 102, display_name: "Beta Vlog", estimate: { status: "empty", reason: "无报价历史" } },
    ],
    total: { estimated_usd_p50: 1200, estimated_usd_low: 800, estimated_usd_high: 1600, priced_members: 1, unpriced_members: 1 },
  },
  schedule: {
    status: "ready",
    kickoff_date: "2026-07-20",
    items: [
      { kol_pool_id: 101, display_name: "Alpha Cam", publish_week: "2026-W31", publish_date: "2026-07-30", shifted_days: 0, leadtime_days: 10, leadtime_basis: { method: "personal_median", sample_count: 3, confidence: "high" } },
      { kol_pool_id: 102, display_name: "Beta Vlog", publish_week: "2026-W32", publish_date: "2026-08-06", shifted_days: 3, leadtime_days: 14, leadtime_basis: { method: "default", confidence: "low" } },
    ],
  },
  playbooks: {
    status: "ready",
    items: [
      { kol_pool_id: 101, status: "ready", line: "以实拍测评切入,人像夜景对比出片", basis: { video_count: 3, avg_views: 120000, views_sample: 3 } },
      { kol_pool_id: 102, status: "empty", line: "缺深析,先走通用开箱" },
    ],
  },
  official_synergy: {
    status: "ready",
    scope: "sku_focal",
    scanned_posts: 200,
    matched_posts: 12,
    suggestions: [{ line: "官号发 85mm 样片合集接力", basis: { source: "official_posts", sample: 12, metric: "均互动" } }],
  },
  coverage: { status: "ready", selected: [{}, {}], dropped_overlap: [{ handle: "@gamma" }], method: "roster_optimizer", note: "受众重叠阈值 0.35" },
  forecast: {
    status: "ready",
    items: [
      { kol_pool_id: 101, display_name: "Alpha Cam", forecast: { status: "ok", expected_views_p50: 150000, expected_views_p10: 60000, expected_views_p90: 400000, engagement_rate: 0.045, confidence: "medium" } },
      { kol_pool_id: 102, display_name: "Beta Vlog", forecast: { status: "empty", reason: "样本不足" } },
    ],
  },
};

// 内容排期(vkpi_project_content_posts 形状):候选 2 + 待复盘 1 → K2=2
const POSTS_OK = {
  status: "ok",
  count: 3,
  items: [
    { id: 11, project_id: 7, kol_pool_id: 101, platform: "youtube", content_url: "https://youtu.be/x1", title: "85mm first look", published_at: "2026-07-01T10:00:00Z", view_count: 1000, like_count: 10, comment_count: 2, matched_terms: "viltrox,85mm", match_confidence: 0.92, match_reason: "标题命中", status: "candidate", created_at: "2026-07-02T00:00:00Z", project_name: "AF 85 铺量", product_name: "AF 85mm" },
    { id: 12, project_id: 7, kol_pool_id: 102, platform: "instagram", content_url: "", title: "second look", published_at: null, status: "candidate", created_at: "2026-07-03T00:00:00Z", project_name: "AF 85 铺量", product_name: "AF 85mm" },
    { id: 13, project_id: 8, kol_pool_id: 103, platform: "tiktok", content_url: "https://tiktok.com/v3", title: "retro video", published_at: "2026-06-20T00:00:00Z", status: "retrospective_ready", created_at: "2026-06-21T00:00:00Z", project_name: "35mm 战役", product_name: "AF 35mm" },
  ],
  note: "own-only 由服务端裁剪",
};

// 发布审批(vkpi_publish_approvals):pending 1 + approved 1 → K4=1
const APPROVALS_OK = {
  available: true,
  count: 2,
  items: [
    { id: 1, source_table: "vkpi_project_content_posts", source_id: "11", platform: "youtube", account_handle: "AF 85 铺量", title: "85mm first look", status: "pending", scheduled_publish_at: null, created_at: "2026-07-03T00:00:00Z" },
    { id: 2, source_table: "vkpi_project_content_posts", source_id: "13", platform: "tiktok", account_handle: "35mm 战役", title: "retro video", status: "approved", scheduled_publish_at: null, approved_at: "2026-07-04T00:00:00Z", created_at: "2026-07-03T00:00:00Z" },
  ],
};

// 履约阶段(归一读侧;content_posted 695 → K3)
const STAGES_OK = {
  status: "ok",
  total: 2189,
  stages: [
    { stage: "device_sent", stage_status: "(空)", n: 849 },
    { stage: "content_posted", stage_status: "(空)", n: 690 },
    { stage: "content_posted", stage_status: "done", n: 5 },
    { stage: "contacted", stage_status: "(空)", n: 242 },
  ],
};

const LAUNCHES_OK = {
  launches: [
    { id: 526, name: "AF 35mm F1.2 LAB FE US Creator Launch", product_sku: "VL-LEN072", product_name: "AF 35mm F1.2", status: "active", launch_window_start: "2026-05-18T00:00:00Z", launch_window_end: "2026-06-17T00:00:00Z", created_at: "2026-05-01T00:00:00Z", updated_at: "2026-05-01T00:00:00Z" },
  ],
};

type Overrides = {
  posts?: unknown;
  approvals?: unknown;
  launches?: unknown;
  stages?: unknown;
};

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    if (p.startsWith("/api/admin/vkpi/sku/list")) return SKUS;
    if (p.startsWith("/api/admin/vkpi/launch/assemble")) return PLAN_OK;
    if (p.startsWith("/api/admin/vkpi/publish/pending")) {
      const value = overrides.approvals ?? APPROVALS_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/publish/approve")) return { status: "success", approval_id: 1, state: "approved" };
    if (p.startsWith("/api/admin/vkpi/publish/reschedule")) {
      const body = JSON.parse(String(init?.body || "{}"));
      return { status: "success", approval_id: 1, state: "scheduled", scheduled_publish_at: body.scheduled_publish_at };
    }
    if (p.startsWith("/api/admin/vkpi/publish/remind")) return { status: "success", approval_id: 1, reminded: true };
    if (p.startsWith("/api/admin/vkpi/product-analysis/launches")) {
      const value = overrides.launches ?? LAUNCHES_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    const patchMatch = p.match(/\/api\/admin\/vkpi\/projects\/content-posts\/(\d+)$/);
    if (patchMatch && method === "PATCH") {
      const body = JSON.parse(String(init?.body || "{}"));
      const item = POSTS_OK.items.find((it) => it.id === Number(patchMatch[1]));
      return { status: "ok", action: body.action, post: { ...(item || {}), status: body.action } };
    }
    if (p.startsWith("/api/admin/vkpi/projects/content-posts")) {
      const value = overrides.posts ?? POSTS_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/projects/deliverable-stages-summary")) {
      const value = overrides.stages ?? STAGES_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) => render(<LaunchPadBoardPage apiToken="t" {...(props as any)} />);

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

/** SKU 选择流程:搜索框输入 → 防抖下拉 → mousedown 选项 → assemble。 */
async function pickSku() {
  fireEvent.focus(screen.getByLabelText("搜索 SKU"));
  fireEvent.change(screen.getByLabelText("搜索 SKU"), { target: { value: "85" } });
  fireEvent.mouseDown(await screen.findByText("AF 85mm F1.4 Pro"));
  expect((await screen.findAllByText("Alpha Cam")).length).toBeGreaterThan(0);
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("LaunchPadBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡:四源真计数(1/2/695/1);无按日序列端点 → 四卡 spempty 虚线零药丸", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect(await screen.findByText("发布计划进行中")).toBeTruthy();
    expect(screen.getByText("内容候选待核")).toBeTruthy();
    expect(screen.getAllByText("已发内容").length).toBeGreaterThan(0);
    expect(screen.getAllByText("审批中").length).toBeGreaterThan(0);
    // K3 = content_posted 690+5 合并 695(同 stage 不同 stage_status 读侧合并)
    expect(await screen.findByText("695")).toBeTruthy();
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4));
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    // 四个真端点全被调
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/publish/pending"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/projects/content-posts"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/product-analysis/launches"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/projects/deliverable-stages-summary"))).toBe(true);
  });

  it("默认布局:九模块在场;plan 六模块未选 SKU 诚实空;palette 备选不进默认", async () => {
    renderBoard();
    expect(await screen.findByText("发布指标带")).toBeTruthy();
    ["KOL 名单", "产品概要", "发布排期", "每人预算", "每人打法", "预测战绩", "内容排期", "发布审批"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    // 未选 SKU → 六个 plan 模块统一诚实空(不摆假图)
    expect(screen.getAllByText("页头搜索并选择 SKU → 生成全案。").length).toBe(6);
    // palette 备选不进默认布局
    expect(screen.queryByText("官号协同")).toBeNull();
    expect(screen.queryByText("覆盖最大化")).toBeNull();
    expect(screen.queryByText("履约阶段")).toBeNull();
    expect(screen.queryByText("物料覆盖")).toBeNull();
    // 内容排期真行 + 行内一键复核在场(高频动作零下钻)
    expect((await screen.findAllByText("85mm first look")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("✓ 确认").length).toBe(2);
    // 发布审批真行 + 行内 ✓ 通过(pending 行才有)
    expect(screen.getAllByText("✓ 通过").length).toBe(1);
    expect(screen.getAllByText("待审批").length).toBeGreaterThan(0);
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出五个备选", async () => {
    renderBoard();
    expect(await screen.findByText("发布指标带")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(await screen.findByText("官号协同")).toBeTruthy();
    expect(screen.getByText("覆盖最大化")).toBeTruthy();
    expect(screen.getAllByText("发布计划").length).toBeGreaterThan(0);
    expect(screen.getByText("履约阶段")).toBeTruthy();
    expect(screen.getByText("物料覆盖")).toBeTruthy();
  });

  it("布局键 vkpi-launchpad-layout-v1 生效;不传 apiToken → 绝不写账户级布局", async () => {
    window.localStorage.setItem("vkpi-launchpad-layout-v1", JSON.stringify([{ moduleKey: "kpiL", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("发布指标带")).toBeTruthy();
    expect(screen.queryByText("KOL 名单")).toBeNull();
    expect(screen.queryByText("内容排期")).toBeNull();
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });
});

describe("LaunchPadBoardPage 全案六段(旧页零丢失)", () => {
  it("SKU 选择 → 六段真渲染:名单招牌拍法+焦段覆盖 / 预算不可估如实 / 排期顺延徽 / 打法 / 预测", async () => {
    renderBoard();
    expect(await screen.findByText("发布指标带")).toBeTruthy();
    await pickSku();
    expect(calledPaths().some((p) => p.includes("/launch/assemble?sku=AF-85MM-F14-PRO-FE&max_roster=8"))).toBe(true);
    // pagehead SKU 徽
    expect(screen.getAllByText("AF-85MM-F14-PRO-FE").length).toBeGreaterThan(0);
    // ① 名单:招牌拍法 chip + 焦段覆盖徽(拍过/空白可切入)+ 无深析样本如实
    expect(screen.getByText(/实拍测评 · 均播 120\.0K/)).toBeTruthy();
    expect(screen.getByText("85mm 拍过 3 条")).toBeTruthy();
    expect(screen.getByText("85mm 空白可切入")).toBeTruthy();
    expect(screen.getByText("无深析样本")).toBeTruthy();
    // ② 预算:p50 真值 + 不可估如实 + 缺口注
    expect(screen.getAllByText("$1,200").length).toBeGreaterThan(0);
    expect(screen.getByText("不可估")).toBeTruthy();
    expect(screen.getByText(/1 人暂无法估价/)).toBeTruthy();
    // ③ 排期:发布周 + 顺延徽 + kickoff 徽
    expect(screen.getByText("2026-W31")).toBeTruthy();
    expect(screen.getByText("顺延 3 天")).toBeTruthy();
    expect(screen.getByText("kickoff 2026-07-20")).toBeTruthy();
    // ④ 打法一行一条(缺深析徽)+ 预测 p50
    expect(screen.getByText("以实拍测评切入,人像夜景对比出片")).toBeTruthy();
    expect(screen.getAllByText("缺深析").length).toBeGreaterThan(0);
    expect(screen.getByText("150.0K")).toBeTruthy();
    expect(screen.getAllByText("不可测").length).toBeGreaterThan(0);
    // 产品概要
    expect(screen.getByText("镜头 / 定焦")).toBeTruthy();
    expect(screen.getByText("$549")).toBeTruthy();
  });

  it("成员详情:六段一档 + ‹#n/N› + ↑↓ 连续翻;打开 KOL 档案 = sessionStorage + 事件管道", async () => {
    const onProfile = vi.fn();
    window.addEventListener("vkpi:open-kol-profile", onProfile);
    renderBoard();
    expect(await screen.findByText("发布指标带")).toBeTruthy();
    await pickSku();
    fireEvent.click(screen.getAllByText("Alpha Cam")[0]);
    expect(await screen.findByText("#1 / 2")).toBeTruthy();
    // 六段一档:预算/排期/打法/预测区块全在
    expect(screen.getByText("报价 p50")).toBeTruthy();
    expect(screen.getByText("个人历史中位(3 样本)")).toBeTruthy();
    expect(screen.getByText(/3 条深析命中该拍法/)).toBeTruthy();
    expect(screen.getByText("60.0K – 400.0K")).toBeTruthy();
    // ↓ 连续翻 → Beta(诚实降级样本:不可估/默认周期/不可测)
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2 / 2")).toBeTruthy();
    expect(screen.getByText(/不可估:无报价历史/)).toBeTruthy();
    expect(screen.getByText("默认周期(无履约历史)")).toBeTruthy();
    expect(screen.getByText(/不可测:样本不足/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByText("#1 / 2")).toBeTruthy();
    // 档案跳转:sessionStorage 传 kol_pool_id + vkpi:open-kol-profile 事件
    fireEvent.click(screen.getByText("打开 KOL 档案 →"));
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(onProfile).toHaveBeenCalledTimes(1);
    window.removeEventListener("vkpi:open-kol-profile", onProfile);
  });
});

describe("LaunchPadBoardPage 闭环动作(端点真实返回才置绿)", () => {
  it("内容候选行内 ✓ 确认:PATCH 真端点 → 状态徽变已确认 + 重拉服务器真数", async () => {
    renderBoard();
    expect((await screen.findAllByText("85mm first look")).length).toBeGreaterThan(0);
    const before = calledPaths().filter((p) => p.endsWith("content-posts?status=all")).length;
    fireEvent.click(screen.getAllByText("✓ 确认")[0]);
    await waitFor(() => expect(screen.getAllByText("已确认").length).toBeGreaterThan(0));
    const patchCall = apiFetchMock.mock.calls.find((call) => String(call[0]).endsWith("/content-posts/11"));
    expect(patchCall).toBeTruthy();
    expect(String((patchCall![1] as RequestInit).method)).toBe("PATCH");
    // version 自增 → 内容端点重拉(列表读服务器真数,绝不本地编)
    await waitFor(() =>
      expect(calledPaths().filter((p) => p.endsWith("content-posts?status=all")).length).toBeGreaterThan(before),
    );
  });

  it("待审批行内 ✓ 通过:POST /publish/approve(真 source 键)→ 重拉审批列表", async () => {
    renderBoard();
    expect((await screen.findAllByText("发布审批")).length).toBeGreaterThan(0);
    const before = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/publish/pending")).length;
    fireEvent.click(await screen.findByText("✓ 通过"));
    await waitFor(() => expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/publish/approve"))).toBe(true));
    const approveCall = apiFetchMock.mock.calls.find((call) => String(call[0]).startsWith("/api/admin/vkpi/publish/approve"));
    const body = JSON.parse(String((approveCall![1] as RequestInit).body));
    expect(body.source_table).toBe("vkpi_project_content_posts");
    expect(body.source_id).toBe("11");
    await waitFor(() =>
      expect(calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/publish/pending")).length).toBeGreaterThan(before),
    );
  });

  it("内容详情:复核 + 审批三动作 + 连续翻;重排走 datetime-local 真调用", async () => {
    renderBoard();
    fireEvent.click((await screen.findAllByText("85mm first look"))[0]);
    expect(await screen.findByText("#1 / 3")).toBeTruthy();
    expect(screen.getByText("人工复核")).toBeTruthy();
    expect(screen.getAllByText("发布审批").length).toBeGreaterThan(0);
    // 重排:datetime-local → ISO POST;端点真实返回才置绿
    fireEvent.change(screen.getByLabelText("新发布时间"), { target: { value: "2026-08-01T14:00" } });
    fireEvent.click(screen.getByText("重排"));
    await waitFor(() => expect(screen.getByText(/✓ 已重排/)).toBeTruthy());
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/publish/reschedule"))).toBe(true);
    // 连续翻:↓ → 第二条
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2 / 3")).toBeTruthy();
    expect(screen.getAllByText("second look").length).toBeGreaterThan(0);
  });
});

describe("LaunchPadBoardPage 诚实空态 / 失败态", () => {
  it("审批表 0 行 = 已建未用如实;审批中 KPI 如实 0", async () => {
    routeApi({ approvals: { available: true, count: 0, items: [] } });
    renderBoard();
    expect(await screen.findByText(/0 条 · 已建未用/)).toBeTruthy();
    expect(await screen.findByText("0 待审")).toBeTruthy();
  });

  it("available=false(迁移173未应用)→ 审批模块与 KPI 双诚实 pending", async () => {
    routeApi({ approvals: { available: false, items: [], count: 0, reason: "migration_173_not_applied" } });
    renderBoard();
    expect((await screen.findAllByText(/审批后端未上线/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/migration_173_not_applied/).length).toBeGreaterThan(0);
  });

  it("内容端点失败 → ErrorCard + 候选 KPI pending 不编数", async () => {
    routeApi({ posts: new Error("HTTP 503") });
    renderBoard();
    expect(await screen.findByText("content-posts 读取失败")).toBeTruthy();
    expect(screen.getAllByText(/HTTP 503/).length).toBeGreaterThan(0);
    // K2 pending(dot pend),其余三卡真值照常
    expect(await screen.findByText("695")).toBeTruthy();
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__dot--pend").length).toBe(1));
  });
});
