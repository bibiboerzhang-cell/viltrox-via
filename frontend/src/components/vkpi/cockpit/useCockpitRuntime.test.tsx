import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchCockpitShellBundle: vi.fn(),
  fetchCockpitDashboardBundle: vi.fn(),
  fetchAiTodayHotSnapshot: vi.fn(),
  runAiTodaySchedulerNow: vi.fn(),
}));

const cacheMocks = vi.hoisted(() => ({
  readCachedResource: vi.fn(),
  writeCachedResource: vi.fn(),
}));

vi.mock("../../../domains/kol", () => ({
  getKolPoolWorkspace: vi.fn(async () => ({ list: { items: [] } })),
  listKolPool: vi.fn(async () => ({ items: [] })),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, ...apiMocks };
});

vi.mock("./lib/resourceCache", () => cacheMocks);

vi.mock("./kolPoolRuntime", () => ({
  toCockpitKolPoolRows: (value: unknown) => value,
}));

vi.mock("./normalizers", () => ({
  normalizeAlerts: () => ({ notifications: [], reminders: [] }),
  normalizeCurrentUser: (_value: unknown, fallback: unknown) => fallback,
  normalizeCockpitDashboard: (value: unknown) => value,
}));

import { useCockpitRuntime } from "./useCockpitRuntime";

const baselineSnapshot = {
  available: true,
  is_ready: true,
  result_status: "ready",
  generated_at: "2026-07-16T10:00:00Z",
  snapshot_date: "2026-07-16",
  model: "gemini-2.5-pro",
  content: { headline: "old" },
};

function dashboardBundle(aiTodayHot: unknown) {
  return {
    dashboard: { summary: { active_roster: 10 } },
    aiTodayHot,
    starredProjects: [],
    _sources: { dashboard: { ok: true }, aiTodayHot: { ok: true } },
  };
}

function renderRuntime() {
  return renderHook(() => useCockpitRuntime({
    apiToken: "token-ai",
    userName: "Admin",
    userRole: "owner",
    userAvatar: "",
    userEmail: "admin@example.com",
    userAuthRole: "owner",
    starredProjects: [],
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
  cacheMocks.readCachedResource.mockResolvedValue(null);
  cacheMocks.writeCachedResource.mockResolvedValue(undefined);
  apiMocks.fetchCockpitShellBundle.mockResolvedValue({ user: null, alerts: [] });
  apiMocks.fetchCockpitDashboardBundle.mockResolvedValue(dashboardBundle(baselineSnapshot));
  apiMocks.runAiTodaySchedulerNow.mockResolvedValue({ triggered: true });
});

describe("useCockpitRuntime AI Today regeneration", () => {
  it("先触发 scheduler，再轮询新快照，最后强制刷新整个 Dashboard bundle", async () => {
    const readySnapshot = {
      ...baselineSnapshot,
      generated_at: "2026-07-16T10:05:00Z",
      content: { headline: "new" },
    };
    const runtime = renderRuntime();
    await waitFor(() => expect(apiMocks.fetchCockpitDashboardBundle).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((runtime.result.current.dashboardRuntime as any).aiTodayHot).toEqual(baselineSnapshot));

    apiMocks.fetchAiTodayHotSnapshot.mockResolvedValueOnce(readySnapshot);
    apiMocks.fetchCockpitDashboardBundle.mockResolvedValueOnce(dashboardBundle(readySnapshot));

    await act(async () => {
      await runtime.result.current.regenerateAiToday();
    });

    expect(apiMocks.runAiTodaySchedulerNow).toHaveBeenCalledWith("token-ai");
    expect(apiMocks.fetchAiTodayHotSnapshot).toHaveBeenCalledWith("token-ai", { forceRefresh: true });
    expect(apiMocks.fetchCockpitDashboardBundle).toHaveBeenLastCalledWith("token-ai", { forceRefresh: true });
    expect(apiMocks.runAiTodaySchedulerNow.mock.invocationCallOrder[0])
      .toBeLessThan(apiMocks.fetchAiTodayHotSnapshot.mock.invocationCallOrder[0]);
    expect(apiMocks.fetchAiTodayHotSnapshot.mock.invocationCallOrder[0])
      .toBeLessThan(apiMocks.fetchCockpitDashboardBundle.mock.invocationCallOrder.at(-1) || 0);
    expect(runtime.result.current.aiTodayRegeneration.phase).toBe("success");
    expect((runtime.result.current.dashboardRuntime as any).aiTodayHot).toEqual(readySnapshot);
    runtime.unmount();
  });

  it("新快照降级时显式报错并保留上一份 ready 快照", async () => {
    const degradedSnapshot = {
      available: true,
      is_ready: false,
      result_status: "degraded",
      generated_at: "2026-07-16T10:06:00Z",
      reason: "budget_guard_blocked",
    };
    const runtime = renderRuntime();
    await waitFor(() => expect((runtime.result.current.dashboardRuntime as any).aiTodayHot).toEqual(baselineSnapshot));

    apiMocks.fetchAiTodayHotSnapshot.mockResolvedValueOnce(degradedSnapshot);
    apiMocks.fetchCockpitDashboardBundle.mockResolvedValueOnce(dashboardBundle(degradedSnapshot));

    await act(async () => {
      await runtime.result.current.regenerateAiToday();
    });

    expect(runtime.result.current.aiTodayRegeneration).toMatchObject({
      phase: "degraded",
      message: expect.stringContaining("继续显示上一份快照"),
    });
    expect(runtime.result.current.aiTodayRegeneration.message).toContain("budget_guard_blocked");
    expect((runtime.result.current.dashboardRuntime as any).aiTodayHot).toEqual(baselineSnapshot);
    runtime.unmount();
  });

  it("快照未变但最新尝试失败时立即停止轮询并保留旧内容", async () => {
    const failedAttemptSnapshot = {
      ...baselineSnapshot,
      latest_attempt: {
        attempted_at: "2026-07-16T10:07:00Z",
        status: "invalid",
        provider: "anthropic",
        reason: "invalid_result_contract",
        generation_status: "all_providers_failed",
      },
      content: {
        ...baselineSnapshot.content,
        latest_attempt: {
          attempted_at: "2026-07-16T10:07:00Z",
          status: "invalid",
          provider: "anthropic",
          reason: "invalid_result_contract",
          generation_status: "all_providers_failed",
        },
      },
    };
    const runtime = renderRuntime();
    await waitFor(() => expect((runtime.result.current.dashboardRuntime as any).aiTodayHot).toEqual(baselineSnapshot));

    apiMocks.fetchAiTodayHotSnapshot.mockResolvedValueOnce(failedAttemptSnapshot);
    apiMocks.fetchCockpitDashboardBundle.mockResolvedValueOnce(dashboardBundle(failedAttemptSnapshot));

    await act(async () => {
      await runtime.result.current.regenerateAiToday();
    });

    expect(apiMocks.fetchAiTodayHotSnapshot).toHaveBeenCalledTimes(1);
    expect(runtime.result.current.aiTodayRegeneration).toMatchObject({
      phase: "degraded",
      message: expect.stringContaining("invalid_result_contract"),
    });
    expect((runtime.result.current.dashboardRuntime as any).aiTodayHot.content.headline).toBe("old");
    expect((runtime.result.current.dashboardRuntime as any).aiTodayHot.latest_attempt).toMatchObject({
      provider: "anthropic",
      reason: "invalid_result_contract",
    });
    runtime.unmount();
  });
});
