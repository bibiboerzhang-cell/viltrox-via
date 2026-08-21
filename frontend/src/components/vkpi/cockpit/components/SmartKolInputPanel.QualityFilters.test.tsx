import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SmartKolQualityFilters, SMART_KOL_MAX_LANGUAGES } from "./SmartKolInputPanel.QualityFilters";
import {
  EMPTY_KOL_SEARCH_FILTERS,
  KolSearchPolicyPanel,
  normalizeKolSearchLanguages,
  toKolSearchApiFilters,
} from "./SmartKolInputPanel.SearchPolicy";
import { nextRequiredPlatformSelection } from "./SmartKolInputPanel.TextResult";

function SharedLanguageSurfaces() {
  const [languages, setLanguages] = useState<string[]>([]);
  return (
    <>
      <KolSearchPolicyPanel
        open
        onToggleOpen={vi.fn()}
        strategy="balanced"
        onStrategyChange={vi.fn()}
        platforms={["youtube"]}
        onPlatformsChange={vi.fn()}
        languages={languages}
        onLanguagesChange={(values) => setLanguages(normalizeKolSearchLanguages(values))}
        filters={EMPTY_KOL_SEARCH_FILTERS}
        onFiltersChange={vi.fn()}
      />
      <SmartKolQualityFilters
        languages={languages}
        profileTypes={[]}
        onLanguagesChange={(values) => setLanguages(normalizeKolSearchLanguages(values))}
        onProfileTypesChange={vi.fn()}
      />
      <output data-testid="canonical-languages">{JSON.stringify(languages)}</output>
    </>
  );
}

describe("SmartKolQualityFilters", () => {
  it("emits explicit language and type selections without browser-side qualification", () => {
    const onLanguagesChange = vi.fn();
    const onProfileTypesChange = vi.fn();
    render(
      <SmartKolQualityFilters
        languages={[]}
        profileTypes={[]}
        onLanguagesChange={onLanguagesChange}
        onProfileTypesChange={onProfileTypesChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "日语" }));
    fireEvent.click(screen.getByRole("button", { name: "器材评测" }));

    expect(onLanguagesChange).toHaveBeenCalledWith(["ja"]);
    expect(onProfileTypesChange).toHaveBeenCalledWith(["reviewer"]);
    expect(screen.getByRole("button", { name: "日语" })).toHaveClass("min-h-7", "text-[10.5px]");
    expect(screen.getByText("未知证据不计入 30")).toBeTruthy();
  });

  it("clears an active filter group", () => {
    const onLanguagesChange = vi.fn();
    render(
      <SmartKolQualityFilters
        languages={["en", "de"]}
        profileTypes={[]}
        onLanguagesChange={onLanguagesChange}
        onProfileTypesChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(onLanguagesChange).toHaveBeenCalledWith([]);
  });

  it("caps content languages at eight while keeping selected languages removable", () => {
    const onLanguagesChange = vi.fn();
    render(
      <SmartKolQualityFilters
        languages={["en", "ja", "ko", "de", "fr", "es", "pt", "it"]}
        profileTypes={[]}
        onLanguagesChange={onLanguagesChange}
        onProfileTypesChange={vi.fn()}
      />,
    );

    expect(SMART_KOL_MAX_LANGUAGES).toBe(8);
    expect(screen.getByText(/不选则不限，最多 8 种/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "俄语" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "俄语" }));
    expect(onLanguagesChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "英语" }));
    expect(onLanguagesChange).toHaveBeenCalledWith(["ja", "ko", "de", "fr", "es", "pt", "it"]);
  });

  it("keeps at least one discovery platform selected", () => {
    expect(nextRequiredPlatformSelection(["youtube"], "youtube")).toEqual(["youtube"]);
    expect(nextRequiredPlatformSelection(["youtube", "instagram"], "youtube")).toEqual(["instagram"]);
    expect(nextRequiredPlatformSelection(["youtube"], "tiktok")).toEqual(["youtube", "tiktok"]);
  });

  it("uses one canonical language state for pre-search and strict qualification surfaces", () => {
    render(<SharedLanguageSurfaces />);

    fireEvent.change(screen.getByLabelText("内容语言"), { target: { value: "ja" } });
    expect(screen.getByTestId("canonical-languages")).toHaveTextContent('["ja"]');
    expect(screen.getByRole("button", { name: "日语" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "英语" }));
    expect(screen.getByTestId("canonical-languages")).toHaveTextContent('["en","ja"]');
    expect(screen.getByLabelText("内容语言")).toHaveValue("__multiple__");

    fireEvent.change(screen.getByLabelText("内容语言"), { target: { value: "" } });
    expect(screen.getByTestId("canonical-languages")).toHaveTextContent("[]");
    expect(screen.getByRole("button", { name: "英语" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "日语" })).toHaveAttribute("aria-pressed", "false");
  });

  it("canonicalizes explicit languages without deriving one from country", () => {
    expect(normalizeKolSearchLanguages(["JA", "en", "ja", "unsupported"])).toEqual(["en", "ja"]);
    expect(toKolSearchApiFilters(
      { ...EMPTY_KOL_SEARCH_FILTERS, country: "JP" },
      ["youtube"],
      [],
    )).toEqual({ platforms: ["youtube"], countries: ["JP"] });
    expect(toKolSearchApiFilters(
      { ...EMPTY_KOL_SEARCH_FILTERS, country: "JP" },
      ["youtube"],
      ["JA", "en", "ja"],
    )).toEqual({ platforms: ["youtube"], countries: ["JP"], languages: ["en", "ja"] });
  });
});
