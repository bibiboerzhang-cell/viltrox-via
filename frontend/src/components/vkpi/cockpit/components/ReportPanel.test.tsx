import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const reportApi = vi.hoisted(() => ({
  archiveVkpiReport: vi.fn(),
  createVkpiReportExport: vi.fn(),
  downloadVkpiFile: vi.fn(),
  generateVkpiReport: vi.fn(),
  listVkpiReports: vi.fn(),
  restoreVkpiReport: vi.fn(),
}));

vi.mock("../../../../services/vkpi/reports-api", () => ({
  VKPI_REPORT_SECTION_KEYS: ["kpiOverview", "attribution", "projects", "ledger", "risks", "summary"],
  ...reportApi,
  reportApiErrorMessage: (error: { status?: number }, fallback: string) => {
    if (error?.status === 403) return "权限不足：你不能执行此报告操作。";
    if (error?.status && error.status >= 500) return `${fallback}，系统未产出可用结果。`;
    return fallback;
  },
  reportModelPolicyLabel: (policy: { mode?: string } | null | undefined) => (
    policy?.mode === "deterministic_descriptive"
      ? "确定性描述模式（未调用模型）"
      : "策略未披露"
  ),
  vkpiReportDownloadPath: (id: number, format: string) => `/api/admin/vkpi/reports/files/${id}/download?format=${format}`,
}));

import { ReportPanel } from "./ReportPanel";

const activeReport = {
  id: 11,
  reportUid: "weekly-11",
  reportType: "weekly",
  periodStart: "2026-07-01T00:00:00Z",
  periodEnd: "2026-07-07T00:00:00Z",
  scopeType: "all",
  scopeId: null,
  triggeredAt: "2026-07-07T12:00:00Z",
  status: "ready",
  summary: "历史服务端摘要",
  dataStatus: "real",
  schemaVersion: "report.v1",
  archivedAt: "",
  archiveReason: "",
  modelPolicy: null,
  claimLevel: "",
};

const archivedReport = {
  ...activeReport,
  id: 12,
  reportUid: "weekly-12",
  status: "archived",
  archivedAt: "2026-07-08T12:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  reportApi.listVkpiReports.mockImplementation(async (_token: string, archived: boolean) => ({
    reports: archived ? [archivedReport] : [activeReport],
    count: 1,
    archived,
  }));
  reportApi.generateVkpiReport.mockResolvedValue({
    reportId: 42,
    reportUid: "monthly-42",
    reportType: "monthly",
    periodStart: "2026-06-14T00:00:00Z",
    periodEnd: "2026-07-13T00:00:00Z",
    status: "ready",
    downloadUrl: "/api/admin/vkpi/reports/files/42/download?format=markdown",
    summary: "新生成的服务端摘要",
    dataStatus: "partial",
    modelPolicy: {
      mode: "deterministic_descriptive",
      provider_calls_allowed: false,
      deterministic_only: true,
      claim_level: "descriptive_only",
    },
    claimLevel: "descriptive_only",
    metrics: [
      { key: "sales", label: "销售额", value: "$0", rawValue: 0, dataStatus: "real", note: "真实零值" },
      { key: "views", label: "播放量", value: null, rawValue: null, dataStatus: "awaiting_source", note: "等待来源" },
    ],
  });
  reportApi.createVkpiReportExport.mockResolvedValue({
    exportId: 7,
    status: "ready",
    downloadUrl: "/api/admin/vkpi/exports/7/download",
  });
  reportApi.downloadVkpiFile.mockResolvedValue("report.pdf");
  reportApi.archiveVkpiReport.mockResolvedValue(undefined);
  reportApi.restoreVkpiReport.mockResolvedValue(undefined);
});

function renderPanel() {
  return render(<ReportPanel apiToken="token" data={{ dashboard: { sourceHealth: { database: "real" } } }} onClose={vi.fn()} />);
}

describe("ReportPanel real server workflow", () => {
  it("submits the selected server contract and renders only the server response", async () => {
    renderPanel();
    expect(await screen.findByText("weekly-11")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "周报" }));
    fireEvent.change(screen.getByLabelText("报告截止日期"), { target: { value: "2026-07-13" } });
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    fireEvent.click(screen.getByRole("button", { name: "仅本人" }));
    fireEvent.click(screen.getByRole("button", { name: "风险提醒" }));
    expect(screen.getByText("策略未披露")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成服务端报告" }));

    await waitFor(() => expect(reportApi.generateVkpiReport).toHaveBeenCalledWith("token", {
      period: "weekly",
      date: "2026-07-13",
      language: "en",
      sections: ["kpiOverview", "attribution", "projects", "ledger", "summary"],
      format: "markdown",
      scope: "self",
    }));
    expect(await screen.findByText("新生成的服务端摘要")).toBeInTheDocument();
    expect(screen.getAllByText("确定性描述模式（未调用模型）").length).toBeGreaterThan(0);
    expect(screen.getByText("$0")).toBeInTheDocument();
    expect(screen.getByText("待数据")).toBeInTheDocument();
    expect(screen.queryByTitle("Report Preview")).not.toBeInTheDocument();
  });

  it("uses authenticated server export and history download paths", async () => {
    renderPanel();
    expect(await screen.findByText("weekly-11")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "CSV" }));
    await waitFor(() => expect(reportApi.createVkpiReportExport).toHaveBeenCalled());
    expect(reportApi.downloadVkpiFile).toHaveBeenCalledWith(
      "token",
      "/api/admin/vkpi/exports/7/download",
      expect.stringMatching(/\.csv$/),
    );

    fireEvent.click(screen.getByRole("button", { name: "下载" }));
    await waitFor(() => expect(reportApi.downloadVkpiFile).toHaveBeenCalledWith(
      "token",
      "/api/admin/vkpi/reports/files/11/download?format=pdf",
      "weekly-11.pdf",
    ));
  });

  it("archives with confirmation and restores from archived history", async () => {
    renderPanel();
    expect(await screen.findByText("weekly-11")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    fireEvent.click(screen.getByRole("button", { name: /确认归档/ }));
    await waitFor(() => expect(reportApi.archiveVkpiReport).toHaveBeenCalledWith("token", 11));

    fireEvent.click(screen.getByRole("button", { name: "已归档" }));
    expect(await screen.findByText("weekly-12")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /恢复/ }));
    await waitFor(() => expect(reportApi.restoreVkpiReport).toHaveBeenCalledWith("token", 12));
  });

  it("does not disguise a permission or server failure as an empty success", async () => {
    reportApi.listVkpiReports.mockRejectedValueOnce({ status: 403 });
    reportApi.generateVkpiReport.mockRejectedValueOnce({ status: 500 });
    renderPanel();

    expect(await screen.findByText("权限不足：你不能执行此报告操作。")).toHaveAttribute("role", "alert");
    fireEvent.click(screen.getByRole("button", { name: "生成服务端报告" }));
    expect(await screen.findByText("报告生成失败，系统未产出可用结果。")).toHaveAttribute("role", "alert");
  });
});
