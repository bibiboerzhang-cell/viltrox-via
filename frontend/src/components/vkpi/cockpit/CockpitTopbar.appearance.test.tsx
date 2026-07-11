import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ThemeProvider } from "../../../app/providers/ThemeProvider";
import { AppearancePopover } from "./CockpitTopbar";

function renderPopover() {
  return render(
    <ThemeProvider>
      <AppearancePopover />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  document.documentElement.setAttribute("data-style", "glass");
  document.documentElement.setAttribute("data-theme", "light");
  window.localStorage.clear();
});

describe("AppearancePopover", () => {
  it("通过 portal 打开，并能切换风格与明暗主题", async () => {
    renderPopover();

    fireEvent.click(screen.getByRole("button", { name: "外观 / 主题" }));
    expect(await screen.findByRole("dialog", { name: "桌面外观" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "玻璃" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "单色" }));
    expect(document.documentElement).toHaveAttribute("data-style", "commandos");
    expect(screen.getByRole("button", { name: "单色" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "深" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(screen.getByRole("button", { name: "深" })).toHaveAttribute("aria-pressed", "true");
  });

  it("玻璃模式可调整面板清晰度并写入首帧偏好", async () => {
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "外观 / 主题" }));

    const range = await screen.findByRole("slider", { name: "玻璃面板清晰度" });
    fireEvent.input(range, { target: { value: "92" } });

    expect(document.documentElement).toHaveAttribute("data-glass-opacity", "92");
    expect(document.documentElement.style.getPropertyValue("--vkpi-glass-opacity")).toBe("0.92");
    expect(JSON.parse(window.localStorage.getItem("vkpi-ui-pref-v1") || "null")).toMatchObject({
      theme: "light",
      style: "glass",
      glassOpacity: 92,
    });

    fireEvent.click(screen.getByRole("button", { name: "降低玻璃面板清晰度" }));
    expect(document.documentElement).toHaveAttribute("data-glass-opacity", "91");

    fireEvent.click(screen.getByRole("button", { name: "提高玻璃面板清晰度" }));
    expect(document.documentElement).toHaveAttribute("data-glass-opacity", "92");
  });

  it("Escape 关闭菜单并把焦点还给触发器", async () => {
    renderPopover();
    const trigger = screen.getByRole("button", { name: "外观 / 主题" });
    fireEvent.click(trigger);
    expect(await screen.findByRole("dialog", { name: "桌面外观" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "桌面外观" })).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});
