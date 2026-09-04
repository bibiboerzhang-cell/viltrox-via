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
  generateKolSearchSessionOutreach: vi.fn(), listKolPoolFavorites: vi.fn(), resolveKolPool: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => domainMocks);
vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { SmartKolInputPanel } from "./SmartKolInputPanel";
import { writePersistedSearchDisplay } from "./SmartKolInputPanel.derivers";

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
    serviceMocks.listKolPoolFavorites.mockResolvedValue({ items: [], total: 0 });
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

  it("continues a clarification choice through preview and queued discovery with the canonical SKU", async () => {
    const clarificationPlan = {
      status: "needs_clarification",
      original_query: "找 35 evo 摄影师",
      clarification: {
        message: "请选择目录产品",
        suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO" }],
      },
    };
    domainMocks.smartKolSearch
      .mockResolvedValueOnce({
        status: "needs_clarification",
        mode: "text",
        query_type: "text_recall",
        result: { ...emptyRecall(), llm_query_plan: clarificationPlan },
      })
      .mockResolvedValueOnce({
        status: "ready",
        mode: "text",
        query_type: "text_recall",
        search_session: { id: 702 },
        result: {
          ...emptyRecall(),
          llm_query_plan: { status: "ready", resolved_product: { sku: "AF-35-EVO", model_name: "AF 35mm F1.8 EVO" } },
        },
      });

    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "找 35 evo 摄影师" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));

    fireEvent.click(await screen.findByRole("button", { name: "选择产品 AF 35mm F1.8 EVO 并自动继续搜索" }));
    await waitFor(() => expect(domainMocks.smartKolSearch).toHaveBeenCalledTimes(2));
    expect(domainMocks.smartKolSearch.mock.calls[1][2]).toMatchObject({ productSku: "AF-35-EVO" });
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({
      productSku: "AF-35-EVO",
      sessionId: 702,
    });
  });

  it("does not apply an old clarification SKU after the operator edits the query", async () => {
    domainMocks.smartKolSearch.mockResolvedValueOnce({
      status: "needs_clarification",
      mode: "text",
      query_type: "text_recall",
      result: {
        ...emptyRecall(),
        llm_query_plan: {
          status: "needs_clarification",
          original_query: "find wedding photographers",
          clarification: {
            message: "请选择目录产品",
            suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO" }],
          },
        },
      },
    });

    render(<SmartKolInputPanel apiToken="token" accountId="account-a" />);
    const input = screen.getByTestId("smart-kol-input");
    fireEvent.change(input, { target: { value: "find wedding photographers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));

    const choice = await screen.findByRole("button", { name: "选择产品 AF 35mm F1.8 EVO 并自动继续搜索" });
    expect(choice).toBeEnabled();
    fireEvent.change(input, { target: { value: "find basketball storytellers" } });
    expect(choice).toBeDisabled();
    fireEvent.click(choice);

    expect(domainMocks.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domainMocks.smartKolSearch.mock.calls[0][1]).toBe("find wedding photographers");
  });

  it("isolates restored results and session polling by the real account id", async () => {
    writePersistedSearchDisplay({
      input: "account A wedding photographers",
      mode: "text",
      recallResult: emptyRecall(),
      urlResult: null,
      activeSearchSession: {
        id: 901,
        query_text: "account A wedding photographers",
        query_type: "text_recall",
        status: "running",
        items: [],
      },
      activeSearchSessionId: 901,
    }, "account-a");
    domainMocks.getKolSearchSession.mockImplementation(() => new Promise(() => undefined));
    domainMocks.listKolSearchHistory.mockImplementation(() => new Promise(() => undefined));
    serviceMocks.listKolPoolFavorites.mockImplementation(() => new Promise(() => undefined));

    const view = render(<SmartKolInputPanel apiToken="cookie-session" accountId="account-a" />);
    expect(screen.getByTestId("smart-kol-input")).toHaveValue("account A wedding photographers");
    await waitFor(() => expect(domainMocks.getKolSearchSession).toHaveBeenCalledWith("cookie-session", 901));

    view.rerender(<SmartKolInputPanel apiToken="cookie-session" accountId="account-b" />);
    expect(screen.getByTestId("smart-kol-input")).toHaveValue("");
    expect(screen.queryByText("account A wedding photographers")).toBeNull();
    expect(domainMocks.getKolSearchSession).toHaveBeenCalledTimes(1);
  });

  it("passes a product resolved by the current preview into its first automatic continuation", async () => {
    domainMocks.smartKolSearch.mockResolvedValueOnce({
      status: "ready",
      mode: "text",
      query_type: "text_recall",
      search_session: { id: 703 },
      result: {
        ...emptyRecall(),
        llm_query_plan: {
          status: "ready",
          resolved_product: { sku: "DC-X2", model_name: "DC-X2 Monitor" },
        },
      },
    });

    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "DC-X2 monitor filmmakers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));

    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({
      productSku: "DC-X2",
      sessionId: 703,
    });
  });

  it("uses each preview's immutable SKU across consecutive natural product searches", async () => {
    domainMocks.smartKolSearch
      .mockResolvedValueOnce({
        status: "ready",
        mode: "text",
        query_type: "text_recall",
        search_session: { id: 704 },
        result: {
          ...emptyRecall(),
          llm_query_plan: { status: "ready", resolved_product: { sku: "Z1-PRO" } },
        },
      })
      .mockResolvedValueOnce({
        status: "ready",
        mode: "text",
        query_type: "text_recall",
        search_session: { id: 705 },
        result: {
          ...emptyRecall(),
          llm_query_plan: { status: "ready", resolved_product: { sku: "DC-X2" } },
        },
      });

    render(<SmartKolInputPanel apiToken="token" />);
    const input = screen.getByTestId("smart-kol-input");
    fireEvent.change(input, { target: { value: "Z1 Pro flash wedding photographers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));

    fireEvent.change(input, { target: { value: "DC-X2 monitor filmmakers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(2));

    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({ productSku: "Z1-PRO" });
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[1][2]).toMatchObject({ productSku: "DC-X2" });
  });

  it("does not carry a previous product into a following people-only search", async () => {
    domainMocks.smartKolSearch
      .mockResolvedValueOnce({
        status: "ready",
        mode: "text",
        query_type: "text_recall",
        search_session: { id: 706 },
        result: {
          ...emptyRecall(),
          llm_query_plan: { status: "ready", resolved_product: { sku: "Z1-PRO" } },
        },
      })
      .mockResolvedValueOnce({
        status: "ready",
        mode: "text",
        query_type: "text_recall",
        search_session: { id: 707 },
        result: {
          ...emptyRecall(),
          llm_query_plan: { status: "ready", target_persona: "wedding photographers" },
        },
      });

    render(<SmartKolInputPanel apiToken="token" />);
    const input = screen.getByTestId("smart-kol-input");
    fireEvent.change(input, { target: { value: "Z1 Pro flash wedding photographers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));

    fireEvent.change(input, { target: { value: "wedding photographers" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domainMocks.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(2));

    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({ productSku: "Z1-PRO" });
    expect(domainMocks.smartKolSearchProfileAdvanceJob.mock.calls[1][2]).not.toHaveProperty("productSku");
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
