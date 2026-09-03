import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../../app/providers/LocaleProvider";
import { SESSION_EXPIRED_NOTICE_KEY } from "../../lib/authSession";

const signIn = vi.fn();
vi.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "guest",
    user: null,
    signIn: (...args: unknown[]) => signIn(...args),
  }),
}));

vi.mock("../../shared/ThemeSwitch", () => ({
  ThemeSwitch: () => null,
}));

vi.mock("../../lib/buildInfo", () => ({
  frontendBuildInfo: { gitBranch: "test", gitSha: "abcdef123456", builtAt: "2026-09-02T00:00:00Z" },
  shortBuildSha: (value: string) => value.slice(0, 8),
}));

import AdminLoginRoute from "./AdminLoginRoute";

function Landing() {
  return <div data-testid="landing">landed</div>;
}

function renderLogin(initialEntry: string) {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<AdminLoginRoute />} />
          <Route path="/" element={<Landing />} />
        </Routes>
      </MemoryRouter>
    </LocaleProvider>,
  );
}

describe("AdminLoginRoute session expiry + return path (U-B3)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    signIn.mockReset();
    signIn.mockResolvedValue({ id: 1, email: "a@b.c", name: "A" });
  });

  it("shows the one-time session-expired notice and clears it after reading", () => {
    window.sessionStorage.setItem(SESSION_EXPIRED_NOTICE_KEY, "1");
    renderLogin("/login?next=%2F%3Fcockpit%3Dmy-kol");

    expect(screen.getByRole("status")).toHaveTextContent("登录已失效，请重新登录。");
    expect(window.sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY)).toBeNull();
  });

  it("stays quiet without the notice flag", () => {
    renderLogin("/login");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("returns to the sanitized next path after a successful sign-in", async () => {
    renderLogin("/login?next=%2F%3Fcockpit%3Dmy-kol");
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.c" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByTestId("landing")).toBeInTheDocument());
    expect(signIn).toHaveBeenCalledWith("a@b.c", "secret");
  });

  it("refuses an external next target and falls back to the root", async () => {
    renderLogin("/login?next=https%3A%2F%2Fevil.example%2F");
    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "a@b.c" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    await waitFor(() => expect(screen.getByTestId("landing")).toBeInTheDocument());
  });
});
