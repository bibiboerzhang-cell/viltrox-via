import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { normalizeVideoTasks } from "../../../../services/vkpi/myKolVideoTasks";
import { VideoTaskStatus } from "./MyKolBoardPage.video-tasks";

// 优化波 B · F3/F7 在 MY KOL 视频列表的落点:
//   深析失败项显示 failure_reason_human + 按类别动作;排队/进行中显示 eta_seconds 新口径;
//   旧契约(无新字段)不渲染任何猜测文案。

const metricIdle = { status: "not_requested", data: { status: "none", freshness: "never" } };

describe("VideoTaskStatus · 失败可读 + ETA", () => {
  afterEach(() => cleanup());

  it("authorization 失败:人话原因 + 「从 MY KOL 重新发起」原地重发", () => {
    const onRetry = vi.fn();
    const tasks = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: {
        status: "failed",
        job_id: 5,
        failure_category: "authorization",
        failure_reason_human: "当前账号不是该 KOL 的收藏负责人",
        data: { status: "none", freshness: "never" },
      },
    });
    render(<VideoTaskStatus tasks={tasks} onRetryAnalysis={onRetry} />);
    expect(screen.getByText("当前账号不是该 KOL 的收藏负责人")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从 MY KOL 重新发起" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    // chip 悬浮说明也带人话原因
    const analysisChip = screen.getAllByRole("status")[1];
    expect(analysisChip.getAttribute("title")).toContain("当前账号不是该 KOL 的收藏负责人");
  });

  it("budget / provider 类:提示而非按钮;只读视图(无 onRetryAnalysis)不渲染按钮", () => {
    const tasks = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: { status: "blocked", failure_category: "budget", failure_reason_human: "本期预算已用完", data: { status: "none", freshness: "never" } },
    });
    render(<VideoTaskStatus tasks={tasks} onRetryAnalysis={vi.fn()} />);
    expect(screen.getByText("本期预算已用完")).toBeTruthy();
    expect(screen.getByText(/预算恢复后可再次发起/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    const providerTasks = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: { status: "failed", failure_category: "provider", data: { status: "none", freshness: "never" } },
    });
    render(<VideoTaskStatus tasks={providerTasks} />);
    expect(screen.getByText("稍后自动重试,无需手动操作")).toBeTruthy();
  });

  it("旧契约失败项(无新字段)不渲染失败可读块;活跃任务只在有 eta_seconds 时显示预计剩余", () => {
    const legacy = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: { status: "failed", data: { status: "none", freshness: "never", attempt_count: 2 } },
    });
    const { container } = render(<VideoTaskStatus tasks={legacy} onRetryAnalysis={vi.fn()} />);
    expect(container.querySelector("[data-vkpi-failure-guidance]")).toBeNull();
    cleanup();
    const queuedNoEta = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: { status: "queued", data: { status: "none", freshness: "never" } },
    });
    const noEta = render(<VideoTaskStatus tasks={queuedNoEta} />);
    expect(noEta.container.querySelector("[data-vkpi-eta-seconds]")).toBeNull();
    cleanup();
    const queuedEta = normalizeVideoTasks({
      metric_refresh: metricIdle,
      final_v1: { status: "queued", eta_seconds: 420, data: { status: "none", freshness: "never" } },
    });
    render(<VideoTaskStatus tasks={queuedEta} />);
    expect(screen.getByText(/预计剩余/).textContent).toContain("约 7 分钟");
  });
});
