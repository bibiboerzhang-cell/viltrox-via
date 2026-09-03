import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_EXPIRED_EVENT,
  SESSION_EXPIRED_NOTICE_KEY,
  buildLoginRedirect,
  consumeSessionExpiredNotice,
  isCredentialEndpoint,
  isLoginPath,
  markSessionExpiredNotice,
  notifyAuthExpired,
  resetAuthExpiredNotice,
  sanitizeNextPath,
} from "./authSession";

describe("authSession helpers", () => {
  beforeEach(() => {
    resetAuthExpiredNotice();
    window.sessionStorage.clear();
  });
  afterEach(() => {
    resetAuthExpiredNotice();
  });

  it("broadcasts the expiry event once until reset", () => {
    const listener = vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT, listener);
    try {
      expect(notifyAuthExpired({ path: "/api/a", status: 401 })).toBe(true);
      expect(notifyAuthExpired({ path: "/api/b", status: 401 })).toBe(false);
      expect(listener).toHaveBeenCalledTimes(1);
      expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ path: "/api/a", status: 401 });
      resetAuthExpiredNotice();
      expect(notifyAuthExpired({ path: "/api/c", status: 401 })).toBe(true);
      expect(listener).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
    }
  });

  it("treats login/logout as credential endpoints (their 401 is not an expiry)", () => {
    expect(isCredentialEndpoint("/api/auth/login")).toBe(true);
    expect(isCredentialEndpoint("/api/auth/logout?x=1")).toBe(true);
    expect(isCredentialEndpoint("/api/auth/me")).toBe(false);
    expect(isCredentialEndpoint("/api/vkpi/dashboard")).toBe(false);
  });

  it("only accepts in-app relative paths for the post-login redirect", () => {
    expect(sanitizeNextPath("/?cockpit=my-kol#x")).toBe("/?cockpit=my-kol#x");
    expect(sanitizeNextPath("https://evil.example/")).toBe("/");
    expect(sanitizeNextPath("//evil.example")).toBe("/");
    expect(sanitizeNextPath("/\\evil.example")).toBe("/");
    expect(sanitizeNextPath("/login?next=%2F")).toBe("/");
    expect(sanitizeNextPath(null)).toBe("/");
  });

  it("builds the login url with an encoded next path", () => {
    expect(buildLoginRedirect("/")).toBe("/login");
    expect(buildLoginRedirect("/?cockpit=projects")).toBe("/login?next=%2F%3Fcockpit%3Dprojects");
    expect(isLoginPath("/login/")).toBe(true);
    expect(isLoginPath("/")).toBe(false);
  });

  it("stores the session-expired notice and consumes it exactly once", () => {
    expect(consumeSessionExpiredNotice()).toBe(false);
    markSessionExpiredNotice();
    expect(window.sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY)).toBe("1");
    expect(consumeSessionExpiredNotice()).toBe(true);
    expect(consumeSessionExpiredNotice()).toBe(false);
  });
});
