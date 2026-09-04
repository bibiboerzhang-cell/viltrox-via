import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const domainMocks = vi.hoisted(() => ({
  listKolSearchHistory: vi.fn(),
}));
const serviceMocks = vi.hoisted(() => ({
  archiveAllKolSearchHistory: vi.fn(),
  archiveKolSearchHistorySession: vi.fn(),
  restoreKolSearchHistorySession: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => domainMocks);
vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { KolSearchHistoryPanel } from "./KolSearchHistoryPanel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function historyResponse(id: number, query: string) {
  return {
    items: [{
      id,
      session_id: id,
      query_text: query,
      query_type: "text_recall",
      status: "ready",
      item_count: 1,
    }],
  };
}

describe("KolSearchHistoryPanel account boundary", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    Object.values(domainMocks).forEach((mock) => mock.mockReset());
    Object.values(serviceMocks).forEach((mock) => mock.mockReset());
    serviceMocks.archiveKolSearchHistorySession.mockResolvedValue({ status: "archived" });
  });

  it("clears account A immediately and ignores its late refresh after same-cookie switch to B", async () => {
    const lateAActive = deferred<ReturnType<typeof historyResponse>>();
    const lateAArchived = deferred<{ items: never[] }>();
    const accountBActive = deferred<ReturnType<typeof historyResponse>>();
    const accountBArchived = deferred<{ items: never[] }>();
    domainMocks.listKolSearchHistory
      .mockResolvedValueOnce(historyResponse(101, "account A visible history"))
      .mockResolvedValueOnce({ items: [] })
      .mockImplementationOnce(() => lateAActive.promise)
      .mockImplementationOnce(() => lateAArchived.promise)
      .mockImplementationOnce(() => accountBActive.promise)
      .mockImplementationOnce(() => accountBArchived.promise);

    const view = render(
      <KolSearchHistoryPanel apiToken="cookie-session" accountId="account-a" />,
    );
    fireEvent.click(await screen.findByText("查看"));
    expect(await screen.findByText("account A visible history")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "移除历史：account A visible history" }));
    await waitFor(() => expect(domainMocks.listKolSearchHistory).toHaveBeenCalledTimes(4));

    view.rerender(
      <KolSearchHistoryPanel apiToken="cookie-session" accountId="account-b" />,
    );
    expect(screen.queryByText("account A visible history")).toBeNull();
    await waitFor(() => expect(domainMocks.listKolSearchHistory).toHaveBeenCalledTimes(6));

    await act(async () => {
      accountBActive.resolve(historyResponse(202, "account B current history"));
      accountBArchived.resolve({ items: [] });
    });
    expect(await screen.findByText("account B current history")).toBeInTheDocument();

    await act(async () => {
      lateAActive.resolve(historyResponse(303, "account A late response"));
      lateAArchived.resolve({ items: [] });
    });
    await waitFor(() => {
      expect(screen.getByText("account B current history")).toBeInTheDocument();
      expect(screen.queryByText("account A late response")).toBeNull();
    });
  });
});
