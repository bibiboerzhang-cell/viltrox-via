import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const httpMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}));

vi.mock("../http", () => ({
  apiFetch: httpMocks.apiFetch,
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import {
  getSearchFeedbackSnapshot,
  labeledCountOf,
  recordSearchFeedback,
  resetSearchFeedbackStore,
  searchFeedbackKey,
  submitSearchFeedback,
} from "./searchFeedback-api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("searchFeedback-api · 契约与路径", () => {
  beforeEach(() => {
    httpMocks.apiFetch.mockReset();
    resetSearchFeedbackStore();
  });
  afterEach(() => resetSearchFeedbackStore());

  it("POST 契约 body:source / kol_pool_id / session_item_id / verdict / reason(只在 down 时带)", async () => {
    httpMocks.apiFetch.mockResolvedValue({ ok: true, feedback_id: 7 });
    await submitSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 12, session_item_id: 99, verdict: "down", reason: "wrong_region" });
    const [path, init, token] = httpMocks.apiFetch.mock.calls[0];
    expect(path).toBe("/api/vkpi/recommendations/search-feedback");
    expect(token).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({ source: "discovery_wall", kol_pool_id: 12, session_item_id: 99, verdict: "down", reason: "wrong_region" });

    await submitSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 12, verdict: "up", reason: "other" });
    expect(JSON.parse(httpMocks.apiFetch.mock.calls[1][1].body)).toEqual({ source: "kol_detail", kol_pool_id: 12, verdict: "up" });
  });

  it("主路径 404 时回退 /api/admin/vkpi 前缀一次;其他错误照实抛", async () => {
    httpMocks.apiFetch
      .mockRejectedValueOnce(Object.assign(new Error("404 Not Found"), { status: 404 }))
      .mockResolvedValueOnce({ ok: true, feedback_id: 1 });
    const result = await submitSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 1, verdict: "up" });
    expect(result.feedback_id).toBe(1);
    expect(httpMocks.apiFetch.mock.calls[1][0]).toBe("/api/admin/vkpi/recommendations/search-feedback");

    httpMocks.apiFetch.mockReset().mockRejectedValueOnce(Object.assign(new Error("500"), { status: 500 }));
    await expect(submitSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 1, verdict: "up" })).rejects.toThrow("500");
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(1);
  });
});

describe("searchFeedback-api · 乐观态 + 去重", () => {
  beforeEach(() => {
    httpMocks.apiFetch.mockReset();
    resetSearchFeedbackStore();
  });
  afterEach(() => resetSearchFeedbackStore());

  it("提交先写 pending,成功写 saved + feedback_id;已标注数随之计数", async () => {
    const pending = deferred<{ ok: boolean; feedback_id: number }>();
    httpMocks.apiFetch.mockReturnValue(pending.promise);
    const request = recordSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 5, verdict: "up" });
    const key = searchFeedbackKey("discovery_wall", 5);
    expect(getSearchFeedbackSnapshot().entries[key]).toMatchObject({ verdict: "up", status: "pending" });
    expect(labeledCountOf(getSearchFeedbackSnapshot())).toBe(1);
    pending.resolve({ ok: true, feedback_id: 31 });
    await request;
    expect(getSearchFeedbackSnapshot().entries[key]).toMatchObject({ verdict: "up", status: "saved", feedback_id: 31 });
  });

  it("同 key 同判定去重:不重复打接口;换判定才再发", async () => {
    httpMocks.apiFetch.mockResolvedValue({ ok: true, feedback_id: 1 });
    await recordSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 5, verdict: "up" });
    await recordSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 5, verdict: "up" });
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(1);
    await recordSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 5, verdict: "down", reason: "too_small" });
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(2);
    // 不同 source 是不同 key
    await recordSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 5, verdict: "down", reason: "too_small" });
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(3);
  });

  it("失败回滚:有上次已保存态就回到它,否则留 error 态可重试;error 不计入已标注数", async () => {
    httpMocks.apiFetch.mockRejectedValueOnce(new Error("boom"));
    const key = searchFeedbackKey("kol_detail", 9);
    await recordSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 9, verdict: "up" });
    expect(getSearchFeedbackSnapshot().entries[key]).toMatchObject({ status: "error", error: "boom" });
    expect(labeledCountOf(getSearchFeedbackSnapshot())).toBe(0);

    httpMocks.apiFetch.mockResolvedValueOnce({ ok: true, feedback_id: 2 }).mockRejectedValueOnce(new Error("again"));
    await recordSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 9, verdict: "up" });
    expect(getSearchFeedbackSnapshot().entries[key]).toMatchObject({ status: "saved", verdict: "up" });
    await recordSearchFeedback("tok", { source: "kol_detail", kol_pool_id: 9, verdict: "down", reason: "duplicate" });
    expect(getSearchFeedbackSnapshot().entries[key]).toMatchObject({ status: "saved", verdict: "up" });
  });

  it("服务端回 labeled_count 时已标注数以服务端为准", async () => {
    httpMocks.apiFetch.mockResolvedValue({ ok: true, feedback_id: 1, labeled_count: 42 });
    await recordSearchFeedback("tok", { source: "discovery_wall", kol_pool_id: 1, verdict: "up" });
    expect(labeledCountOf(getSearchFeedbackSnapshot())).toBe(42);
  });
});
