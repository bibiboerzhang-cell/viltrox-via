import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// GTM Command 板块页范式改版冒烟(金样板 MarketVoicePage/MyKolBoardPage.smoke 同构):
// - 页壳:pagehead(GTM Command + SKU 徽 + 编辑布局钮)+ 作战控件行 + 可编辑看板;
// - KPI 带四卡:现值全真(本周信号/产品机会=summary;待执行/增长押注=actions-inbox
//   近 50 条窗口),GTM 域无时序端点 → 四卡 spempty 诚实虚线零药丸;
// - 默认八行:route 全局态 Top 机会 +「生成路线」一键出预览(高频动作少一层点击);
//   thesis/forecast/roadmap/todayActions 无 SKU = 诚实 pending;
// - 生成行动流:预演(dry_run=true 零写库)→ 确认落库(dry_run=false,人审回执);
// - palette 备选八件(opps/actions/sim/dealer/officialPlan/indie/mix/footnote);
// - 布局键 vkpi-gtm-command-layout-v1 + 不传 apiToken → 绝不写账户级 dashboard 布局;
// - 【总脑纯读红线】全程绝不调用 marketing-brain/daily 与 market/trends(隐藏写入)。
// mock seam:services/http.apiFetch(全页唯一网络出口,useCachedGet/embeds 同缝)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { GtmCommandBoardPage } from "./GtmCommandBoardPage";

const SUMMARY_OK = {
  weekly_signals: {
    items: [
      { signal: "85mm 人像话题热度上行", kind: "brand_pulse", freshness: "3 天内", sample_size: 120, confidence: "medium" },
      { signal: "US 赛道位次进入前三", kind: "race", freshness: "7 天内", sample_size: 40, confidence: "high" },
      { signal: "对焦马达噪音抱怨走高", kind: "voice", freshness: "3 天内", sample_size: 18, confidence: "low" },
    ],
    sources_note: "外部信号雷达 GTM-5 待接入",
    status: "ready",
  },
  product_opportunities: {
    items: [
      { sku: "VL-LEN072", market: "US", persona: "portrait creator", content_angle: "实拍对比", opportunity_score: 74, basis: "赛道位次 × 内容表现" },
      { sku: "AF-56MM-F12", market: "JP", persona: "vlog", content_angle: "夜景实测", opportunity_score: 61, basis: "评论热词" },
    ],
    note: "",
    status: "ready",
  },
  recommended_actions: {
    items: [
      { action: "跟进超期寄样 5 单", reason: "寄样超期", evidence_summary: "assignments 超期行", cost_note: "$0", risk: "低", expected_gain: "内容产出", ref: "inbox#12" },
    ],
    note: "",
    status: "ready",
  },
  strategy_defaults: { sku_hint: "VL-LEN072", budget_hint: 3000, note: "", status: "ready" },
  learning_digest: {
    validated: ["85mm × US 人像转化假设成立"],
    effective_styles: ["实拍对比长测"],
    dropped_channels: [],
    next_change: "上调 YouTube 长测权重",
    honesty_note: "样本 2 条,结论可被新证据推翻",
    status: "ready",
  },
  generated_at: "2026-07-12T02:00:00Z",
};

// 近 50 条建议窗口:6 条 · gtm_bet 2 条(KPI 带 K3=6 / K4=2 同源)
const INBOX_OK = {
  items: [
    { id: 1, category: "gtm_bet", title: "押注:美国人像 KOL", status: "suggested" },
    { id: 2, category: "gtm_bet", title: "押注:官号首发", status: "suggested" },
    { id: 3, category: "failed_retry", title: "重试 A", status: "suggested" },
    { id: 4, category: "failed_retry", title: "重试 B", status: "suggested" },
    { id: 5, category: "content_candidate", title: "内容确认 C", status: "suggested" },
    { id: 6, category: "inventory_low", title: "库存预警 D", status: "suggested" },
  ],
  available: true,
  today_summary: { today_executed_count: 1 },
};

const NORTHSTAR_OK = {
  status: "ok",
  metrics: {
    launch_briefs: { value: 1 },
    dealers: { value: 0 },
    verdict_rate: { value: 0, decided: 0, total: 2 },
  },
  generated_at: "2026-07-12T02:00:00Z",
};

const HEALTH_OK = { trust: { sha_aligned: true, server_git_sha: "1a6c4453abc", db_migration_max: 241, worker_online: true } };
const SCHED_OK = { status: { total: 60, enabled: 12 } };
const RUNTIME_OK = { requests_persisted: { available: true } };

const PLAN_OK = {
  public_plan: {
    thesis: {
      go_nogo: "GO",
      market: "US",
      persona: "portrait creator",
      mainline: "KOL 实测内容主线",
      confidence: "medium",
      basis_summary: "赛道位次 × 内容表现 × 人群画像",
      status: "ok",
    },
    forecast: [
      {
        horizon_days: 30,
        statement: "30 天内曝光过 50 万",
        signals_summary: "近 30 天信号走强",
        confidence: "medium",
        escalate_if: "7 天曝光 ≥ 100000",
        retreat_if: "14 天曝光 < 20000",
      },
    ],
    roadmap: {
      w1: { items: ["寄样 5 位候选"], channels: [{ channel: "KOL", play: "首发实测" }] },
      w2_4: { items: [], channels: [] },
      m2_3: { items: [], channels: [] },
    },
    market_opportunity: { items: [{ name: "US 人像赛道", basis: "位次前三" }] },
    kol_candidates: { items: [{ display_name: "Alpha Cam", why_fit: "人群契合", confidence: "high", risk_labels: ["合作费高"] }] },
    dealer_targets: { items: [], status: "data_missing", note: "Dealer 数据未导入(GTM-2 激活)" },
    official_channel_actions: { items: [] },
    shopify_indie_site_actions: { items: [] },
    content_angles: { items: [{ angle: "85mm 人像 vs 原厂", reason: "评论热词" }] },
    budget_mix: { items: [{ name: "头部集中", split: "70/30" }] },
    risks: ["库存未核验"],
    data_gaps: ["vkpi_dealers 0 行"],
    action_inbox_items: [
      { action: "寄样 Alpha Cam", reason: "fit 契合", evidence_summary: "深析产物", cost_note: "$120", risk: "低", expected_gain: "1 条实测视频", ref: "bet#1" },
    ],
    success_metrics: [{ metric: "7 天曝光", threshold: "100000", confidence: "medium", basis: "规则库" }],
  },
  meta: { generated_at: "2026-07-12T03:00:00Z", coverage: { kol: "ready" }, data_gaps: ["dealers 0 行"] },
};

const MAT_DRY_OK = {
  ok: true,
  status: "ok",
  dry_run: true,
  gtm_plan_id: "gp-1",
  sku: "VL-LEN072",
  goal: "conversion",
  bets_total: 3,
  already_present: 1,
  would_insert: 2,
  skipped_incomplete: [],
  requires_approval_all: true,
  bets: [
    { dedupe_key: "bet:a", title: "押注:美国人像 KOL 实测", requires_approval: true },
    { dedupe_key: "bet:b", title: "押注:官号首发承接", requires_approval: true },
  ],
};

const MAT_PERSIST_OK = {
  ok: true,
  status: "ok",
  dry_run: false,
  gtm_plan_id: "gp-1",
  sku: "VL-LEN072",
  goal: "conversion",
  bets_total: 3,
  already_present: 1,
  persisted: 2,
  inserted_new: 2,
  skipped_incomplete: [],
  requires_approval_all: true,
  bets: [],
};

function routeApi(overrides: { summary?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: { body?: unknown }) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/market-brain/summary")) {
      const value = overrides.summary ?? SUMMARY_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/market-brain/gtm-plan/preview")) return PLAN_OK;
    if (p.startsWith("/api/admin/vkpi/market-brain/gtm-plan/materialize")) {
      const body = JSON.parse(String(init?.body || "{}"));
      return body.dry_run === false ? MAT_PERSIST_OK : MAT_DRY_OK;
    }
    if (p.startsWith("/api/admin/vkpi/sku/list")) {
      return { items: [{ sku: "VL-LEN072", model_name: "AF 35mm F1.2 LAB FE", marketing_name: "", category_main: "lens", mount: "FE", price_usd: 999 }] };
    }
    if (p.startsWith("/api/admin/vkpi/actions/inbox")) return INBOX_OK;
    if (p.startsWith("/api/admin/vkpi/gtm/northstar")) return NORTHSTAR_OK;
    if (p.startsWith("/health")) return HEALTH_OK;
    if (p.startsWith("/api/admin/vkpi/settings/scheduler-tasks")) return SCHED_OK;
    if (p.startsWith("/api/admin/runtime/metrics")) return RUNTIME_OK;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

// useCachedGet/cachedApiFetch 按 path+token 全局缓存 —— 每测独立 token 防串缓存
let tokenSeq = 0;
const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<GtmCommandBoardPage apiToken={`t${++tokenSeq}`} {...(props as any)} />);

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  routeApi();
});

describe("GtmCommandBoardPage smoke(页壳 + KPI 带真数 + 默认八行 + 生成行动流 + 布局键)", () => {
  it("KPI 带四卡现值全真(同源 actions-inbox);GTM 域无时序 → 四卡诚实虚线零药丸;纯读红线全程守住", async () => {
    expect(() => renderBoard()).not.toThrow();
    // 「本周信号」双出现 = KPI 卡标签 + 模块标题(同名同源)
    expect((await screen.findAllByText("本周信号")).length).toBeGreaterThan(0);
    await screen.findAllByText("待执行动作");

    await waitFor(() => {
      const vals = [...document.querySelectorAll(".ds-kpi__val")].map((el) => (el.textContent || "").trim());
      expect(vals).toContain("3条"); // 本周信号 = summary.weekly_signals 3 条
      expect(vals).toContain("2个"); // 产品机会 = product_opportunities 2 个
      expect(vals).toContain("6条"); // 待执行动作 = inbox 近 50 条窗口 6 条
      expect(vals).toContain("2条"); // 增长押注 = category=gtm_bet 2 条
    });
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    // 无按日时序端点:四卡全 spempty 虚线,零 sparkline 零环比药丸(不编序列)
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // pagehead + 控件行(按钮动词直说)
    expect(screen.getByText("GTM Command")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
    expect(screen.getByText("生成作战预览")).toBeTruthy();

    // 【总脑纯读红线】marketing-brain/daily 与 market/trends 全程零调用
    const paths = calledPaths();
    expect(paths.some((pp) => pp.includes("marketing-brain/daily"))).toBe(false);
    expect(paths.some((pp) => pp.includes("market/trends"))).toBe(false);
    expect(paths.some((pp) => pp.startsWith("/api/admin/vkpi/market-brain/summary"))).toBe(true);
    expect(paths.some((pp) => pp.startsWith("/api/admin/vkpi/actions/inbox"))).toBe(true);
  });

  it("默认八行:全局态真身(路线 Top 机会/信号/队列/健康/北极星/复盘)+ 预览模块诚实 pending;palette 不进默认", async () => {
    renderBoard();
    expect(await screen.findByText("今日增长路线")).toBeTruthy();

    // route 全局态:Top 机会行 + 一键「生成路线」(高频动作直达)
    expect(await screen.findByText("VL-LEN072")).toBeTruthy();
    expect((await screen.findAllByText("生成路线")).length).toBe(2);
    expect(screen.getAllByText(/机会分 74/).length).toBeGreaterThan(0);

    // signals 真身:kind 徽 + freshness + 样本
    expect(screen.getAllByText("85mm 人像话题热度上行").length).toBeGreaterThan(0);
    expect(screen.getAllByText("样本 120").length).toBeGreaterThan(0);

    // queue 真身:类型 chips + 跳 Action Inbox + 今日已执行真数
    expect(await screen.findByText("失败重试")).toBeTruthy();
    expect(screen.getAllByText("增长押注").length).toBeGreaterThan(0);
    expect(screen.getByText("去 Action Inbox 人审")).toBeTruthy();
    expect(screen.getByText("今日已执行 1 条")).toBeTruthy();

    // 复盘学习真身 + 北极星 + 系统健康(embeds)
    expect(screen.getByText("85mm × US 人像转化假设成立")).toBeTruthy();
    expect(screen.getByText(/下次推荐怎么变/)).toBeTruthy();
    expect(screen.getAllByText("90 天北极星").length).toBeGreaterThan(0);
    expect(screen.getAllByText("系统健康").length).toBeGreaterThan(0);

    // 预览四模块:无 SKU = 诚实 pending 短句(thesis/forecast/roadmap/todayActions)
    expect(screen.getAllByText("选 SKU 生成作战预览后点亮。").length).toBe(4);
    // bets 七段:仅「构想」点亮(卡头 cnt 徽 + 段标签双出现)
    expect(screen.getAllByText("构想").length).toBeGreaterThan(0);
    expect(screen.getByText("沉淀")).toBeTruthy();

    // palette 备选不进默认布局
    expect(screen.queryByText("策略模拟器")).toBeNull();
    expect(screen.queryByText("Dealer 适配")).toBeNull();
    expect(screen.queryByText("官号内容计划")).toBeNull();
    expect(screen.queryByText("独立站承接")).toBeNull();
    expect(screen.queryByText("渠道组合")).toBeNull();
    expect(screen.queryByText("风险与缺口")).toBeNull();
    expect(screen.queryByText("建议行动")).toBeNull();
  });

  it("一键生成路线 → 作战预览点亮:路线链/主判断/预判/路线图/今日行动全真;生成行动流 dry-run → 落库人审回执", async () => {
    const onNavigate = vi.fn();
    renderBoard({ onNavigate });
    const buttons = await screen.findAllByText("生成路线");
    fireEvent.click(buttons[0]);

    // 路线链 + 决策徽
    expect(await screen.findByText("决策 GO")).toBeTruthy();
    expect(screen.getAllByText("KOL 实测内容主线").length).toBeGreaterThan(0);
    // 主判断 2×2 + 依据
    expect(screen.getByText("该不该推")).toBeTruthy();
    expect(screen.getByText(/判断依据:赛道位次/)).toBeTruthy();
    // 条件化预判四段式(缺条件才标「条件缺失」,本例齐全)
    expect(screen.getByText("30 天内曝光过 50 万")).toBeTruthy();
    expect(screen.getByText("7 天曝光 ≥ 100000")).toBeTruthy();
    expect(screen.queryByText("条件缺失")).toBeNull();
    // 路线图:W1 段 + 渠道五段(Dealer 未导入如实标)
    expect(screen.getByText("W1 · 首周点火")).toBeTruthy();
    expect(screen.getByText("Dealer 数据未导入(GTM-2 激活)")).toBeTruthy();
    // Bet 七段:点亮到「预览」
    expect(screen.getAllByText("预览").length).toBeGreaterThan(0);

    // 生成行动流:预演(dry_run=true 零写库)→ 对账真数
    fireEvent.click(screen.getByText("生成行动(预演)"));
    expect(await screen.findByText(/预演对账:共 3 条 bet · 可新增 2 条 · 已存在 1 条/)).toBeTruthy();
    expect(screen.getByText("押注:美国人像 KOL 实测")).toBeTruthy();

    // 确认落库(dry_run=false)→ 人审回执 + 跳 Action Inbox
    fireEvent.click(screen.getByText("确认落库(新增 2 条)"));
    expect(await screen.findByText(/已落库:新增 2 条 · 已存在 1 条未重插/)).toBeTruthy();
    fireEvent.click(screen.getByText("去 Action Inbox 人审 →"));
    expect(onNavigate).toHaveBeenCalledWith("dashboard");

    // materialize 恰两次:先 dry_run=true 后 false(人审红线同向)
    const matCalls = apiFetchMock.mock.calls.filter((c) => String(c[0]).includes("gtm-plan/materialize"));
    expect(matCalls.length).toBe(2);
    const bodies = matCalls.map((c) => JSON.parse(String((c[1] as { body?: unknown })?.body || "{}")));
    expect(bodies[0].dry_run).toBe(true);
    expect(bodies[1].dry_run).toBe(false);
    expect(bodies[1].sku).toBe("VL-LEN072");
  });

  it("summary 失败 = 全局模块诚实错误卡;预览模块不受拖累仍是 pending", async () => {
    routeApi({ summary: new Error("boom") });
    renderBoard();
    expect((await screen.findAllByText("market-brain/summary 读取失败")).length).toBeGreaterThan(0);
    // 预览模块不吃 summary:仍是「选 SKU」pending,不是错误卡
    expect(screen.getAllByText("选 SKU 生成作战预览后点亮。").length).toBe(4);
    // 队列模块独立源(actions/inbox),照常真身
    expect(await screen.findByText("失败重试")).toBeTruthy();
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出八个备选", async () => {
    renderBoard();
    expect(await screen.findByText("今日增长路线")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    // 「产品机会」双出现 = KPI 卡标签 + palette 条目
    await waitFor(() => expect(screen.getAllByText("产品机会").length).toBeGreaterThan(1));
    expect(screen.getByText("建议行动")).toBeTruthy();
    expect(screen.getByText("策略模拟器")).toBeTruthy();
    expect(screen.getByText("Dealer 适配")).toBeTruthy();
    expect(screen.getByText("官号内容计划")).toBeTruthy();
    expect(screen.getByText("独立站承接")).toBeTruthy();
    expect(screen.getByText("渠道组合")).toBeTruthy();
    expect(screen.getByText("风险与缺口")).toBeTruthy();
  });

  it("布局键 vkpi-gtm-command-layout-v1 生效;不传 apiToken 给板组件 → 绝不写账户级 dashboard 布局", async () => {
    window.localStorage.setItem("vkpi-gtm-command-layout-v1", JSON.stringify([{ moduleKey: "kpiG", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("本周信号")).toBeTruthy();
    expect(screen.queryByText("今日增长路线")).toBeNull();
    expect(screen.queryByText("增长路线图")).toBeNull();
    expect(screen.queryByText("Bet 生命周期")).toBeNull();
    expect(calledPaths().some((pp) => pp.includes("preference"))).toBe(false);
  });
});
