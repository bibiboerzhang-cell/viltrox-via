import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Events 板块页范式改版冒烟(金样板 MarketVoicePage/MyKolBoardPage.smoke 同构):
// - 页壳:pagehead(Events · 市场活动 + 活动数药丸徽 + 公司库存/新建 Event/编辑布局钮)
//   + 可编辑看板(布局键 vkpi-events-board-layout-v1,不传 apiToken → 绝不写账户级
//   dashboard_layout_v1);
// - KPI 带四卡全真值:未完结管线(工作流状态,不冒充日期意义上的进行中)/ 本月活动(起止与本月重叠)/
//   物料备货(vkpi_inventory 行数)/ 费用合计(budget_json spent 合计);无历史快照
//   → 诚实虚线不编序列;
// - 活动看板 = 旧 EventsPage 收编:我参与的/全部 + 状态/类型/搜索过滤零丢失,
//   EventCard 旧件复用,点卡 → EventDetailTakeover(旧详情七 tab 零改动);
// - 联动:状态环图图例点击 → 看板状态过滤(page 层共享筛选);
// - 诚实空态/错误卡:活动 0 行如实空;端点失败 = ErrorCard;库存失败 = KPI pending;
// - dashboard 跳转 initialEventId 自动开详情 + consume 回调(旧页行为 1:1)。
// mock seam:services/http.apiFetch(全页唯一网络出口,按 path 路由)+ useAuth。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("../../../../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "authenticated",
    token: "test-token",
    user: { id: "7", name: "Jia" },
    signIn: vi.fn(),
    signOut: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

import { EventsBoardPage, canManageEventRadar } from "./EventsBoardPage";

/* ---------- 日期夹具(真实今天派生,KPI 口径确定性:ev1 跨今天=本月;
   ev2 +40 天必出本月;ev3 -60 天必在本月前) ---------- */
const isoDay = (offsetDays: number) => {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// 后端 vkpi_events snake 形状(events-api toUiEvent 入参)
const EV1 = {
  id: "ev_live_1",
  title: "IBC 展会实测",
  type_key: "tradeshow",
  status: "live",
  health_score: 90,
  start_date: isoDay(-1),
  end_date: isoDay(2),
  location_name: "RAI",
  location_city: "Amsterdam",
  location_country: "NL",
  budget_total: 1000,
  budget_json: { booth: { plan: 500, spent: 200 } },
  owner_id: "7",
  team_ids: ["7"],
  related_project_ids: [],
  invited_kols_json: [],
  updated_at: "2026-07-10T08:00:00Z",
};
const EV2 = {
  id: "ev_plan_2",
  title: "媒体探营日",
  type_key: "media",
  status: "planning",
  health_score: 100,
  start_date: isoDay(40),
  end_date: isoDay(41),
  location_name: "",
  location_city: "Shenzhen",
  location_country: "CN",
  budget_total: 0,
  budget_json: {},
  owner_id: "8",
  team_ids: ["8"],
  related_project_ids: [],
  invited_kols_json: [],
  updated_at: "2026-07-09T08:00:00Z",
};
const EV3 = {
  id: "ev_done_3",
  title: "春季 KOL 聚会",
  type_key: "kol_meetup",
  status: "done",
  health_score: 88,
  start_date: isoDay(-60),
  end_date: isoDay(-59),
  location_name: "",
  location_city: "Tokyo",
  location_country: "JP",
  budget_total: 100,
  budget_json: { travel: { plan: 100, spent: 150 } },
  owner_id: "7",
  team_ids: ["7"],
  related_project_ids: [],
  invited_kols_json: [],
  roi: 3.5,
  leads: 10,
  videos: 4,
  updated_at: "2026-07-01T08:00:00Z",
};

const INVENTORY = [
  { id: "i1", sku: "AF-85", name: "AF 85mm 样机", category: "lens", qty: 3, location: "SZ", note: "", is_sample: true },
  { id: "i2", sku: "TRIPOD-1", name: "三脚架", category: "equipment", qty: 5, location: "SZ", note: "", is_sample: false },
];

const STAFF = [
  { id: "7", name: "Jia", email: "jia@viltrox.com", isAdmin: true, avatar: "J", color: "#a855f7" },
  { id: "8", name: "Pei", email: "pei@viltrox.com", isAdmin: false, avatar: "P", color: "#06b6d4" },
];

// board-series?board=events 真形状(对照 backend board_series._events_board 出参;
// 缺省不路由 → 空对象 → 卡面照旧 spempty 诚实虚线)。
const BOARD_SERIES_OK = {
  status: "ready",
  board: "events",
  days: 30,
  window: { since: "2026-06-13", until: "2026-07-12", prev_since: "2026-05-14", prev_until: "2026-06-12" },
  series: {
    events_new: [
      { date: "2026-07-10", count: 0 },
      { date: "2026-07-11", count: 2 },
      { date: "2026-07-12", count: 3 },
    ],
    events_started: [
      { date: "2026-07-10", count: 1 },
      { date: "2026-07-11", count: 0 },
      { date: "2026-07-12", count: 2 },
    ],
    event_expense_amount: [
      { date: "2026-07-10", value: 0 },
      { date: "2026-07-11", value: 200 },
      { date: "2026-07-12", value: 0 },
    ],
  },
  metrics: {
    events_new: { status: "ready", current: 5, previous: 0, delta_pct: null, table: "vkpi_events", unit: "rows" },
    events_started: { status: "ready", current: 3, previous: 1, delta_pct: 200.0, table: "vkpi_events", unit: "rows" },
    event_expense_amount: { status: "ready", current: 200, previous: 0, delta_pct: null, table: "vkpi_event_expenses", unit: "amount" },
  },
  basis: {},
  method: "board_series_v1",
  generated_at: "2026-07-12T02:00:00+00:00",
};

type RouteOverrides = {
  events?: () => Promise<unknown>;
  inventory?: () => Promise<unknown>;
  detail?: () => Promise<unknown>;
  boardSeries?: () => Promise<unknown>;
};

function routeApi(overrides: RouteOverrides = {}) {
  apiFetchMock.mockImplementation((path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/board-series")) {
      return overrides.boardSeries ? overrides.boardSeries() : Promise.resolve({});
    }
    if (p.startsWith("/api/admin/vkpi/events?")) {
      return overrides.events ? overrides.events() : Promise.resolve({ items: [EV1, EV2, EV3] });
    }
    if (/\/api\/admin\/vkpi\/events\/[^/]+$/.test(p)) {
      return overrides.detail
        ? overrides.detail()
        : Promise.resolve({ item: EV1, tasks: [], expenses: [], invites: [], materials: [], products: [] });
    }
    if (p.startsWith("/api/admin/vkpi/inventory")) {
      return overrides.inventory ? overrides.inventory() : Promise.resolve({ items: INVENTORY });
    }
    if (p.startsWith("/api/admin/vkpi/projects")) {
      return Promise.resolve({ projects: [] });
    }
    return Promise.resolve({});
  });
}

function renderBoard(props: Record<string, unknown> = {}) {
  return render(
    React.createElement(EventsBoardPage as React.ComponentType<any>, {
      userName: "Jia",
      staff: STAFF,
      currentUser: { id: "7", name: "Jia" },
      initialEventId: null,
      onConsumeInitialEvent: () => {},
      ...props,
    }),
  );
}

/** KPI 卡按眉题定位后取整卡文本(眉题词也可能出现在 SrcChip 口径行,按 .ds-kpi 收敛) */
async function kpiCardText(label: string): Promise<string> {
  const els = await screen.findAllByText(label);
  const el = els.find((node) => node.closest(".ds-kpi"));
  return el?.closest(".ds-kpi")?.textContent || "";
}

/** 模块卡按卡头标题定位(board/upcoming 标题可能同词,断言按卡内 scope) */
async function moduleSection(title: string): Promise<HTMLElement> {
  const heads = await screen.findAllByText(title);
  const head = heads.find((el) => el.tagName === "H3") || heads[0];
  return head.closest("section") as HTMLElement;
}

beforeEach(() => {
  apiFetchMock.mockReset();
  window.localStorage.clear();
  routeApi();
});

describe("EventsBoardPage 页壳 + KPI 带(全真值)", () => {
  it("maps the backend manager plus vkpi-write gate before rendering Event Radar actions", () => {
    expect(canManageEventRadar({ role: "manager", permissions: { vkpi: "write" } })).toBe(true);
    expect(canManageEventRadar({ role: "marketing-manager", permissions: { vkpi: "admin" } })).toBe(true);
    expect(canManageEventRadar({ role: "manager", permissions: { vkpi: "read" } })).toBe(false);
    expect(canManageEventRadar({ role: "employee", permissions: { vkpi: "admin" } })).toBe(false);
    expect(canManageEventRadar({ role: "employee", permissions: { vkpi: "read" }, is_owner: true })).toBe(true);
  });

  it("pagehead:标题 + 活动数药丸徽 + 公司库存(真计数)/新建 Event/编辑布局钮;布局零账户级写入", async () => {
    renderBoard();
    expect(await screen.findByText("Events · 市场活动")).toBeTruthy();
    expect((await screen.findAllByText("3 活动")).length).toBeGreaterThan(0); // 药丸徽 + kpiE 卡头 cnt 同源同数
    expect(screen.getByText("可编辑看板")).toBeTruthy();
    expect(await screen.findByText("公司库存(2)")).toBeTruthy();
    expect(screen.getByText("新建 Event")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
    // 不传 apiToken → 绝不写账户级 dashboard_layout_v1(布局只走本机 storageKey)
    expect(window.localStorage.getItem("dashboard_layout_v1")).toBeNull();
  });

  it("KPI 四卡真值:未完结管线 2(live+planning)/ 本月 1(跨今天)/ 物料备货 2 / 费用合计 $350", async () => {
    renderBoard();
    expect(await kpiCardText("未完结管线")).toContain("2");
    expect(await kpiCardText("本月活动")).toContain("1");
    expect(await kpiCardText("物料备货")).toContain("2");
    expect(await kpiCardText("费用合计")).toContain("$350");
  });

  it("库存端点失败 → 物料备货卡诚实 pending(绝不摆 0 冒充);活动卡不受拖累", async () => {
    routeApi({ inventory: () => Promise.reject(new Error("inventory down")) });
    renderBoard();
    const text = await waitFor(async () => {
      const t = await kpiCardText("物料备货");
      expect(t).toContain("库存端点读取失败");
      return t;
    });
    expect(text).toContain("—");
    expect(await kpiCardText("未完结管线")).toContain("2");
  });

  it("board-series 就绪 → 进行中/本月/费用三卡点亮真 sparkline(关联指标零环比药丸);物料备货点时无序列照旧虚线", async () => {
    routeApi({ boardSeries: () => Promise.resolve(BOARD_SERIES_OK) });
    renderBoard();
    expect((await screen.findAllByText("未完结管线")).length).toBeGreaterThan(0);
    await waitFor(() => expect(document.querySelectorAll(".ds-kpi__spark").length).toBe(3));
    // 物料备货 = 点时库存无历史 → 唯一诚实虚线;关联指标卡零环比药丸
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(1);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);
    // 序列请求真发向 board-series?board=events
    const bs = apiFetchMock.mock.calls.map((c) => String(c[0])).filter((p) => p.startsWith("/api/admin/vkpi/board-series"));
    expect(bs.length).toBeGreaterThan(0);
    expect(bs[0]).toContain("board=events");
  });
});

describe("活动看板(旧 EventsPage 功能收编零丢失)", () => {
  it("看板卡面:EventCard 复用 + 我参与的/全部 + 状态/类型/搜索过滤;搜索只影响看板不吞其他模块", async () => {
    renderBoard();
    const board = await moduleSection("活动看板");
    expect(await within(board).findByText("IBC 展会实测")).toBeTruthy();
    expect(within(board).getByText("媒体探营日")).toBeTruthy();
    expect(within(board).getByText("我参与的")).toBeTruthy();
    expect(within(board).getByText("全部")).toBeTruthy();
    // 搜索(标题/城市双命中口径)
    fireEvent.change(within(board).getByPlaceholderText("搜索活动 / 城市"), { target: { value: "IBC" } });
    expect(within(board).queryByText("媒体探营日")).toBeNull();
    expect(within(board).getByText("IBC 展会实测")).toBeTruthy();
    // 即将开幕模块不受看板筛选影响(独立视图,同一份真活动行)
    const upcoming = await moduleSection("即将开幕");
    expect(within(upcoming).getByText("媒体探营日")).toBeTruthy();
  });

  it("「我参与的」= team_ids(jsonb)含当前登录人(服务端同口径的前端复现)", async () => {
    renderBoard();
    const board = await moduleSection("活动看板");
    expect(await within(board).findByText("媒体探营日")).toBeTruthy();
    fireEvent.click(within(board).getByText("我参与的"));
    expect(within(board).queryByText("媒体探营日")).toBeNull(); // team_ids=["8"] 不含 7
    expect(within(board).getByText("IBC 展会实测")).toBeTruthy();
  });

  it("状态环图图例点击 → 看板状态过滤联动(再点还原)", async () => {
    renderBoard();
    const donut = await moduleSection("状态构成");
    const board = await moduleSection("活动看板");
    expect(await within(board).findByText("媒体探营日")).toBeTruthy();
    fireEvent.click(within(donut).getByText("进行中"));
    expect(within(board).queryByText("媒体探营日")).toBeNull();
    expect(within(board).getByText("IBC 展会实测")).toBeTruthy();
  });

  it("即将开幕:end_date ≥ 今天升序 + 真实倒计时;已完成活动不入列", async () => {
    renderBoard();
    const upcoming = await moduleSection("即将开幕");
    expect(await within(upcoming).findByText("IBC 展会实测")).toBeTruthy();
    expect(within(upcoming).getByText("媒体探营日")).toBeTruthy();
    expect(within(upcoming).queryByText("春季 KOL 聚会")).toBeNull();
  });

  it("预算执行:每活动 spent/plan 真账;未设预算行如实标注", async () => {
    renderBoard();
    const budget = await moduleSection("预算执行");
    expect(await within(budget).findByText(/\$200 \/ \$1\.0K · 20%/)).toBeTruthy();
    expect(within(budget).getByText(/\$150 \/ \$100 · 150%/)).toBeTruthy();
    expect(within(budget).getByText(/\$0 · 未设预算/)).toBeTruthy(); // SrcChip 口径行也含「未设预算」词,按行值收敛
  });
});

describe("详情接管(EventDetailTakeover · 旧详情七 tab 零改动)", () => {
  it("点活动卡 → 详情(七 tab 全在)→ 返回看板", async () => {
    renderBoard();
    const board = await moduleSection("活动看板");
    fireEvent.click(within(board).getByText("IBC 展会实测"));
    // 详情拉真端点(GET /events/{id})
    await waitFor(() => {
      const paths = apiFetchMock.mock.calls.map((call) => String(call[0]));
      expect(paths.some((p) => p.endsWith("/api/admin/vkpi/events/ev_live_1"))).toBe(true);
    });
    expect(await screen.findByText("概览")).toBeTruthy();
    for (const tab of ["预算+费用", "任务", "参与 KOL", "物料", "现场", "复盘"]) {
      expect(screen.getByText(tab)).toBeTruthy();
    }
    fireEvent.click(screen.getByText("返回 Events"));
    expect(await screen.findByText("新建 Event")).toBeTruthy();
  });

  it("dashboard 跳转 initialEventId:自动开详情 + consume 回调(旧页行为 1:1)", async () => {
    const onConsume = vi.fn();
    renderBoard({ initialEventId: "ev_live_1", onConsumeInitialEvent: onConsume });
    expect(await screen.findByText("返回 Events")).toBeTruthy();
    await waitFor(() => expect(onConsume).toHaveBeenCalled());
  });

  it("非参与者详情:权限隔离拦截页(team_ids 口径,旧组件原样)", async () => {
    renderBoard();
    const board = await moduleSection("活动看板");
    fireEvent.click(await within(board).findByText("媒体探营日")); // team_ids=["8"],当前登录 7
    expect(await screen.findByText(/不在/)).toBeTruthy();
    expect(screen.getByText(/你只能看到自己参与的 Event 详情/)).toBeTruthy();
  });
});

describe("弹窗 + 诚实空态/错误卡", () => {
  it("公司库存钮 → StockManagerModal(旧件零改动,真库存行)", async () => {
    renderBoard();
    fireEvent.click(await screen.findByText("公司库存(2)"));
    expect(await screen.findByText("公司库存表")).toBeTruthy();
    expect(screen.getByText("AF 85mm 样机")).toBeTruthy();
  });

  it("活动 0 行:看板如实空 + 环图诚实不画(不摆假卡)", async () => {
    routeApi({ events: () => Promise.resolve({ items: [] }) });
    renderBoard();
    expect(await screen.findByText(/暂无活动 —— 点右上「新建 Event」创建第一个/)).toBeTruthy();
    expect(await screen.findByText("暂无活动,状态环图诚实不画。")).toBeTruthy();
  });

  it("活动端点失败:页级横幅 + 模块错误卡(绝不静默)", async () => {
    routeApi({ events: () => Promise.reject(new Error("events endpoint down")) });
    renderBoard();
    const errors = await screen.findAllByText("活动列表读取失败");
    expect(errors.length).toBeGreaterThan(0);
    expect(screen.getAllByText(/events endpoint down/).length).toBeGreaterThan(0);
  });
});
