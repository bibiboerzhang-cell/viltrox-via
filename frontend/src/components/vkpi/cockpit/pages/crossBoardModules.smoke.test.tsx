import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Dashboard 跨板块拉卡(task #76)冒烟:
// - 注册表契约:key 全带 xb- 前缀且唯一(进 dashboard_layout_v1 零冲突)、
//   category=跨板块模块、palette 描述带来源板块前缀;
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

import { CROSS_BOARD_CATEGORY, CROSS_BOARD_ENTRIES, buildCrossBoardModules } from "./crossBoardModules";
import { VoiceAlertsXbCard } from "./crossBoardModules.voice";
import { PoolDiscoveryFunnelXbCard } from "./crossBoardModules.pool";
import { ReplyIntentXbCard } from "./crossBoardModules.reply";
import { ProjectsDueXbCard } from "./crossBoardModules.projects";

// CockpitApp NAV_ITEMS 真实 navKey 集(canViewBoard / setActiveNav 同口径)
const KNOWN_NAV_KEYS = new Set(["marketVoice", "my-kol", "kol-pool", "projects", "replyQueue"]);

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
});

describe("crossBoardModules 注册表契约", () => {
  it("key 全带 xb- 前缀且唯一;板块 navKey 全在已接线集合内", () => {
    const keys = CROSS_BOARD_ENTRIES.map((entry) => entry.key);
    expect(keys.length).toBeGreaterThanOrEqual(6);
    keys.forEach((key) => expect(key.startsWith("xb-")).toBe(true));
    expect(new Set(keys).size).toBe(keys.length);
    CROSS_BOARD_ENTRIES.forEach((entry) => expect(KNOWN_NAV_KEYS.has(entry.board)).toBe(true));
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
