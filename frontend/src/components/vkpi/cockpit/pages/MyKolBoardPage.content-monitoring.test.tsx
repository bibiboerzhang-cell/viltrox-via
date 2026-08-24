import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
import { KolContentMonitoringSection } from "./MyKolBoardPage.content-monitoring";

const path = "/api/admin/vkpi/my-kol/101/content-monitoring";

function state(overrides: Record<string, unknown> = {}) {
  return {
    status: "ready",
    kol_pool_id: 101,
    subscription: null,
    own_subscription: false,
    active_subscription_count: 0,
    read_only: false,
    can_enable_or_pause_own: true,
    scope: "none",
    scheduler: { task_key: "vkpi_kol_content_monitoring", configured: true, enabled: true },
    provider_calls_performed: false,
    ...overrides,
  };
}

function renderSection(readOnly = false) {
  return render(
    <KolContentMonitoringSection
      apiToken="tok"
      kolPoolId={101}
      paidActionsReadOnly={readOnly}
      paidActionsReadOnlyHint="共享 KOL 仅可查看"
    />,
  );
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe("KolContentMonitoringSection", () => {
  it("separates active subscription, scheduler gate, bounded window, and last success", async () => {
    apiFetchMock.mockResolvedValue(state({
      subscription: {
        status: "active",
        cadence_hours: 24,
        last_job_status: "done",
        last_success_at: "2026-08-23T12:30:00Z",
        window: { kind: "recent_posts", max_posts: 12, full_history: false },
      },
      own_subscription: true,
      active_subscription_count: 1,
      scope: "own",
      scheduler: { task_key: "vkpi_kol_content_monitoring", configured: true, enabled: false },
    }));

    renderSection();

    expect(await screen.findByText("本人已订阅")).toBeInTheDocument();
    expect(screen.getByText(/最近 12 条内容，不代表频道完整历史/)).toBeInTheDocument();
    expect(screen.getByText(/后台巡检当前未开启/)).toBeInTheDocument();
    expect(screen.getByText(/订阅登记 ≠ 已抓取/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "暂停我的跟进" })).toBeEnabled();
    expect(apiFetchMock).toHaveBeenCalledWith(path, {}, "tok");
  });

  it("only reports registration after POST confirmation and a server re-read", async () => {
    let reads = 0;
    apiFetchMock.mockImplementation(async (_path: unknown, init: RequestInit = {}) => {
      if (init.method === "POST") return { status: "enabled", provider_calls_performed: false };
      reads += 1;
      return reads === 1
        ? state()
        : state({
            subscription: { status: "active", cadence_hours: 48, last_job_status: "", window: { max_posts: 12, full_history: false } },
            own_subscription: true,
            active_subscription_count: 1,
            scope: "own",
          });
    });

    renderSection();
    await screen.findByText("尚未订阅");
    fireEvent.change(screen.getByLabelText("内容跟进频率"), { target: { value: "48" } });
    fireEvent.click(screen.getByRole("button", { name: "开启我的跟进" }));

    expect(await screen.findByText(/订阅已登记；本次没有直接调用平台/)).toBeInTheDocument();
    expect(screen.getByText("本人已订阅")).toBeInTheDocument();
    expect(screen.queryByText(/已抓取成功/)).toBeNull();
    expect(apiFetchMock).toHaveBeenCalledWith(
      path,
      { method: "POST", body: JSON.stringify({ cadence_hours: 48 }) },
      "tok",
    );
    expect(reads).toBe(2);
  });

  it("fails closed for shared/read-only viewers before any write request", async () => {
    apiFetchMock.mockResolvedValue(state({
      read_only: true,
      can_enable_or_pause_own: false,
      subscription: { status: "active", cadence_hours: 24, window: { max_posts: 12, full_history: false } },
      active_subscription_count: 1,
      scope: "target_aggregate",
    }));

    renderSection(true);
    expect(await screen.findByText("团队已有订阅（共享只读）")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "开启我的跟进" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(apiFetchMock.mock.calls.some(([, init]) => Boolean((init as RequestInit)?.method))).toBe(false);
  });
});
