import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch } from "./api";
import { AUTH_EXPIRED_EVENT, resetAuthExpiredNotice } from "./authSession";

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

describe("apiFetch global 401 handling (U-B3)", () => {
  const listener = vi.fn();

  function stubStatus(status: number) {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: status < 400,
      status,
      statusText: status === 401 ? "Unauthorized" : "OK",
      text: async () => JSON.stringify({ detail: status === 401 ? "token expired" : "ok" }),
    }) as Response));
  }

  beforeEach(() => {
    resetAuthExpiredNotice();
    listener.mockClear();
    window.addEventListener(AUTH_EXPIRED_EVENT, listener);
  });
  afterEach(() => {
    window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
    resetAuthExpiredNotice();
    vi.unstubAllGlobals();
  });

  it("broadcasts vkpi:auth-expired once for a 401 on a token-bearing business request", async () => {
    stubStatus(401);
    await expect(apiFetch("/api/vkpi/dashboard", {}, "tok")).rejects.toMatchObject({ status: 401 });
    await expect(apiFetch("/api/vkpi/kol-pool", {}, "tok")).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ path: "/api/vkpi/dashboard", status: 401 });
  });

  it("stays silent for guest requests, credential endpoints and 403", async () => {
    stubStatus(401);
    await expect(apiFetch("/api/vkpi/dashboard")).rejects.toMatchObject({ status: 401 });
    await expect(apiFetch("/api/auth/login", { method: "POST" }, "tok")).rejects.toMatchObject({ status: 401 });
    stubStatus(403);
    await expect(apiFetch("/api/vkpi/dashboard", {}, "tok")).rejects.toMatchObject({ status: 403 });
    expect(listener).not.toHaveBeenCalled();
  });
});
