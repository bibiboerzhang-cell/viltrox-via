import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// 波 C·C3 观察清单模块冒烟:
// - 总览 = 每组一张卡(KOL 数 / 追踪 / 未登记 / 7 天播放 / 深析 / 失败),诚实空态按后端 reason;
// - 点卡展开 → 拉 groups/{id}/watch-overview,逐 KOL 行(样本不足 / 未登记追踪 不编数);
// - 404 → 整块「该版本暂无观察清单」;no_groups → 新建分组入口(vkpi:open-team-groups);
// - 团队浮层回跳:sessionStorage vkpi:my-kol-watch-group → 自动展开该组。
// mock seam:services/http.apiFetch(与页面 smoke 同款),ApiResponseError 真类用于 404 判定。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ApiResponseError } from "../../../../services/http";
import { WatchlistModule, WATCH_GROUP_FOCUS_KEY } from "./MyKolBoardPage.watchlist";

const OVERVIEW = {
  contract: "my_kol_watchlist_overview_v1",
  read_only: true,
  generated_at: "2026-08-23T01:00:00+00:00",
  groups: [
    {
      group_id: "g-1", name: "85mm 重点跟进", description: "Q3 铺量", member_count: 3, kol_ids: [101, 102],
      summary: {
        kol_count: 2, tracked_count: 1, untracked_count: 1, no_videos_count: 0, tracked_videos_total: 3, tracked_paused_total: 0,
        last_snapshot_at: "2026-08-22T10:00:00+00:00", views_delta_7d: 12345, views_delta_7d_reason: null, views_delta_7d_kols: 1,
        deep_analysis: { completed: 4, in_progress: 1, failed: 1, not_requested: 10, scope_videos: 16, completion_ratio: 0.25 },
        open_failures: 2, last_activity_at: "2026-08-22T12:00:00+00:00", empty_reason: null,
      },
    },
    {
      group_id: "g-2", name: "空组", description: "", member_count: 1, kol_ids: [],
      summary: { kol_count: 0, tracked_count: 0, untracked_count: 0, no_videos_count: 0, tracked_videos_total: 0, views_delta_7d: null, views_delta_7d_reason: "no_tracked_videos", deep_analysis: { completed: 0, scope_videos: 0 }, open_failures: 0, last_activity_at: null, empty_reason: "no_kols_in_group" },
    },
  ],
  totals: { kol_count: 2, tracked_videos_total: 3, untracked_count: 1, views_delta_7d: 12345, views_delta_7d_reason: null, deep_analysis: { completed: 4, scope_videos: 16 }, open_failures: 2, group_count: 2, groups_with_kols: 1, favorites_not_in_any_group: 5 },
  scheduler: { enabled: false },
  window: "7d",
  empty_reason: null,
};

const GROUP_DETAIL = {
  contract: "my_kol_watchlist_overview_v1",
  group: { group_id: "g-1", name: "85mm 重点跟进", member_count: 3 },
  summary: OVERVIEW.groups[0].summary,
  items: [
    {
      kol_pool_id: 101, display_name: "Alpha Cam", handle: "@alpha", platform: "youtube", tracking_state: "tracked", video_evidence_total: 8,
      tracked_videos: 3, tracked_paused: 0, last_snapshot_at: "2026-08-22T10:00:00+00:00", snapshot_samples: 6,
      views_delta_7d: 12345, views_delta_7d_reason: null, views_delta_7d_videos: 2, views_delta_7d_status: "ready",
      deep_analysis: { completed: 4, in_progress: 1, failed: 1, not_requested: 2, scope_videos: 8, completion_ratio: 0.5 },
      open_failures: 1, lens_families: [{ lens_key: "af85", display_name: "AF 85mm F1.4 Pro", mentions: 9, videos: 4 }],
      last_activity_at: "2026-08-22T12:00:00+00:00",
    },
    {
      kol_pool_id: 102, display_name: "Beta Vlog", handle: "@beta", platform: "tiktok", tracking_state: "untracked", video_evidence_total: 5,
      tracked_videos: 0, tracked_paused: 0, last_snapshot_at: null, snapshot_samples: 0,
      views_delta_7d: null, views_delta_7d_reason: "no_tracked_videos", views_delta_7d_videos: 0, views_delta_7d_status: null,
      deep_analysis: { completed: 0, in_progress: 0, failed: 1, not_requested: 4, scope_videos: 5, completion_ratio: 0 },
      open_failures: 1, lens_families: [], last_activity_at: null,
    },
  ],
  kol_ids_configured: 2, kols_truncated: false, missing_kol_ids: [], tracked_truncated: false, scheduler: { enabled: false }, window: "7d", empty_reason: null,
};

function route(overrides: { overview?: unknown; group?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p === "/api/admin/vkpi/my-kol/watch-overview") {
      const value = overrides.overview ?? OVERVIEW;
      if (value instanceof Error) throw value;
      return value;
    }
    if (/\/api\/admin\/vkpi\/my-kol\/groups\/[^/]+\/watch-overview$/.test(p)) {
      const value = overrides.group ?? GROUP_DETAIL;
      if (value instanceof Error) throw value;
      return value;
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

function notFound(): ApiResponseError {
  return new ApiResponseError({ status: 404, statusText: "Not Found" } as Response, { detail: "Not Found" });
}

beforeEach(() => {
  window.sessionStorage.clear();
  route();
});

describe("MY KOL 观察清单模块", () => {
  it("总览:每组一张卡,汇总真值 + 空组诚实空态 + 未分组收藏提示", async () => {
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("85mm 重点跟进")).toBeTruthy();
    expect(screen.getByText("空组")).toBeTruthy();
    expect(screen.getByText("本组还没有 KOL —— 编辑分组勾选「共享 KOL 池」后出现在这里。")).toBeTruthy();
    // 汇总行(totals + 组卡)都出 +12,345;未登记 1;深析 4/16
    expect(screen.getAllByText("+12,345").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4/16").length).toBeGreaterThan(0);
    expect(screen.getByText(/你收藏的 KOL 里有 5 个不在任何分组/)).toBeTruthy();
    // 调度闸关 → 诚实提示
    expect(screen.getByText("自动刷新未开启")).toBeTruthy();
    // 只拉总览,不预拉组详情
    expect(apiFetchMock.mock.calls.filter((c) => String(c[0]).includes("/groups/")).length).toBe(0);
  });

  it("点卡展开:拉组详情,逐 KOL 行(未登记 / 样本口径 / 深析 / 镜头),档案跳转走 sessionStorage + 事件", async () => {
    const dispatched: string[] = [];
    const listener = () => dispatched.push("vkpi:open-kol-profile");
    window.addEventListener("vkpi:open-kol-profile", listener);
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    fireEvent.click(await screen.findByText("85mm 重点跟进"));
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    expect(screen.getByText("Beta Vlog")).toBeTruthy();
    expect(screen.getByText("AF 85mm F1.4 Pro")).toBeTruthy();
    expect(screen.getByText("未登记追踪")).toBeTruthy();
    expect(screen.getAllByText("未登记").length).toBeGreaterThan(0);
    expect(screen.getByText(/本组有 1 个 KOL 已采集视频但未登记追踪/)).toBeTruthy();
    expect(screen.getByText("4/8")).toBeTruthy();
    expect(screen.getByText("未实测")).toBeTruthy();
    expect(apiFetchMock.mock.calls.some((c) => String(c[0]) === "/api/admin/vkpi/my-kol/groups/g-1/watch-overview")).toBe(true);
    fireEvent.click(screen.getByText("Alpha Cam"));
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    expect(dispatched).toEqual(["vkpi:open-kol-profile"]);
    window.removeEventListener("vkpi:open-kol-profile", listener);
  });

  it("团队浮层回跳:sessionStorage 组 id → 自动展开该组", async () => {
    window.sessionStorage.setItem(WATCH_GROUP_FOCUS_KEY, "g-1");
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("Alpha Cam")).toBeTruthy();
    expect(window.sessionStorage.getItem(WATCH_GROUP_FOCUS_KEY)).toBeNull();
  });

  it("404 → 整块「该版本暂无观察清单」,不摆假数", async () => {
    route({ overview: notFound() });
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("该版本暂无观察清单")).toBeTruthy();
    expect(screen.queryByText("管理分组")).toBeNull();
  });

  it("无分组 → 新建分组入口派发 vkpi:open-team-groups(mode=new);其它错误 → 错误卡", async () => {
    route({ overview: { contract: "my_kol_watchlist_overview_v1", groups: [], totals: { kol_count: 0, group_count: 0 }, scheduler: { enabled: true }, empty_reason: "no_groups" } });
    const events: Array<Record<string, unknown>> = [];
    const listener = (ev: Event) => events.push((ev as CustomEvent).detail || {});
    window.addEventListener("vkpi:open-team-groups", listener);
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    expect(await screen.findByText("还没有分组")).toBeTruthy();
    fireEvent.click(screen.getByText("新建分组"));
    expect(events).toEqual([{ mode: "new" }]);
    window.removeEventListener("vkpi:open-team-groups", listener);

    route({ overview: Object.assign(new Error("HTTP 500"), { detail: "scope unavailable" }) });
    render(<WatchlistModule apiToken="t" noToken={<div>no token</div>} />);
    await waitFor(() => expect(screen.getByText("观察清单读取失败")).toBeTruthy());
    expect(screen.getByText("scope unavailable")).toBeTruthy();
  });

  it("无 token → noToken 占位,不发请求", () => {
    render(<WatchlistModule apiToken="" noToken={<div>no token</div>} />);
    expect(screen.getByText("no token")).toBeTruthy();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
