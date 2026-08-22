import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
} from "../app/providers/LocaleProvider";

vi.mock("../app/providers/ThemeProvider", () => ({
  useTheme: () => ({
    theme: "light",
    style: "glass",
    styleLabel: "玻璃",
    toggleTheme: vi.fn(),
    cycleStyle: vi.fn(),
  }),
}));

import { ThemeSwitch } from "./ThemeSwitch";

describe("ThemeSwitch language control", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "zh-CN";
  });

  it("switches the shared application language and localizes its own controls", async () => {
    render(
      <LocaleProvider>
        <ThemeSwitch />
      </LocaleProvider>,
    );

    expect(screen.getByRole("group", { name: "外观与语言" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "风格:玻璃,点击切换" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));

    await waitFor(() => {
      expect(screen.getByRole("group", { name: "Appearance and language" })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Switch to Chinese" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Style: Glass, click to switch" })).toBeInTheDocument();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });
});
