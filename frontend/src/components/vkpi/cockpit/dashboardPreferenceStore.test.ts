import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getPreferenceSettings, updatePreferenceSettings } from "../../../services/vkpi/settings-api";
import {
  DASHBOARD_LAYOUT_COLUMNS,
  DASHBOARD_LAYOUT_PREFERENCE,
  DASHBOARD_LAYOUT_SCHEMA_VERSION,
  decodeDashboardLayoutPreference,
  encodeDashboardLayoutPreference,
  loadDashboardPreference,
  resetDashboardPreferenceStoreForTests,
  saveDashboardPreference,
} from "./dashboardPreferenceStore";

vi.mock("../../../services/vkpi/settings-api", () => ({
  getPreferenceSettings: vi.fn(),
  updatePreferenceSettings: vi.fn(),
}));

const getPreferencesMock = vi.mocked(getPreferenceSettings);
const updatePreferencesMock = vi.mocked(updatePreferenceSettings);

describe("dashboardPreferenceStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetDashboardPreferenceStoreForTests();
    getPreferencesMock.mockReset();
    updatePreferencesMock.mockReset();
  });

  afterEach(() => {
    resetDashboardPreferenceStoreForTests();
    vi.useRealTimers();
  });

  it("保留其他账户偏好并合并看板布局", async () => {
    getPreferencesMock.mockResolvedValue({
      preference: {
        preferences: {
          locale: "zh-CN",
          custom_flag: true,
          [DASHBOARD_LAYOUT_PREFERENCE]: [{ instanceId: "old", moduleKey: "alpha", span: 4 }],
        },
      },
    });
    updatePreferencesMock.mockResolvedValue({
      preference: {
        preferences: {
          locale: "zh-CN",
          custom_flag: true,
          [DASHBOARD_LAYOUT_PREFERENCE]: [{ instanceId: "new", moduleKey: "beta", span: 8 }],
        },
      },
    });

    const existing = await loadDashboardPreference<Array<Record<string, unknown>>>("token", DASHBOARD_LAYOUT_PREFERENCE);
    expect(existing?.[0]?.instanceId).toBe("old");

    const save = saveDashboardPreference(
      "token",
      DASHBOARD_LAYOUT_PREFERENCE,
      [{ instanceId: "new", moduleKey: "beta", span: 8 }],
    );
    await vi.advanceTimersByTimeAsync(700);
    await save;

    expect(updatePreferencesMock).toHaveBeenCalledWith("token", {
      preferences: {
        locale: "zh-CN",
        custom_flag: true,
        [DASHBOARD_LAYOUT_PREFERENCE]: [{ instanceId: "new", moduleKey: "beta", span: 8 }],
      },
    });
  });

  it("读取旧数组与早期 envelope，并写出版本化布局", () => {
    const legacy = [{ instanceId: "legacy", moduleKey: "alpha", span: 4 }];
    expect(decodeDashboardLayoutPreference(legacy)).toEqual(legacy);
    expect(decodeDashboardLayoutPreference({ layout: legacy })).toEqual(legacy);
    expect(decodeDashboardLayoutPreference({ items: legacy, version: 99 })).toEqual(legacy);
    expect(decodeDashboardLayoutPreference({ items: "invalid" })).toBeNull();

    expect(encodeDashboardLayoutPreference(legacy)).toEqual({
      version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
      columns: DASHBOARD_LAYOUT_COLUMNS,
      items: legacy,
    });
  });
});
