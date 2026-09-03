// S-02:login 走 ?session=cookie(后端不回 token);/me 以占位 token 走 cookie。
import { afterEach, describe, expect, it, vi } from "vitest";

import { COOKIE_SESSION_TOKEN } from "../lib/authCookieSession";
import { LOGIN_PATH_COOKIE_SESSION, fetchMe, login, logout } from "./auth.service";

interface Captured {
  url: string;
  init: RequestInit;
}

function stubFetch(body: unknown): Captured[] {
  const calls: Captured[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init ?? {} });
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        text: async () => JSON.stringify(body),
      } as Response;
    }),
  );
  return calls;
}

describe("auth.service transport (S-02)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("login asks the backend for a cookie session and sends no bearer header", async () => {
    const calls = stubFetch({ status: "success", user: { id: 1 } });
    await login("a@b.c", "pw");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain(LOGIN_PATH_COOKIE_SESSION);
    expect(calls[0].url).toContain("session=cookie");
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(calls[0].init.credentials).toBeDefined();
  });

  it("fetchMe carries the cookie-session marker, never a JWT", async () => {
    const calls = stubFetch({ status: "success", user: { id: 1 } });
    await fetchMe(COOKIE_SESSION_TOKEN);
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${COOKIE_SESSION_TOKEN}`);
  });

  it("logout is a cookie-authenticated POST (server-side revocation)", async () => {
    const calls = stubFetch({ status: "success" });
    await logout();
    expect(calls[0].url).toContain("/api/auth/logout");
    expect(calls[0].init.method).toBe("POST");
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("X-Requested-With")).toBe("XMLHttpRequest");
  });
});
