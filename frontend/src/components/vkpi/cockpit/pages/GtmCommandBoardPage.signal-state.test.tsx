import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { normalizeMarketBrainSummary } from "../../../../services/vkpi/gtmCommand-api";
import { GtmKpiBand, SignalsBody } from "./GtmCommandBoardPage.modules";

afterEach(cleanup);

function summary(status: string, withSignal = false) {
  return normalizeMarketBrainSummary({ weekly_signals: {
    status, items: withSignal ? [{ signal: "fixture observed demand", kind: "brand_pulse" }] : [],
  } });
}

describe("signal card separates failed reads from an empty window", () => {
  it("shows recovery guidance for a failed read, not an empty-market conclusion", () => {
    render(<SignalsBody summary={summary("error")} />);
    expect(screen.getByRole("alert").textContent).toContain("市场信号读取失败");
    expect(screen.getByText(/不能据此判断市场没有需求/)).toBeTruthy();
    expect(screen.queryByText(/暂无信号/)).toBeNull();
  });

  it("preserves observed items alongside a partial-coverage warning", () => {
    render(<SignalsBody summary={summary("partial", true)} />);
    expect(screen.getByRole("status").textContent).toContain("仅有部分来源结果");
    expect(screen.getByText("fixture observed demand")).toBeTruthy();
  });

  it("keeps an unknown state distinct from a successful empty read", () => {
    render(<SignalsBody summary={summary("")} />);
    expect(screen.getByRole("status").textContent).toContain("来源状态待核验");
    expect(screen.queryByText(/暂无信号/)).toBeNull();
  });

  it("shows a scoped empty state only after a completed read", () => {
    render(<SignalsBody summary={summary("empty")} />);
    expect(screen.getByText("当前已读来源在对应窗口内暂无信号。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each(["error", "partial", ""])("does not publish an unqualified zero KPI for %s", (status) => {
    render(<GtmKpiBand summary={summary(status)} inbox={null} inboxError="" />);
    const tile = screen.getByText("本周信号").closest(".ds-kpi") as HTMLElement;
    expect(within(tile).getByText("—")).toBeTruthy();
    expect(within(tile).queryByText("0")).toBeNull();
    expect(within(tile).getByText("信号来源待核验")).toBeTruthy();
  });
});
