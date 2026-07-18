import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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

describe("AdminLoginRoute public surface", () => {
  it("uses the generic test brand and announces validation errors", () => {
    render(
      <MemoryRouter>
        <AdminLoginRoute />
      </MemoryRouter>,
    );

    expect(screen.getByText("Viltrox Test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入邮箱和密码");
    expect(screen.getByLabelText("邮箱")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("密码")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("邮箱")).toHaveAttribute("aria-describedby", "ax-login-error");
    expect(screen.getByLabelText("密码")).toHaveAttribute("aria-describedby", "ax-login-error");
  });
});
