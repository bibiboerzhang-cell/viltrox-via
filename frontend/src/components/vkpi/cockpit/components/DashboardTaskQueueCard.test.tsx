import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetch, fetchProgressCenter } = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  fetchProgressCenter: vi.fn(),
}));

vi.mock("../../../../services/http", () => ({ apiFetch }));
vi.mock("../../../../services/vkpi/progressCenter-api", () => ({ fetchProgressCenter }));

import { DashboardTaskQueueCard } from "./DashboardTaskQueueCard";

function progressPayload(active = false) {
  const running = active ? [{
    id: "run-1",
    source: "ledger",
    kind: "账号深析",
    label: "youtube/@creator",
    status: "running",
    stage: "thinking",
    stage_label: "分析",
    created_at: "2026-07-09T11:59:00Z",
    updated_at: "2026-07-09T11:59:30Z",
    masked: false,
    progress_pct: 20,
    eta_seconds: 60,
  }] : [];
  return {
    status: "ready",
    generated_at: "2026-07-09T12:00:00Z",
    counts: { running: running.length, queued: 0, active_total: running.length, recent_total: 0 },
    running,
    queued: [],
    recent_done: [],
    stage_flow: [],
    diagnostics: { worker_online: true },
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function setVisibility(value: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  Object.defineProperty(document, "hidden", { configurable: true, value: value === "hidden" });
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  fetchProgressCenter.mockReset().mockResolvedValue({
    status: "ready",
    generated_at: "2026-07-09T12:00:00Z",
    counts: { running: 0, queued: 2, active_total: 2, recent_total: 0 },
    running: [],
    queued: [{
      id: "q-1",
      source: "ledger",
      kind: "账号深析",
      label: "youtube/@creator",
      status: "queued",
      stage: "queued",
      stage_label: "排队",
      created_at: "2026-07-09T11:59:00Z",
      updated_at: "2026-07-09T11:59:00Z",
      masked: false,
      progress_pct: 0,
      eta_seconds: null,
    }],
    recent_done: [],
    stage_flow: [],
    diagnostics: { worker_online: true },
  });
  apiFetch.mockReset().mockResolvedValue({
    today: { apify_calls: 1, llm_calls: 2, total_usd: 0.34 },
    budgets: { monthly_total: { configured: false } },
  });
});

describe("DashboardTaskQueueCard", () => {
  it("排队任务使用真实计数和静态等待态，不伪装成处理中动画", async () => {
    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("2 排队")).toBeInTheDocument();
    expect(screen.queryByText("2 处理中")).not.toBeInTheDocument();
    const queuedLane = screen.getByText("排队").closest(".vkpi-dashboard-task-queue__lane");
    expect(queuedLane).toBeTruthy();
    expect(queuedLane).not.toHaveTextContent("12%");
    expect(queuedLane?.querySelector("i")).toHaveClass("is-waiting");
    expect(queuedLane?.querySelector("i")).not.toHaveClass("is-indeterminate");
    expect(container).toHaveTextContent("今日 3 次 · $0.34");
  });

  it("Worker 离线时明确显示等待原因", async () => {
    const payload = await fetchProgressCenter();
    fetchProgressCenter.mockReset().mockResolvedValue({
      ...payload,
      diagnostics: { worker_online: false },
    });

    render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("Worker 离线 · 2 等待")).toBeInTheDocument();
    expect(screen.getByText("等待 Worker 上线")).toBeInTheDocument();
  });

  it("compact 跑中任务显示身份；超历史均时保持不定态而不是伪装成 0%", async () => {
    fetchProgressCenter.mockResolvedValue({
      status: "ready",
      generated_at: "2026-07-14T04:00:00Z",
      counts: { running: 1, queued: 0, active_total: 1, recent_total: 0 },
      running: [{
        id: "2241",
        source: "apify_jobs",
        kind: "评论采集",
        job_type: "kol_comments_collect",
        label: "dianakenyeres",
        status: "running",
        stage: "search",
        stage_label: "抓取",
        created_at: "2026-07-14T03:00:00Z",
        updated_at: "2026-07-14T03:59:30Z",
        masked: false,
        progress_pct: null,
        progress_estimated: false,
        progress_overdue: true,
        progress_label: "已超历史均时",
        eta_seconds: null,
      }],
      queued: [],
      recent_done: [],
      stage_flow: [],
      diagnostics: { worker_online: true },
    });

    const { container } = render(<DashboardTaskQueueCard apiToken="token" compact />);

    expect(await screen.findByText("评论采集 · dianakenyeres")).toBeInTheDocument();
    expect(screen.getByText("#2241")).toBeInTheDocument();
    expect(screen.getByText("超均时")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("0%");
    expect(container.querySelector(".vkpi-dashboard-task-queue__bar i")).toHaveClass("is-indeterminate");
  });
});

describe("DashboardTaskQueueCard polling lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    setVisibility("visible");
  });

  it("请求结算后才启动空闲 30 秒计时，慢请求期间不重入", async () => {
    const first = deferred<ReturnType<typeof progressPayload>>();
    fetchProgressCenter
      .mockReset()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(progressPayload(false));

    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve(progressPayload(false));
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
  });

  it("存在活跃任务时保持 10 秒一拍，任务空闲后退回 30 秒", async () => {
    fetchProgressCenter
      .mockReset()
      .mockResolvedValueOnce(progressPayload(true))
      .mockResolvedValue(progressPayload(false));

    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(3);
  });

  it("页面隐藏中止并暂停，恢复只补一拍，卸载也中止当前请求", async () => {
    const signals: AbortSignal[] = [];
    fetchProgressCenter.mockReset().mockImplementation(
      ({ signal }: { signal?: AbortSignal }) => new Promise((_, reject) => {
        if (!signal) throw new Error("missing abort signal");
        signals.push(signal);
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      }),
    );

    const view = render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);
    expect(signals[0].aborted).toBe(false);

    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(signals[0].aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
    document.dispatchEvent(new Event("visibilitychange"));
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(signals[1].aborted).toBe(true);
  });
});
