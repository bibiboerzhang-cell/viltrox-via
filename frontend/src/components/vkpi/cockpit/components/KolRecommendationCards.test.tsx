import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KolRecommendationCards } from "./KolRecommendationCards";

function renderCards(item: Record<string, unknown>) {
  render(
    <KolRecommendationCards
      items={[{ id: 1, handle: "truthful_creator", platform: "youtube", ...item }]}
      myList={new Set()}
      onOpenItem={vi.fn()}
    />,
  );
}

describe("KolRecommendationCards metric truth labels", () => {
  it("shows independent observed metrics without combining followers and views", () => {
    renderCards({ followers: 742000, avg_views: 17515, avg_likes: 820, avg_comments: 30 });

    expect(screen.getByText("粉丝")).toBeTruthy();
    expect(screen.getByText("742.0K")).toBeTruthy();
    expect(screen.getByText("均播")).toBeTruthy();
    expect(screen.getByText("17.5K")).toBeTruthy();
    expect(screen.getByText("均赞")).toBeTruthy();
    expect(screen.getByText("820")).toBeTruthy();
    expect(screen.getByText("均评")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
    expect(screen.queryByText(/粉\/播/)).toBeNull();
  });

  it("labels a sourced ordinary rate as 互动率, never Real ER", () => {
    renderCards({
      real_er_pct: null,
      engagement_rate: 4.3,
      engagement_rate_displayable: true,
      engagement_rate_source: "youtube_api",
      engagement_rate_updated_at: "2026-08-01T12:00:00Z",
    });

    expect(screen.getByText("互动率")).toBeTruthy();
    expect(screen.getByText("4.30%")).toBeTruthy();
    expect(screen.queryByText(/Real ER/)).toBeNull();
    expect(screen.getByText("互动率").closest("span")?.getAttribute("title")).toContain("youtube_api");
  });

  it("hides an ordinary rate without source/update provenance", () => {
    renderCards({ engagement_rate: 4.3, engagement_rate_displayable: false });

    expect(screen.queryByText("互动率")).toBeNull();
    expect(screen.queryByText("4.30%")).toBeNull();
    expect(screen.queryByText(/Real ER/)).toBeNull();
  });

  it("shows Real ER only for the verified runtime field", () => {
    renderCards({ real_er_pct: 2.1, real_er_sample_n: 40, engagement_rate: 3.8 });

    expect(screen.getByText(/Real ER/)).toBeTruthy();
    expect(screen.getByText("2.10%")).toBeTruthy();
    expect(screen.queryByText("互动率")).toBeNull();
  });
});
