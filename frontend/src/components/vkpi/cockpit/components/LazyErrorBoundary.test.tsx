import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LazyErrorBoundary } from "./LazyErrorBoundary";

function BrokenModal(): React.ReactElement {
  throw new Error("modal render failed");
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LazyErrorBoundary", () => {
  it("isolates a lazy modal failure in a dismissible overlay", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onDismiss = vi.fn();
    render(
      <LazyErrorBoundary name="活动详情" variant="overlay" onDismiss={onDismiss}>
        <BrokenModal />
      </LazyErrorBoundary>,
    );

    expect(screen.getByRole("dialog", { name: "活动详情 加载失败" })).toHaveTextContent("活动详情 暂时出错");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
