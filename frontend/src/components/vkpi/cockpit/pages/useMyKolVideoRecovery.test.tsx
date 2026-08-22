import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MY_KOL_RECOVERY_POLL_MS, useMyKolVideoRecovery } from "./useMyKolVideoRecovery";

function page(items: Array<Record<string, unknown>>, extra: Record<string, unknown> = {}) {
  return {
    contract: "my_kol_video_recovery_v1",
    kol_pool_id: 101,
    read_only: true,
    profile_crawl: { status: "not_requested", job_id: null, data: { status: "none", freshness: "never" } },
    items,
    summary: { total: 3, views_total: 0, views_measured: 0, final_v1_ready: 0 },
    page: { limit: 2, returned: items.length, has_more: false, next_cursor: null, cursor_kind: "published_at_id", order: "published_at_desc_id_desc" },
    ...extra,
  };
}

const video = (id: number, metricStatus = "not_requested") => ({
  evidence_id: id,
  title: `clip ${id}`,
  tasks: {
    metric_refresh: { status: metricStatus, job_id: metricStatus === "not_requested" ? null : 5, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
    final_v1: { status: "not_requested", job_id: null, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
  },
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  apiFetchMock.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useMyKolVideoRecovery (contract my_kol_video_recovery_v1)", () => {
  it("restores in-flight task state on open, polls only while active, and stops at terminal state", async () => {
    let tick = 0;
    apiFetchMock.mockImplementation(async () => {
      tick += 1;
      return page([video(1, tick < 3 ? "queued" : "ready"), video(2)]);
    });
    const { result } = renderHook(() => useMyKolVideoRecovery({ apiToken: "t", kolPoolId: 101, pageSize: 2 }));
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(result.current.videos).toHaveLength(2));
    expect(result.current.videos[0].tasks?.metric_refresh.status).toBe("queued");
    expect(result.current.polling).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(MY_KOL_RECOVERY_POLL_MS + 5); });
    expect(result.current.videos[0].tasks?.metric_refresh.status).toBe("queued");
    await act(async () => { await vi.advanceTimersByTimeAsync(MY_KOL_RECOVERY_POLL_MS + 5); });
    expect(result.current.videos[0].tasks?.metric_refresh.status).toBe("ready");
    expect(result.current.polling).toBe(false);
    const callsAfterTerminal = apiFetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(MY_KOL_RECOVERY_POLL_MS * 3); });
    expect(apiFetchMock.mock.calls.length).toBe(callsAfterTerminal);
  });

  it("pages with the opaque keyset cursor from page.next_cursor and never an offset", async () => {
    apiFetchMock.mockImplementation(async (path: unknown) => {
      const url = String(path);
      if (url.includes("cursor=")) {
        expect(url).toContain("cursor=ks-2");
        return page([video(3)], { page: { limit: 2, returned: 1, has_more: false, next_cursor: null, cursor_kind: "published_at_id", order: "published_at_desc_id_desc" } });
      }
      return page([video(1), video(2)], { page: { limit: 2, returned: 2, has_more: true, next_cursor: "ks-2", cursor_kind: "published_at_id", order: "published_at_desc_id_desc" } });
    });
    const { result } = renderHook(() => useMyKolVideoRecovery({ apiToken: "t", kolPoolId: 101, pageSize: 2 }));
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(result.current.hasMore).toBe(true));
    expect(String(apiFetchMock.mock.calls[0][0])).toBe("/api/admin/vkpi/my-kol/101/videos?limit=2");
    await act(async () => { await result.current.loadMore(); });
    expect(result.current.videos.map((v) => v.evidence_id)).toEqual([1, 2, 3]);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.total).toBe(3);
    expect(apiFetchMock.mock.calls.some(([p]) => /offset=/.test(String(p)))).toBe(false);
  });

  it("refresh() re-reads every loaded page with the same cursors so chips follow the server ledger", async () => {
    let metric = "not_requested";
    apiFetchMock.mockImplementation(async (path: unknown) => {
      const url = String(path);
      if (url.includes("cursor=ks-2")) return page([video(3, metric)]);
      return page([video(1), video(2)], { page: { limit: 2, returned: 2, has_more: true, next_cursor: "ks-2", cursor_kind: "published_at_id", order: "published_at_desc_id_desc" } });
    });
    const { result } = renderHook(() => useMyKolVideoRecovery({ apiToken: "t", kolPoolId: 101, pageSize: 2 }));
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(result.current.hasMore).toBe(true));
    await act(async () => { await result.current.loadMore(); });
    metric = "queued";
    await act(async () => { await result.current.refresh(); });
    expect(result.current.videos[2].tasks?.metric_refresh.status).toBe("queued");
    expect(result.current.polling).toBe(true);
    const cursors = apiFetchMock.mock.calls.map(([p]) => String(p)).filter((p) => p.includes("cursor="));
    expect(cursors.every((p) => p.includes("cursor=ks-2"))).toBe(true);
  });
});
