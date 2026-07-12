import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Projects 改版冒烟(金样板 MarketVoice/MyKol/LaunchPad.smoke 同构):
// - 页壳:pagehead(项目跟进 + 项目组徽 + 新建推广 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡 = 四源真值(进行中项目/在项 KOL/本窗发布/归因销售);无按日序列端点 →
//   四卡 spempty 虚线零药丸(诚实,不编时序);
// - 金额红线:美分→美元唯一换算点单测(历史 100 倍缺陷:649549 分必须是 $6,495.49,
//   绝不是 $649,549);合计 null/0 语义分明;
// - 旧页零丢失:卡片墙(筛选/新建/星标钮)内嵌真渲染、履约待办点行开详情(骨架分支)、
//   新建推广表单 payload 口径原样;
// - 履约闭环:内容行内 ✓ 复核(PATCH 真端点,真实返回才置绿)+ 推进复盘(POST
//   advance-retrospective,非 ok 不写 ✓ —— gone 不写 ✓ 口径);
// - 布局键 vkpi-projects-layout-v1 + 不传 apiToken 给板 → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口)+ useAuth,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// useAuth 在没有 AuthProvider 时会 throw —— 必须 mock(旧页冒烟同款)。
vi.mock("../../../../hooks/useAuth", () => ({
  useAuth: () => ({ status: "authenticated", token: "t", user: null, signIn: vi.fn(), signOut: vi.fn(), refreshUser: vi.fn() }),
}));

import { ProjectsBoardPage } from "./ProjectsBoardPage";
import { centsToUsd, fmtUsd2, sumRevenueUsd } from "../../../../services/vkpi/projectsBoard-api";
import type { VkpiDashboardData, VkpiProjectRow } from "../../vkpiTypes";

const daysAgo = (n: number) => new Date(Date.now() - n * 24 * 3600 * 1000).toISOString();

// dashboard 项目行:同名 campaign 收拢 → 2 组;「AF 85 上市」组含 shipped(进行中)+
// published(收尾)→ 组状态=进行中;「35 战役」全 cancelled → 已取消(不算在项)。
const ROWS = [
  { id: "1", campaign: "AF 85 上市", stage: "shipped", kolCount: 6, cost: 1000, gmv: null, clicks: null, orders: null, roi: null, views: 100, platform: "YouTube", kolName: "A", kolHandle: "@a", ownerName: "J", latestMessageAt: daysAgo(1), latestMessageSource: "DM" },
  { id: "2", campaign: "AF 85 上市", stage: "published", kolCount: 3, cost: null, gmv: null, clicks: null, orders: null, roi: null, views: 50, platform: "YouTube", kolName: "B", kolHandle: "@b", ownerName: "J", latestMessageAt: daysAgo(2), latestMessageSource: "DM" },
  { id: "3", campaign: "35 战役", stage: "cancelled", kolCount: 7, cost: 200, gmv: null, clicks: null, orders: null, roi: null, views: 10, platform: "TikTok", kolName: "C", kolHandle: "@c", ownerName: "M", latestMessageAt: daysAgo(3), latestMessageSource: "Email" },
] as unknown as VkpiProjectRow[];

const data = {
  staffMembers: [{ id: "s1", name: "Jianbo" }],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
  projects: ROWS,
} as unknown as VkpiDashboardData;

// 内容帖:candidate(5 天前发布 → 本窗发布=1)+ matched(60 天前)+ 待复盘(无时间)
const POSTS_OK = {
  status: "ok",
  count: 3,
  items: [
    { id: 11, project_id: 7, kol_pool_id: 101, platform: "youtube", content_url: "https://youtu.be/x1", title: "85mm first look", published_at: daysAgo(5), view_count: 1000, status: "candidate", project_name: "AF 85 上市", product_name: "AF 85mm" },
    { id: 12, project_id: 7, kol_pool_id: 102, platform: "instagram", content_url: "", title: "matched clip", published_at: daysAgo(60), status: "matched", project_name: "AF 85 上市", product_name: "AF 85mm" },
    { id: 13, project_id: 8, kol_pool_id: 103, platform: "tiktok", content_url: "", title: "retro video", published_at: null, status: "retrospective_ready", project_name: "35 战役", product_name: "AF 35mm" },
  ],
  note: "own-only 由服务端裁剪",
};

// 观察窗口 8 个(>6 触发「查看全量」);matched 1 + scanning 7
const WINDOWS_OK = {
  status: "ok",
  count: 8,
  items: Array.from({ length: 8 }).map((_, i) => ({
    id: 100 + i,
    project_id: 7,
    assignment_id: 200 + i,
    kol_pool_id: 300 + i,
    starts_at: "2026-07-01T00:00:00Z",
    ends_at: "2026-07-31T00:00:00Z",
    status: i === 0 ? "matched" : "scanning",
    scan_count: i,
    last_scan_at: i > 0 ? "2026-07-10T00:00:00Z" : null,
    matched_content_post_id: i === 0 ? 12 : null,
    project_name: `窗口项目 ${i}`,
    product_name: "AF 85mm",
  })),
};

// 归因:649549 + 100 美分 → 合计 $6,496.49(100 倍缺陷守卫)
const ATTR_OK = {
  attributions: [
    { id: 1, source_platform: "shopify", source_ref: "ref-1", project_id: 7, kol_id: 9, staff_id: 3, product_sku: "VL-LEN072", order_id: "SO-1001", revenue_cents: 649549, commission_cents: 64955, currency: "USD", attribution_model: "link", confidence: "confirmed", occurred_at: "2026-07-01T10:00:00Z", imported_at: "2026-07-02T00:00:00Z" },
    { id: 2, source_platform: "amazon", source_ref: "ref-2", project_id: null, kol_id: null, staff_id: null, product_sku: null, order_id: null, revenue_cents: 100, commission_cents: null, currency: "USD", attribution_model: "manual", confidence: "estimated", occurred_at: "2026-07-03T10:00:00Z", imported_at: "2026-07-03T12:00:00Z" },
  ],
};

const STAGES_OK = {
  status: "ok",
  total: 2189,
  stages: [
    { stage: "device_sent", stage_status: "(空)", n: 842 },
    { stage: "content_posted", stage_status: "(空)", n: 690 },
    { stage: "content_posted", stage_status: "done", n: 5 },
    { stage: "contacted", stage_status: "(空)", n: 209 },
  ],
};

const DUE_OK = {
  status: "ok",
  count: 1,
  items: [{ project_id: 777, project_name: "逾期项目甲", product_name: "AF 85mm", days_since_delivered: 9, assignment_count: 4 }],
  note: "",
};

type Overrides = { posts?: unknown; attr?: unknown; advance?: unknown };

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    if (p.startsWith("/api/admin/vkpi/projects/deliverable-stages-summary")) return STAGES_OK;
    const patchMatch = p.match(/\/api\/admin\/vkpi\/projects\/content-posts\/(\d+)$/);
    if (patchMatch && method === "PATCH") {
      const body = JSON.parse(String(init?.body || "{}"));
      const item = POSTS_OK.items.find((it) => it.id === Number(patchMatch[1]));
      return { status: "ok", action: body.action, post: { ...(item || {}), status: body.action } };
    }
    const advanceMatch = p.match(/\/api\/admin\/vkpi\/projects\/(\d+)\/content-posts\/advance-retrospective$/);
    if (advanceMatch && method === "POST") {
      const value = overrides.advance ?? { status: "ok", advanced_count: 1, retrospective: { status: "queued" } };
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/projects/content-posts")) {
      const value = overrides.posts ?? POSTS_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/projects/observation-windows")) return WINDOWS_OK;
    if (p.startsWith("/api/admin/vkpi/attribution")) {
      const value = overrides.attr ?? ATTR_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/projects/due-list")) return DUE_OK;
    if (p.startsWith("/api/admin/vkpi/publish/")) return { status: "success", approval_id: 1 };
    if (p.startsWith("/api/marketing/product-catalog")) return { products: [] };
    // 详情分支:挂起不返回 → 骨架屏路径(本冒烟不深挂整棵详情树)
    if (p.startsWith("/api/marketing/projects/")) return new Promise(() => {});
    throw new Error(`unexpected apiFetch: ${method} ${p}`);
  });
}

const noop = () => {};
const renderBoard = (props: Record<string, unknown> = {}) =>
  render(
    <ProjectsBoardPage
      data={data}
      filteredProjects={ROWS}
      viewMode="manager"
      apiToken="t"
      onSelectProject={noop}
      {...(props as any)}
    />,
  );

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

/* ============ 金额红线单测(展示端算术;历史 100 倍缺陷守卫) ============ */
describe("金额换算(美分→美元唯一换算点)", () => {
  it("centsToUsd:649549 分 = 6495.49 美元;非数/空 → null 诚实缺席", () => {
    expect(centsToUsd(649549)).toBe(6495.49);
    expect(centsToUsd("100")).toBe(1);
    expect(centsToUsd(0)).toBe(0);
    expect(centsToUsd(null)).toBeNull();
    expect(centsToUsd(undefined)).toBeNull();
    expect(centsToUsd("abc")).toBeNull();
  });

  it("fmtUsd2:两位小数门面;100 倍缺陷守卫(绝不把分当元)", () => {
    expect(fmtUsd2(centsToUsd(649549))).toBe("$6,495.49");
    expect(fmtUsd2(centsToUsd(649549))).not.toBe("$649,549.00");
    expect(fmtUsd2(0)).toBe("$0.00");
    expect(fmtUsd2(null)).toBe("—");
  });

  it("sumRevenueUsd:非数组=null(pending)/空数组=0(真零)/行内非数跳过", () => {
    expect(sumRevenueUsd(null)).toBeNull();
    expect(sumRevenueUsd([])).toBe(0);
    expect(sumRevenueUsd([{ revenue_cents: 649549 }, { revenue_cents: "100" }, { revenue_cents: null }])).toBe(6496.49);
  });
});

/* ============ 页壳 + KPI 带 + 注册表 ============ */
describe("ProjectsBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡真值:进行中项目 1 / 在项 KOL 9 / 本窗发布 1 / 归因销售 $6,496.49;无序列 → spempty 零药丸", async () => {
    expect(() => renderBoard()).not.toThrow();
    // 注:「进行中项目/归因销售」等词也住 SrcChip 口径行 → 用 findAll(≥1)断言
    expect((await screen.findAllByText("进行中项目")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("在项 KOL").length).toBeGreaterThan(0);
    expect(screen.getAllByText("本窗发布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("归因销售").length).toBeGreaterThan(0);
    // 进行中组=1(取消组不算);在项 KOL = 6+3=9(取消组 7 不算)
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    expect(screen.getAllByText("9").length).toBeGreaterThan(0);
    // 归因合计 = (649549+100)/100 = $6,496.49(美分→美元)
    expect(await screen.findByText("$6,496.49")).toBeTruthy();
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4));
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    // 四个真端点全被调
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/projects/deliverable-stages-summary"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/projects/content-posts"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/projects/observation-windows"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/attribution"))).toBe(true);
  });

  it("默认布局六模块在场;palette 备选(归因/成本)不进默认;编辑布局可从 palette 添加", async () => {
    renderBoard();
    expect(await screen.findByText("项目指标带")).toBeTruthy();
    ["履约待办", "履约阶段漏斗", "项目卡片墙", "内容匹配", "观察窗口"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    // palette 备选不进默认:成本概览缺席;「归因销售」出现次数在开 palette 后增加
    expect(screen.queryByText("成本概览")).toBeNull();
    const attrMentionsBefore = screen.getAllByText("归因销售").length;
    // 阶段漏斗真值:content_posted 690+5 读侧合并 695
    expect(await screen.findByText(/695/)).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(screen.getAllByText("归因销售").length).toBeGreaterThan(attrMentionsBefore);
    expect(screen.getByText("成本概览")).toBeTruthy();
  });

  it("布局键 vkpi-projects-layout-v1 生效;不传 apiToken 给板 → 绝不写账户级布局", async () => {
    window.localStorage.setItem("vkpi-projects-layout-v1", JSON.stringify([{ moduleKey: "kpiP", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("项目指标带")).toBeTruthy();
    expect(screen.queryByText("项目卡片墙")).toBeNull();
    expect(screen.queryByText("内容匹配")).toBeNull();
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });
});

/* ============ 旧页零丢失(卡片墙/履约待办/新建表单) ============ */
describe("ProjectsBoardPage 旧功能零丢失", () => {
  it("卡片墙内嵌:项目卡、状态筛选、星标/暂停钮、墙内新建/导入入口全在", async () => {
    renderBoard();
    expect(await screen.findByText("AF 85 上市")).toBeTruthy();
    expect(screen.getByText("35 战役")).toBeTruthy();
    expect(screen.getAllByText("全部").length).toBeGreaterThan(0);
    expect(screen.getAllByText("重点").length).toBeGreaterThan(0);
    expect(screen.getAllByText("暂停本轮跟进").length).toBeGreaterThan(0);
    expect(screen.getByText("✦ 新建推广")).toBeTruthy(); // 墙内 filter-bar 按钮
    expect(screen.getByText("导入 KOL 名单")).toBeTruthy();
  });

  it("履约待办内嵌真行;点行 → 项目详情分支(骨架 + 返回)", async () => {
    renderBoard();
    expect(await screen.findByText("逾期项目甲")).toBeTruthy();
    expect(screen.getByText("已签收 9 天")).toBeTruthy();
    fireEvent.click(screen.getByText("逾期项目甲"));
    expect(await screen.findByText("← 返回项目列表")).toBeTruthy();
    expect(calledPaths().some((p) => p.startsWith("/api/marketing/projects/777"))).toBe(true);
    // 返回后回看板
    fireEvent.click(screen.getByText("← 返回项目列表"));
    expect(await screen.findByText("项目指标带")).toBeTruthy();
  });

  it("新建推广:pagehead 入口 → 旧表单;提交 payload 口径原样(sourceType=cockpit_projects_ui)", async () => {
    const onCreateProject = vi.fn().mockResolvedValue({});
    renderBoard({ onCreateProject });
    expect(await screen.findByText("项目指标带")).toBeTruthy();
    // pagehead 与墙内 filter-bar 双入口(旧页零丢失 + 板块页快捷入口),点 pagehead 那枚
    fireEvent.click(screen.getAllByRole("button", { name: "✦ 新建推广" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "新建推广" });
    expect(dialog).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("例：AF 35mm F1.2 LAB FE 上市推广"), { target: { value: "测试推广" } });
    fireEvent.click(screen.getByText("创建推广"));
    await waitFor(() => expect(onCreateProject).toHaveBeenCalledTimes(1));
    const payload = onCreateProject.mock.calls[0][0];
    expect(payload.projectName).toBe("测试推广");
    expect(payload.sourceType).toBe("cockpit_projects_ui");
    expect(payload.metadata).toBeUndefined();
    expect(await screen.findByText(/推广项目已创建/)).toBeTruthy();
  });
});

/* ============ 履约闭环(复核 ✓ / 推进复盘;gone 不写 ✓) ============ */
describe("ProjectsBoardPage 履约闭环动作", () => {
  it("内容行内 ✓ 确认:PATCH 真端点,真实返回才置「已确认」并重拉服务器", async () => {
    renderBoard();
    expect(await screen.findByText("85mm first look")).toBeTruthy();
    const before = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/projects/content-posts") && !p.includes("/11")).length;
    fireEvent.click(screen.getAllByText("✓ 确认")[0]);
    await waitFor(() => expect(calledPaths().some((p) => p.endsWith("/content-posts/11"))).toBe(true));
    expect(await screen.findAllByText("已确认")).toBeTruthy();
    // version 自增 → 列表端点重拉(服务器真数,不本地编)
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/projects/content-posts") && !p.includes("/11")).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("推进复盘:matched 帖按项目一键 POST advance-retrospective;端点 ok 才 ✓ + 回执真计数", async () => {
    renderBoard();
    expect(await screen.findByText("↦ 推进复盘")).toBeTruthy();
    expect(screen.getByText("已匹配 ×1")).toBeTruthy();
    fireEvent.click(screen.getByText("↦ 推进复盘"));
    await waitFor(() =>
      expect(calledPaths().some((p) => p.endsWith("/projects/7/content-posts/advance-retrospective"))).toBe(true),
    );
    expect(await screen.findByText(/✓ 已推进 1 条 · 聚合已排队/)).toBeTruthy();
  });

  it("推进复盘非 ok 返回:不写 ✓,失败原因原样透出(gone 不写 ✓ 口径)", async () => {
    routeApi({ advance: { status: "error", error: "no matched posts" } });
    renderBoard();
    expect(await screen.findByText("↦ 推进复盘")).toBeTruthy();
    fireEvent.click(screen.getByText("↦ 推进复盘"));
    expect(await screen.findByText(/no matched posts/)).toBeTruthy();
    expect(screen.queryByText(/✓ 已推进/)).toBeNull();
  });
});

/* ============ 观察窗口 / 归因行(全量 + 连续翻) ============ */
describe("ProjectsBoardPage 行模块弹窗", () => {
  it("观察窗口:卡面 6 条 + 查看全量弹窗;单条详情 ‹#n/N› + ↓ 连续翻", async () => {
    renderBoard();
    expect(await screen.findByText("窗口项目 0")).toBeTruthy();
    fireEvent.click(screen.getByText(/≡ 查看全量 8 个/));
    expect(await screen.findByText("观察窗口 · 全量")).toBeTruthy();
    fireEvent.click(screen.getAllByText("窗口项目 1")[0]);
    expect(await screen.findByText("#2/8")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#3/8")).toBeTruthy();
  });

  it("归因端点失败 → KPI 第四卡 pending 带原因(绝不编数)", async () => {
    routeApi({ attr: new Error("boom") });
    renderBoard();
    expect((await screen.findAllByText("进行中项目")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/读取失败[:：]boom/)).length).toBeGreaterThan(0);
    expect(screen.queryByText("$6,496.49")).toBeNull();
  });
});

/* ============ open-project 管道 consume-reset(同项目二次 ⌘K 不再死点击) ============ */
describe("openProjectId consume-reset", () => {
  const boardProps = (openProjectId: string, onConsumeOpenProject: () => void) => (
    <ProjectsBoardPage
      data={data}
      filteredProjects={ROWS}
      viewMode="manager"
      apiToken="t"
      onSelectProject={noop}
      openProjectId={openProjectId}
      onConsumeOpenProject={onConsumeOpenProject}
    />
  );

  it("消费 openProjectId 即回执 onConsumeOpenProject;宿主清空后同 id 再来 → 再消费(二次打开)", async () => {
    const onConsume = vi.fn();
    const view = render(boardProps("7", onConsume));
    // 消费:详情打开(详情分支真发起 /api/marketing/projects/7)+ 回执一次
    await waitFor(() => expect(onConsume).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(calledPaths().some((p) => p.includes("/api/marketing/projects/7"))).toBe(true));
    // 宿主(CockpitApp)收到回执后清空管道 → 同一项目二次点开:id ""→"7" 再变 → effect 再触发。
    // 修复前 openLegacyProjectId 永不清空,第二次 setOpenLegacyProjectId("7") 是 no-op = 死点击。
    view.rerender(boardProps("", onConsume));
    view.rerender(boardProps("7", onConsume));
    await waitFor(() => expect(onConsume).toHaveBeenCalledTimes(2));
  });
});
