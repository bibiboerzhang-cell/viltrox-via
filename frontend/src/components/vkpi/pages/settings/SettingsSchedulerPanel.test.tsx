import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SettingsSchedulerPanel } from "./SettingsSchedulerPanel";


describe("SettingsSchedulerPanel execution truth", () => {
  it("shows paid future execution clearly before enabling", () => {
    const onToggleTask = vi.fn();
    render(
      <SettingsSchedulerPanel
        tasks={[
          {
            task_key: "kol_profile_incremental_refresh",
            label: "KOL 搜索库存每日增量刷新",
            enabled: false,
            risk_level: "medium",
            execution_wired: true,
            paid_execution: true,
            enable_warning: "启用后每日最多排队 5 个维护刷新任务；5 个维护任务不等于 5 次外部 provider 调用；本次不会立即运行。",
          },
        ]}
        status={{ total: 1, enabled: 0, by_risk: { low: 0, medium: 1, high: 0 } }}
        busy={false}
        onToggleTask={onToggleTask}
      />,
    );

    expect(screen.getByText("付费执行 · 开启前需确认")).toBeTruthy();
    expect(screen.getByText(/每日最多排队 5 个维护刷新任务/)).toBeTruthy();
    expect(screen.getByText(/5 个维护任务不等于 5 次外部 provider 调用/)).toBeTruthy();
    expect(screen.getByText(/已经接线的任务会在后续调度窗口执行/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "开启" }));
    expect(onToggleTask).toHaveBeenCalledWith("kol_profile_incremental_refresh", true);
  });
});
