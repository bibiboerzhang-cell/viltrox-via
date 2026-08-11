import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewPanelBoundary } from "./AutonomyDrivePage.embeds";

function BrokenReviewPanel(): React.ReactElement {
  throw new Error("review panel render failed");
}

describe("Autonomy review panel isolation", () => {
  it("keeps the surrounding page available when one lazy review panel fails", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <div>
        <div>自治页仍可用</div>
        <ReviewPanelBoundary name="外联真值复核">
          <BrokenReviewPanel />
        </ReviewPanelBoundary>
      </div>,
    );

    expect(screen.getByText("自治页仍可用")).toBeInTheDocument();
    expect(screen.getByRole("alert", { name: "外联真值复核 暂不可用" })).toHaveTextContent(
      "其他自治模块仍可继续使用",
    );
    expect(screen.getByRole("button", { name: "刷新后重试" })).toBeInTheDocument();
  });
});
