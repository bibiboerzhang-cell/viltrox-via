import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Dashboard 跨板块拉卡(task #76)冒烟:
// - 注册表契约:当前 Cockpit 已实现页(业务 + Ops)全覆盖；key 全带 xb- 前缀且唯一
//   (进 dashboard_layout_v1 零冲突),category=跨板块模块、palette 描述带来源板块前缀;
// - 权限过滤:canViewBoard=false 的板块,其模块不出现(palette 与渲染同一份注册表);
// - 真身卡自带取数:市场之声告警 / KOL Pool 发现漏斗 / 回复队列意向环图 /
//   Projects 履约待办 逐卡挂载 —— 卡头「来源板块」徽点击跳源板块;
//   履约待办点行 = 派发既有 vkpi:open-project-task 事件(CockpitApp 直开详情管道);
// - 诚实空态沿源模块(回复队列 0 条 → 环图诚实不画)。
// mock seam:services/http.apiFetch(全部卡唯一网络出口,零真实 HTTP)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  CROSS_BOARD_CATEGORY,
  CROSS_BOARD_ENTRIES,
  CROSS_BOARD_SOURCES,
  buildCrossBoardModuleGroups,
  buildCrossBoardModules,
} from "./crossBoardModules";
import { VoiceAlertsXbCard } from "./crossBoardModules.voice";
import { PoolDiscoveryFunnelXbCard, PoolSearchHistoryXbCard, PoolSmartSearchXbCard } from "./crossBoardModules.pool";
import { FullBoardModuleXbCard } from "./crossBoardModules.full";
import { ReplyIntentXbCard } from "./crossBoardModules.reply";
import { ProjectsDueXbCard } from "./crossBoardModules.projects";
import { ProfileSignatureXbCard } from "./crossBoardModules.profile";
import { EventsUpcomingXbCard } from "./crossBoardModules.events";
import { ShopifyGmvXbCard } from "./crossBoardModules.shopify";
import { DealersRegionsXbCard } from "./crossBoardModules.dealers";
import { IntelligentStatsXbCard } from "./crossBoardModules.intelligent";
import { SkuCatalogXbCard } from "./crossBoardModules.sku";
import { CreativeIndexXbCard } from "./crossBoardModules.creative";
import { LaunchpadPlansXbCard } from "./crossBoardModules.launchpad";
import { AutonomyScorecardXbCard } from "./crossBoardModules.autonomy";
import { StrategySovXbCard } from "./crossBoardModules.strategy";
import { GtmAiReadinessXbCard, GtmSignalsXbCard } from "./crossBoardModules.gtm";
import { COCKPIT_BOARDS } from "../CockpitApp.lazyBoards";

// 直接跟随 Cockpit 真正可达页，防新增已实现页面后跨页目录静默漏接。
const SOURCE_NAV_KEYS = COCKPIT_BOARDS.filter((key) => key !== "dashboard");
const KNOWN_NAV_KEYS = new Set<string>(SOURCE_NAV_KEYS);

type Routes = Record<string, unknown>;
function routeApi(routes: Routes) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    for (const [prefix, value] of Object.entries(routes)) {
      if (p.startsWith(prefix)) {
        if (value instanceof Error) throw value;
        return value;
      }
    }
    throw new Error(`unmocked path: ${p}`);
  });
}

beforeEach(() => {
  apiFetchMock.mockReset();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

describe("crossBoardModules 注册表契约", () => {
  it("key 全带 xb- 前缀且唯一;板块 navKey 全在已接线集合内", () => {
    const keys = CROSS_BOARD_ENTRIES.map((entry) => entry.key);
    expect(keys.length).toBeGreaterThanOrEqual(6);
    keys.forEach((key) => expect(key.startsWith("xb-")).toBe(true));
    expect(new Set(keys).size).toBe(keys.length);
    CROSS_BOARD_ENTRIES.forEach((entry) => expect(KNOWN_NAV_KEYS.has(entry.board)).toBe(true));
  });

  it("按来源页面分组且扁平 registry 不漏项、不重复", () => {
    const groupedKeys = CROSS_BOARD_SOURCES.flatMap((source) =>
      source.entries.map((entry) => `${source.board}:${entry.key}`),
    );
    const flatKeys = CROSS_BOARD_ENTRIES.map((entry) => `${entry.board}:${entry.key}`);
    expect(flatKeys).toEqual(groupedKeys);
    expect(new Set(CROSS_BOARD_SOURCES.map((source) => source.board))).toEqual(new Set(SOURCE_NAV_KEYS));
    expect(CROSS_BOARD_SOURCES).toHaveLength(SOURCE_NAV_KEYS.length);
    CROSS_BOARD_SOURCES.forEach((source) => expect(source.entries.length).toBeGreaterThanOrEqual(1));
    expect(CROSS_BOARD_ENTRIES.length).toBeGreaterThanOrEqual(CROSS_BOARD_SOURCES.length);
    expect(CROSS_BOARD_ENTRIES.filter((entry) => entry.availability === "ready").length).toBeGreaterThan(0);
    expect(CROSS_BOARD_ENTRIES.filter((entry) => entry.availability === "context")).toHaveLength(12);
    expect(CROSS_BOARD_ENTRIES.filter((entry) => entry.sourceModuleKey).length).toBeGreaterThan(0);
    ["triage", "dataQuery", "marketTrends", "skillStudio"].forEach((board) => {
      expect(CROSS_BOARD_ENTRIES.some((entry) => entry.board === board && entry.sourceModuleKey === "overview")).toBe(true);
    });
  });

  it("build:category=跨板块模块,描述带来源板块前缀,render 出元素", () => {
    const modules = buildCrossBoardModules({ apiToken: "t", canViewBoard: () => true, onOpenBoard: () => {} });
    expect(modules.length).toBe(CROSS_BOARD_ENTRIES.length);
    modules.forEach((module) => {
      expect(module.category).toBe(CROSS_BOARD_CATEGORY);
      expect(module.description.includes(" · ")).toBe(true);
      expect(React.isValidElement(module.render() as React.ReactElement)).toBe(true);
    });
    const voice = modules.find((module) => module.key === "xb-voice-alerts");
    expect(voice?.description.startsWith("市场之声")).toBe(true);
    const eventRadar = modules.find((module) => module.key === "xb-events-radar");
    expect(eventRadar?.description.startsWith("Events · ")).toBe(true);
    expect(eventRadar?.sourceBoard).toBe("events");
  });

  it("权限过滤:canViewBoard=false 的板块,其模块不出现", () => {
    const modules = buildCrossBoardModules({
      apiToken: "t",
      canViewBoard: (navKey) => navKey !== "marketVoice",
      onOpenBoard: () => {},
    });
    expect(modules.some((module) => module.key.startsWith("xb-voice-"))).toBe(false);
    expect(modules.some((module) => module.key === "xb-projects-due")).toBe(true);
    // 全关 = palette 零跨板块项
    expect(buildCrossBoardModules({ apiToken: "t", canViewBoard: () => false, onOpenBoard: () => {} })).toHaveLength(0);
  });

  it("来源分组与 palette 使用同一权限过滤结果", () => {
    const options = {
      apiToken: "t",
      canViewBoard: (navKey: string) => navKey !== "projects" && navKey !== "replyQueue",
      onOpenBoard: () => {},
    };
    const groups = buildCrossBoardModuleGroups(options);
    expect(new Set(groups.map((group) => group.board))).toEqual(
      new Set(SOURCE_NAV_KEYS.filter((board) => board !== "projects" && board !== "replyQueue")),
    );
    expect(groups.flatMap((group) => group.modules.map((module) => module.key))).toEqual(
      buildCrossBoardModules(options).map((module) => module.key),
    );
    groups.forEach((group) => group.modules.forEach((module) => {
      expect(module.sourceBoard).toBe(group.board);
      expect(module.sourceLabel).toBe(group.boardLabel);
      expect(module.description.startsWith(`${group.boardLabel} · `)).toBe(true);
      expect(["ready", "context"]).toContain(module.availability);
    }));
  });
});

describe("新增来源 wrapper 诚实空态", () => {
  it("KOL 档案无最近选择 → 待选择且不发请求", () => {
    render(<ProfileSignatureXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(screen.getByText("待选择 KOL")).toBeInTheDocument();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("Events 空列表 → 不伪造活动", async () => {
    routeApi({ "/api/admin/vkpi/events?limit=200": { items: [] } });
    render(<EventsUpcomingXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText(/没有未来窗口内的活动/)).toBeInTheDocument();
  });

  it("Shopify 两本台账为空 → GMV 不摆假数", async () => {
    routeApi({
      "/api/admin/vkpi/shopify/gmv": {
        gmv_cents: 0,
        order_count: 0,
        currency: "USD",
        gmv_source: "awaiting_data",
      },
    });
    render(<ShopifyGmvXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText(/订单台账与归因台账当前均无行/)).toBeInTheDocument();
  });

  it("Dealers 库空 → 沿源页诚实空态", async () => {
    routeApi({ "/api/admin/vkpi/dealers?limit=500": { dealers: [] } });
    render(<DealersRegionsXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText(/本地库 0 行/)).toBeInTheDocument();
  });

  it("Intelligent 服务端无留痕 → 不编调用趋势", async () => {
    routeApi({ "/api/admin/vkpi/intelligent/stats": { status: "empty", reason: "尚无综合问答留痕" } });
    render(<IntelligentStatsXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("尚无综合问答留痕")).toBeInTheDocument();
  });

  it("SKU 目录为空 → 不猜 SKU", async () => {
    routeApi({ "/api/admin/vkpi/sku/list?query=&limit=1": { items: [] } });
    render(<SkuCatalogXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText(/产品目录当前无可用 SKU/)).toBeInTheDocument();
  });

  it("创意深析库为空 → 不编索引", async () => {
    routeApi({ "/api/admin/vkpi/creative-segments/search": { status: "empty", reason: "暂无 ready 深析视频" } });
    render(<CreativeIndexXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("暂无 ready 深析视频")).toBeInTheDocument();
  });

  it("发射台无计划 → 显示真实 0 行", async () => {
    routeApi({ "/api/admin/vkpi/product-analysis/launches?limit=20": { launches: [] } });
    render(<LaunchpadPlansXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("0 行 · 尚无发布计划。")).toBeInTheDocument();
  });

  it("自治周窗无裁决 → 显示端点原因", async () => {
    routeApi({ "/api/admin/vkpi/learning/weekly-scorecard?weeks=8": { status: "empty", reason: "窗口内暂无裁决" } });
    render(<AutonomyScorecardXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("窗口内暂无裁决")).toBeInTheDocument();
  });

  it("自治记分卡已判样本 <20 → 命中率位显示「样本不足(n/20)」而非百分比", async () => {
    routeApi({
      "/api/admin/vkpi/learning/weekly-scorecard?weeks=8": {
        status: "ok",
        overall: { in_range_judged: 7, in_range_hits: 5, in_range_hit_rate: 0.7143 },
        pending_backlog: { pending_total: 12 },
        groups: [],
      },
    });
    render(<AutonomyScorecardXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("样本不足(7/20)")).toBeInTheDocument();
    expect(screen.queryByText("71.4%")).not.toBeInTheDocument();
    cleanup();
  });

  it("战略台窗口无证据 → 不画品牌排名", async () => {
    routeApi({ "/api/admin/vkpi/strategy/industry-benchmark?window_days=90": { status: "no_data_in_window" } });
    render(<StrategySovXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("窗口内暂无入库视频证据。")).toBeInTheDocument();
  });

  it("GTM 本周无信号 → 沿源组件诚实空态", async () => {
    routeApi({
      "/api/admin/vkpi/market-brain/summary": {
        weekly_signals: { status: "empty", items: [], sources_note: "本周暂无可用信号" },
      },
    });
    render(<GtmSignalsXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("本周暂无可用信号")).toBeInTheDocument();
    cleanup();
  });

  it("AI 证据就绪度分开显示能力与真实证据，三腿未齐保持仅描述", async () => {
    routeApi({
      "/api/admin/vkpi/agents/marketing-brain/scorecard": {
        status: "ok",
        capability_score: 96.4,
        observed_evidence_score: 45.9,
        claim_status: "descriptive_only",
        data_readiness: {
          checks: {
            finalized_outcomes: { status: "insufficient", observed: 0, minimum: 5 },
            prediction_evals: { status: "insufficient", observed: 0, minimum: 5 },
            real_feedback: { status: "insufficient", observed: 0, minimum: 5 },
          },
        },
      },
    });
    render(<GtmAiReadinessXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("96.4")).toBeInTheDocument();
    expect(screen.getByText("45.9")).toBeInTheDocument();
    expect(screen.getAllByText("仅描述性结论")).toHaveLength(1);
    expect(screen.getByText("人工 finalized outcome")).toBeInTheDocument();
    expect(screen.getByText("非演示人工反馈")).toBeInTheDocument();
    expect(screen.getByText("带真实 actual 的预测评测")).toBeInTheDocument();
  });
});

describe("市场之声 · 声量告警(自带取数)", () => {
  it("挂载即取 voice-report-ext;来源徽点击跳源板块;告警行可点跳源板块", async () => {
    routeApi({
      "/api/admin/vkpi/market/voice-report-ext": {
        alerts_state: {
          status: "ready",
          window_hours: 8,
          threshold: 3,
          scanned: 900,
          categories: [
            { category: "af", label: "对焦", triggered: true, negative_count: 5, score: 7 },
            { category: "build", label: "做工", triggered: false, negative_count: 0 },
          ],
          recent_pushed: [],
        },
      },
    });
    const onOpenBoard = vi.fn();
    render(<VoiceAlertsXbCard apiToken="t" onOpenBoard={onOpenBoard} />);
    expect(await screen.findByText("对焦")).toBeInTheDocument();
    expect(screen.getByText("声量告警")).toBeInTheDocument();
    expect(screen.getByText("1 触发")).toBeInTheDocument();
    // 卡头来源徽 → 跳源板块
    fireEvent.click(screen.getByRole("button", { name: "打开 市场之声 板块" }));
    expect(onOpenBoard).toHaveBeenCalledTimes(1);
    // 告警行点开 = 跨板块下钻 → 跳源板块(源板块内才是下钻弹窗)
    fireEvent.click(screen.getByText("对焦"));
    expect(onOpenBoard).toHaveBeenCalledTimes(2);
  });

  it("端点失败 → 诚实错误卡(不摆假图)", async () => {
    routeApi({ "/api/admin/vkpi/market/voice-report-ext": new Error("boom") });
    render(<VoiceAlertsXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("voice-report-ext 读取失败")).toBeInTheDocument();
  });
});

describe("KOL Pool · 发现转化漏斗(源 hook 原件自取)", () => {
  it("summary 到手 → 四段条形 + cnt=发现计数", async () => {
    routeApi({
      "/api/admin/vkpi/kol-pool/summary": {
        low_reach_hidden_count: 4,
        discovery_funnel_30d: { window_days: 30, discovered: 120, enrolled: 80, deep_analyzed: 30, favorited: 12 },
      },
    });
    render(<PoolDiscoveryFunnelXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("发现")).toBeInTheDocument();
    // 「自动入库」在漏斗条形行与 SrcChip 口径行各出现一次(口径同源,双处如实);
    // 「120」在卡头 cnt 短徽与条形行数值各出现一次(同一真值双处)
    expect(screen.getAllByText("自动入库").length).toBeGreaterThan(0);
    expect(screen.getAllByText("120").length).toBeGreaterThan(0);
  });
});

describe("KOL Pool · 找达人(可操作原件)", () => {
  it("Dashboard 内保留查找框和历史入口，点来源徽回 KOL Pool", async () => {
    routeApi({ "/api/admin/vkpi/kol-search-history": { items: [] } });
    const onOpenBoard = vi.fn();
    render(<PoolSmartSearchXbCard apiToken="t" onOpenBoard={onOpenBoard} />);

    expect(await screen.findByTestId("dashboard-kol-smart-search")).toBeInTheDocument();
    expect(screen.getByTestId("smart-kol-input")).toBeInTheDocument();
    expect(await screen.findByText("历史记录")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 KOL Pool 板块" }));
    expect(onOpenBoard).toHaveBeenCalledTimes(1);
  });
});

describe("KOL Pool · 独立搜索历史", () => {
  it("Dashboard 可单独添加；点记录写入待恢复会话并打开 KOL Pool", async () => {
    routeApi({
      "/api/admin/vkpi/kol-search-history": {
        items: [{ id: 778, session_id: 778, query_text: "85mm portrait reviewer", query_type: "text", status: "ready", item_count: 3 }],
      },
    });
    const onOpenBoard = vi.fn();
    render(<PoolSearchHistoryXbCard apiToken="t" onOpenBoard={onOpenBoard} />);

    fireEvent.click(await screen.findByText("查看"));
    fireEvent.click(await screen.findByText("85mm portrait reviewer"));
    expect(window.localStorage.getItem("vkpi:pendingKolSearchSessionId")).toBe("778");
    expect(onOpenBoard).toHaveBeenCalledTimes(1);
  });
});

describe("全盘源页面模块宿主", () => {
  it("按需挂载源页面注册表原件，而不是静态说明卡", async () => {
    routeApi({
      "/api/admin/vkpi/intelligent/suggestions": { items: [] },
      "/api/admin/vkpi/intelligent/stats": { status: "empty", reason: "尚无综合问答留痕" },
    });
    render(
      <FullBoardModuleXbCard
        apiToken="t"
        board="intelligent"
        boardLabel="Intelligent 问答"
        sourceModuleKey="history"
        onOpenBoard={() => {}}
        onNavigate={() => {}}
      />,
    );
    expect(await screen.findByText("历史会话")).toBeInTheDocument();
    expect(screen.getByTestId("full-board-module-intelligent-history")).toBeInTheDocument();
  });

  it("缺少 Projects 页面上下文时显示诚实待接，不挂空壳", () => {
    render(
      <FullBoardModuleXbCard
        apiToken="t"
        board="projects"
        boardLabel="Projects"
        sourceModuleKey="kpiP"
        onOpenBoard={() => {}}
        onNavigate={() => {}}
      />,
    );
    expect(screen.getByText("等待源页面上下文")).toBeInTheDocument();
  });
});

describe("回复队列 · 意向构成(源 hook 原件自取)", () => {
  const row = (id: number, intent: string) => ({
    id,
    platform: "instagram",
    kol_pool_id: null,
    comment_external_id: `ext-${id}`,
    comment_text: "How much?",
    intent_tag: intent,
    lang: "en",
    draft_reply: "",
    status: "pending",
    created_at: "2026-07-07T05:26:53Z",
    updated_at: "2026-07-07T05:26:53Z",
  });

  it("队列到手 → 环图 + cnt=类数;分段点开 = 跳源板块", async () => {
    routeApi({ "/api/admin/vkpi/reply-queue": { items: [row(1, "price"), row(2, "price"), row(3, "compat")] } });
    const onOpenBoard = vi.fn();
    render(<ReplyIntentXbCard apiToken="t" onOpenBoard={onOpenBoard} />);
    expect(await screen.findByText("2 类")).toBeInTheDocument();
    expect(screen.getByText("意向构成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 回复队列 板块" }));
    expect(onOpenBoard).toHaveBeenCalled();
  });

  it("队列 0 条 → 环图诚实不画(源模块同文案)", async () => {
    routeApi({ "/api/admin/vkpi/reply-queue": { items: [] } });
    render(<ReplyIntentXbCard apiToken="t" onOpenBoard={() => {}} />);
    expect(await screen.findByText("队列 0 条,环图诚实不画。")).toBeInTheDocument();
  });
});

describe("Projects · 履约待办(内嵌原件自取)", () => {
  it("点行 = 派发 vkpi:open-project-task(CockpitApp 直开详情管道)", async () => {
    routeApi({
      "/api/admin/vkpi/projects/due-list": {
        status: "ok",
        days_overdue: 7,
        count: 1,
        items: [{ project_id: 88, project_name: "AF 85 送测", product_name: "AF 85mm", days_since_delivered: 9, assignment_count: 2 }],
      },
    });
    const events: Array<CustomEvent> = [];
    const listener = (event: Event) => events.push(event as CustomEvent);
    window.addEventListener("vkpi:open-project-task", listener);
    try {
      render(<ProjectsDueXbCard apiToken="t" onOpenBoard={() => {}} />);
      const rowNode = await screen.findByText("AF 85 送测");
      fireEvent.click(rowNode);
      await waitFor(() => expect(events.length).toBe(1));
      expect(events[0].detail).toEqual({ projectId: "88" });
    } finally {
      window.removeEventListener("vkpi:open-project-task", listener);
    }
  });

  it("无 token → 未登录诚实卡(不发请求)", () => {
    render(<ProjectsDueXbCard apiToken="" onOpenBoard={() => {}} />);
    expect(screen.getByText(/未登录 \/ 无 token/)).toBeInTheDocument();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
