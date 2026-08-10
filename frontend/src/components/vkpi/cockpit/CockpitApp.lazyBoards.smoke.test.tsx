import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// lazy 三板块收尾冒烟(2026-07-12):KOL Pool / Shopify / Dealers 由静态 import 改
// React.lazy(CockpitApp.tsx)后,此处守三件事:
// 1) 动态导入路径 + 具名导出→default 映射拼写(KOL Pool 是具名别名 import,最易拼错;
//    映射落空 = 运行时 undefined 组件白屏,tsc 不拦);
// 2) Suspense fallback 先出现(chunk 在途);
// 3) chunk 到货后页面真渲染(apiToken="" → 三页各自诚实「未登录 / 无 token」卡,零网络)。
// mock seam:services/http.apiFetch 桩死 + RealMap 桩(jsdom 无 Leaflet 运行时,
// DealersBoardPage.smoke 同款),零真实 HTTP。

vi.mock("../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../services/http")>();
  return { ...actual, apiFetch: vi.fn(async () => { throw new Error("lazy smoke 禁真实网络"); }) };
});
vi.mock("./components/RealMap", () => ({
  RealMap: () => React.createElement("div", { "data-testid": "real-map-stub" }),
}));

import { LazyErrorBoundary } from "./components/LazyErrorBoundary";

// 与 CockpitApp.tsx 完全同款的三个 lazy 工厂(路径/具名→default 映射一字不差)
const KOLPoolPage = React.lazy(() => import("./pages/KolPoolBoardPage").then((module) => ({ default: module.KolPoolBoardPage }))) as React.ComponentType<any>;
const ShopifyBoardPage = React.lazy(() => import("./pages/ShopifyBoardPage").then((module) => ({ default: module.ShopifyBoardPage }))) as React.ComponentType<any>;
const DealerMapPage = React.lazy(() => import("./pages/DealersBoardPage").then((module) => ({ default: module.DealersBoardPage }))) as React.ComponentType<any>;

const mountLazy = (node: React.ReactNode, name: string) =>
  render(
    <LazyErrorBoundary name={name}>
      <React.Suspense fallback={<div>{name} 加载中...</div>}>{node}</React.Suspense>
    </LazyErrorBoundary>,
  );

describe("lazy 三板块(KolPool/Shopify/Dealers)Suspense 挂载", () => {
  it("KOL Pool:fallback 先出 → chunk 到货真渲染(具名别名→default 映射不落空)", async () => {
    mountLazy(<KOLPoolPage items={[]} loading={false} error="" apiToken="" staff={[]} />, "KolPool");
    expect(screen.getByText("KolPool 加载中...")).toBeTruthy();
    expect(
      (await screen.findAllByText(/未登录 \/ 无 token/, {}, { timeout: 5_000 })).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("KolPool 加载中...")).toBeNull();
  });

  it("Shopify:fallback 先出 → 无 token 诚实卡", async () => {
    mountLazy(<ShopifyBoardPage apiToken="" />, "Shopify");
    expect(screen.getByText("Shopify 加载中...")).toBeTruthy();
    expect(
      (await screen.findAllByText(/未登录 \/ 无 token/, {}, { timeout: 5_000 })).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Shopify 加载中...")).toBeNull();
  });

  it("Dealers:fallback 先出 → 无 token 诚实卡", async () => {
    mountLazy(<DealerMapPage apiToken="" />, "Dealers");
    expect(screen.getByText("Dealers 加载中...")).toBeTruthy();
    expect(
      (await screen.findAllByText(/未登录 \/ 无 token/, {}, { timeout: 5_000 })).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Dealers 加载中...")).toBeNull();
  });
});
