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

const kolMocks = vi.hoisted(() => ({
  getKolPoolWorkspace: vi.fn(),
  listKolPool: vi.fn(),
}));

vi.mock("../../../domains/kol", () => kolMocks);

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

import { sanitizeKolPoolRowsForCache, useCockpitRuntime } from "./useCockpitRuntime";

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
  kolMocks.getKolPoolWorkspace.mockResolvedValue({ list: { items: [] } });
  kolMocks.listKolPool.mockResolvedValue({ items: [] });
  cacheMocks.readCachedResource.mockResolvedValue(null);
  cacheMocks.writeCachedResource.mockResolvedValue(undefined);
  apiMocks.fetchCockpitShellBundle.mockResolvedValue({ user: null, alerts: [] });
  apiMocks.fetchCockpitDashboardBundle.mockResolvedValue(dashboardBundle(baselineSnapshot));
  apiMocks.runAiTodaySchedulerNow.mockResolvedValue({ triggered: true });
});

describe("KOL Pool persistent cache privacy", () => {
  it("requests the list-only workspace because summary analytics load separately", async () => {
    const runtime = renderRuntime();

    await waitFor(() => expect(kolMocks.getKolPoolWorkspace).toHaveBeenCalled());
    expect(kolMocks.getKolPoolWorkspace).toHaveBeenCalledWith(
      "token-ai",
      expect.objectContaining({ limit: 500, offset: 0, sortBy: "fit", includeAggregates: false }),
    );
    runtime.unmount();
  });

  it("renders the first cold page while later workspace pages continue loading", async () => {
    let resolveSecondPage: ((value: unknown) => void) | undefined;
    const firstPage = Array.from({ length: 500 }, (_, index) => ({ id: index + 1 }));
    kolMocks.getKolPoolWorkspace.mockImplementation((_token: string, params: { offset?: number }) => {
      if ((params.offset || 0) === 0) return Promise.resolve({ list: { items: firstPage } });
      return new Promise((resolve) => { resolveSecondPage = resolve; });
    });
    const runtime = renderRuntime();

    await waitFor(() => expect(runtime.result.current.kolPoolRows).toHaveLength(500));
    expect(runtime.result.current.kolPoolLoading).toBe(true);

    await act(async () => {
      resolveSecondPage?.({ list: { items: [{ id: 501 }] } });
    });
    await waitFor(() => expect(runtime.result.current.kolPoolRows).toHaveLength(501));
    await waitFor(() => expect(runtime.result.current.kolPoolLoading).toBe(false));
    expect(kolMocks.getKolPoolWorkspace).toHaveBeenNthCalledWith(
      2,
      "token-ai",
      expect.objectContaining({ offset: 500, includeAggregates: false }),
    );
    runtime.unmount();
  });

  it("recursively strips every contact projection before serialization", () => {
    const secretEmail = "creator-secret@example.com";
    const secretPhone = "+12025550199";
    const sanitized = sanitizeKolPoolRowsForCache([{
      id: 7,
      display_name: "Creator",
      email: secretEmail,
      contact_phone: secretPhone,
      other_contacts_json: [{ contact_type: "email", contact_value: secretEmail }],
      nested: {
        business_email: secretEmail,
        contact_channels: { whatsapp: secretPhone },
        raw_platform_data: { biography: `Business: ${secretEmail}` },
        safe_value: "keep-me",
      },
    }]);
    const serialized = JSON.stringify(sanitized);

    expect(serialized).not.toContain(secretEmail);
    expect(serialized).not.toContain(secretPhone);
    expect(serialized).not.toContain("other_contacts_json");
    expect(serialized).not.toContain("contact_channels");
    expect(sanitized).toEqual([{ id: 7, display_name: "Creator", nested: { safe_value: "keep-me" } }]);
  });

  it("hides account A rows synchronously on an A to B token switch", async () => {
    const secretEmail = "account-a-secret@example.com";
    let resolveB: ((value: unknown) => void) | undefined;
    kolMocks.getKolPoolWorkspace.mockImplementation((token: string) => {
      if (token === "token-a") {
        return Promise.resolve({ list: { items: [{ id: 1, email: secretEmail, contact_masked: false }] } });
      }
      return new Promise((resolve) => { resolveB = resolve; });
    });
    const runtime = renderHook(
      ({ apiToken }) => useCockpitRuntime({
        apiToken,
        userName: "Staff",
        userRole: "employee",
        userAvatar: "",
        userEmail: "staff@example.com",
        userAuthRole: "employee",
        starredProjects: [],
      }),
      { initialProps: { apiToken: "token-a" } },
    );
    await waitFor(() => expect(JSON.stringify(runtime.result.current.kolPoolRows)).toContain(secretEmail));

    runtime.rerender({ apiToken: "token-b" });
    expect(JSON.stringify(runtime.result.current.kolPoolRows)).not.toContain(secretEmail);
    expect(runtime.result.current.kolPoolRows).toEqual([]);

    await act(async () => { resolveB?.({ list: { items: [] } }); });
    runtime.unmount();
  });

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
