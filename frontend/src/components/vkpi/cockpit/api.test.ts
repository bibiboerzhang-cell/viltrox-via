import { beforeEach, describe, expect, it, vi } from "vitest";

const cachedApiFetch = vi.hoisted(() => vi.fn());
const apiFetch = vi.hoisted(() => vi.fn());

vi.mock("../../../lib/apiCache", () => ({
  cachedApiFetch,
  clearApiCache: vi.fn(),
}));

vi.mock("../../../services/http", () => ({
  apiFetch,
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import {
  AI_TODAY_SCHEDULER_JOB_ID,
  aiTodayAttemptFingerprint,
  aiTodaySnapshotFailureReason,
  aiTodaySnapshotFingerprint,
  fetchAiTodayHotSnapshot,
  fetchCockpitDashboardBundle,
  isAiTodaySnapshotReady,
  runAiTodaySchedulerNow,
} from "./api";

function responseFor(path: string) {
  if (path.startsWith("/api/admin/vkpi/dashboard?")) {
    return { summary: { active_roster: 525 } };
  }
  if (path.includes("recent-content")) return { items: [] };
  if (path.includes("projects?")) return { projects: [] };
  return {};
}

beforeEach(() => {
  cachedApiFetch.mockReset();
  apiFetch.mockReset();
  cachedApiFetch.mockImplementation((path: string) => Promise.resolve(responseFor(path)));
});

describe("fetchCockpitDashboardBundle", () => {
  it("强制刷新会穿透全部内存缓存，并保留数据源健康状态", async () => {
    const bundle = await fetchCockpitDashboardBundle("token-a", { forceRefresh: true });

    expect(cachedApiFetch).toHaveBeenCalledTimes(10);
    for (const [, init, token] of cachedApiFetch.mock.calls) {
      expect(init).toMatchObject({ forceRefresh: true });
      expect(token).toBe("token-a");
    }
    expect(bundle.dashboard).toMatchObject({ summary: { active_roster: 525 } });
    expect(bundle._sources.dashboard.ok).toBe(true);
  });

  it("主 summary 请求失败时明确标红，不把空对象伪装成已接入", async () => {
    cachedApiFetch.mockImplementation((path: string) => {
      if (path.startsWith("/api/admin/vkpi/dashboard?")) return Promise.reject(new Error("offline"));
      return Promise.resolve(responseFor(path));
    });

    const bundle = await fetchCockpitDashboardBundle("token-b", { forceRefresh: true });

    expect(bundle.dashboard).toEqual({});
    expect(bundle._sources.dashboard.ok).toBe(false);
  });
});

describe("AI Today runtime API", () => {
  it("通过真实 scheduler run-now 触发指定任务", async () => {
    apiFetch.mockResolvedValue({ status: "triggered", triggered: true });

    await expect(runAiTodaySchedulerNow("token-ai")).resolves.toMatchObject({ triggered: true });
    expect(apiFetch).toHaveBeenCalledWith(
      `/api/admin/runtime/scheduler/${AI_TODAY_SCHEDULER_JOB_ID}/run-now`,
      { method: "POST", body: "{}" },
      "token-ai",
    );
  });

  it("独立 scheduler 拓扑下接受已持久化入队的 run-now", async () => {
    apiFetch.mockResolvedValue({
      status: "queued",
      queued: true,
      triggered: false,
      request_id: 71,
    });

    await expect(runAiTodaySchedulerNow("token-ai")).resolves.toMatchObject({
      status: "queued",
      queued: true,
      request_id: 71,
    });
  });

  it("调度未启动时显式失败，不伪装成生成中", async () => {
    apiFetch.mockResolvedValue({ status: "not_started", triggered: false, error: "scheduler offline" });
    await expect(runAiTodaySchedulerNow("token-ai")).rejects.toThrow("scheduler offline");
  });

  it("快照轮询强制穿透缓存，且只有完整 ready 合同才通过门禁", async () => {
    const ready = {
      available: true,
      is_ready: true,
      result_status: "ready",
      generated_at: "2026-07-16T12:00:00Z",
      snapshot_date: "2026-07-16",
      model: "gemini-2.5-pro",
    };
    cachedApiFetch.mockResolvedValue(ready);

    await expect(fetchAiTodayHotSnapshot("token-ai")).resolves.toEqual(ready);
    expect(cachedApiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/dashboard/ai-today-hot",
      { timeoutMs: 5000, forceRefresh: true },
      "token-ai",
    );
    expect(isAiTodaySnapshotReady(ready)).toBe(true);
    expect(isAiTodaySnapshotReady({ ...ready, is_ready: false })).toBe(false);
    expect(aiTodaySnapshotFingerprint(ready)).toBe(
      "2026-07-16T12:00:00Z|2026-07-16|ready|gemini-2.5-pro",
    );
  });

  it("独立指纹识别旧快照之后的最新失败尝试", () => {
    const snapshot = {
      available: true,
      is_ready: true,
      result_status: "ready",
      generated_at: "2026-07-16T12:00:00Z",
      latest_attempt: {
        attempted_at: "2026-07-16T12:05:00Z",
        status: "invalid",
        provider: "anthropic",
        reason: "invalid_result_contract",
        generation_status: "all_providers_failed",
      },
    };

    expect(aiTodayAttemptFingerprint(snapshot)).toBe(
      "2026-07-16T12:05:00Z|invalid|anthropic|invalid_result_contract|all_providers_failed",
    );
    expect(aiTodaySnapshotFailureReason(snapshot)).toBe("invalid_result_contract");
  });
});
