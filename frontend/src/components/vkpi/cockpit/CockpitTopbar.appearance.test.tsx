import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../../../app/providers/ThemeProvider";
import { AppearancePopover, CockpitTopbar } from "./CockpitTopbar";
import { I18nContext, makeT } from "./lib/i18n";
import { I18N_EN } from "./data/i18nEn";

vi.mock("./components/AskCommandOverlay", () => ({
  AskCommandOverlay: () => null,
}));

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

const topbarProps = {
  activeNav: "dashboard",
  helpBtnRef: React.createRef(),
  setShowHelp: vi.fn(),
  messagesBtnRef: React.createRef(),
  setShowMessages: vi.fn(),
  activeReminders: [],
  setReportOpen: vi.fn(),
  notifsBtnRef: React.createRef(),
  setShowNotifs: vi.fn(),
  runtimeNotifications: [],
  userMenuBtnRef: React.createRef(),
  setShowUserMenu: vi.fn(),
  viewingAs: null,
  currentUser: { name: "Admin", role: "admin", avatar: "A", avatarUrl: "", avatarGradient: "" },
  apiToken: "",
  onNavigate: vi.fn(),
  dashboardEditing: false,
  setDashboardEditing: vi.fn(),
};

function renderTopbar(lang: "zh" | "en") {
  const t = makeT(lang, lang === "en" ? I18N_EN : undefined);
  return render(
    <ThemeProvider>
      <I18nContext.Provider value={{ t, lang, setLang: vi.fn() }}>
        <CockpitTopbar {...topbarProps} t={t} />
      </I18nContext.Provider>
    </ThemeProvider>,
  );
}

describe("CockpitTopbar i18n", () => {
  it("默认中文化 Dashboard 标题和通用操作", () => {
    renderTopbar("zh");

    expect(screen.getByRole("heading", { name: "仪表盘" })).toBeInTheDocument();
    expect(screen.getByText("增长总览")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更多工具" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "通知" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "用户菜单" })).toBeInTheDocument();
  });

  it("英文模式保持英文标题和通用操作", () => {
    renderTopbar("en");

    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("Growth Overview")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "More tools" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "User Menu" })).toBeInTheDocument();
  });
});
