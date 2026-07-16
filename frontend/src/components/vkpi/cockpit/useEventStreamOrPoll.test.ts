import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useEventStreamOrPoll } from "./useEventStreamOrPoll";
import { prepareSseStream } from "../../../services/sse-api";

vi.mock("../../../services/sse-api", () => ({
  prepareSseStream: vi.fn(async (url: string) => url),
}));

const prepareSseStreamMock = vi.mocked(prepareSseStream);

// SSE 优先 + 轮询兜底基元冒烟:
//  - 无 streamUrl → 立即轮询一拍(兜底路径,行为与切换前一致);
//  - enabled=false → 既不订阅也不轮询;
//  - 有 streamUrl 且支持 EventSource → 走 SSE:先播种一拍 + 收到事件后重拉;
//  - SSE onerror → 静默回退轮询(不抛)。

describe("useEventStreamOrPoll", () => {
  const realES = globalThis.EventSource;

  afterEach(() => {
    vi.restoreAllMocks();
    // 还原真实 EventSource(jsdom 下通常为 undefined)。
    (globalThis as { EventSource?: unknown }).EventSource = realES;
  });

  beforeEach(() => {
    prepareSseStreamMock.mockImplementation(async (url: string) => url);
  });

  describe("轮询兜底(无 streamUrl)", () => {
    beforeEach(() => {
      (globalThis as { EventSource?: unknown }).EventSource = undefined;
    });

    it("挂载即立刻拉一拍", () => {
      const pollFn = vi.fn();
      renderHook(() => useEventStreamOrPoll({ pollFn, interval: 10000, streamUrl: null }));
      expect(pollFn).toHaveBeenCalledTimes(1);
    });

    it("enabled=false → 不轮询", () => {
      const pollFn = vi.fn();
      renderHook(() => useEventStreamOrPoll({ pollFn, interval: 10000, streamUrl: null, enabled: false }));
      expect(pollFn).not.toHaveBeenCalled();
    });
  });

  describe("SSE 优先", () => {
    let instances: FakeEventSource[];

    class FakeEventSource {
      url: string;
      listeners: Record<string, ((ev: unknown) => void)[]> = {};
      onerror: ((ev: unknown) => void) | null = null;
      closed = false;
      constructor(url: string) {
        this.url = url;
        instances.push(this);
      }
      addEventListener(name: string, fn: (ev: unknown) => void) {
        (this.listeners[name] ||= []).push(fn);
      }
      emit(name: string, ev: unknown = {}) {
        (this.listeners[name] || []).forEach((fn) => fn(ev));
      }
      close() {
        this.closed = true;
      }
    }

    beforeEach(() => {
      instances = [];
      (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource as unknown as typeof EventSource;
    });

    it("先签发一次性 ticket 再订阅 SSE，URL 不含长期 token", async () => {
      const pollFn = vi.fn();
      renderHook(() =>
        useEventStreamOrPoll({ pollFn, streamUrl: "/stream", streamToken: "long-jwt", events: ["update"] }),
      );
      expect(prepareSseStreamMock).toHaveBeenCalledWith("/stream", "long-jwt");
      await waitFor(() => expect(instances).toHaveLength(1));
      expect(instances[0].url).toBe("/stream");
      expect(instances[0].url).not.toContain("access_token");
      expect(pollFn).toHaveBeenCalledTimes(1);
      // 收到事件 → 再拉一拍。
      instances[0].emit("update");
      expect(pollFn).toHaveBeenCalledTimes(2);
    });

    it("onerror → 静默回退轮询(补一拍),不抛", async () => {
      const pollFn = vi.fn();
      renderHook(() => useEventStreamOrPoll({ pollFn, streamUrl: "/stream" }));
      expect(pollFn).toHaveBeenCalledTimes(1); // 播种
      await waitFor(() => expect(instances).toHaveLength(1));
      expect(() => instances[0].onerror?.({})).not.toThrow();
      expect(instances[0].closed).toBe(true);
      // 回退轮询立即补一拍。
      expect(pollFn).toHaveBeenCalledTimes(2);
    });

    it("签发失败时不建流并回退轮询", async () => {
      prepareSseStreamMock.mockRejectedValueOnce(new Error("unavailable"));
      const pollFn = vi.fn();
      renderHook(() => useEventStreamOrPoll({ pollFn, streamUrl: "/stream" }));
      await waitFor(() => expect(pollFn).toHaveBeenCalledTimes(2));
      expect(instances).toHaveLength(0);
    });

    it("卸载时关闭连接", async () => {
      const pollFn = vi.fn();
      const { unmount } = renderHook(() => useEventStreamOrPoll({ pollFn, streamUrl: "/stream" }));
      await waitFor(() => expect(instances).toHaveLength(1));
      unmount();
      expect(instances[0].closed).toBe(true);
    });
  });
});
