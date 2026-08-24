import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";


const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ClosureReadinessCard } from "./MyKolBoardPage.closure-readiness";


const RESPONSE = {
  contract: "my_kol_closure_readiness_v1",
  status: "attention",
  scope: { mode: "own", staff_scope_id: 7 },
  counts: {
    kol_count: 2,
    writable_kol_count: 2,
    monitoring_active_kols: 1,
    share_grants: 1,
    outbound_share_grants: 0,
    received_share_grants: 1,
    unattributed_received_share_grants: 0,
    candidate_videos: 2,
    trackable_videos: 2,
    tracked_videos: 1,
    employee_explicit_tracked_videos: 1,
    other_employee_explicit_tracked_videos: 0,
    employee_explicit_tracking_gap_videos: 1,
    system_seeded_tracked_videos: 0,
    unclassified_tracked_videos: 0,
    measured_tracked_videos: 1,
    sku_linked_tracked_videos: 1,
    sku_detected_videos: 1,
    sku_confirmed_videos: 0,
    final_v1_ready_videos: 1,
    final_v1_lens_scanned_videos: 0,
  },
  flows: {
    content_monitoring: { state: "configured_scheduler_disabled" },
    sharing: { state: "received_only" },
    video_tracking: { state: "partially_measured" },
    sku_linking: { state: "detected_pending_human_confirmation" },
    gemini_analysis: { state: "lens_extraction_pending" },
  },
  blockers: [
    { code: "content_monitoring_scheduler_disabled", count: 1, owner: "manager", approval_required: true },
    { code: "detected_sku_pending_confirmation", count: 1, owner: "employee", approval_required: true },
  ],
  summary: { blocker_kinds: 2, automatic_changes_performed: 0 },
  claim_status: "descriptive_only",
};


beforeEach(() => {
  apiFetchMock.mockReset().mockResolvedValue(RESPONSE);
});


describe("MY KOL 业务闭环状态卡", () => {
  it("分开展示配置、调度、追踪、SKU 人工确认和 Gemini 证据", async () => {
    render(<ClosureReadinessCard apiToken="token" />);

    expect(await screen.findByText("业务闭环状态")).toBeTruthy();
    expect(screen.getByText("内容订阅")).toBeTruthy();
    expect(screen.getByText("视频追踪")).toBeTruthy();
    expect(screen.getByText("SKU 关联")).toBeTruthy();
    expect(screen.getByText("Gemini 视频深析")).toBeTruthy();
    expect(screen.getAllByText("1 / 2").length).toBeGreaterThan(0);
    expect(screen.getByText("1 人工 · 0 系统")).toBeTruthy();
    expect(screen.getByText("发出 0 · 收到 1")).toBeTruthy();
    expect(screen.getByText("仅收到共享 · 非本人发起")).toBeTruthy();
    expect(screen.getByText(/员工待选 1 · 总追踪 1 \/ 2/)).toBeTruthy();
    expect(screen.getByText("0 / 1 同源成套")).toBeTruthy();
    expect(screen.getByText(/深析 1 \/ 2/)).toBeTruthy();
    expect(screen.getByText("已配置 · 调度未开启")).toBeTruthy();
    expect(screen.getByText("系统检出 · 待人工确认")).toBeTruthy();
    expect(screen.getByText(/深析 1 \/ 2 · 已深析 · 镜头证据待整理/)).toBeTruthy();
    expect(screen.queryByText(/KOL 内容订阅未选择/)).toBeNull();
    expect(screen.getByText(/内容巡检调度未开启 · 1 · 管理员开闸/)).toBeTruthy();
    expect(apiFetchMock).toHaveBeenCalledWith("/api/admin/vkpi/my-kol/closure-readiness", {}, "token");
  });

  it("端点失败时如实显示，不编造完成度", async () => {
    apiFetchMock.mockRejectedValueOnce(Object.assign(new Error("HTTP 500"), { detail: "closure failed" }));
    render(<ClosureReadinessCard apiToken="token" />);

    await waitFor(() => expect(screen.getByText(/closure failed/)).toBeTruthy());
    expect(screen.queryByText("暂无未闭环项")).toBeNull();
  });

  it("无 token 不发请求", () => {
    const { container } = render(<ClosureReadinessCard apiToken="" />);
    expect(container.firstChild).toBeNull();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });
});
