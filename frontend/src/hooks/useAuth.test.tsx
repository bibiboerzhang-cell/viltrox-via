import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTH_EXPIRED_EVENT, SESSION_EXPIRED_NOTICE_KEY, resetAuthExpiredNotice } from "../lib/authSession";

const fetchMe = vi.fn();
vi.mock("../services/auth.service", () => ({
  fetchMe: (...args: unknown[]) => fetchMe(...args),
  login: vi.fn(),
  logout: vi.fn(async () => ({ status: "success" })),
}));

const clearApiCache = vi.fn();
vi.mock("../lib/apiCache", () => ({
  clearApiCache: () => clearApiCache(),
}));

import { AuthProvider, useAuth } from "./useAuth";

const TOKEN_KEY = "viltrox_marketing_token_v1";

function Probe() {
  const { status, token } = useAuth();
  return <div data-testid="probe">{`${status}:${token || "-"}`}</div>;
}

describe("AuthProvider global session expiry (U-B3)", () => {
  beforeEach(() => {
    resetAuthExpiredNotice();
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearApiCache.mockClear();
    fetchMe.mockResolvedValue({ status: "success", user: { id: 1, email: "a@b.c", name: "A" } });
    window.history.replaceState(null, "", "/?cockpit=my-kol#top");
  });
  afterEach(() => {
    resetAuthExpiredNotice();
    window.history.replaceState(null, "", "/");
  });

  it("clears the session, marks the one-time notice and redirects to /login with a return path", async () => {
    window.localStorage.setItem(TOKEN_KEY, "tok");
    const redirect = vi.fn();
    render(
      <AuthProvider onSessionExpiredRedirect={redirect}>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("authenticated:cookie-session"));

    act(() => {
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { path: "/api/x", status: 401 } }));
    });

    expect(screen.getByTestId("probe")).toHaveTextContent("guest:-");
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(clearApiCache).toHaveBeenCalled();
    expect(window.sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY)).toBe("1");
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(redirect).toHaveBeenCalledWith("/login?next=%2F%3Fcockpit%3Dmy-kol%23top");
  });

  it("does not redirect again when already on the login page", async () => {
    window.history.replaceState(null, "", "/login");
    window.localStorage.setItem(TOKEN_KEY, "tok");
    const redirect = vi.fn();
    render(
      <AuthProvider onSessionExpiredRedirect={redirect}>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("authenticated:cookie-session"));

    act(() => {
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { path: "/api/x", status: 401 } }));
    });

    expect(screen.getByTestId("probe")).toHaveTextContent("guest:-");
    expect(redirect).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY)).toBeNull();
  });
});
