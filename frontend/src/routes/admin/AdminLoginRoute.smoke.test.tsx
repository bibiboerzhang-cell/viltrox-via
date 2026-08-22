import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
} from "../../app/providers/LocaleProvider";

vi.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({
    status: "unauthenticated",
    user: null,
    signIn: vi.fn(),
  }),
}));

vi.mock("../../shared/ThemeSwitch", () => ({
  ThemeSwitch: () => null,
}));

vi.mock("../../lib/buildInfo", () => ({
  frontendBuildInfo: { gitBranch: "test", gitSha: "abcdef123456", builtAt: "2026-07-18T00:00:00Z" },
  shortBuildSha: (value: string) => value.slice(0, 8),
}));

import AdminLoginRoute from "./AdminLoginRoute";

function renderLogin() {
  return render(
    <LocaleProvider>
      <MemoryRouter>
        <AdminLoginRoute />
      </MemoryRouter>
    </LocaleProvider>,
  );
}

describe("AdminLoginRoute public surface", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("uses the generic test brand and announces validation errors", () => {
    renderLogin();

    expect(screen.getByText("Viltrox Test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入邮箱和密码");
    expect(screen.getByLabelText("邮箱")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("密码")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("邮箱")).toHaveAttribute("aria-describedby", "ax-login-error");
    expect(screen.getByLabelText("密码")).toHaveAttribute("aria-describedby", "ax-login-error");
  });

  it("restores English before localizing the login form and validation", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    renderLogin();

    const submit = await screen.findByRole("button", { name: "Sign in" });
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();

    fireEvent.click(submit);
    expect(screen.getByRole("alert")).toHaveTextContent("Enter your email and password");
    expect(document.documentElement.lang).toBe("en");
  });
});
