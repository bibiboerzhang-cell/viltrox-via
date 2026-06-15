// Smoke test for KolPortalPage — 真挂载(非 shallow),mock 掉网络服务层。
//
// 该页无 props:从 window.location 取 token(useMemo,挂载时跑),
// 然后调 fetchPortal(token) 拉公开只读数据。我们:
//   1) vi.mock 服务层 —— 保留真 PortalInvalidError(页面用 instanceof 判它),
//      只把 fetchPortal 换成 resolve 一份贴近真实形状的 PortalData。
//   2) 把 window.location 指到带 token 的 URL,驱动「有数据」渲染分支
//      (即最可能暴露 React-child / map 渲染崩溃的那条路径)。
// 断言:① 不抛 ② 页面真实文案出现在 DOM。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { render, screen, cleanup } from "@testing-library/react";

// 保留真 PortalInvalidError(class),只覆盖 fetchPortal。
// 注意:vi.mock 会被提升到文件顶部,工厂里不能引用顶层变量 ——
// 所以 samplePortal 直接在工厂内构造。
vi.mock("../../../services/vkpi/kolPortal-api", async () => {
  const actual = await vi.importActual<
    typeof import("../../../services/vkpi/kolPortal-api")
  >("../../../services/vkpi/kolPortal-api");
  const samplePortal = {
    kol: {
      kol_pool_id: 101,
      platform: "youtube",
      handle: "creator_jane",
      display_name: "Jane Creator",
      avatar_url: "",
      profile_url: "https://example.com/jane",
      country: "US",
      language: "en",
      primary_topic: "tech",
      followers: 123456,
    },
    projects: [
      {
        project_id: 7,
        project_name: "Spring Launch",
        product_sku: "SKU-9",
        product_name: "Mega Lens",
        platform: "youtube",
        stage: "outreach",
        stage_status: "active",
      },
    ],
    gmv: { orders_count: 12, gmv_cents: 345600, currency: "USD" },
    clicks: { total: 5000, valid: 4200 },
    attributions: [
      {
        revenue_cents: 28800,
        commission_cents: 2880,
        currency: "USD",
        occurred_at: "2026-05-01T00:00:00Z",
      },
    ],
  };
  return {
    ...actual,
    fetchPortal: vi.fn().mockResolvedValue(samplePortal),
  };
});

import KolPortalPage from "./KolPortalPage";

const originalLocation = window.location;

function setLocation(href: string): void {
  // jsdom 的 window.location 默认只读 —— 用 delete + 赋值覆盖。
  delete (window as unknown as { location?: unknown }).location;
  (window as unknown as { location: unknown }).location = new URL(href);
}

beforeEach(() => {
  setLocation("http://localhost/portal/kol/demo-token-abc");
});

afterEach(() => {
  cleanup();
  (window as unknown as { location: unknown }).location = originalLocation;
  vi.clearAllMocks();
});

describe("KolPortalPage", () => {
  it("mounts and renders the read-only KOL portal without crashing (data path)", async () => {
    render(React.createElement(KolPortalPage));

    // fetchPortal 是 resolved promise,等异步渲染落地后断言真实文案。
    expect(await screen.findByText("我的合作项目")).toBeTruthy();
    // KPI 卡片 + footer = 页面专属内容,确认确实渲染了主体而非 fallback。
    expect(screen.getByText("归因 GMV")).toBeTruthy();
    expect(screen.getByText("Jane Creator")).toBeTruthy();
    expect(
      screen.getByText("这是您的只读专属页面，仅展示您自己的数据。"),
    ).toBeTruthy();
  });

  it("shows the friendly invalid-link notice when no token is present", async () => {
    setLocation("http://localhost/portal/kol/");
    render(React.createElement(KolPortalPage));
    expect(await screen.findByText("链接无效或已过期")).toBeTruthy();
  });
});
