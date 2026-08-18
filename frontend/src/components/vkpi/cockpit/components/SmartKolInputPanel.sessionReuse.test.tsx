import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const domainMocks = vi.hoisted(() => ({
  deepCrawlKolUrl: vi.fn(), getKolSearchSession: vi.fn(), listKolSearchHistory: vi.fn(),
  smartKolSearch: vi.fn(), smartKolSearchProfileAdvanceJob: vi.fn(),
}));
const serviceMocks = vi.hoisted(() => ({
  archiveAllKolSearchHistory: vi.fn(), archiveKolSearchHistorySession: vi.fn(),
  restoreKolSearchHistorySession: vi.fn(), approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(), favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(), resolveKolPool: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => domainMocks);
vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { SmartKolInputPanel } from "./SmartKolInputPanel";

function emptyRecall() {
  return {
    method: "vector_recall", query: {},
    ratio: { creator_quota: 15, reviewer_quota: 15, policy: "smart", mixed_policy: "smart", dedupe: true },
    items: [], buckets: { creator: [], reviewer: [] }, diagnostics: {},
  };
}

describe("Smart KOL preview session reuse", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    Object.values(domainMocks).forEach((mock) => mock.mockReset());
    Object.values(serviceMocks).forEach((mock) => mock.mockReset());
    domainMocks.listKolSearchHistory.mockResolvedValue({ items: [] });
    domainMocks.smartKolSearch.mockResolvedValue({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: emptyRecall(),
    });
    domainMocks.smartKolSearchProfileAdvanceJob.mockResolvedValue({ status: "queued" });
  });

  it("reuses preview session A for its automatic continuation but not for a manual re-filter", async () => {
    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "35mm portrait" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));

    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({ sessionId: 701 });

    fireEvent.click(screen.getByRole("button", { name: "重新全网查找" }));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(2));
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[1][2]).not.toHaveProperty("sessionId");
  });

  it("restores historical text recall as view-only until the current filters are re-run", async () => {
    domainMocks.getKolSearchSession.mockResolvedValue({
      id: 801,
      query_text: "35mm portrait",
      query_type: "text_recall",
      status: "done",
      result_summary: {
        required_tasks_complete: true,
        local_qualification: { schema: "smart_local_qualified_v2", returned_count: 0, qualified_count: 0 },
      },
      items: [],
    });
    render(<SmartKolInputPanel apiToken="token" />);
    await waitFor(() => expect(domainMocks.listKolSearchHistory).toHaveBeenCalled());
    act(() => {
      window.dispatchEvent(new CustomEvent("vkpi:open-kol-search-session", { detail: { sessionId: 801 } }));
    });

    expect(await screen.findByText(/下方是上一轮结果，仅供参考且不可批准/)).toBeTruthy();
    expect(screen.getByTestId("smart-kol-input")).toHaveValue("35mm portrait");

    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText(/下方是上一轮结果，仅供参考且不可批准/)).toBeNull());
  });
});
