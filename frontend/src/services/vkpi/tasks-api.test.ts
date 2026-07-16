import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// W5 recon: tasks-api。listTasks 单次拉取 + 本地 status 过滤 + 归一 + 去重 + 按时间排序;
// 还覆盖 isRecentTask(终态超 1h 丢弃)、cancel/retry/getTaskQueue 的入参与 buildTaskEventStreamUrl。
const apiFetch = vi.fn();
const buildApiUrl = (p: string) => `/base${p}`;
const jsonBody = (payload: unknown) => JSON.stringify(payload);
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  buildApiUrl: (p: string) => buildApiUrl(p),
  jsonBody: (payload: unknown) => jsonBody(payload),
}));

import {
  listTasks,
  getTaskQueue,
  getTaskQueueCompact,
  getTaskRealtimeStatus,
  cancelTask,
  retryTask,
  buildTaskEventStreamUrl,
  TERMINAL_STATUSES,
} from "./tasks-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ tasks: [] });
});

describe("listTasks(归一 / 去重 / 排序 / recent 过滤)", () => {
  it("无 status 过滤时只发一次有界全状态请求", async () => {
    await listTasks("tok");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch.mock.calls[0][0]).toBe("/api/marketing/tasks?limit=200");
    expect(apiFetch.mock.calls[0][2]).toBe("tok");
  });

  it("显式 status 列表在单次响应中本地过滤", async () => {
    apiFetch.mockResolvedValue({
      tasks: [
        { task_id: "Q1", status: "queued" },
        { task_id: "R1", status: "running" },
        { task_id: "D1", status: "done", finished_at: new Date().toISOString() },
      ],
    });
    const tasks = await listTasks("tok", { status: ["queued", "queued", "running"] });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(tasks.map((task) => task.task_id)).toEqual(["Q1", "R1"]);
  });

  it("normalizeAsyncTask: 缺 task_id 的行被丢弃", async () => {
    apiFetch.mockResolvedValue({ tasks: [{ status: "running" }] });
    const tasks = await listTasks("tok", { status: ["running"] });
    expect(tasks).toHaveLength(0);
  });

  it("同 task_id 跨状态去重为一条;running(非终态)恒保留", async () => {
    apiFetch.mockResolvedValue({
      tasks: [{ task_id: "T1", status: "running", created_at: "2026-01-01T00:00:00Z" }],
    });
    const tasks = await listTasks("tok", { status: ["running", "processing"] });
    expect(tasks).toHaveLength(1);
    expect(tasks[0].task_id).toBe("T1");
  });

  it("读 items 兜底(无 tasks 字段时)", async () => {
    apiFetch.mockResolvedValue({ items: [{ task_id: "T9", status: "running" }] });
    const tasks = await listTasks("tok", { status: ["running"] });
    expect(tasks).toHaveLength(1);
    expect(tasks[0].task_id).toBe("T9");
  });
});

describe("isRecentTask(终态 1h 窗口)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("终态且 finished_at 在 1h 内 → 保留", async () => {
    apiFetch.mockResolvedValue({
      tasks: [{ task_id: "R1", status: "done", finished_at: "2026-06-15T11:30:00Z" }],
    });
    const tasks = await listTasks("tok", { status: ["done"] });
    expect(tasks).toHaveLength(1);
  });

  it("终态但 finished_at 超 1h → 丢弃", async () => {
    apiFetch.mockResolvedValue({
      tasks: [{ task_id: "R2", status: "done", finished_at: "2026-06-15T09:00:00Z" }],
    });
    const tasks = await listTasks("tok", { status: ["done"] });
    expect(tasks).toHaveLength(0);
  });

  it("终态但无 finished_at → 丢弃", async () => {
    apiFetch.mockResolvedValue({ tasks: [{ task_id: "R3", status: "failed" }] });
    const tasks = await listTasks("tok", { status: ["failed"] });
    expect(tasks).toHaveLength(0);
  });
});

describe("getTaskQueue / compact(query 默认值)", () => {
  it("getTaskQueue 默认 limit=50 recent_minutes=10 include_llm_calls=true", async () => {
    apiFetch.mockResolvedValue({ status: "ready" });
    await getTaskQueue("tok");
    const [path] = apiFetch.mock.calls[0];
    const url = new URL(`http://x${path}`);
    expect(url.pathname).toBe("/api/admin/vkpi/task-queue");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("recent_minutes")).toBe("10");
    expect(url.searchParams.get("include_llm_calls")).toBe("true");
  });

  it("include_llm_calls=false 显式关闭", async () => {
    apiFetch.mockResolvedValue({ status: "ready" });
    await getTaskQueue("tok", { includeLlmCalls: false });
    const [path] = apiFetch.mock.calls[0];
    expect(new URL(`http://x${path}`).searchParams.get("include_llm_calls")).toBe("false");
  });

  it("compact 默认 limit=30 recent_minutes=5", async () => {
    apiFetch.mockResolvedValue({ status: "ready" });
    await getTaskQueueCompact("tok");
    const [path] = apiFetch.mock.calls[0];
    const url = new URL(`http://x${path}`);
    expect(url.pathname).toBe("/api/admin/vkpi/task-queue/compact");
    expect(url.searchParams.get("limit")).toBe("30");
    expect(url.searchParams.get("recent_minutes")).toBe("5");
  });
});

describe("realtime / cancel / retry / stream URL", () => {
  it("getTaskRealtimeStatus → 固定路径", async () => {
    apiFetch.mockResolvedValue({});
    await getTaskRealtimeStatus("tok");
    expect(apiFetch.mock.calls[0][0]).toBe("/api/admin/vkpi/tasks/realtime-status");
  });

  it("cancelTask → POST + encode + 空 body", async () => {
    apiFetch.mockResolvedValue({});
    await cancelTask("tok", "a/b");
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/marketing/tasks/a%2Fb/cancel");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("retryTask → 返回归一后的 task_id", async () => {
    apiFetch.mockResolvedValue({ task_id: 777 });
    const res = await retryTask("tok", "x");
    expect(res).toEqual({ task_id: "777" });
  });

  it("buildTaskEventStreamUrl 走 buildApiUrl + encode", () => {
    expect(buildTaskEventStreamUrl("a b")).toBe("/base/api/audit/stream/a%20b");
  });
});

describe("TERMINAL_STATUSES(常量契约)", () => {
  it("含 6 个终态键", () => {
    expect([...TERMINAL_STATUSES]).toEqual([
      "done",
      "failed",
      "cancelled",
      "timeout",
      "partial_done",
      "prefilter_rejected",
    ]);
  });
});
