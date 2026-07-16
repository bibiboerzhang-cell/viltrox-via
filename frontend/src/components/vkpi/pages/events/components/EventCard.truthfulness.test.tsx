import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import EventCard from "./EventCard";
import type { EventVm } from "../shared/types";

function event(overrides: Partial<EventVm> = {}): EventVm {
  return {
    id: "truth-event",
    title: "未命名活动",
    typeKey: "other",
    status: "planning",
    healthScore: null,
    startDate: "2000-01-01",
    endDate: "2000-01-02",
    location: { city: "", country: "" },
    budgetTotal: 0,
    budgetByCategory: {},
    ownerId: "1",
    teamUserIds: [],
    relatedProjectIds: [],
    invitedKols: [],
    updatedAt: "",
    ...overrides,
  };
}

describe("EventCard truthfulness gates", () => {
  it("shows unknown budget/health and an ended schedule without NaN", () => {
    const { container } = render(<EventCard ev={event()} onOpen={vi.fn()} />);

    expect(screen.getByText(/已结束 \d+ 天/)).toBeTruthy();
    expect(screen.getByText("待评估")).toBeTruthy();
    expect(screen.getByText(/未设预算/)).toBeTruthy();
    expect(container.textContent).not.toContain("NaN");
    expect(container.textContent).not.toContain("进行中 9690 天");
  });

  it("qualifies a numeric score as an unproven recorded value", () => {
    render(<EventCard ev={event({ healthScore: 100 })} onOpen={vi.fn()} />);

    const score = screen.getByText("录入 100");
    expect(score.getAttribute("title")).toContain("未提供评分来源");
  });
});
