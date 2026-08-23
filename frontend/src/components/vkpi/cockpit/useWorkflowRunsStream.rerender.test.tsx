import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// C9(优化波 B)重渲染计数证明:5s 轮询投影内容未变时,消费方零重渲染;
// 内容真变时只渲染一次(此前每拍 loading true→false + 新 payload 引用 = 2~3 次)。

const taskMocks = vi.hoisted(() => ({
  getTaskQueueCompact: vi.fn(),
}));

vi.mock("../../../services/vkpi/tasks-api", () => ({
  getTaskQueueCompact: taskMocks.getTaskQueueCompact,
}));

import { useWorkflowRunsStream } from "./useWorkflowRunsStream";

let renders = 0;

function Consumer() {
  renders += 1;
  const stream = useWorkflowRunsStream("token-a", { intervalMs: 5000 });
  return <output data-testid="runs">{stream.runs.map((item) => item.id).join(",")}{stream.loading && !stream.payload ? "|loading" : ""}</output>;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useWorkflowRunsStream · 轮询不击穿消费方", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    renders = 0;
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    taskMocks.getTaskQueueCompact.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("内容相同的拍子零重渲染;内容变化只渲染一次;后台刷新不再翻 loading", async () => {
    const snapshot = (ids: string[]) => ({ status: "ready", active: ids.map((id) => ({ id })), recent: [] });
    taskMocks.getTaskQueueCompact
      .mockResolvedValueOnce(snapshot(["a"]))
      .mockResolvedValueOnce(snapshot(["a"]))
      .mockResolvedValueOnce(snapshot(["a"]))
      .mockResolvedValueOnce(snapshot(["a", "b"]))
      .mockResolvedValue(snapshot(["a", "b"]));

    render(<Consumer />);
    await flush();
    expect(screen.getByTestId("runs").textContent).toBe("a");
    const afterFirst = renders;

    // 两拍内容相同 → 零重渲染。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await flush();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await flush();
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(3);
    expect(renders).toBe(afterFirst);

    // 第四拍内容变化 → 恰好多渲染一次(无 loading 翻动)。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await flush();
    expect(screen.getByTestId("runs").textContent).toBe("a,b");
    expect(renders).toBe(afterFirst + 1);
  });
});
