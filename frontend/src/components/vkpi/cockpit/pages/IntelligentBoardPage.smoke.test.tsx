import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Intelligent 问答板块页改版冒烟(金样板 MarketVoicePage.smoke / CreativeLibraryBoardPage.smoke 同构):
// - 页壳:pagehead(标题 + 可编辑看板徽 + 编辑布局钮)+ 可编辑看板(布局键
//   vkpi-intelligent-layout-v1;不传 apiToken → 绝不写账户级 dashboard_layout_v1);
// - KPI 带四卡:会话数/今日问答/命中引用率 = 本机留痕真数(0 也如实 0,无时序 →
//   spempty 诚实虚线零环比药丸;零留痕 → 引用率诚实 pending);综合回答 = /stats
//   服务端真数 + 14 天真按日 sparkline(端点失败 → 诚实 pending);
// - 问答旧功能零丢失:输入 + Enter/按钮 + 思考中、建议 chips 点击即问、答案卡
//   (车道徽/当日缓存徽/结论/动作直跳)、证据三轨(intent 表格/search 候选/synth);
// - 引用来源标注(重点):回答旁引用 chip 可点开证据弹窗;检索候选带 kol_pool_id
//   可跳 KOL 档案(sessionStorage + onNavigate);无库内引用如实明标;
// - 历史会话:成功回答入本机留痕(localStorage);卡面 6 条 + 全量弹窗 → 详情
//   ‹ #n/N › + ↑↓ 连续翻 + 重新提问;两步确认清空(可反悔);
// - 诚实态:无 token 零请求 / ask 失败原样透出 / suggestions·stats 失败诚实降级。
// mock seam:services/http.apiFetch(全页唯一网络出口),按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { IntelligentBoardPage } from "./IntelligentBoardPage";
import { HISTORY_KEY, type AskHistoryEntry } from "./IntelligentBoardPage.modules";

/* ---------- 三车道答案样本(形状对齐 vkpi_intelligent.py _answer 出参) ---------- */
const SEARCH_ANSWER = {
  answer: "未命中固定问法,已按关键词检索到 2 个候选。",
  mode: "search",
  cached: false,
  evidence: [
    {
      kind: "search_results",
      count: 2,
      results: [
        { name: "Alpha Cam", platform: "youtube", kol_pool_id: 101 },
        { name: "beta-shooter", platform: "tiktok" },
      ],
      provider_status: {},
    },
  ],
  actions: [{ label: "去 KOL 池检索", route: "kol-pool" }],
};

const INTENT_ANSWER = {
  answer: "命中「近30天优先跟进 KOL」:返回 1 行。",
  mode: "intent",
  cached: false,
  evidence: [
    {
      kind: "intent_result",
      intent: "top_kols",
      title: "近30天优先跟进 KOL",
      columns: ["name", "score"],
      rows: [{ name: "Alpha", score: 88 }],
      sql_explain: "top kols by recent engagement",
    },
  ],
  actions: [{ label: "打开问数页查看完整结果", route: "dataQuery" }],
};

const SYNTH_ANSWER = {
  answer: "结论:近30天转化承压。要点:1)流量结构偏移;2)新品期波动。",
  mode: "synth",
  cached: true,
  evidence: [
    { kind: "search_results", count: 0, results: [], provider_status: {} },
    { kind: "synth", provider: "google", model: "gemini-flash-latest" },
  ],
  actions: [{ label: "去 KOL 池检索", route: "kol-pool" }],
};

const DEGRADED_ANSWER = {
  answer: "综合分析降级,已回退到检索结果。未命中固定问法,当前池内没有匹配的候选。",
  mode: "degraded",
  cached: false,
  evidence: [{ kind: "search_results", count: 0, results: [], provider_status: {} }],
  actions: [{ label: "去 KOL 池检索", route: "kol-pool" }],
};

function answerFor(question: string) {
  if (question.includes("告警")) return INTENT_ANSWER;
  if (question.includes("为什么")) return SYNTH_ANSWER;
  if (question.includes("怎么")) return DEGRADED_ANSWER;
  return SEARCH_ANSWER;
}

const SUGG = { suggestions: ["最近哪些 KOL 值得优先跟进?", "近30天各地区的表现怎么样?"], source: "seeds" };

const utcDay = (offset: number) => {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - offset)).toISOString().slice(0, 10);
};
const STATS = {
  status: "ready",
  total: 5,
  last_at: "2026-07-08 13:07:57+00:00",
  by_day: [
    { date: utcDay(1), count: 3 },
    { date: utcDay(0), count: 2 },
  ],
  note: "仅综合车道",
};

type Overrides = { ask?: unknown; suggestions?: unknown; stats?: unknown };

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: unknown) => {
    const p = String(path);
    const pick = (value: unknown) => {
      if (value instanceof Error) throw value;
      return value;
    };
    if (p.startsWith("/api/admin/vkpi/intelligent/suggestions")) return pick(overrides.suggestions ?? SUGG);
    if (p.startsWith("/api/admin/vkpi/intelligent/stats")) return pick(overrides.stats ?? STATS);
    if (p.startsWith("/api/admin/vkpi/intelligent/ask")) {
      if (overrides.ask !== undefined) return pick(overrides.ask);
      const body = (init as { body?: unknown } | undefined)?.body;
      const q = typeof body === "string" ? String(JSON.parse(body).question || "") : "";
      return answerFor(q);
    }
    if (p.startsWith("/api/admin/vkpi/marketing-advisor/readiness")) {
      return {
        status: "degraded",
        provider_ready: false,
        provider_called: false,
        reason: "advisor_provider_not_connected",
        persistence_ready: true,
        action_mode: "draft_only",
        retryable: true,
      };
    }
    if (p.startsWith("/api/admin/vkpi/marketing-advisor/threads")) return { status: "ok", threads: [], count: 0 };
    if (p.startsWith("/api/admin/vkpi/marketing-advisor/memory")) {
      return {
        status: "ok",
        settings: { state: "active", retention_days: 180, persisted: false },
        candidates: [],
        facts: [],
      };
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

function seedHistory(n: number, libEvery = 2): AskHistoryEntry[] {
  const at = new Date().toISOString();
  const list: AskHistoryEntry[] = [];
  for (let i = 0; i < n; i += 1) {
    const lib = i % libEvery === 0;
    list.push({
      id: `hist-${i}`,
      q: `hist-q-${i} 池内检索样例`,
      at,
      mode: lib ? "search" : "synth",
      cached: false,
      answer: `hist-a-${i}`,
      evidence: lib ? [{ kind: "search_results", count: 2, results: [{ name: `seed-${i}`, platform: "youtube" }] }] : [],
      actions: [],
    });
  }
  window.localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  return list;
}

const askInput = () => document.querySelector('input[placeholder^="问点什么"]') as HTMLInputElement;

// KPI 卡定位:label 文本也会出现在 SrcChip 口径行里 —— 一律限定到 .ds-kpi 卡体内
const kpiCard = (label: string) => {
  const el = screen
    .getAllByText(label)
    .map((node) => node.closest(".ds-kpi"))
    .find((node): node is HTMLElement => !!node);
  expect(el).toBeTruthy();
  return el as HTMLElement;
};

async function doAsk(question: string) {
  fireEvent.change(askInput(), { target: { value: question } });
  fireEvent.click(screen.getByText("提问"));
}

const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<IntelligentBoardPage apiToken="t" {...(props as any)} />);

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

describe("IntelligentBoardPage smoke(页壳 + KPI 带 + 布局键)", () => {
  it("为旧的自定义布局一次性补入顾问和记忆模块,不要求用户恢复默认", async () => {
    window.localStorage.setItem("vkpi-intelligent-layout-v1", JSON.stringify([
      { instanceId: "legacy-qa", moduleKey: "qa", span: 8, height: 12, x: 0, y: 0 },
      { instanceId: "legacy-history", moduleKey: "history", span: 4, height: 7, x: 8, y: 0 },
    ]));

    renderBoard();
    expect(await screen.findByRole("heading", { name: "顾问" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "记忆" })).toBeTruthy();

    const stored = JSON.parse(String(window.localStorage.getItem("vkpi-intelligent-layout-v1"))) as {
      items?: Array<{ moduleKey?: string }>;
    };
    const keys = (stored.items || []).map((item) => item.moduleKey);
    expect(keys).toEqual(expect.arrayContaining(["qa", "history", "advisor", "memory"]));
    expect(window.localStorage.getItem("vkpi-intelligent-layout-v1:advisor-memory-added-v1")).toBe("1");
  });

  it("KPI 四卡:本机三卡真 0 + 引用率诚实 pending;综合回答服务端真数 + 真 sparkline;布局本机键零账户级写", async () => {
    expect(() => renderBoard()).not.toThrow();

    expect(await screen.findByText("会话数")).toBeTruthy();
    kpiCard("今日问答");
    kpiCard("命中引用率");
    kpiCard("综合回答");
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);

    // 本机零留痕:会话数/今日 = 真 0 + spempty 虚线;引用率 = pending 药丸;零环比药丸
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(2);
    expect(screen.getByText("本机暂无留痕,成功问答后点亮")).toBeTruthy();
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    // 综合回答:服务端 total=5 真值 + 14 天真按日 sparkline(唯一有时序的卡)
    const synthCard = kpiCard("综合回答");
    expect(synthCard.textContent).toContain("5");
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(1));

    // pagehead + SrcChip 口径(真端点/真表名/本机留痕键;旧页头介绍句收编在 qa 行)
    expect(screen.getByText("可编辑看板")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
    expect(screen.getAllByText(/vkpi_llm_calls/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/vkpi-intelligent-history-v1/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/POST \/api\/admin\/vkpi\/intelligent\/ask/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/意图秒回 → 池内检索 → 模型综合/).length).toBeGreaterThan(0);

    // 布局:本机 storageKey 落盘;绝不写账户级偏好端点。新增的顾问/记忆只读取服务端私有状态。
    await waitFor(() => expect(window.localStorage.getItem("vkpi-intelligent-layout-v1")).toBeTruthy());
    const calledPaths = apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calledPaths.length).toBeGreaterThan(0);
    expect(calledPaths.every((p) => (
      p.startsWith("/api/admin/vkpi/intelligent/")
      || p.startsWith("/api/admin/vkpi/marketing-advisor/")
    ))).toBe(true);
  });
});

describe("问答主体(旧功能零丢失 + 引用来源标注)", () => {
  it("检索答案:车道徽 + 引用 chip 可点开证据弹窗;候选带 kol_pool_id 跳 KOL 档案;动作直跳;成功入本机留痕", async () => {
    const onNavigate = vi.fn();
    renderBoard({ onNavigate });
    await screen.findByText("会话数");

    await doAsk("Alpha 相关达人");
    expect(await screen.findByText(SEARCH_ANSWER.answer)).toBeTruthy();
    expect(screen.getAllByText("检索").length).toBeGreaterThan(0);

    // 引用来源标注:真来源 chip 可点 → 证据弹窗(候选列表)
    fireEvent.click(screen.getByText(/池内检索 · 2 候选/));
    expect(await screen.findByText("引用来源")).toBeTruthy();
    const modal = screen.getByText("引用来源").closest('[role="dialog"]') as HTMLElement;
    expect(within(modal).getByText("Alpha Cam")).toBeTruthy();
    expect(within(modal).getByText("beta-shooter")).toBeTruthy();

    // 候选身份跳:kol_pool_id → sessionStorage + onNavigate(kolProfile);无 id 候选无档案钮
    expect(within(modal).getAllByText("档案 →").length).toBe(1);
    fireEvent.click(within(modal).getByText("档案 →"));
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(onNavigate).toHaveBeenCalledWith("kolProfile");

    // 动作按钮直跳路由(旧页零丢失)
    fireEvent.click(screen.getAllByText("去 KOL 池检索 →")[0]);
    expect(onNavigate).toHaveBeenCalledWith("kol-pool");

    // 成功回答入本机留痕(历史会话模块 + localStorage)
    const stored = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]");
    expect(stored.length).toBe(1);
    expect(stored[0].q).toBe("Alpha 相关达人");
    expect(screen.getAllByText("Alpha 相关达人").length).toBeGreaterThan(1);
  });

  it("意图答案:秒回徽 + 意图查询 chip → 结构化小表格;问数页动作直跳", async () => {
    const onNavigate = vi.fn();
    renderBoard({ onNavigate });
    await screen.findByText("会话数");

    await doAsk("有哪些待处理的告警?");
    expect(await screen.findByText(INTENT_ANSWER.answer)).toBeTruthy();
    expect(screen.getAllByText("秒回").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText(/意图查询「近30天优先跟进 KOL」· 1 行/));
    const modal = (await screen.findByText("引用来源")).closest('[role="dialog"]') as HTMLElement;
    expect(within(modal).getByText("name")).toBeTruthy();
    expect(within(modal).getByText("Alpha")).toBeTruthy();
    expect(within(modal).getByText("88")).toBeTruthy();

    fireEvent.click(screen.getByText("打开问数页查看完整结果 →"));
    expect(onNavigate).toHaveBeenCalledWith("dataQuery");
  });

  it("综合答案:综合徽 + 当日缓存徽;零候选 → 「无库内引用」如实明标;生成信息进弹窗不上卡面", async () => {
    renderBoard();
    await screen.findByText("会话数");

    await doAsk("为什么最近转化率下降,给点建议");
    expect(await screen.findByText(SYNTH_ANSWER.answer)).toBeTruthy();
    expect(screen.getAllByText("综合").length).toBeGreaterThan(0);
    expect(screen.getByText("当日缓存")).toBeTruthy();
    expect(screen.getByText("无库内引用")).toBeTruthy();

    // 卡面只留「生成信息」入口,provider/model 细节住弹窗(卡面去术语)
    expect(screen.queryByText(/gemini-flash-latest/)).toBeNull();
    fireEvent.click(screen.getByText(/生成信息/));
    const modal = (await screen.findByText("引用来源")).closest('[role="dialog"]') as HTMLElement;
    expect(within(modal).getByText("gemini-flash-latest")).toBeTruthy();
    expect(within(modal).getAllByText(/正文由模型生成,未直接读库/).length).toBeGreaterThan(0);
  });

  it("降级答案:降级徽(诚实回退口径);建议 chips 点击即问(同一 ask 通路)", async () => {
    renderBoard();
    await screen.findByText("会话数");

    // 建议 chips 来自 /suggestions 真返回
    const chip = await screen.findByText("最近哪些 KOL 值得优先跟进?");
    fireEvent.click(chip);
    expect(await screen.findByText(SEARCH_ANSWER.answer)).toBeTruthy();
    const askCalls = apiFetchMock.mock.calls.filter((call) => String(call[0]).startsWith("/api/admin/vkpi/intelligent/ask"));
    expect(askCalls.length).toBe(1);
    expect(String(askCalls[0][1]?.body)).toContain("最近哪些 KOL 值得优先跟进?");

    await doAsk("这个怎么处理");
    expect(await screen.findByText(DEGRADED_ANSWER.answer)).toBeTruthy();
    expect(screen.getAllByText("降级").length).toBeGreaterThan(0);
  });
});

describe("历史会话(本机留痕 + 连续翻 + KPI 真数)", () => {
  it("留痕 8 条:KPI 会话数/今日 8 + 引用率 50%;卡面 6 条 + 全量弹窗 → 详情 ‹#n/N› + ↑↓ 连续翻 + 重新提问在场", async () => {
    seedHistory(8); // 偶数位带检索候选 → 4/8 = 50%
    renderBoard();
    await screen.findByText("会话数");

    expect(kpiCard("会话数").textContent).toContain("8");
    expect(kpiCard("今日问答").textContent).toContain("8");
    expect(kpiCard("命中引用率").textContent).toContain("50");

    // 卡面收敛 6 条 + 全量入口
    expect(screen.getAllByText(/hist-q-/).length).toBe(6);
    fireEvent.click(screen.getByText(/≡ 查看全量 8 条/));
    expect(await screen.findByText("历史会话 · 全量")).toBeTruthy();

    // 点第一条 → 详情 #1/8;↓ 连续翻到 #2/8;引用留痕快照 + 真动作在场
    const listModal = screen.getByText("历史会话 · 全量").closest('[role="dialog"]') as HTMLElement;
    fireEvent.click(within(listModal).getAllByText(/hist-q-0/)[0]);
    expect(await screen.findByText("#1 / 8")).toBeTruthy();
    expect(screen.getByText("hist-a-0")).toBeTruthy();
    expect(screen.getByText("↻ 重新提问")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2 / 8")).toBeTruthy();
    expect(screen.getByText("hist-a-1")).toBeTruthy();
  });

  it("清空 = 两步确认可反悔;清空后本机留痕真归零(KPI 回 0 + 诚实空态)", async () => {
    seedHistory(8);
    renderBoard();
    await screen.findByText("会话数");

    fireEvent.click(screen.getByText(/≡ 查看全量 8 条/));
    await screen.findByText("历史会话 · 全量");

    // 第一步只上膛(可取消),不清
    fireEvent.click(screen.getByText("清空本机留痕"));
    expect(JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "[]").length).toBe(8);
    // 第二步确认 → 真清空
    fireEvent.click(screen.getByText(/确认清空本机 8 条留痕/));
    await waitFor(() => expect(JSON.parse(window.localStorage.getItem(HISTORY_KEY) || "null")).toEqual([]));
    expect((await screen.findAllByText(/本机暂无留痕/)).length).toBeGreaterThan(0);
    expect(kpiCard("会话数").textContent).toContain("0");
  });
});

describe("诚实态三轨", () => {
  it("无 token:pending 卡 + 零请求(本机 KPI 照常真 0)", async () => {
    renderBoard({ apiToken: "" });
    expect((await screen.findAllByText(/未登录 \/ 无 token/)).length).toBeGreaterThan(0);
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("会话数")).toBeTruthy();
  });

  it("ask 失败原样透出;失败不入留痕", async () => {
    routeApi({ ask: new Error("boom") });
    renderBoard();
    await screen.findByText("会话数");
    await doAsk("任意问题");
    expect((await screen.findAllByText(/boom/)).length).toBeGreaterThan(0);
    expect(window.localStorage.getItem(HISTORY_KEY)).toBeNull();
  });

  it("suggestions / stats 端点失败:诚实错误卡 + 综合回答卡 pending(绝不编数)", async () => {
    routeApi({ suggestions: new Error("sugg down"), stats: new Error("stats down") });
    renderBoard();
    expect(await screen.findByText("建议读取失败")).toBeTruthy();
    expect(screen.getAllByText(/sugg down/).length).toBeGreaterThan(0);
    expect(await screen.findByText("统计端点读取失败")).toBeTruthy();
    expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(0);
  });

  it("stats 缺表(status empty 带 reason):综合回答卡诚实 pending 原样透出 reason", async () => {
    routeApi({ stats: { status: "empty", reason: "vkpi_llm_calls 表未建 —— 综合车道尚无服务端留痕" } });
    renderBoard();
    expect((await screen.findAllByText(/综合车道尚无服务端留痕/)).length).toBeGreaterThan(0);
  });
});
