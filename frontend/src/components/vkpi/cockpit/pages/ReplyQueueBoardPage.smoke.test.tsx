import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// 回复队列改版冒烟(金样板 MarketVoice/Projects.smoke 同构):
// - 页壳:pagehead(回复队列 + 扫描新意向 + 刷新 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡 = 已载入队列真值(待起草/待回复/已回复/价格购买意向)+ kpi-series
//   按日真序列 sparkline + 真环比(上窗 0 → 该卡诚实无药丸);时序端点失败 →
//   四卡回落 spempty 虚线零药丸(诚实,不编时序);
// - 服务端分页(MarketVoice feed 先例):首页 limit=500&offset=0 + total 真分母;
//   >500 由全量弹窗「载入更多(已显 X/Y)」逐页追加;载入更多失败不吞已载入列表;
// - 旧页零丢失:状态过滤 chips(计数徽)、意向/语言/平台徽、扫描新意向真回执
//   (已扫/命中/新入队)、生成草稿/复制/标记已回/忽略 全真端点;
// - 动作纪律:端点真实返回才落状态;409(claimed_by_other/status_conflict)诚实透出
//   不写 ✓(gone 不写 ✓ 口径);标记带 expected_status 乐观锁;
// - 溯源:单条详情溯源链链回 vkpi_comments 源评论(幂等键回链),库节点开记录预览;
// - 布局键 vkpi-reply-queue-layout-v1 + 不传 apiToken 给板 → 绝不写账户级布局。
// mock seam:services/http.apiFetch(全页唯一网络出口),零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ReplyQueueBoardPage } from "./ReplyQueueBoardPage";
import { queueCounts } from "./ReplyQueueBoardPage.modules";
import type { ReplyQueueItem } from "../../../../services/vkpi/replyQueue-api";

const T = "2026-07-07T05:26:53Z";
const row = (over: Partial<ReplyQueueItem>): ReplyQueueItem => ({
  id: 0,
  platform: "instagram",
  kol_pool_id: null,
  comment_external_id: "ext-0",
  comment_text: "",
  intent_tag: "question",
  lang: "en",
  draft_reply: "",
  status: "pending",
  created_at: T,
  updated_at: T,
  ...over,
});

// 7 行覆盖四态 × 四意向 × 双平台 × 多语种;服务端排序 pending → drafted → 终态
const ITEMS: ReplyQueueItem[] = [
  row({ id: 1, comment_external_id: "ext-1", intent_tag: "price", comment_text: "Where can I buy this lens?" }),
  row({ id: 2, comment_external_id: "ext-2", intent_tag: "price", platform: "facebook", lang: "es", comment_text: "Cuanto cuesta?" }),
  row({ id: 3, comment_external_id: "ext-3", intent_tag: "compat", lang: "de", kol_pool_id: 42, comment_text: "Passt das an Nikon Z?" }),
  row({ id: 4, comment_external_id: "ext-4", comment_text: "How many frames per second?" }),
  row({ id: 5, comment_external_id: "ext-5", status: "drafted", draft_reply: "Thanks for your interest in Viltrox!", comment_text: "Is there a coupon?" }),
  row({ id: 6, comment_external_id: "ext-6", status: "replied", intent_tag: "manual", platform: "facebook", lang: "ja", comment_text: "値段はいくらですか" }),
  row({ id: 7, comment_external_id: "ext-7", status: "dismissed", lang: "ko", comment_text: "가격이 얼마인가요" }),
];

// kpi-series 缺省 mock:三日真序列 + 真环比(drafted 上窗 0 → delta null,该卡诚实无药丸)
const pts = (nums: number[]) => nums.map((n, i) => ({ date: `2026-07-${String(10 + i).padStart(2, "0")}`, count: n }));
const KPI_SERIES = {
  status: "ready",
  granularity: "day",
  days: 3,
  window: { since: "2026-07-10T00:00:00Z", until: "2026-07-12T08:30:00Z" },
  series: {
    enqueued: pts([2, 3, 2]),
    pending: pts([1, 2, 1]),
    drafted: pts([0, 1, 0]),
    replied: pts([0, 1, 1]),
    price: pts([1, 1, 0]),
  },
  prev: {
    enqueued: { current: 7, previous: 4, delta_pct: 75 },
    pending: { current: 4, previous: 2, delta_pct: 100 },
    drafted: { current: 1, previous: 0, delta_pct: null },
    replied: { current: 2, previous: 1, delta_pct: 100 },
    price: { current: 2, previous: 4, delta_pct: -50 },
  },
};

type Overrides = {
  /** 值 / Error / 按 offset 出页的函数(服务端分页仿真) */
  list?: unknown;
  kpi?: unknown;
  draft?: unknown;
  mark?: unknown;
  screen?: unknown;
};

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    if (p.startsWith("/api/admin/vkpi/reply-queue/kpi-series") && method === "GET") {
      const value = overrides.kpi ?? KPI_SERIES;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/reply-queue/screen") && method === "POST") {
      const value = overrides.screen ?? { ok: true, scanned: 800, matched: 12, enqueued: 3 };
      if (value instanceof Error) throw value;
      return value;
    }
    const draftMatch = p.match(/\/api\/admin\/vkpi\/reply-queue\/(\d+)\/draft$/);
    if (draftMatch && method === "POST") {
      const id = Number(draftMatch[1]);
      const value =
        overrides.draft ??
        { ok: true, id, status: "drafted", provider: "template", draft_reply: `DRAFT-${id}`, retrieved_skus: ["AF85"] };
      if (value instanceof Error) throw value;
      return value;
    }
    const markMatch = p.match(/\/api\/admin\/vkpi\/reply-queue\/(\d+)\/mark\?(.+)$/);
    if (markMatch && method === "POST") {
      const value = overrides.mark ?? { ok: true, id: Number(markMatch[1]), status: new URLSearchParams(markMatch[2]).get("status") };
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/reply-queue") && method === "GET") {
      const offset = Number(new URLSearchParams(p.split("?")[1] || "").get("offset")) || 0;
      const value =
        typeof overrides.list === "function"
          ? (overrides.list as (offset: number) => unknown)(offset)
          : overrides.list ?? { items: ITEMS, count: ITEMS.length, total: ITEMS.length, offset, limit: 500 };
      if (value instanceof Error) throw value;
      return value;
    }
    throw new Error(`unexpected apiFetch: ${method} ${p}`);
  });
}

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));
const renderBoard = () => render(<ReplyQueueBoardPage apiToken="t" />);
const err409 = (reason: string) => Object.assign(new Error(reason), { detail: reason, status: 409 });

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

/* ============ 口径函数单测(KPI/图表同一份计数) ============ */
describe("queueCounts(同一份口径函数)", () => {
  it("四态/意向/平台/语言计数与降序真值", () => {
    const c = queueCounts(ITEMS);
    expect(c.total).toBe(7);
    expect(c.byStatus).toEqual({ pending: 4, drafted: 1, replied: 1, dismissed: 1 });
    expect(c.byIntent[0]).toEqual({ key: "question", label: "问询", count: 3 });
    expect(c.byIntent.find((it) => it.key === "price")?.count).toBe(2);
    expect(c.byIntent.find((it) => it.key === "manual")?.label).toBe("手动入队");
    expect(c.byPlatform[0]).toEqual({ key: "instagram", count: 5 });
    expect(c.byLang[0]).toEqual({ key: "en", count: 3 });
  });
});

/* ============ 页壳 + KPI 带 + 注册表 ============ */
describe("ReplyQueueBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡真值 + kpi-series 真时序:sparkline ×4;环比药丸 3 枚(drafted 上窗 0 诚实无药丸)", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect((await screen.findAllByText("待起草")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("已回复").length).toBeGreaterThan(0);
    expect(screen.getAllByText("价格/购买意向").length).toBeGreaterThan(0);
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    // kpi-series ready → 四卡真 sparkline,零 spempty 虚线
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(4));
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(0);
    // 真环比:pending +100 / replied +100 / price -50;drafted 上窗 0 → null 无药丸
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(3);
    expect(document.querySelectorAll(".ds-kpi__delta--down").length).toBe(1);
    // 首页:status='' + limit=500 + offset=0(服务端分页);时序端点独立拉取
    expect(calledPaths().some((p) => p === "/api/admin/vkpi/reply-queue?limit=500&offset=0")).toBe(true);
    expect(calledPaths().some((p) => p === "/api/admin/vkpi/reply-queue/kpi-series?days=30")).toBe(true);
  });

  it("kpi-series 失败 → 四卡诚实回落 spempty 虚线零药丸(数值仍为已载入真值,绝不编时序)", async () => {
    routeApi({ kpi: err409("series boom") });
    renderBoard();
    expect((await screen.findAllByText("待起草")).length).toBeGreaterThan(0);
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4));
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
  });

  it("默认布局四模块在场;palette 备选(平台/语言分布)不进默认;编辑布局可从 palette 添加", async () => {
    renderBoard();
    expect(await screen.findByText("队列总览")).toBeTruthy();
    ["回复队列", "意向构成", "处理进度"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("平台分布")).toBeNull();
    expect(screen.queryByText("语言分布")).toBeNull();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(screen.getAllByText("平台分布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("语言分布").length).toBeGreaterThan(0);
  });

  it("布局键 vkpi-reply-queue-layout-v1 生效;不传 apiToken 给板 → 绝不写账户级布局", async () => {
    window.localStorage.setItem("vkpi-reply-queue-layout-v1", JSON.stringify([{ moduleKey: "kpiQ", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("队列总览")).toBeTruthy();
    expect(screen.queryByText("意向构成")).toBeNull();
    expect(screen.queryByText("处理进度")).toBeNull();
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });

  it("端点失败 → 诚实错误卡(绝不编数据);空队列 → 诚实空态引导扫描", async () => {
    routeApi({ list: err409("boom") });
    const first = renderBoard();
    expect((await screen.findAllByText("reply-queue 读取失败")).length).toBeGreaterThan(0);
    first.unmount();
    routeApi({ list: { items: [], count: 0 } });
    renderBoard();
    expect(await screen.findByText(/队列 0 条。点右上「扫描新意向」/)).toBeTruthy();
  });
});

/* ============ 旧功能零丢失(状态过滤 / 扫描回执 / 徽) ============ */
describe("ReplyQueueBoardPage 旧功能零丢失", () => {
  it("状态过滤 chips:默认待起草 4 行;切「全部」7 条(卡面 6 + 查看全量);切「已忽略」1 行", async () => {
    renderBoard();
    expect(await screen.findByText("Where can I buy this lens?")).toBeTruthy();
    // 默认 pending:drafted 行不在卡面
    expect(screen.queryByText("Is there a coupon?")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /全部/ }));
    expect(await screen.findByText("Is there a coupon?")).toBeTruthy();
    expect(screen.getByText(/≡ 查看全量 7 条/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^已忽略\s?1$/ }));
    expect(await screen.findByText("가격이 얼마인가요")).toBeTruthy();
    expect(screen.queryByText("Where can I buy this lens?")).toBeNull();
  });

  it("意向徽零丢失:price/compat/question + 市场之声单条转入的 manual=手动入队", async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: /全部/ }));
    expect((await screen.findAllByText("价格/购买")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("兼容/型号").length).toBeGreaterThan(0);
    expect(screen.getAllByText("问询").length).toBeGreaterThan(0);
    expect(screen.getAllByText("手动入队").length).toBeGreaterThan(0);
  });

  it("扫描新意向:POST /reply-queue/screen 真回执(已扫/命中/新入队)+ 成功后重拉列表", async () => {
    renderBoard();
    expect(await screen.findByText("Where can I buy this lens?")).toBeTruthy();
    const firstPage = "/api/admin/vkpi/reply-queue?limit=500&offset=0";
    const listCallsBefore = calledPaths().filter((p) => p === firstPage).length;
    fireEvent.click(screen.getByText("⌁ 扫描新意向"));
    expect(await screen.findByText(/已扫 800 条 · 命中 12 · 新入队 3/)).toBeTruthy();
    await waitFor(() => {
      const after = calledPaths().filter((p) => p === firstPage).length;
      expect(after).toBeGreaterThan(listCallsBefore);
    });
  });

  it("扫描失败 → 错误原样透出,不摆假回执", async () => {
    routeApi({ screen: err409("screen boom") });
    renderBoard();
    expect(await screen.findByText("Where can I buy this lens?")).toBeTruthy();
    fireEvent.click(screen.getByText("⌁ 扫描新意向"));
    expect(await screen.findByText("扫描新意向失败")).toBeTruthy();
    expect(screen.queryByText(/已扫/)).toBeNull();
  });
});

/* ============ 服务端分页(>500 队列 · 弹窗「载入更多(已显 X/Y)」) ============ */
describe("ReplyQueueBoardPage 服务端分页", () => {
  const EXTRA = [
    row({ id: 8, comment_external_id: "ext-8", comment_text: "EXTRA-8" }),
    row({ id: 9, comment_external_id: "ext-9", comment_text: "EXTRA-9", status: "drafted" }),
  ];
  const paged = (offset: number) =>
    offset === 0
      ? { items: ITEMS, count: ITEMS.length, total: 9, offset: 0, limit: 500 }
      : { items: EXTRA, count: EXTRA.length, total: 9, offset, limit: 500 };

  it("载入更多(已显 7/9)追加下一页:offset=已载入行数;拉齐后按钮消失", async () => {
    routeApi({ list: paged });
    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: /全部/ }));
    // 卡面入口如实标注已载入/总数
    fireEvent.click(await screen.findByText(/≡ 查看全量 7 条\(队列已载入 7\/9\)/));
    expect(await screen.findByText("回复队列 · 全量")).toBeTruthy();
    // 第二页未载入:EXTRA 行不在,载入更多按钮带真分母
    expect(screen.queryByText("EXTRA-8")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^≡ 载入更多\(已显 7\/9\)/ }));
    expect(await screen.findByText("EXTRA-8")).toBeTruthy();
    expect(screen.getByText("EXTRA-9")).toBeTruthy();
    // 追加页走 offset=7(服务端分页真参数)
    expect(calledPaths().some((p) => p === "/api/admin/vkpi/reply-queue?limit=500&offset=7")).toBe(true);
    // 拉齐(9/9)→ hasMore=false,按钮消失
    expect(screen.queryByRole("button", { name: /^≡ 载入更多/ })).toBeNull();
  });

  it("载入更多失败:已载入列表照常渲染 + 行内错误 + 可重试(失败不吞列表)", async () => {
    routeApi({ list: (offset: number) => (offset === 0 ? paged(0) : err409("page boom")) });
    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: /全部/ }));
    fireEvent.click(await screen.findByText(/≡ 查看全量 7 条/));
    fireEvent.click(await screen.findByRole("button", { name: /^≡ 载入更多/ }));
    expect(await screen.findByText(/载入更多失败:page boom/)).toBeTruthy();
    // 已载入 7 行照常在列表里(整卡只留零数据才清)
    expect(screen.getAllByText("Where can I buy this lens?").length).toBeGreaterThan(0);
    // 按钮仍在 → 可重试
    expect(screen.getByRole("button", { name: /^≡ 载入更多/ })).toBeTruthy();
  });

  it("total=已载入(≤500 小队列)→ 零「载入更多」,行为与旧全量单页一致", async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: /全部/ }));
    fireEvent.click(await screen.findByText(/≡ 查看全量 7 条/));
    expect(await screen.findByText("回复队列 · 全量")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^≡ 载入更多/ })).toBeNull();
  });
});

/* ============ 闭环动作(草稿/复制/标记;gone 不写 ✓) ============ */
describe("ReplyQueueBoardPage 闭环动作", () => {
  it("生成草稿:POST /{id}/draft 真端点;服务器返回的草稿/状态才落地", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Where can I buy this lens?"));
    expect(await screen.findByText(/#1 \/ 4/)).toBeTruthy();
    // 无草稿时复制钮 disabled(v0 铁律:先起草再人工复制)
    expect((screen.getByText("⧉ 复制草稿") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("✎ 生成草稿"));
    await waitFor(() => expect(calledPaths().some((p) => p.endsWith("/reply-queue/1/draft"))).toBe(true));
    expect(await screen.findByText("DRAFT-1")).toBeTruthy();
    expect(screen.getByText("✎ 重新起草")).toBeTruthy();
    expect((screen.getByText("⧉ 复制草稿") as HTMLButtonElement).disabled).toBe(false);
  });

  it("草稿 409(claimed_by_other):不写草稿不置绿,原因诚实透出", async () => {
    routeApi({ draft: err409("claimed_by_other") });
    renderBoard();
    fireEvent.click(await screen.findByText("Where can I buy this lens?"));
    fireEvent.click(await screen.findByText("✎ 生成草稿"));
    expect(await screen.findByText(/claimed_by_other/)).toBeTruthy();
    expect(screen.queryByText(/DRAFT-1/)).toBeNull();
    expect(screen.queryByText("✎ 重新起草")).toBeNull();
  });

  it("标记已回:POST mark 带 expected_status 乐观锁;真实返回才置 ✓ 已回复", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Where can I buy this lens?"));
    fireEvent.click(await screen.findByText("标记已回"));
    await waitFor(() =>
      expect(calledPaths().some((p) => p.endsWith("/reply-queue/1/mark?status=replied&expected_status=pending"))).toBe(true),
    );
    expect(await screen.findByText("✓ 已回复")).toBeTruthy();
  });

  it("标记 409(status_conflict):不写 ✓,冲突诚实透出", async () => {
    routeApi({ mark: err409("status_conflict") });
    renderBoard();
    fireEvent.click(await screen.findByText("Where can I buy this lens?"));
    fireEvent.click(await screen.findByText("标记已回"));
    expect(await screen.findByText(/status_conflict/)).toBeTruthy();
    expect(screen.queryByText("✓ 已回复")).toBeNull();
  });
});

/* ============ 溯源 + 全量列表 + 连续翻 ============ */
describe("ReplyQueueBoardPage 溯源与连续翻", () => {
  it("详情溯源链:源评论 vkpi_comments 回链 + 队列行库节点开记录预览;KOL 命中带身份节点", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("Passt das an Nikon Z?"));
    expect(await screen.findByText(/源评论 vkpi_comments · IG/)).toBeTruthy();
    expect(screen.getByText(/身份 KOL #42/)).toBeTruthy();
    fireEvent.click(screen.getByText("队列行 vkpi_reply_queue #3"));
    expect(await screen.findByText("库记录预览 · 点其他节点切换")).toBeTruthy();
    expect(screen.getAllByText("vkpi_reply_queue").length).toBeGreaterThan(0);
    // 源评论节点 → 幂等键回链口径
    fireEvent.click(screen.getByText(/源评论 vkpi_comments · IG/));
    expect(await screen.findByText("ext-3")).toBeTruthy();
  });

  it("全量列表弹窗 + 单条详情 ‹#n/N› + ↑ 方向键连续翻(id 快照冻结)", async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole("button", { name: /全部/ }));
    fireEvent.click(await screen.findByText(/≡ 查看全量 7 条/));
    expect(await screen.findByText("回复队列 · 全量")).toBeTruthy();
    // 第 7 行只在弹窗里(卡面收敛 6 条)
    fireEvent.click(screen.getAllByText("가격이 얼마인가요")[0]);
    expect(await screen.findByText(/#7 \/ 7/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByText(/#6 \/ 7/)).toBeTruthy();
    // 终态行(已回复)不回炉:起草钮 disabled + 诚实 title
    const draftBtn = screen.getByText("✎ 生成草稿") as HTMLButtonElement;
    expect(draftBtn.disabled).toBe(true);
    expect(draftBtn.title).toContain("终态");
  });
});
