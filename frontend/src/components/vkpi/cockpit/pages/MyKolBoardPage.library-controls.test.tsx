import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { KolLibraryPageActions } from "./MyKolBoardPage.library-controls";

describe("MY KOL library progressive list density", () => {
  it("keeps progress and paging actions readable and reachable", () => {
    render(
      <KolLibraryPageActions
        page={{ total: 130, has_more: true, next_cursor: "next" }}
        rowCount={50}
        filteredCount={42}
        loadingMore={false}
        loadMoreError=""
        onLoadMore={vi.fn()}
        onOpenList={vi.fn()}
      />,
    );

    expect(screen.getByText("已渐进展示 50 / 130 条").parentElement).toHaveClass("text-[11px]", "leading-5");
    expect(screen.getByRole("button", { name: "加载更多 50 条" })).toHaveClass("min-h-8");
    expect(screen.getByRole("button", { name: /查看已加载 42 条/ })).toHaveClass("min-h-9", "text-[11.5px]");
  });
});
