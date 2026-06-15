import { describe, it, expect, vi, beforeEach } from "vitest";

// W5 recon: kolMemory-api 对 ../http.apiFetch 的入参断言。
// 重点:kolId 经 encodeURIComponent + String 归一;rebuild 是 POST;plan 带 top_n。
const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import {
  getKolMemory,
  rebuildKolMemory,
  getKolVideoFullscanPlan,
} from "./kolMemory-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({});
});

describe("getKolMemory(GET + no-store + token)", () => {
  it("数字 id 归一为字符串路径", async () => {
    await getKolMemory("tok", 123);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-memory/123");
    expect(init).toEqual({ cache: "no-store" });
    expect(token).toBe("tok");
  });

  it("字符串 id 经 encodeURIComponent 转义", async () => {
    await getKolMemory("tok", "a b/c");
    const [path] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-memory/a%20b%2Fc");
  });
});

describe("rebuildKolMemory(POST,无 cache)", () => {
  it("POST + 路径含 /rebuild", async () => {
    await rebuildKolMemory("tok", 5);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-memory/5/rebuild");
    expect(init).toEqual({ method: "POST" });
    expect(token).toBe("tok");
  });
});

describe("getKolVideoFullscanPlan(GET + top_n 默认 5)", () => {
  it("默认 top_n=5", async () => {
    await getKolVideoFullscanPlan("tok", 8);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-memory/8/video-fullscan-plan?top_n=5");
    expect(init).toEqual({ cache: "no-store" });
  });

  it("自定义 top_n 透传", async () => {
    await getKolVideoFullscanPlan("tok", 8, 12);
    const [path] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-memory/8/video-fullscan-plan?top_n=12");
  });
});
