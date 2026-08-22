import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  LOCALE_STORAGE_KEY,
  LocaleProvider,
  useLocale,
} from "./LocaleProvider";

function LocaleProbe() {
  const { lang, languageLoading, setLang, t } = useLocale();
  return (
    <div>
      <output data-testid="lang">{lang}</output>
      <output data-testid="loading">{String(languageLoading)}</output>
      <output data-testid="message">
        {t("正在进入 {surface}...", { surface: "Viltrox Test" })}
      </output>
      <button type="button" onClick={() => setLang("en")}>to-en</button>
      <button type="button" onClick={() => setLang("zh")}>to-zh</button>
    </div>
  );
}

describe("LocaleProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "zh-CN";
  });

  it("defaults to Chinese, persists changes, and synchronizes the html lang attribute", async () => {
    render(
      <LocaleProvider>
        <LocaleProbe />
      </LocaleProvider>,
    );

    expect(screen.getByTestId("lang")).toHaveTextContent("zh");
    expect(screen.getByTestId("message")).toHaveTextContent("正在进入 Viltrox Test...");
    await waitFor(() => expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh"));
    expect(document.documentElement.lang).toBe("zh-CN");

    fireEvent.click(screen.getByRole("button", { name: "to-en" }));

    await waitFor(() => expect(screen.getByTestId("lang")).toHaveTextContent("en"));
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    expect(screen.getByTestId("message")).toHaveTextContent("Entering Viltrox Test...");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");

    fireEvent.click(screen.getByRole("button", { name: "to-zh" }));
    await waitFor(() => expect(screen.getByTestId("lang")).toHaveTextContent("zh"));
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("migrates the legacy Cockpit language preference without changing the legacy record", async () => {
    const legacy = JSON.stringify({ activeNav: "dashboard", lang: "en" });
    window.localStorage.setItem("vkpi-dashboard-state-v1", legacy);

    render(
      <LocaleProvider>
        <LocaleProbe />
      </LocaleProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("lang")).toHaveTextContent("en"));
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
    expect(window.localStorage.getItem("vkpi-dashboard-state-v1")).toBe(legacy);
  });

  it("prefers the dedicated global language key over the legacy Cockpit value", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "zh");
    window.localStorage.setItem(
      "vkpi-dashboard-state-v1",
      JSON.stringify({ lang: "en" }),
    );

    render(
      <LocaleProvider>
        <LocaleProbe />
      </LocaleProvider>,
    );

    expect(screen.getByTestId("lang")).toHaveTextContent("zh");
    await waitFor(() => expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh"));
    expect(document.documentElement.lang).toBe("zh-CN");
  });
});
