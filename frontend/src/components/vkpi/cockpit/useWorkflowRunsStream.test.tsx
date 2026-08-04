import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskMocks = vi.hoisted(() => ({
  getTaskQueueCompact: vi.fn(),
}));

vi.mock("../../../services/vkpi/tasks-api", () => ({
  getTaskQueueCompact: taskMocks.getTaskQueueCompact,
}));

import { useWorkflowRunsStream } from "./useWorkflowRunsStream";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function setVisibility(value: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value,
  });
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: value === "hidden",
  });
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("useWorkflowRunsStream polling lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
    taskMocks.getTaskQueueCompact.mockReset();
    taskMocks.getTaskQueueCompact.mockResolvedValue({ status: "ready", active: [], recent: [] });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    setVisibility("visible");
  });

  it("waits for the request to settle before starting the next delay", async () => {
    const first = deferred<{ status: string; active: never[]; recent: never[] }>();
    taskMocks.getTaskQueueCompact
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue({ status: "ready", active: [], recent: [] });

    renderHook(() => useWorkflowRunsStream("token-a", { intervalMs: 5000 }));
    await flushMicrotasks();
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve({ status: "ready", active: [], recent: [] });
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(2);
  });

  it("aborts while hidden and refreshes exactly once when visible", async () => {
    const observedSignals: AbortSignal[] = [];
    taskMocks.getTaskQueueCompact.mockImplementation(
      (_token: string, _query: unknown, request: { signal: AbortSignal }) => new Promise((_, reject) => {
        observedSignals.push(request.signal);
        request.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }),
    );

    renderHook(() => useWorkflowRunsStream("token-a", { intervalMs: 5000 }));
    await flushMicrotasks();
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(1);
    expect(observedSignals[0].aborted).toBe(false);

    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(observedSignals[0].aborted).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(2);
    expect(observedSignals[1].aborted).toBe(false);
  });

  it("does not wait for a delayed aborted request before refreshing on visibility restore", async () => {
    const first = deferred<{ status: string; active: Array<{ id: string }>; recent: never[] }>();
    const second = deferred<{ status: string; active: Array<{ id: string }>; recent: never[] }>();
    taskMocks.getTaskQueueCompact
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const view = renderHook(() => useWorkflowRunsStream("token-a"));
    await flushMicrotasks();
    const firstSignal = taskMocks.getTaskQueueCompact.mock.calls[0][2].signal as AbortSignal;

    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(firstSignal.aborted).toBe(true);

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(2);

    await act(async () => {
      second.resolve({ status: "ready", active: [{ id: "fresh-task" }], recent: [] });
      await second.promise;
    });
    expect(view.result.current.runs.map((item) => item.id)).toEqual(["fresh-task"]);

    await act(async () => {
      first.resolve({ status: "ready", active: [{ id: "stale-task" }], recent: [] });
      await first.promise;
    });
    expect(view.result.current.runs.map((item) => item.id)).toEqual(["fresh-task"]);
  });

  it("aborts an old-token request and ignores its late response", async () => {
    const oldRequest = deferred<{ status: string; active: Array<{ id: string }>; recent: never[] }>();
    const newRequest = deferred<{ status: string; active: Array<{ id: string }>; recent: never[] }>();
    taskMocks.getTaskQueueCompact.mockImplementation((token: string) => (
      token === "token-a" ? oldRequest.promise : newRequest.promise
    ));

    const view = renderHook(
      ({ token }) => useWorkflowRunsStream(token),
      { initialProps: { token: "token-a" } },
    );
    await flushMicrotasks();
    const oldSignal = taskMocks.getTaskQueueCompact.mock.calls[0][2].signal as AbortSignal;

    view.rerender({ token: "token-b" });
    await flushMicrotasks();
    expect(oldSignal.aborted).toBe(true);
    expect(taskMocks.getTaskQueueCompact).toHaveBeenCalledTimes(2);

    await act(async () => {
      newRequest.resolve({ status: "ready", active: [{ id: "new-task" }], recent: [] });
      await newRequest.promise;
    });
    expect(view.result.current.runs.map((item) => item.id)).toEqual(["new-task"]);

    await act(async () => {
      oldRequest.resolve({ status: "ready", active: [{ id: "old-task" }], recent: [] });
      await oldRequest.promise;
    });
    expect(view.result.current.runs.map((item) => item.id)).toEqual(["new-task"]);
  });

  it("clears loading when an active token is removed", async () => {
    const request = deferred<{ status: string; active: never[]; recent: never[] }>();
    taskMocks.getTaskQueueCompact.mockReturnValue(request.promise);

    const view = renderHook(
      ({ token }) => useWorkflowRunsStream(token),
      { initialProps: { token: "token-a" } },
    );
    await flushMicrotasks();
    const oldSignal = taskMocks.getTaskQueueCompact.mock.calls[0][2].signal as AbortSignal;
    expect(view.result.current.loading).toBe(true);

    view.rerender({ token: "" });
    await flushMicrotasks();

    expect(oldSignal.aborted).toBe(true);
    expect(view.result.current.loading).toBe(false);
    expect(view.result.current.payload).toBeNull();
    expect(view.result.current.error).toBe("缺少 API token");

    await act(async () => {
      request.resolve({ status: "ready", active: [], recent: [] });
      await request.promise;
    });
    expect(view.result.current.loading).toBe(false);
  });

  it("aborts the active request on unmount", async () => {
    const request = deferred<{ status: string; active: never[]; recent: never[] }>();
    taskMocks.getTaskQueueCompact.mockReturnValue(request.promise);

    const view = renderHook(() => useWorkflowRunsStream("token-a"));
    await flushMicrotasks();
    const signal = taskMocks.getTaskQueueCompact.mock.calls[0][2].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    view.unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      request.resolve({ status: "ready", active: [], recent: [] });
      await request.promise;
    });
  });
});
