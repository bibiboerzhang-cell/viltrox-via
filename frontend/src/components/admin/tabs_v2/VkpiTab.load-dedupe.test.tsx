import React, { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { createVkpiReportExport, fetchVkpiDashboardData, writeVkpiHash } = vi.hoisted(() => ({
  createVkpiReportExport: vi.fn(),
  fetchVkpiDashboardData: vi.fn(),
  writeVkpiHash: vi.fn(),
}));

vi.mock("../../../domains/dashboard", () => ({
  copyTextToClipboard: vi.fn(),
  fetchVkpiDashboardData,
  runKpiRollup: vi.fn(),
}));

vi.mock("../../../hooks/useAuth", () => ({
  useAuth: () => ({ refreshUser: vi.fn() }),
}));

vi.mock("../../../services/vkpi/reports-api", () => ({
  VKPI_REPORT_SECTION_KEYS: ["summary"],
  createVkpiReportExport,
  downloadVkpiFile: vi.fn(),
  generateVkpiReport: vi.fn(),
  reportApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("../../vkpi/layout/vkpiDashboardRouting", () => ({
  getInitialVkpiPage: () => "cockpit",
  writeVkpiHash,
}));

vi.mock("../../vkpi", async () => {
  const ReactModule = await import("react");
  return {
    VkpiDashboard: ({ data, isRefreshing, onRefreshData, onToggleView, onExportPDF }: any) => ReactModule.createElement(
      "div",
      null,
      ReactModule.createElement("output", { "data-testid": "dashboard-marker" }, data?.marker || "empty"),
      ReactModule.createElement("output", { "data-testid": "dashboard-loading" }, isRefreshing ? "loading" : "idle"),
      ReactModule.createElement("button", { type: "button", onClick: onRefreshData }, "refresh"),
      ReactModule.createElement("button", { type: "button", onClick: () => onToggleView?.("projects") }, "switch-to-projects"),
      ReactModule.createElement("button", { type: "button", onClick: onExportPDF }, "export-pdf"),
    ),
  };
});

import { VkpiTab } from "./VkpiTab";
import { I18nContext } from "../../vkpi/cockpit/lib/i18n";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function payload(marker: string) {
  return { marker, staffMembers: [], lastSyncedAt: `2026-07-15T00:00:0${marker.length}Z` } as any;
}

const owner = { staff_id: 7, is_owner: true, role: "owner" };

describe("VkpiTab dashboard load coalescing", () => {
  beforeEach(() => {
    window.localStorage.clear();
    createVkpiReportExport.mockReset();
    fetchVkpiDashboardData.mockReset();
    writeVkpiHash.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("coalesces StrictMode effect re-entry into one request for the same token and scope", async () => {
    const request = deferred<any>();
    fetchVkpiDashboardData.mockReturnValue(request.promise);

    render(
      <StrictMode>
        <VkpiTab token="token-a" user={owner} />
      </StrictMode>,
    );

    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(1));
    await act(async () => request.resolve(payload("A")));
    await waitFor(() => expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("A"));
    expect(screen.getByTestId("dashboard-loading")).toHaveTextContent("idle");
  });

  it("does not join or overwrite across a token change", async () => {
    const first = deferred<any>();
    const second = deferred<any>();
    fetchVkpiDashboardData.mockImplementation((token: string) => token === "token-a" ? first.promise : second.promise);

    const view = render(<VkpiTab token="token-a" user={owner} />);
    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(1));

    view.rerender(<VkpiTab token="token-b" user={owner} />);
    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(2));
    await act(async () => second.resolve(payload("B")));
    await waitFor(() => expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("B"));

    await act(async () => first.resolve(payload("A")));
    expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("B");
  });

  it("clears a failed in-flight entry so a manual retry issues a new request", async () => {
    fetchVkpiDashboardData
      .mockRejectedValueOnce(new Error("first load failed"))
      .mockResolvedValueOnce(payload("retry"));

    render(<VkpiTab token="token-a" user={owner} />);
    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("first load failed")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "refresh" }));
    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("retry"));
  });

  it("does not publish data or persistent cache after unmount", async () => {
    const request = deferred<any>();
    fetchVkpiDashboardData.mockReturnValue(request.promise);
    const view = render(<VkpiTab token="token-a" user={owner} />);
    await waitFor(() => expect(fetchVkpiDashboardData).toHaveBeenCalledTimes(1));

    view.unmount();
    await act(async () => request.resolve(payload("late")));
    expect(window.localStorage.length).toBe(0);
  });

  it("delegates the post-view-switch route write to the query-preserving hash helper", async () => {
    fetchVkpiDashboardData.mockResolvedValue(payload("route"));
    render(<VkpiTab token="token-a" user={owner} />);
    await waitFor(() => expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("route"));

    fireEvent.click(screen.getByRole("button", { name: "switch-to-projects" }));

    await waitFor(() => expect(writeVkpiHash).toHaveBeenCalledWith("projects"));
    expect(writeVkpiHash).toHaveBeenCalledTimes(1);
  });

  it("uses the active global language for report generation", async () => {
    fetchVkpiDashboardData.mockResolvedValue(payload("report"));
    createVkpiReportExport.mockResolvedValue({ downloadUrl: "" });

    render(
      <I18nContext.Provider value={{
        lang: "en",
        setLang: vi.fn(),
        t: (source) => source,
      }}>
        <VkpiTab token="token-a" user={owner} />
      </I18nContext.Provider>,
    );

    await waitFor(() => expect(screen.getByTestId("dashboard-marker")).toHaveTextContent("report"));
    fireEvent.click(screen.getByRole("button", { name: "export-pdf" }));

    await waitFor(() => expect(createVkpiReportExport).toHaveBeenCalledTimes(1));
    expect(createVkpiReportExport.mock.calls[0]?.[2]).toMatchObject({
      language: "en",
      period: "weekly",
      scope: "all",
    });
  });
});
