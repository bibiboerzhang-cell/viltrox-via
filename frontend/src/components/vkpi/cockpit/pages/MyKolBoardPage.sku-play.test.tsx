import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

// 波 D·C 车道 单品播放数据模块冒烟:
// - 汇总条 = 单品/视频/红人/已实测 真值 + 手动「刷新」重读;
// - 每单品一行组头(名称+code / N 视频·N 红人 / 累计播放 null=未实测 / Δ7 天 ↑↓ 色 / 最后实测),
//   点组头展开逐视频行(标题链接原帖 / KOL+平台 / 播放·点赞 / Δ7 天 / 追踪状态 chip);
// - 诚实空态:无登记 → 引导去内容墙/KOL 详情点「数据关注」;404 → 该版本暂无;错误 → 错误卡。
// mock seam:services/http.apiFetch(与 watchlist 冒烟同款),ApiResponseError 真类用于 404 判定。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ApiResponseError } from "../../../../services/http";
import { SKU_PLAY_CHANGED_EVENT } from "./MyKolBoardPage.data-watch";
import { SkuPlayModule } from "./MyKolBoardPage.sku-play";

const OVERVIEW = {
  contract: "my_kol_sku_play_overview_v1",
  generated_at: "2026-08-23T01:00:00+00:00",
  summary: { skus: 2, videos: 3, kols: 2, measured_videos: 2 },
  groups: [
    {
      sku_code: "AF85F14-Z",
      sku_name: "AF 85mm F1.4 Pro",
      videos: 2,
      kols: 2,
      latest_measured_at: "2026-08-22T10:00:00+00:00",
      total_views: 13579,
      delta: { d1: null, d7: 2468, d30: null },
      items: [
        {
          evidence_id: 501, kol_pool_id: 101, kol_name: "Alpha Cam", platform: "youtube",
          title: "85mm 实拍评测", content_url: "https://youtube.com/watch?v=abc",
          view_count: 12345, like_count: 678, measured_at: "2026-08-22T10:00:00+00:00",
          delta: { d1: 100, d7: 2468, d30: null }, tracking_status: "active", link_relation_type: "manual",
        },
        {
          evidence_id: 502, kol_pool_id: 102, kol_name: "Beta Vlog", platform: "tiktok",
          title: "开箱短片", content_url: "https://tiktok.com/@beta/video/2",
          view_count: 1234, like_count: null, measured_at: "2026-08-21T09:00:00+00:00",
          delta: { d1: null, d7: -20, d30: null }, tracking_status: "paused", link_relation_type: "detected",
        },
      ],
    },
    {
      sku_code: "AF135F18-E",
      sku_name: "AF 135mm F1.8 LAB",
      videos: 1,
      kols: 1,
      latest_measured_at: null,
      total_views: null,
      delta: { d1: null, d7: null, d30: null },
      items: [
        {
          evidence_id: 503, kol_pool_id: 102, kol_name: "Beta Vlog", platform: "tiktok",
          title: "135mm 预告", content_url: "",
          view_count: null, like_count: null, measured_at: null,
          delta: { d1: null, d7: null, d30: null }, tracking_status: "active", link_relation_type: "confirmed",
        },
      ],
    },
  ],
  truncated: false,
  empty_reason: null,
};

function route(overview: unknown = OVERVIEW) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p === "/api/admin/vkpi/my-kol/sku-play-overview") {
      if (overview instanceof Error) throw overview;
      return overview;
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

function notFound(): ApiResponseError {
  return new ApiResponseError({ status: 404, statusText: "Not Found" } as Response, { detail: "Not Found" });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  route();
});

describe("MY KOL 单品播放数据模块", () => {
  it("汇总条真值 + 每单品一组头(累计播放 / Δ7 天 / 未实测诚实口径)", async () => {
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("AF 85mm F1.4 Pro")).toBeTruthy();
    expect(screen.getByText("AF 135mm F1.8 LAB")).toBeTruthy();
    expect(screen.getByText("AF85F14-Z")).toBeTruthy();
    // 汇总条:单品 2 · 视频 3 · 红人 2 · 已实测 2
    expect(screen.getByText("单品")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    // 组头:累计播放实测值 + Δ7 天 ↑ 正值;未实测组显示「未实测」+ 待积累
    expect(screen.getByText("13,579")).toBeTruthy();
    expect(screen.getByText("↑+2,468")).toBeTruthy();
    expect(screen.getAllByText("未实测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("待积累").length).toBeGreaterThan(0);
    // 未展开时不出逐视频行
    expect(screen.queryByText("85mm 实拍评测")).toBeNull();
  });

  it("点组头展开逐视频行:标题链接原帖 / KOL+平台 / 播放点赞 / 状态 chip", async () => {
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    fireEvent.click(await screen.findByText("AF 85mm F1.4 Pro"));
    expect(await screen.findByText("85mm 实拍评测")).toBeTruthy();
    const link = screen.getByText("85mm 实拍评测").closest("a");
    expect(link?.getAttribute("href")).toBe("https://youtube.com/watch?v=abc");
    expect(screen.getByText("Alpha Cam")).toBeTruthy();
    expect(screen.getAllByText("Youtube").length).toBeGreaterThan(0);
    expect(screen.getByText("12,345")).toBeTruthy();
    expect(screen.getByText("678")).toBeTruthy();
    expect(screen.getByText("追踪中")).toBeTruthy();
    expect(screen.getByText("已暂停")).toBeTruthy();
    expect(screen.getByText("员工关联")).toBeTruthy();
    expect(screen.getByText("系统检测·待确认")).toBeTruthy();
    // 负增量 ↓ 红向文案
    expect(screen.getByText("↓-20")).toBeTruthy();
    // 再点收起
    fireEvent.click(screen.getByText("AF 85mm F1.4 Pro"));
    await waitFor(() => expect(screen.queryByText("85mm 实拍评测")).toBeNull());
  });

  it("手动「刷新」重读端点", async () => {
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await screen.findByText("AF 85mm F1.4 Pro");
    const calls = apiFetchMock.mock.calls.length;
    fireEvent.click(screen.getByText("刷新"));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 1));
  });

  it("数据关注事件携 SKU/evidence 重读后会展开 SKU，优先定位并聚焦具体视频行", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await screen.findByText("AF 85mm F1.4 Pro");
    const firstGroup = document.querySelector('[data-vkpi-sku-play-sku="AF85F14-Z"]') as HTMLElement;
    const secondGroup = document.querySelector('[data-vkpi-sku-play-sku="AF135F18-E"]') as HTMLElement;
    const calls = apiFetchMock.mock.calls.length;
    act(() => window.dispatchEvent(new CustomEvent(SKU_PLAY_CHANGED_EVENT, {
      detail: { evidenceId: 501, skus: ["AF85F14-Z", "AF135F18-E"] },
    })));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 1));
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("85mm 实拍评测")).toBeTruthy());
    expect(screen.getByText("135mm 预告")).toBeTruthy();
    const evidenceRow = document.querySelector('[data-vkpi-sku-play-evidence="501"]') as HTMLTableRowElement;
    expect(firstGroup).toHaveAttribute("data-vkpi-sku-play-focus", "active");
    expect(secondGroup).toHaveAttribute("data-vkpi-sku-play-focus", "active");
    expect(evidenceRow).toHaveAttribute("data-vkpi-sku-play-evidence-focus", "active");
    expect(evidenceRow).toHaveAttribute("aria-current", "true");
    expect(scrollIntoView.mock.instances).toContain(evidenceRow);
    expect(document.activeElement).toBe(evidenceRow);
    expect(document.querySelector("[data-vkpi-sku-play-module]"))
      .toHaveAttribute("data-vkpi-sku-play-evidence-id", "501");
  });

  it("旧版无 detail 事件仅重读一次，并回落定位单品播放模块", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await screen.findByText("AF 85mm F1.4 Pro");
    const calls = apiFetchMock.mock.calls.length;
    const module = document.querySelector("[data-vkpi-sku-play-module]") as HTMLElement;

    act(() => window.dispatchEvent(new CustomEvent(SKU_PLAY_CHANGED_EVENT)));

    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 1));
    await waitFor(() => expect(scrollIntoView.mock.instances).toContain(module));
    expect(apiFetchMock.mock.calls.length).toBe(calls + 1);
    expect(module).not.toHaveAttribute("data-vkpi-sku-play-evidence-id");
  });

  it("快速连续事件且 GET 乱序返回时，只定位最新 pending target", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await screen.findByText("AF 85mm F1.4 Pro");
    const first = deferred<typeof OVERVIEW>();
    const latest = deferred<typeof OVERVIEW>();
    apiFetchMock.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => latest.promise);
    const calls = apiFetchMock.mock.calls.length;

    act(() => window.dispatchEvent(new CustomEvent(SKU_PLAY_CHANGED_EVENT, {
      detail: { evidenceId: 501, skus: ["AF85F14-Z"] },
    })));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 1));
    act(() => window.dispatchEvent(new CustomEvent(SKU_PLAY_CHANGED_EVENT, {
      detail: { evidenceId: 503, skus: ["AF135F18-E"] },
    })));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 2));

    await act(async () => {
      latest.resolve(OVERVIEW);
      await latest.promise;
    });
    const latestRow = await waitFor(() => {
      const row = document.querySelector('[data-vkpi-sku-play-evidence="503"]') as HTMLTableRowElement | null;
      expect(row).toHaveAttribute("data-vkpi-sku-play-evidence-focus", "active");
      return row as HTMLTableRowElement;
    });
    await waitFor(() => expect(document.activeElement).toBe(latestRow));

    await act(async () => {
      first.resolve(OVERVIEW);
      await first.promise;
    });
    expect(document.activeElement).toBe(latestRow);
    expect(scrollIntoView.mock.instances.some((node) => (
      node as HTMLElement | undefined
    )?.dataset?.vkpiSkuPlayEvidence === "501")).toBe(false);
  });

  it("事件重读期间手动刷新会保留最新 target，旧请求返回不抢焦点", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await screen.findByText("AF 85mm F1.4 Pro");
    const eventRead = deferred<typeof OVERVIEW>();
    const manualRead = deferred<typeof OVERVIEW>();
    apiFetchMock.mockImplementationOnce(() => eventRead.promise).mockImplementationOnce(() => manualRead.promise);
    const calls = apiFetchMock.mock.calls.length;

    act(() => window.dispatchEvent(new CustomEvent(SKU_PLAY_CHANGED_EVENT, {
      detail: { evidenceId: 501, skus: ["AF85F14-Z"] },
    })));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 1));
    fireEvent.click(screen.getByText("刷新"));
    await waitFor(() => expect(apiFetchMock.mock.calls.length).toBe(calls + 2));

    await act(async () => {
      manualRead.resolve(OVERVIEW);
      await manualRead.promise;
    });
    const targetRow = await waitFor(() => {
      const row = document.querySelector('[data-vkpi-sku-play-evidence="501"]') as HTMLTableRowElement | null;
      expect(row).toHaveAttribute("data-vkpi-sku-play-evidence-focus", "active");
      return row as HTMLTableRowElement;
    });
    await waitFor(() => expect(document.activeElement).toBe(targetRow));

    await act(async () => {
      eventRead.resolve(OVERVIEW);
      await eventRead.promise;
    });
    expect(document.activeElement).toBe(targetRow);
    expect(scrollIntoView.mock.instances).toContain(targetRow);
  });

  it("无登记 → 诚实空态引导「数据关注」入口", async () => {
    route({ contract: "my_kol_sku_play_overview_v1", summary: { skus: 0, videos: 0, kols: 0, measured_videos: 0 }, groups: [], truncated: false, empty_reason: "no_tracked_sku_videos" });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("还没有登记「数据关注」的视频")).toBeTruthy();
    expect(screen.getByText(/在内容墙或 KOL 详情的视频卡上点「数据关注」即可开始追踪/)).toBeTruthy();
  });

  it("404 → 该版本暂无;其它错误 → 错误卡", async () => {
    route(notFound());
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("该版本暂无单品播放总览")).toBeTruthy();

    route(Object.assign(new Error("HTTP 500"), { detail: "sku play overview failed" }));
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    await waitFor(() => expect(screen.getByText("单品播放数据读取失败")).toBeTruthy());
    expect(screen.getByText("sku play overview failed")).toBeTruthy();
  });

  it("无 token → noToken 占位,不发请求", () => {
    render(<SkuPlayModule apiToken="" noToken={<div>no token</div>} />);
    expect(screen.getByText("no token")).toBeTruthy();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
