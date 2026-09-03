import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  COOKIE_SESSION_TOKEN,
  LEGACY_TOKEN_STORAGE_KEYS,
  isCookieSessionActive,
  purgeLegacyTokenStorage,
  readSessionToken,
  setCookieSessionActive,
} from "./authCookieSession";

describe("authCookieSession (S-02 cookie-only session helpers)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setCookieSessionActive(false);
  });
  afterEach(() => {
    setCookieSessionActive(false);
    vi.restoreAllMocks();
  });

  it("marker matches the backend COOKIE_SESSION_MARKER contract", () => {
    expect(COOKIE_SESSION_TOKEN).toBe("cookie-session");
  });

  it("readSessionToken follows the active flag and never reads storage", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem");
    expect(readSessionToken()).toBe("");
    expect(isCookieSessionActive()).toBe(false);
    setCookieSessionActive(true);
    expect(readSessionToken()).toBe(COOKIE_SESSION_TOKEN);
    expect(isCookieSessionActive()).toBe(true);
    setCookieSessionActive(false);
    expect(readSessionToken()).toBe("");
    expect(getItem).not.toHaveBeenCalled();
  });

  it("purges every legacy token key and reports what it removed", () => {
    for (const key of LEGACY_TOKEN_STORAGE_KEYS) {
      window.localStorage.setItem(key, "stale-jwt");
    }
    window.localStorage.setItem("vkpi:theme", "dark");
    expect(purgeLegacyTokenStorage()).toEqual([...LEGACY_TOKEN_STORAGE_KEYS]);
    for (const key of LEGACY_TOKEN_STORAGE_KEYS) {
      expect(window.localStorage.getItem(key)).toBeNull();
    }
    expect(window.localStorage.getItem("vkpi:theme")).toBe("dark");
    expect(purgeLegacyTokenStorage()).toEqual([]);
  });

  it("purge survives a blocked localStorage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(purgeLegacyTokenStorage()).toEqual([]);
  });
});
