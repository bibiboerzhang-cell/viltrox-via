import { afterEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "./api";

describe("apiFetch timeout lifecycle", () => {
  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps the timeout active while a response body is stalled", async () => {
    vi.useFakeTimers();

    let requestSignal!: AbortSignal;
    let markBodyStarted!: () => void;
    const bodyStarted = new Promise<void>((resolve) => {
      markBodyStarted = resolve;
    });

    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal as AbortSignal;
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        text: () => {
          markBodyStarted();
          return new Promise<string>((_resolve, reject) => {
            requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true });
          });
        },
      } as Response;
    }));

    const pending = apiFetch("/slow-body", { timeoutMs: 100 });
    const rejection = pending.catch((error) => error);
    await bodyStarted;

    await vi.advanceTimersByTimeAsync(100);

    expect(requestSignal.aborted).toBe(true);
    expect(await rejection).toMatchObject({ message: "请求超时：100ms" });
  });
});
