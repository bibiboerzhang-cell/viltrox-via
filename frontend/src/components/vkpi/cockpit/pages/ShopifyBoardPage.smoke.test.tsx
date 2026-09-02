import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// Shopify 改版冒烟(金样板 ProjectsBoardPage.smoke 同构):
// - 页壳:pagehead(Shopify + 归因行徽 + 刷新 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡:有真数才真值(归因收入对账净额 / 归因明细行数 / 联盟推广 GMV),
//   连不上的源 pending 诚实卡注明缺配置(直连订单未配置店铺凭据);
// - 金额红线双向守卫:*_cents → centsToUsd 唯一换算点(历史 100 倍缺陷:518350 分
//   必须是 $5,183.50,绝不是 $518,350.00);GOAFFPRO *_usd 已是美元 → asUsd 直读,
//   绝不二次 ÷100($6,495.49 绝不显示成 $64.95);
// - 旧页零丢失:联盟归因汇总(totals 条/搜索/同步刷新/partial ⚠)、连接向导整页内嵌
//   (环境变量/Webhook/测试同步/同步历史)、店铺凭据旧表单(palette 添加后可见);
// - 全量 + 连续翻:归因明细单条详情 ‹#n/N› + 方向键;
// - 布局键 vkpi-shopify-layout-v1 + 不传 apiToken 给板 → 绝不写账户级 dashboard_layout_v1。
// mock seam:services/http.apiFetch(全页唯一网络出口),零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ShopifyBoardPage } from "./ShopifyBoardPage";
import { centsToUsd, fmtUsd2 } from "../../../../services/vkpi/projectsBoard-api";
import { asUsd } from "../../../../services/vkpi/shopifyBoard-api";

/* ============ 真形状 mock(对照本地库实测:归因 7 行净额 518350 分;联盟按已配置演) ============ */

// 对账 GMV:订单台账空 → 归因净额(confirmed+refund)= 518350 分 = $5,183.50
const GMV_OK = {
  gmv_cents: 518350,
  order_count: 5,
  currency: "USD",
  mixed_currency: false,
  gmv_source: "vkpi_sales_attributions.revenue_cents[shopify,confirmed+refund,net]",
  attribution_gmv_cents: 518350,
  order_ledger_gmv_cents: 0,
};

const ATTR_OK = {
  attributions: [
    { id: 1, source_platform: "shopify", source_ref: "ref-1", project_id: 7, kol_id: 9, staff_id: 3, product_sku: "VL-LEN072", order_id: "SO-1001", revenue_cents: 649549, commission_cents: 64955, currency: "USD", attribution_model: "link", confidence: "confirmed", occurred_at: "2026-07-01T10:00:00Z", imported_at: "2026-07-02T00:00:00Z" },
    { id: 2, source_platform: "shopify", source_ref: "ref-2", project_id: null, kol_id: null, staff_id: null, product_sku: null, order_id: "SO-1002", revenue_cents: -1250, commission_cents: null, currency: "USD", attribution_model: "webhook", confidence: "refund", occurred_at: "2026-07-03T10:00:00Z", imported_at: "2026-07-03T12:00:00Z" },
  ],
};

// 店铺直连:未配置(本地实况:vkpi_shopify_sync_runs 全 not_configured)
const STATUS_NOT_CONFIGURED = {
  provider_status: "not_configured",
  shop_domain: null,
  shop_domain_configured: false,
  access_token_configured: false,
  webhook_secret_configured: false,
  env_vars: { SHOPIFY_SHOP_DOMAIN: false, SHOPIFY_ADMIN_ACCESS_TOKEN: false, SHOPIFY_WEBHOOK_SECRET: false },
  webhooks: { orders_create: "/api/vkpi/webhooks/shopify/orders", orders_refund_create: "/api/vkpi/webhooks/shopify/refunds" },
  sync_runs: [
    { id: 1, sync_uid: "s-1", source: "manual", started_at: "2026-07-09T00:00:00Z", status: "not_configured", orders_received: 0, orders_matched: 0, orders_unmatched: 0 },
  ],
  message: "Shopify Admin API 未配置",
};

const STATUS_CONNECTED = {
  ...STATUS_NOT_CONFIGURED,
  provider_status: "connected",
  shop_domain: "demo.myshopify.com",
  shop_domain_configured: true,
  access_token_configured: true,
  webhook_secret_configured: true,
  credential_fields: { shop_domain: true, access_token: true, webhook_secret: true },
  message: "Shopify Admin API 已通过真实探测",
};

const GOAFF_CONNECTED = { status: "connected", access_token_configured: true, public_token_configured: false, source: "db", api_base: "https://api.goaffpro.com/v1", token: "abcd...wxyz" };
const GOAFF_NOT_CONFIGURED = { status: "not_configured", access_token_configured: false, public_token_configured: false, source: "none" };

// 联盟归因汇总:gmv_usd/commission_usd 服务端已是美元(缓存 gmv_cents ÷ 100 后端换算)
const SUMMARY_OK = {
  ok: true,
  count: 2,
  items: [
    { kol_pool_id: 101, kol_name: "Alpha Creator", kol_handle: "@alpha", kol_platform: "youtube", kol_avatar: "https://scontent-ord5-1.cdninstagram.com/v/avatar.jpg", affiliate_id: "a1", ref_code: "alpha10", coupon: "ALPHA10", commission_rate: "10%", status: "approved", tracking_url: "https://viltroxvia.com/?ref=alpha10", clicks: 100, orders: 6, gmv_usd: 6495.49, commission_usd: 649.55, currency: "USD", partial: false },
    { kol_pool_id: 102, kol_name: "Beta Creator", kol_handle: "@beta", kol_platform: "tiktok", affiliate_id: "b2", ref_code: "beta10", coupon: "", commission_rate: "10%", status: "approved", tracking_url: "", clicks: 20, orders: 1, gmv_usd: 100, commission_usd: 10, currency: "USD", partial: true },
  ],
  totals: { kol_count: 2, clicks: 120, orders: 7, gmv_usd: 6595.49, commission_usd: 659.55 },
  last_synced_at: "2026-07-10T00:00:00Z",
  stale_count: 0,
  note: null,
};

type Overrides = { gmv?: unknown; attr?: unknown; goaffCreds?: unknown; summary?: unknown; status?: unknown };

function routeApi(overrides: Overrides = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: RequestInit) => {
    const p = String(path);
    const method = String(init?.method || "GET").toUpperCase();
    const pick = (value: unknown, fallback: unknown) => {
      const v = value ?? fallback;
      if (v instanceof Error) throw v;
      return v;
    };
    if (p.startsWith("/api/admin/vkpi/shopify/gmv")) return pick(overrides.gmv, GMV_OK);
    if (p.startsWith("/api/admin/vkpi/attribution")) return pick(overrides.attr, ATTR_OK);
    if (p.startsWith("/api/marketing/shopify/status")) return pick(overrides.status, STATUS_NOT_CONFIGURED);
    if (p.startsWith("/api/marketing/shopify/sync") && method === "POST") return { sync_uid: "s-2", status: "not_configured", provider_status: "not_configured", orders_received: 0 };
    if (p.startsWith("/api/admin/vkpi/goaffpro/creds")) return pick(overrides.goaffCreds, GOAFF_CONNECTED);
    if (p.startsWith("/api/admin/vkpi/goaffpro/sync-metrics") && method === "POST") return { ok: true, synced: 2, errors: 0, synced_at: "2026-07-10T01:00:00Z" };
    if (p.startsWith("/api/admin/vkpi/goaffpro/summary")) return pick(overrides.summary, SUMMARY_OK);
    if (p.startsWith("/api/admin/vkpi/shopify/creds") && method === "POST") return { status: "ok", provider_status: "configured", message: "凭据已加密保存。" };
    throw new Error(`unexpected apiFetch: ${method} ${p}`);
  });
}

const renderBoard = (props: Record<string, unknown> = {}) => render(<ShopifyBoardPage apiToken="t" {...(props as any)} />);

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  routeApi();
});

/* ============ 金额红线单测(双向;历史 100 倍缺陷守卫,照抄金样板) ============ */
describe("金额换算(美分→美元唯一换算点 + 已美元直读)", () => {
  it("centsToUsd:518350 分 = 5183.50 美元;非数/空 → null 诚实缺席", () => {
    expect(centsToUsd(518350)).toBe(5183.5);
    expect(centsToUsd(649549)).toBe(6495.49);
    expect(centsToUsd("100")).toBe(1);
    expect(centsToUsd(0)).toBe(0);
    expect(centsToUsd(-1250)).toBe(-12.5);
    expect(centsToUsd(null)).toBeNull();
    expect(centsToUsd(undefined)).toBeNull();
    expect(centsToUsd("abc")).toBeNull();
  });

  it("fmtUsd2:两位小数门面;100 倍缺陷守卫(绝不把分当元)", () => {
    expect(fmtUsd2(centsToUsd(518350))).toBe("$5,183.50");
    expect(fmtUsd2(centsToUsd(518350))).not.toBe("$518,350.00");
    expect(fmtUsd2(0)).toBe("$0.00");
    expect(fmtUsd2(null)).toBe("—");
  });

  it("asUsd:*_usd 字段已是美元 → 直读零二次换算(6495.49 绝不变 64.95)", () => {
    expect(asUsd(6495.49)).toBe(6495.49);
    expect(asUsd("6495.49")).toBe(6495.49);
    expect(fmtUsd2(asUsd(6495.49))).toBe("$6,495.49");
    expect(fmtUsd2(asUsd(6495.49))).not.toBe("$64.95");
    expect(asUsd(0)).toBe(0);
    expect(asUsd(null)).toBeNull();
    expect(asUsd(undefined)).toBeNull();
    expect(asUsd("abc")).toBeNull();
  });
});

/* ============ 页壳 + KPI 带 + 注册表 ============ */
describe("ShopifyBoardPage smoke(页壳 + KPI 带 + 注册表 + 布局键)", () => {
  it("KPI 带四卡:归因收入 $5,183.50(对账净额)/ 归因明细 2 行 / 联盟推广 $6,595.49 / 直连订单 pending 注明缺配置", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect((await screen.findAllByText("归因收入")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("归因明细").length).toBeGreaterThan(0);
    expect(screen.getAllByText("联盟推广").length).toBeGreaterThan(0);
    expect(screen.getAllByText("直连订单").length).toBeGreaterThan(0);
    const kpis = document.querySelectorAll(".ds-kpi");
    expect(kpis.length).toBe(4);
    // 归因收入:518350 分 → $5,183.50(绝不是 $518,350.00)
    expect(await screen.findByText("$5,183.50")).toBeTruthy();
    expect(screen.queryByText("$518,350.00")).toBeNull();
    // 联盟推广:totals.gmv_usd 已是美元 → $6,595.49(绝不二次 ÷100 成 $65.95)
    expect((await screen.findAllByText("$6,595.49")).length).toBeGreaterThan(0);
    expect(screen.queryByText("$65.95")).toBeNull();
    // 直连订单:店铺凭据未配置 → pending 诚实卡注明缺配置
    expect(screen.getAllByText(/未配置店铺凭据/).length).toBeGreaterThan(0);
    // 真端点全被调
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/shopify/gmv"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/attribution"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/marketing/shopify/status"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/goaffpro/creds"))).toBe(true);
    expect(calledPaths().some((p) => p.startsWith("/api/admin/vkpi/goaffpro/summary"))).toBe(true);
  });

  it("accepts provider_status connected as configured across the current Shopify UI", async () => {
    routeApi({ status: STATUS_CONNECTED });
    renderBoard();

    await screen.findByText("Shopify");
    // 页壳先于 GMV 请求落地;慢 runner(GitHub Linux)上此时 KPI 还是「直连订单—读取中…」。
    // 等到真值出现再断言,否则是竞态不是行为。
    const readDirectKpi = () =>
      Array.from(document.querySelectorAll(".ds-kpi")).find((row) =>
        row.textContent?.includes("直连订单"),
      );
    await waitFor(() => {
      expect(readDirectKpi()?.textContent).toContain("$0.00");
    });
    const directKpi = readDirectKpi();
    expect(directKpi?.textContent).not.toContain("未配置店铺凭据");
    expect(screen.getAllByText(/已配置/).length).toBeGreaterThan(0);
  });

  it("默认布局五模块在场;palette 备选(店铺凭据旧表单)不进默认;编辑布局可从 palette 添加", async () => {
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    ["联盟归因汇总", "联盟连接", "归因明细", "店铺连接向导"].forEach((title) => {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    });
    // palette 备选不进默认
    expect(screen.queryByText("店铺凭据(旧)")).toBeNull();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    expect(screen.getAllByText("店铺凭据(旧)").length).toBeGreaterThan(0);
  });

  it("布局键 vkpi-shopify-layout-v1 生效;不传 apiToken 给板 → 绝不写账户级布局", async () => {
    window.localStorage.setItem("vkpi-shopify-layout-v1", JSON.stringify([{ moduleKey: "kpiS", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    expect(screen.queryByText("联盟归因汇总")).toBeNull();
    expect(screen.queryByText("店铺连接向导")).toBeNull();
    expect(calledPaths().some((p) => p.includes("preference"))).toBe(false);
  });

  it("联盟未配置:KPI 第三卡 pending 注明配置入口;汇总模块 pending 卡如实(绝不装数)", async () => {
    routeApi({ goaffCreds: GOAFF_NOT_CONFIGURED, summary: { ok: true, items: [], count: 0, totals: { kol_count: 0, clicks: 0, orders: 0, gmv_usd: 0, commission_usd: 0 } } });
    renderBoard();
    expect((await screen.findAllByText(/设置 → 联盟营销/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/联盟推广 token 未填/)).length).toBeGreaterThan(0);
    expect(screen.queryByText("$6,595.49")).toBeNull();
  });
});

/* ============ 旧页零丢失(联盟归因汇总 / 连接向导 / 旧凭据表单) ============ */
describe("ShopifyBoardPage 旧功能零丢失", () => {
  it("联盟归因汇总:totals 条真值 + 行(partial ⚠)+ 搜索提交带参重拉 + 同步刷新 POST sync-metrics", async () => {
    renderBoard();
    expect(await screen.findByText("Alpha Creator")).toBeTruthy();
    const avatar = screen.getByAltText("Alpha Creator");
    expect(avatar.getAttribute("src")).toBe(
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent("https://scontent-ord5-1.cdninstagram.com/v/avatar.jpg")}`,
    );
    expect(document.querySelector('img[src^="https://scontent-ord5-1.cdninstagram.com"]')).toBeNull();
    // totals 条(gmv_usd 已美元直读)
    expect(screen.getByText("总点击")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
    expect(screen.getByText("总订单")).toBeTruthy();
    expect(screen.getAllByText("$659.55").length).toBeGreaterThan(0);
    // partial ⚠ 徽(Beta 行拉数失败如实标)
    expect(screen.getByText("Beta Creator").parentElement?.textContent).toContain("⚠");
    // 搜索:提交后带 search 参重拉
    fireEvent.change(screen.getByPlaceholderText("搜 KOL 名 / handle / ref 码 / 优惠码"), { target: { value: "alpha" } });
    fireEvent.click(screen.getByText("查询"));
    await waitFor(() => expect(calledPaths().some((p) => p.includes("/goaffpro/summary") && p.includes("search=alpha"))).toBe(true));
    // 同步刷新:POST sync-metrics 后重拉
    const summaryCallsBefore = calledPaths().filter((p) => p.includes("/goaffpro/summary")).length;
    fireEvent.click(screen.getByText("同步"));
    await waitFor(() => expect(calledPaths().some((p) => p.includes("/goaffpro/sync-metrics"))).toBe(true));
    await waitFor(() => {
      const after = calledPaths().filter((p) => p.includes("/goaffpro/summary")).length;
      expect(after).toBeGreaterThan(summaryCallsBefore);
    });
  });

  it("联盟连接模块:状态徽 + 凭据布尔 + 来源人话(masked-only,零明文)", async () => {
    renderBoard();
    expect(await screen.findByText("已连接")).toBeTruthy();
    expect(screen.getByText("管理密钥")).toBeTruthy();
    expect(screen.getByText("加密落库")).toBeTruthy();
    expect(screen.getByText("abcd...wxyz")).toBeTruthy();
  });

  it("连接向导整页内嵌:环境变量三行 + Webhook 地址 + 测试同步 + 同步历史(not_configured 如实)", async () => {
    renderBoard();
    expect(await screen.findByText("第 1 步 · 配置环境变量")).toBeTruthy();
    expect(screen.getAllByText("SHOPIFY_SHOP_DOMAIN").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SHOPIFY_ADMIN_ACCESS_TOKEN").length).toBeGreaterThan(0);
    expect(screen.getByText("第 2 步 · 在 Shopify Admin 注册 Webhook")).toBeTruthy();
    // 「测试同步」词也住 SrcChip 口径行 → 用 role=button 精确取按钮
    expect(screen.getByRole("button", { name: "测试同步" })).toBeTruthy();
    expect(screen.getByText("同步历史 (vkpi_shopify_sync_runs)")).toBeTruthy();
    expect((await screen.findAllByText("not_configured")).length).toBeGreaterThan(0);
    // 测试同步:POST 真端点 + 历史重拉
    fireEvent.click(screen.getByRole("button", { name: "测试同步" }));
    await waitFor(() => expect(calledPaths().some((p) => p.startsWith("/api/marketing/shopify/sync"))).toBe(true));
  });

  it("店铺凭据(旧):palette 添加后折叠块在场,展开可见表单(零改动内嵌)", async () => {
    renderBoard();
    expect(await screen.findByText("指标带")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    const addButtons = screen.getAllByText("店铺凭据(旧)");
    fireEvent.click(addButtons[addButtons.length - 1]);
    expect(await screen.findByText("自建 Shopify（旧 · 待退役）")).toBeTruthy();
    fireEvent.click(screen.getByText("自建 Shopify（旧 · 待退役）"));
    expect(await screen.findByText("保存并连接")).toBeTruthy();
    expect(screen.getByPlaceholderText("your-store.myshopify.com")).toBeTruthy();
  });
});

/* ============ 全量 + 连续翻(归因明细) ============ */
describe("ShopifyBoardPage 行模块弹窗", () => {
  it("归因明细:点行 → 详情 ‹#n/N› + 金额美分→美元 + 退款负行如实;↓ 连续翻", async () => {
    renderBoard();
    expect(await screen.findByText("SO-1001")).toBeTruthy();
    fireEvent.click(screen.getByText("SO-1001"));
    expect(await screen.findByText("#1/2")).toBeTruthy();
    expect(screen.getAllByText(/\$6,495\.49/).length).toBeGreaterThan(0);
    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByText("#2/2")).toBeTruthy();
    // 退款负行:-1250 分 → -$12.50 语义(fmtUsd2 输出含负号),绝不吞行
    expect(screen.getAllByText(/refund/).length).toBeGreaterThan(0);
  });

  it("归因端点失败 → KPI 明细卡 pending 带原因(绝不编数)", async () => {
    routeApi({ attr: new Error("boom") });
    renderBoard();
    expect((await screen.findAllByText("归因收入")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/读取失败[:：]boom/)).length).toBeGreaterThan(0);
  });
});
