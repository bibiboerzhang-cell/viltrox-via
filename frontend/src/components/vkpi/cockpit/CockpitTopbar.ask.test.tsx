import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../../../app/providers/ThemeProvider";
import { CockpitTopbar } from "./CockpitTopbar";
import { I18nContext, makeT } from "./lib/i18n";

vi.mock("./components/TopProgressCenter", () => ({ TopProgressCenter: () => null }));
vi.mock("../../../services/vkpi/intelligent-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../services/vkpi/intelligent-api")>();
  return { ...original, fetchSuggestions: vi.fn().mockResolvedValue([]) };
});

describe("CockpitTopbar Ask & Find entry", () => {
  it("keeps both desktop and mobile entry points wired to the same overlay", async () => {
    const t = makeT("en");
    render(
      <ThemeProvider>
        <I18nContext.Provider value={{ t, lang: "en", setLang: vi.fn() }}>
          <CockpitTopbar
            activeNav="kol-pool"
            helpBtnRef={React.createRef()}
            setShowHelp={vi.fn()}
            messagesBtnRef={React.createRef()}
            setShowMessages={vi.fn()}
            activeReminders={[]}
            setReportOpen={vi.fn()}
            notifsBtnRef={React.createRef()}
            setShowNotifs={vi.fn()}
            runtimeNotifications={[]}
            userMenuBtnRef={React.createRef()}
            setShowUserMenu={vi.fn()}
            viewingAs={null}
            currentUser={{ name: "Tester", role: "admin", avatar: "T", avatarGradient: "" }}
            t={t}
            apiToken="token-1"
            onNavigate={vi.fn()}
          />
        </I18nContext.Provider>
      </ThemeProvider>,
    );

    const entries = screen.getAllByRole("button", { name: "Open Ask & Find" });
    expect(entries).toHaveLength(2);
    expect(entries.some((button) => button.className.includes("md:hidden"))).toBe(true);
    fireEvent.click(entries.find((button) => button.className.includes("md:hidden"))!);
    expect(screen.getByRole("dialog", { name: "V-KPI Ask & Find" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Ask AI and global search" })).toHaveFocus());
  });
});
