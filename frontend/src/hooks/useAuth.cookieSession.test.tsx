// S-02:JWT 不再进 JS / localStorage;登录态只靠 HttpOnly cookie(/me 回答)。
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { COOKIE_SESSION_TOKEN, readSessionToken, setCookieSessionActive } from "../lib/authCookieSession";

const fetchMe = vi.fn();
const login = vi.fn();
const logout = vi.fn(async () => ({ status: "success" }));
vi.mock("../services/auth.service", () => ({
  fetchMe: (...args: unknown[]) => fetchMe(...args),
  login: (...args: unknown[]) => login(...args),
  logout: () => logout(),
}));

vi.mock("../lib/apiCache", () => ({
  clearApiCache: () => undefined,
}));

import { AuthProvider, useAuth } from "./useAuth";

const LEGACY_KEY = "viltrox_marketing_token_v1";
const LEAKED_JWT = "eyJhbGciOiJIUzI1NiJ9.must-never-be-stored.sig";
const USER = { id: 7, email: "staff@example.test", name: "Staff" };

function Probe() {
  const { status, token, signIn, signOut } = useAuth();
  return (
    <div>
      <div data-testid="probe">{`${status}:${token || "-"}`}</div>
      <button type="button" onClick={() => void signIn("staff@example.test", "pw")}>sign-in</button>
      <button type="button" onClick={() => void signOut()}>sign-out</button>
    </div>
  );
}

function storageSnapshot(): string {
  const entries: Record<string, string | null> = {};
  for (let i = 0; i < window.localStorage.length; i += 1) {
    const key = window.localStorage.key(i) as string;
    entries[key] = window.localStorage.getItem(key);
  }
  return JSON.stringify(entries);
}

describe("AuthProvider cookie-only session (S-02)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    setCookieSessionActive(false);
    fetchMe.mockReset();
    login.mockReset();
    logout.mockClear();
  });
  afterEach(() => {
    setCookieSessionActive(false);
  });

  it("restores the session from the cookie via /me and purges the legacy localStorage JWT", async () => {
    window.localStorage.setItem(LEGACY_KEY, LEAKED_JWT);
    fetchMe.mockResolvedValue({ status: "success", user: USER });
    const getItem = vi.spyOn(Storage.prototype, "getItem");

    render(
      <AuthProvider onSessionExpiredRedirect={() => undefined}>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("loading:-");
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent(`authenticated:${COOKIE_SESSION_TOKEN}`));

    // /me 以占位 token 调用(后端当作走 cookie),而不是任何存储里读出来的 JWT。
    expect(fetchMe).toHaveBeenCalledWith(COOKIE_SESSION_TOKEN);
    expect(window.localStorage.getItem(LEGACY_KEY)).toBeNull();
    expect(storageSnapshot()).not.toContain(LEAKED_JWT);
    // localStorage 只被读来清理旧键,没有任何读取被当作凭证来源。
    expect(getItem.mock.calls.every(([key]) => key === LEGACY_KEY)).toBe(true);
    expect(readSessionToken()).toBe(COOKIE_SESSION_TOKEN);
    getItem.mockRestore();
  });

  it("falls back to guest when /me says not authenticated", async () => {
    fetchMe.mockResolvedValue({ status: "error", message: "Not authenticated" });
    render(
      <AuthProvider onSessionExpiredRedirect={() => undefined}>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("guest:-"));
    expect(readSessionToken()).toBe("");
  });

  it("signIn never persists the login response token and signOut drops the session marker", async () => {
    fetchMe.mockResolvedValue({ status: "error", message: "Not authenticated" });
    // 即便后端(非 cookie 会话客户端)回了 token,前端也不得落盘。
    login.mockResolvedValue({ status: "success", token: LEAKED_JWT, user: USER });
    render(
      <AuthProvider onSessionExpiredRedirect={() => undefined}>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("guest:-"));

    await act(async () => {
      screen.getByText("sign-in").click();
    });
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent(`authenticated:${COOKIE_SESSION_TOKEN}`));
    expect(login).toHaveBeenCalledWith("staff@example.test", "pw");
    expect(window.localStorage.length).toBe(0);
    expect(storageSnapshot()).not.toContain(LEAKED_JWT);
    expect(JSON.stringify(window.sessionStorage)).not.toContain(LEAKED_JWT);
    expect(readSessionToken()).toBe(COOKIE_SESSION_TOKEN);

    await act(async () => {
      screen.getByText("sign-out").click();
    });
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("guest:-"));
    expect(logout).toHaveBeenCalledTimes(1);
    expect(readSessionToken()).toBe("");
    expect(window.localStorage.length).toBe(0);
  });

  it("signIn without a user in the response is a login failure (token alone is not enough)", async () => {
    fetchMe.mockResolvedValue({ status: "error" });
    login.mockResolvedValue({ status: "success", token: LEAKED_JWT });
    let captured: Error | null = null;
    function Trigger() {
      const { signIn } = useAuth();
      return (
        <button
          type="button"
          onClick={() => {
            void signIn("a@b.c", "pw").catch((error: Error) => {
              captured = error;
            });
          }}
        >
          go
        </button>
      );
    }
    render(
      <AuthProvider onSessionExpiredRedirect={() => undefined}>
        <Trigger />
      </AuthProvider>,
    );
    await act(async () => {
      screen.getByText("go").click();
    });
    await waitFor(() => expect(captured).not.toBeNull());
    expect(window.localStorage.length).toBe(0);
    expect(readSessionToken()).toBe("");
  });
});
