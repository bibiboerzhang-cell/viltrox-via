import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetch, fetchProgressCenter } = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  fetchProgressCenter: vi.fn(),
}));

vi.mock("../../../../services/http", () => ({ apiFetch }));
vi.mock("../../../../services/vkpi/progressCenter-api", () => ({ fetchProgressCenter }));

import { DashboardTaskQueueCard } from "./DashboardTaskQueueCard";

beforeEach(() => {
  fetchProgressCenter.mockReset().mockResolvedValue({
    status: "ready",
    generated_at: "2026-07-09T12:00:00Z",
    counts: { running: 0, queued: 2, active_total: 2, recent_total: 0 },
    running: [],
    queued: [{
      id: "q-1",
      source: "ledger",
      kind: "账号深析",
      label: "youtube/@creator",
      status: "queued",
      stage: "queued",
      stage_label: "排队",
      created_at: "2026-07-09T11:59:00Z",
      updated_at: "2026-07-09T11:59:00Z",
      masked: false,
      progress_pct: 0,
      eta_seconds: null,
    }],
    recent_done: [],
    stage_flow: [],
    diagnostics: { worker_online: true },
  });
  apiFetch.mockReset().mockResolvedValue({
    today: { apify_calls: 1, llm_calls: 2, total_usd: 0.34 },
    budgets: { monthly_total: { configured: false } },
  });
});

describe("DashboardTaskQueueCard", () => {
  it("排队任务使用真实计数和静态等待态，不伪装成处理中动画", async () => {
    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("2 排队")).toBeInTheDocument();
    expect(screen.queryByText("2 处理中")).not.toBeInTheDocument();
    const queuedLane = screen.getByText("排队").closest(".vkpi-dashboard-task-queue__lane");
    expect(queuedLane).toBeTruthy();
    expect(queuedLane).not.toHaveTextContent("12%");
    expect(queuedLane?.querySelector("i")).toHaveClass("is-waiting");
    expect(queuedLane?.querySelector("i")).not.toHaveClass("is-indeterminate");
    expect(container).toHaveTextContent("今日 3 次 · $0.34");
  });

  it("Worker 离线时明确显示等待原因", async () => {
    const payload = await fetchProgressCenter();
    fetchProgressCenter.mockReset().mockResolvedValue({
      ...payload,
      diagnostics: { worker_online: false },
    });

    render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("Worker 离线 · 2 等待")).toBeInTheDocument();
    expect(screen.getByText("等待 Worker 上线")).toBeInTheDocument();
  });
});
