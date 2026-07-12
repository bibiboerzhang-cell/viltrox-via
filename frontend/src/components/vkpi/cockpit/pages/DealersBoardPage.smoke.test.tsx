import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Dealers 改版冒烟(金样板 ShopifyBoardPage.smoke 同构):
// - 页壳:pagehead(经销商 + 家数徽 + 刷新 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡:有真数才真值(经销商数 / 已定位 / 覆盖州 / 国家数);
//   vkpi_dealers 0 行 → 全带 pending 诚实空态注明数据在线上库(本刀核心验收);
// - 地区分布:有数据才画(NY/CA 真行);0 行 → 诚实空,绝不编条形;
// - 地图 embed:RealMap 零改动收编(jsdom 无 Leaflet 运行时 → 桩,同旧页冒烟);
//   0 定位点 → 角标诚实注明;
// - 旧页零丢失:预检(record_only=true)/ 有界抓取(≤20,record_only=false + 重拉)/
//   回执行真字段 / 手动添加(必填闸 + 幂等 POST + 成功清空 + 重拉)/ 待补定位清单;
// - 全量 + 连续翻:名录单条详情 ‹#n/N› + 方向键 + 绝对入库时间;
// - 布局键 vkpi-dealers-layout-v1 + 不传 apiToken 给板 → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口)+ RealMap 桩,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// RealMap 用 Leaflet,jsdom 里无地图运行时 → 桩成占位 div(旧页冒烟同款;
// 零改动收编红线:真组件文件本刀一字未动)。
vi.mock("../components/RealMap", () => ({
  RealMap: (props: Record<string, unknown>) =>
    React.createElement("div", { "data-testid": "real-map-stub", "data-pins": Array.isArray(props.pins) ? (props.pins as unknown[]).length : 0 }),
}));

import { DealersBoardPage } from "./DealersBoardPage";
import { scrapeReceiptText } from "./DealersBoardPage.modules";
import { ThemeProvider } from "../../../../app/providers/ThemeProvider";

/* ============ 真形状 mock(对照 vkpi_dealers 列 id/name/address/city/state/lat/lng/source/created_at;
   本地库实况 0 行 → 空态用真空形,有数据形按后端字段演) ============ */

const DEALERS_OK = {
  dealers: [
    { id: 1, name: "B&H Photo", address: "420 9th Ave", city: "New York", state: "NY", lat: 40.7539, lng: -73.9962, source: "manual", created_at: "2026-07-01T10:00:00Z" },
    { id: 2, name: "Adorama", address: "42 W 18th St", city: "New York", state: "NY", lat: 40.7405, lng: -73.9936, source: "scrape", created_at: "2026-07-02T10:00:00Z" },
    { id: 3, name: "Samy's Camera", address: "431 S Fairfax Ave", city: "Los Angeles", state: "CA", lat: null, lng: null, source: "manual", created_at: "2026-07-03T10:00:00Z" },
  ],
};

// locations 端点只吐 lat/lng 齐全行(后端 list_dealer_pins 口径);color 为服务端下发值
const LOCS_OK = {
  pins: [
    { name: "B&H Photo", address: "420 9th Ave", city: "New York", state: "NY", lat: 40.7539, lng: -73.9962, color: "#10b981" },
    { name: "Adorama", address: "42 W 18th St", city: "New York", state: "NY", lat: 40.7405, lng: -73.9936, color: "#10b981" },
  ],
};

const SCRAPE_PREVIEW = { ok: true, source: "usa_camera_retailers", requested: 20, inserted: 0, skipped: 0, geocoded: 0, pending_geocode: 0, record_only: true, plan: [], errors: [] };
const SCRAPE_RUN = { ok: true, source: "usa_camera_retailers", requested: 20, inserted: 5, skipped: 15, geocoded: 4, pending_geocode: 1, record_only: false, errors: [{ name: "X", error: "geocode miss" }] };

type Overrides = { dealers?: unknown; locs?: unknown };

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    const pick = (value: unknown, fallback: unknown) => {
      const v = value ?? fallback;
      if (v instanceof Error) throw v;
      return v;
    };
    if (p.startsWith("/api/admin/vkpi/dealers/locations")) return pick(overrides.locs, LOCS_OK);
    if (p.startsWith("/api/admin/vkpi/dealers/scrape-enqueue") && method === "POST") {
      const body = JSON.parse(String(init?.body || "{}"));
      return body.record_only ? SCRAPE_PREVIEW : SCRAPE_RUN;
    }
    if (p.startsWith("/api/admin/vkpi/dealers") && method === "POST") return { id: 9, geocoded: false, pending_geocode: true };
    if (p.startsWith("/api/admin/vkpi/dealers")) return pick(overrides.dealers, DEALERS_OK);
    throw new Error(`unexpected apiFetch: ${method} ${p}`);
  });
}

// 地图收编件运行时读 --ds-accent(useTheme 依赖)→ 冒烟同真栈包 ThemeProvider
const renderBoard = () =>
  render(
    <ThemeProvider>
      <DealersBoardPage apiToken="t" />
    </ThemeProvider>,
  );

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

/* ============ 回执文案单测(字段全来自端点真实返回) ============ */
describe("scrapeReceiptText(回执行真字段)", () => {
  it("预检 / 抓取动词按 record_only 分流;errors 有才带失败段", () => {
    expect(scrapeReceiptText(SCRAPE_PREVIEW as never)).toBe("预检:请求 20 · 新增 0 · 跳过 0 · 已定位 0 · 待补 0");
    expect(scrapeReceiptText(SCRAPE_RUN as never)).toBe("抓取:请求 20 · 新增 5 · 跳过 15 · 已定位 4 · 待补 1 · 失败 1");
  });
});

/* ============ 页壳 + KPI 带 + 注册表 ============ */
describe("DealersBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡真值:经销商数 3 / 已定位 2 / 覆盖州 2 / 国家数 1;地区条形 NY/CA;端点全被调", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect((await screen.findAllByText("经销商数")).length).toBeGreaterThan(0);
    ["已定位", "覆盖州", "国家数"].forEach((label) => {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    });
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    const bandText = Array.from(kpis).map((el) => el.textContent || "").join("|");
    expect(bandText).toContain("3");
    expect(bandText).toContain("2");
    expect(bandText).toContain("1");
    // 地区分布条形(有数据才画):NY 2 / CA 1
    expect(screen.getAllByText("NY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("CA").length).toBeGreaterThan(0);
    // 地图 embed 收编在场(桩),吃到 2 个定位 pin
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("2");
    // 待补定位清单:缺经纬度的 Samy's 在列
    expect(screen.getAllByText("Samy's Camera").length).toBeGreaterThan(0);
    // 真端点全被调
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/dealers?") || p === "/api/admin/vkpi/dealers")).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/dealers/locations"))).toBe(true);
  });

  it("vkpi_dealers 0 行 → KPI 带全 pending 诚实空态注明数据在线上库;地区不画;地图角标如实", async () => {
    routeApi({ dealers: { dealers: [] }, locs: { pins: [] } });
    renderBoard();
    expect((await screen.findAllByText(/经销商数据在线上库/)).length).toBeGreaterThan(0);
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    // 四卡全 pending(值位 — 空态),绝不编数
    Array.from(kpis).forEach((el) => {
      expect(el.textContent).toContain("—");
      expect(el.textContent).toContain("库内 0 行");
    });
    // 地区分布:有数据才画 → 诚实空,零条形
    expect(screen.getAllByText(/有数据才画分布/).length).toBeGreaterThan(0);
    // 地图 embed 仍在(0 pin)+ 角标诚实注明
    expect(screen.getByTestId("real-map-stub").getAttribute("data-pins")).toBe("0");
    expect(screen.getAllByText(/0 个定位点/).length).toBeGreaterThan(0);
  });

  it("默认布局六模块在场;不传 apiToken 给板 → 绝不写账户级布局", async () => {
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    ["地区分布", "待补定位", "经销商地图", "经销商名录", "录入与采集"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });

  it("布局键 vkpi-dealers-layout-v1 生效(本机记忆收窄后其余模块退场)", async () => {
    window.localStorage.setItem("vkpi-dealers-layout-v1", JSON.stringify([{ moduleKey: "kpiD", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    expect(screen.queryByText("经销商名录")).toBeNull();
    expect(screen.queryByText("录入与采集")).toBeNull();
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });
});

/* ============ 旧页零丢失(预检 / 有界抓取 / 手动添加 / 待补清单) ============ */
describe("DealersBoardPage 旧功能零丢失", () => {
  it("预检:POST scrape-enqueue record_only=true,回执行真字段,不重拉", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(screen.getByRole("button", { name: "预检" }));
    expect(await screen.findByText("预检:请求 20 · 新增 0 · 跳过 0 · 已定位 0 · 待补 0")).toBeTruthy();
    const scrapeCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes("scrape-enqueue"));
    expect(scrapeCall).toBeTruthy();
    expect(JSON.parse(String((scrapeCall![1] as RequestInit).body))).toEqual({ limit: 20, record_only: true });
    // 预检不重拉(旧页同款:record-only 零副作用)
    const dealersCallsAfter = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    expect(dealersCallsAfter).toBe(dealersCallsBefore);
  });

  it("有界抓取:record_only=false + 回执带失败段 + 成功后重拉", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(screen.getByRole("button", { name: "有界抓取(≤20)" }));
    expect(await screen.findByText("抓取:请求 20 · 新增 5 · 跳过 15 · 已定位 4 · 待补 1 · 失败 1")).toBeTruthy();
    const scrapeCall = apiFetchMock.mock.calls.find((call) => String(call[0]).includes("scrape-enqueue"));
    expect(JSON.parse(String((scrapeCall![1] as RequestInit).body))).toEqual({ limit: 20, record_only: false });
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
      expect(after).toBeGreaterThan(dealersCallsBefore);
    });
  });

  it("手动添加:名称+地址必填闸(按钮禁用);填齐 → POST 幂等 payload + 成功清空 + 重拉", async () => {
    renderBoard();
    expect(await screen.findByText("录入与采集")).toBeTruthy();
    const addButton = screen.getByRole("button", { name: "添加" });
    expect((addButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText("名称*"), { target: { value: "KEH Camera" } });
    fireEvent.change(screen.getByPlaceholderText("地址*"), { target: { value: "4900 Highlands Pkwy" } });
    fireEvent.change(screen.getByPlaceholderText("城市"), { target: { value: "Smyrna" } });
    fireEvent.change(screen.getByPlaceholderText("州"), { target: { value: "GA" } });
    expect((addButton as HTMLButtonElement).disabled).toBe(false);
    const dealersCallsBefore = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
    fireEvent.click(addButton);
    expect(await screen.findByText("已添加:KEH Camera")).toBeTruthy();
    const createCall = apiFetchMock.mock.calls.find(
      (call) => String(call[0]) === "/api/admin/vkpi/dealers" && String((call[1] as RequestInit)?.method).toUpperCase() === "POST",
    );
    expect(JSON.parse(String((createCall![1] as RequestInit).body))).toEqual({ name: "KEH Camera", address: "4900 Highlands Pkwy", city: "Smyrna", state: "GA" });
    // 成功清空(旧页同款)+ 重拉
    expect((screen.getByPlaceholderText("名称*") as HTMLInputElement).value).toBe("");
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.startsWith("/api/admin/vkpi/dealers?")).length;
      expect(after).toBeGreaterThan(dealersCallsBefore);
    });
  });

  it("待补定位:缺经纬度行在列,徽「待定位」", async () => {
    renderBoard();
    // Samy's 同时住待补定位 + 名录两个模块 → findAll
    expect((await screen.findAllByText("Samy's Camera")).length).toBeGreaterThan(1);
    expect(screen.getAllByText("待定位").length).toBeGreaterThan(0);
  });

  it("待补定位:全部已定位时如实空行(不装数据也不装空)", async () => {
    routeApi({ dealers: { dealers: DEALERS_OK.dealers.slice(0, 2) } });
    renderBoard();
    expect(await screen.findByText("全部已定位。")).toBeTruthy();
  });
});

/* ============ 全量 + 连续翻(经销商名录) ============ */
describe("DealersBoardPage 行模块弹窗", () => {
  it("名录:点行 → 详情 ‹#n/N› + 绝对入库时间 + 库记录 id;↓ 连续翻", async () => {
    renderBoard();
    expect((await screen.findAllByText("B&H Photo")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText("B&H Photo")[0]);
    expect(await screen.findByText("#1/3")).toBeTruthy();
    expect(screen.getAllByText(/vkpi_dealers #1/).length).toBeGreaterThan(0);
    // 绝对时间戳口径(存 UTC · 按浏览器时区显示)在详情行如实标注
    expect(screen.getAllByText(/UTC 存 · 按浏览器时区显示/).length).toBeGreaterThan(0);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2/3")).toBeTruthy();
    fireEvent.keyDown(window, { key: "ArrowDown" });
    // 第 3 条是待定位行:定位字段如实「待补经纬度」
    expect(await screen.findByText("#3/3")).toBeTruthy();
    expect(screen.getAllByText(/待补经纬度/).length).toBeGreaterThan(0);
  });

  it("名录端点失败 → KPI pending 带原因 + 模块 ErrorCard(绝不编数)", async () => {
    routeApi({ dealers: new Error("boom") });
    renderBoard();
    expect((await screen.findAllByText(/读取失败[::]boom/)).length).toBeGreaterThan(0);
  });
});
