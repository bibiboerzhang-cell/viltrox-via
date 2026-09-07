import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OnlineQueryCellCoverage, queryCellCount, queryCellCoverageRows } from "./SmartKolInputPanel.QueryCellCoverage";

const fixture = { status: "partial", execution_status: "covered", cells: [
  { query_cell_id: "youtube-food", coverage_status: "covered", result_coverage_status: "covered",
    qualification_status: "observed", qualified_count: 2, pending_count: 1, selected_count: 1,
    duplicate_count: 3, retrieved_count: 8, unique_count: 5, actual_cost_usd: null,
    cost_attribution: "unavailable_no_cell_scoped_settled_observation" },
  { query_cell_id: "youtube-street", coverage_status: "not_executed", result_coverage_status: "partial",
    qualification_status: "not_evaluated", qualified_count: null, pending_count: null,
    selected_count: 0, duplicate_count: null, retrieved_count: 0, unique_count: null },
] };

describe("bounded query-cell execution and result evidence", () => {
  it("projects actual execution separately from qualification and preserves unknowns", () => {
    expect(queryCellCoverageRows(fixture)).toEqual([
      { id: "youtube-food", execution: "已执行", result: "已覆盖", qualified: "2", pending: "1", selected: "1", duplicates: "3", retrieval: "返回 8 · 唯一 5", cost: "未归因" },
      { id: "youtube-street", execution: "未执行", result: "尚未验收", qualified: "待核", pending: "待核", selected: "0", duplicates: "待核", retrieval: "返回 0 · 唯一 待核", cost: "未归因" },
    ]);
  });
  it.each([null, undefined, "0", false, -1, Number.NaN])("never turns invalid or missing %s into a measured zero", (value) => {
    expect(queryCellCount({ qualified_count: value }, "qualified_count")).toBe("待核");
  });
  it("lets explicit unknown status override a stale zero and never apportions aggregate cost", () => {
    const rows = queryCellCoverageRows({ actual_cost_usd: 9, cells: [{
      ...fixture.cells[0], qualified_count: 0, count_statuses: { qualified_count: "unknown" }, actual_cost_usd: 3,
    }] });
    expect(rows[0].qualified).toBe("待核");
    expect(rows[0].cost).toBe("未归因");
  });
  it("renders at most eight rows, truthful counts and no action buttons", () => {
    render(<OnlineQueryCellCoverage coverage={{ ...fixture, cells: Array.from({ length: 10 }, (_, i) => ({ ...fixture.cells[0], query_cell_id: `cell-${i}` })) }} />);
    const rows = screen.getAllByTestId("online-query-cell-row");
    expect(rows).toHaveLength(8);
    expect(within(rows[0]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["已执行", "已覆盖", "2", "1", "1", "3", "未归因"]);
    expect(screen.getByText(/不可相加当总人数/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("cell-8")).toBeNull();
  });
  it("shows missing-source actionability without calling it completed or empty", () => {
    render(<OnlineQueryCellCoverage coverage={null} blockedReason="未接入可信受众证据源，已停止本次联网调用" />);
    expect(screen.getByTestId("online-source-blocked")).toHaveTextContent("联网检索待接入处理");
    expect(screen.getByRole("status")).toHaveTextContent("这不是零结果，也不会自动重试");
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
