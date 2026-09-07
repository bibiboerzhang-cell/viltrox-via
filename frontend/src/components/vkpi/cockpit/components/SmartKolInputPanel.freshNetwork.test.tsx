import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const domain = vi.hoisted(() => ({
  deepCrawlKolUrl: vi.fn(), getKolSearchSession: vi.fn(), listKolSearchHistory: vi.fn(),
  smartKolSearch: vi.fn(), smartKolSearchProfileAdvanceJob: vi.fn(),
}));
const service = vi.hoisted(() => ({
  archiveAllKolSearchHistory: vi.fn(), archiveKolSearchHistorySession: vi.fn(),
  restoreKolSearchHistorySession: vi.fn(), approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(), favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(), listKolPoolFavorites: vi.fn(), resolveKolPool: vi.fn(),
}));
vi.mock("../../../../domains/kol", () => domain);
vi.mock("../../../../services/vkpi/kolPool-api", () => service);
import { SmartKolInputPanel } from "./SmartKolInputPanel";

const emptyRecall = { method: "saved", query: {}, items: [], buckets: { creator: [], reviewer: [] }, diagnostics: {} };

describe("explicit network search is independent of inventory preview", () => {
  beforeEach(() => {
    window.sessionStorage.clear(); window.localStorage.clear();
    Object.values(domain).forEach((mock) => mock.mockReset());
    Object.values(service).forEach((mock) => mock.mockReset());
    domain.listKolSearchHistory.mockResolvedValue({ items: [] });
    service.listKolPoolFavorites.mockResolvedValue({ items: [], total: 0 });
    domain.smartKolSearch.mockResolvedValue({ status: "ready", mode: "text", result: emptyRecall, search_session: { id: 701 } });
    domain.smartKolSearchProfileAdvanceJob.mockResolvedValue({ status: "queued", search_session: { id: 701, status: "queued" } });
    domain.getKolSearchSession.mockReturnValue(new Promise(() => {}));
  });

  it("typing does not call either provider-capable entrypoint", async () => {
    await act(async () => { render(<SmartKolInputPanel apiToken="token" />); });
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street night photography" } });
    expect(domain.smartKolSearch).not.toHaveBeenCalled();
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("explicit network button queues without asking for any inventory preview", async () => {
    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street night photography" } });
    fireEvent.click(screen.getByTestId("smart-kol-fresh-network"));
    await waitFor(() => expect(domain.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    expect(domain.smartKolSearch).not.toHaveBeenCalled();
    expect(domain.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).toMatchObject({ searchMode: "fresh_network" });
    expect(domain.smartKolSearchProfileAdvanceJob.mock.calls[0][2]).not.toHaveProperty("sessionId");
    await waitFor(() => expect(screen.getByTestId("smart-kol-network-status")).toHaveTextContent("后台查找中"));
  });

  it("inventory failure does not hide or break the explicit network action", async () => {
    domain.smartKolSearch.mockRejectedValue(new Error("本地库存暂不可用"));
    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street night photography" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await screen.findByText("本地库存暂不可用");
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("smart-kol-fresh-network"));
    await waitFor(() => expect(domain.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob.mock.calls[0][2].searchMode).toBe("fresh_network");
  });

  it("ordinary search explicitly submits hybrid once without a preview continuation POST", async () => {
    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street photography" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    await waitFor(() => expect(domain.smartKolSearch).toHaveBeenCalledTimes(1));
    expect(domain.smartKolSearch.mock.calls[0][2]).toMatchObject({ searchMode: "hybrid", includeNewDiscovery: true, executeNewDiscovery: true });
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("can supersede a stuck inventory preview without its late response queueing again", async () => {
    let resolvePreview!: (value: unknown) => void;
    domain.smartKolSearch.mockReturnValue(new Promise((resolve) => { resolvePreview = resolve; }));
    render(<SmartKolInputPanel apiToken="token" />);
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street photography" } });
    fireEvent.click(screen.getByTestId("smart-kol-run"));
    expect(screen.getByTestId("smart-kol-fresh-network")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("smart-kol-fresh-network"));
    await waitFor(() => expect(domain.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1));
    await act(async () => { resolvePreview({ status: "ready", mode: "text", result: emptyRecall, search_session: { id: 999 } }); });
    expect(domain.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob.mock.calls[0][2].searchMode).toBe("fresh_network");
  });

  it("does not silently use the text network mode for a URL", async () => {
    await act(async () => { render(<SmartKolInputPanel apiToken="token" />); });
    fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "https://youtube.com/@example" } });
    expect(screen.getByTestId("smart-kol-fresh-network")).toBeDisabled();
  });
});
