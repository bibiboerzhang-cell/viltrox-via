import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FilterBar } from "./FilterBar";

const kindCounts = {
  total: 12,
  existing: 8,
  lowConfidence: 1,
  new: 4,
  newPromoted: 2,
  newDiscovered: 2,
};

function renderFilterBar(overrides: Record<string, unknown> = {}) {
  const setSearch = vi.fn();
  render(
    <FilterBar
      search=""
      setSearch={setSearch}
      country=""
      setCountry={vi.fn()}
      audienceType=""
      setAudienceType={vi.fn()}
      trendLevel=""
      setTrendLevel={vi.fn()}
      sortBy="v6_fit"
      setSortBy={vi.fn()}
      hasViltrox={false}
      setHasViltrox={vi.fn()}
      hasCompetitor={false}
      setHasCompetitor={vi.fn()}
      searchMode="balanced"
      setSearchMode={vi.fn()}
      kindFilter=""
      setKindFilter={vi.fn()}
      kindCounts={kindCounts}
      myListFilter={false}
      setMyListFilter={vi.fn()}
      myListCount={0}
      {...overrides}
    />,
  );
  return { setSearch };
}

describe("FilterBar local filtering", () => {
  it("filters immediately while typing and Enter does not submit another action", () => {
    const { setSearch } = renderFilterBar();
    const input = screen.getByPlaceholderText("输入即筛选关键词、@handle 或 URL...");

    fireEvent.change(input, { target: { value: "85mm" } });
    expect(setSearch).toHaveBeenCalledTimes(1);
    expect(setSearch).toHaveBeenLastCalledWith("85mm");

    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(setSearch).toHaveBeenCalledTimes(1);
    expect(screen.queryByTitle(/本地筛选\(Enter\)/)).toBeNull();
  });

  it("hides unfinished import and keeps only a real clear action", () => {
    const setSearch = vi.fn();
    renderFilterBar({ search: "portrait", setSearch });

    expect(screen.queryByText(/一键导入/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "清除本地筛选" }));
    expect(setSearch).toHaveBeenCalledWith("");
  });
});
