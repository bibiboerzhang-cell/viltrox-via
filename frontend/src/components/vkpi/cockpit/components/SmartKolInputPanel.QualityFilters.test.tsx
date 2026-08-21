import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SmartKolQualityFilters, SMART_KOL_MAX_LANGUAGES } from "./SmartKolInputPanel.QualityFilters";
import { nextRequiredPlatformSelection } from "./SmartKolInputPanel.TextResult";

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
});
