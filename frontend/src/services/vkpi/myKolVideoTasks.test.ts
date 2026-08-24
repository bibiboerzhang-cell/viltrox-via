import { describe, expect, it } from "vitest";

import { normalizeVideoPage } from "./myKolBoard-api";
import { freshnessText, isTaskActive, normalizeTaskState, taskChip } from "./myKolVideoTasks";

const state = (status: string, data: Partial<Record<string, unknown>> = {}, extra: Record<string, unknown> = {}) => normalizeTaskState({
  status,
  job_id: 7,
  requested_at: "2026-08-21T10:00:00+00:00",
  updated_at: "2026-08-21T10:05:00+00:00",
  data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false, ...data },
  ...extra,
});

describe("my_kol_video_recovery_v1 task state facade", () => {
  it("normalizes missing or foreign shapes to not_requested + unavailable instead of faking completion", () => {
    expect(normalizeTaskState(undefined)).toEqual({
      status: "not_requested", job_id: null, requested_at: null, updated_at: null,
      data: { status: "none", freshness: "unavailable", updated_at: null, superseded_by_job: false },
    });
    expect(normalizeTaskState({ status: "done" }).status).toBe("failed");
    expect(normalizeTaskState({ status: "ready", data: { status: "ready", freshness: "fresh", updated_at: "2026-08-01" } }).data.status).toBe("ready");
  });

  it("keeps task state and data freshness as two layers", () => {
    const queued = state("queued", { status: "ready", freshness: "stale", updated_at: "2026-08-01T00:00:00Z", superseded_by_job: true });
    expect(isTaskActive(queued)).toBe(true);
    expect(taskChip("metric", queued)).toMatchObject({ label: "重测中 · 上次结果可见", tone: "active" });
    expect(taskChip("analysis", { ...queued, status: "running" })).toMatchObject({ label: "重分析中 · 上次结果可见", tone: "active" });
    expect(freshnessText("metric", queued).label).toMatch(/^已过期 · 上次实测 /);

    const fresh = state("ready", { status: "ready", freshness: "fresh", updated_at: new Date(Date.now() - 3 * 3600_000).toISOString() });
    expect(taskChip("metric", fresh)).toMatchObject({ label: "播放追踪已完成", tone: "ready" });
    expect(freshnessText("metric", fresh).label).toBe("实测于 3 小时前");

    const never = state("not_requested");
    expect(taskChip("analysis", never)).toMatchObject({ label: "深析未发起", tone: "idle" });
    expect(taskChip("review", never)).toMatchObject({ label: "关键帧复核未发起", tone: "idle" });
    expect(freshnessText("analysis", never).label).toBe("从未分析");
    expect(freshnessText("metric", normalizeTaskState(undefined)).label).toBe("实测数据暂不可用");
  });

  it("gives honest failure levels without leaking raw worker text", () => {
    const blocked = state("blocked");
    expect(taskChip("metric", blocked)).toMatchObject({ label: "播放追踪已阻断", tone: "blocked" });
    expect(taskChip("metric", blocked).title).toContain("需人工处理");
    const failed = state("failed", { attempt_count: 3 });
    expect(taskChip("metric", failed).title).toContain("已尝试 3 次");
    const failedWithOld = state("failed", { status: "stale", freshness: "stale", updated_at: "2026-08-01T00:00:00Z" });
    expect(taskChip("analysis", failedWithOld).title).toContain("上次结果仍可见");
    expect(JSON.stringify(taskChip("analysis", failed))).not.toMatch(/apify|traceback|provider/i);
  });
});

describe("normalizeVideoPage (keyset contract)", () => {
  it("reads page.* and mirrors, drops a next_cursor when has_more is false, and normalizes tasks per row", () => {
    const page = normalizeVideoPage({
      contract: "my_kol_video_recovery_v1",
      kol_pool_id: 101,
      profile_crawl: { status: "running", job_id: 3 },
      items: [{ evidence_id: 9, tasks: { metric_refresh: { status: "queued" }, final_v1: null } }, { evidence_id: 8 }],
      summary: { total: 120, views_total: 5, views_measured: 2, final_v1_ready: 1 },
      page: { limit: 60, returned: 2, has_more: true, next_cursor: "abc", cursor_kind: "published_at_id", order: "published_at_desc_id_desc" },
    });
    expect(page.page?.next_cursor).toBe("abc");
    expect(page.has_more).toBe(true);
    expect(page.total).toBe(120);
    expect(page.profile_crawl?.status).toBe("running");
    expect(page.items?.[0].tasks?.metric_refresh.status).toBe("queued");
    expect(page.items?.[0].tasks?.final_v1.status).toBe("not_requested");
    expect(page.items?.[0].tasks?.keyframe_qa?.status).toBe("not_requested");
    expect(page.items?.[1].tasks).toBeNull();

    const last = normalizeVideoPage({ items: [], page: { limit: 60, returned: 0, has_more: false, next_cursor: "stale", cursor_kind: "published_at_id", order: "published_at_desc_id_desc" } });
    expect(last.page?.next_cursor).toBeNull();
    expect(last.has_more).toBe(false);

    // 旧形状(无 page 块)也能读,不炸
    const legacy = normalizeVideoPage({ items: [{ evidence_id: 1 }], total: 1 });
    expect(legacy.page?.has_more).toBe(false);
    expect(legacy.items?.[0].tasks).toBeNull();
  });
});
