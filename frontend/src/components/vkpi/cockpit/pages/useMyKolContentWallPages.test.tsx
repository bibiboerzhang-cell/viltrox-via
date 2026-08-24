import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getRecentMock = vi.fn();

vi.mock("../../../../services/vkpi/myKolBoard-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/vkpi/myKolBoard-api")>();
  return {
    ...actual,
    getMyKolRecentVideos: (...args: unknown[]) => getRecentMock(...args),
  };
});

import { useMyKolContentWallPages } from "./useMyKolContentWallPages";

function group(metricStatus: string, evidenceId = 77) {
  return {
    status: "ready",
    items: [{
      evidence_id: evidenceId,
      id: evidenceId,
      kol_pool_id: 101,
      title: "Polling recovery",
      content_url: "https://example.com/polling-recovery",
      tasks: {
        metric_refresh: { status: metricStatus },
        final_v1: { status: "not_requested" },
        keyframe_qa: { status: "not_requested" },
      },
    }],
    page: {
      limit: 60,
      returned: 1,
      has_more: false,
      next_cursor: null as string | null,
      cursor_kind: "published_at_id",
      order: "published_at_desc_id_desc",
    },
    filters: { days: 7, kol_pool_id: 101, since: "2026-08-17T12:00:00+00:00" },
  };
}

describe("useMyKolContentWallPages polling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getRecentMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("单次轮询失败后仍继续下一轮，恢复后收敛任务态", async () => {
    getRecentMock
      .mockResolvedValueOnce(group("queued"))
      .mockRejectedValueOnce(new Error("transient 503"))
      .mockResolvedValueOnce(group("success"));

    const initialGroup = { status: "empty", items: [] };
    const { result, unmount } = renderHook(() => useMyKolContentWallPages({
      apiToken: "poll-token",
      initialGroup,
      kolPoolId: 101,
      days: 7,
    }));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.polling).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(getRecentMock).toHaveBeenCalledTimes(2);
    expect(result.current.polling).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(getRecentMock).toHaveBeenCalledTimes(3);
    expect(result.current.items[0]?.tasks?.metric_refresh?.status).toBe("success");
    expect(result.current.polling).toBe(false);
    unmount();
  });

  it("有限时间窗刷新首页不沿用旧 since，让窗口真正滚动", async () => {
    getRecentMock.mockResolvedValue(group("success"));
    const initialGroup = { status: "empty", items: [] };
    const { result, unmount } = renderHook(() => useMyKolContentWallPages({
      apiToken: "rolling-token",
      initialGroup,
      kolPoolId: 101,
      days: 7,
    }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getRecentMock).toHaveBeenCalledTimes(1);

    await act(async () => { await result.current.refresh(); });
    expect(getRecentMock).toHaveBeenCalledTimes(2);
    expect(getRecentMock.mock.calls[1][1]).toMatchObject({
      days: 7, kolPoolId: 101, cursor: null, since: undefined,
    });
    unmount();
  });

  it("轮询只重读含活跃任务的页，不每 2 秒重走全量游标", async () => {
    const first = group("success");
    first.page = { ...first.page, has_more: true, next_cursor: "p2" };
    getRecentMock
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(group("queued", 78))
      .mockResolvedValueOnce(group("success", 78));
    const initialGroup = { status: "empty", items: [] };
    const { result, unmount } = renderHook(() => useMyKolContentWallPages({
      apiToken: "active-page-token", initialGroup, kolPoolId: 101, days: 7,
    }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await result.current.loadMore(); });
    expect(result.current.polling).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(getRecentMock).toHaveBeenCalledTimes(3);
    expect(getRecentMock.mock.calls[2][1]).toMatchObject({ cursor: "p2" });
    expect(result.current.polling).toBe(false);
    unmount();
  });
});
