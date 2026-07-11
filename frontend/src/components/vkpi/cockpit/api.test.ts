import { beforeEach, describe, expect, it, vi } from "vitest";

const cachedApiFetch = vi.hoisted(() => vi.fn());

vi.mock("../../../lib/apiCache", () => ({
  cachedApiFetch,
  clearApiCache: vi.fn(),
}));

import { fetchCockpitDashboardBundle } from "./api";

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
