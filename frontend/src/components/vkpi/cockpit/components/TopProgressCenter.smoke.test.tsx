// U1 顶栏任务进度中心渲染冒烟:mock 服务层,断言 忙态药丸/抽屉内容/闲态收起。
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ProgressCenterData } from "../../../../services/vkpi/progressCenter-api";

const { fetchProgressCenter } = vi.hoisted(() => ({ fetchProgressCenter: vi.fn() }));
vi.mock("../../../../services/vkpi/progressCenter-api", () => ({ fetchProgressCenter }));

import { TopProgressCenter } from "./TopProgressCenter";

const STAGE_FLOW = [
  { stage: "queued", label: "队列中" },
  { stage: "search", label: "抓取" },
  { stage: "thinking", label: "分析" },
  { stage: "summarizing", label: "落库" },
];

function fixture(overrides: Partial<ProgressCenterData> = {}): ProgressCenterData {
  return {
    status: "ready",
    generated_at: "2026-07-07T08:00:00+00:00",
    counts: { running: 2, queued: 3, active_total: 5, recent_total: 5 },
    running: [
      {
        id: "101", source: "apify_jobs", kind: "video深析", job_type: "kol_video_deep_analysis",
        label: "youtube/@gearhead", platform: "youtube", kol_pool_id: "77",
        status: "running", stage: "thinking", stage_label: "思考",
        created_at: "2026-07-07T07:50:00+00:00", updated_at: "2026-07-07T07:59:00+00:00",
        masked: false, progress_pct: 42, eta_seconds: 180,
      },
      {
        id: "102", source: "ledger", kind: "物流同步", job_type: "logistics_track_sync",
        label: "SF123456", platform: null, kol_pool_id: null,
        status: "running", stage: "search", stage_label: "搜索",
        created_at: "2026-07-07T07:58:00+00:00", updated_at: "2026-07-07T07:59:30+00:00",
        masked: false, progress_pct: null, eta_seconds: null,
      },
    ],
    queued: [
      {
        id: "103", source: "apify_jobs", kind: "账号分析", job_type: "kol_profile_deep_crawl",
        label: "tiktok/@lensqueen", platform: "tiktok", kol_pool_id: "88",
        status: "queued", stage: "queued", stage_label: "排队",
        created_at: "2026-07-07T07:59:00+00:00", updated_at: "2026-07-07T07:59:00+00:00",
        masked: false, progress_pct: 0, eta_seconds: 420, queue_position: 1, ahead_count: 2,
      },
    ],
    recent_done: [
      {
        id: "99", source: "apify_jobs", kind: "评论采集", label: "instagram/@filmlook",
        status: "done", finished_at: "2026-07-07T07:45:00+00:00", has_error: false, masked: false,
      },
      {
        id: "98", source: "ledger", kind: "报告生成", label: "周报 W27",
        status: "failed", finished_at: "2026-07-07T07:40:00+00:00", has_error: true, masked: false,
      },
    ],
    stage_flow: STAGE_FLOW,
    ...overrides,
  };
}

describe("TopProgressCenter 渲染冒烟", () => {
  // 注意要用大括号:mockReset() 返回 mock 自身,而 vitest 会把 hook 返回的函数当
  // teardown 回调在测后调用——rejected 实现会让"拉取失败"用例被误判为测试失败。
  beforeEach(() => { fetchProgressCenter.mockReset(); });

  it("忙态:药丸显示「N 跑中 · M 排队」;点开抽屉见进度/阶段流/排队/最近完成", async () => {
    fetchProgressCenter.mockResolvedValue(fixture());
    render(React.createElement(TopProgressCenter));

    // 药丸态计数(等首拉落地)
    expect(await screen.findByText("2 跑中 · 3 排队")).toBeTruthy();

    // 点开抽屉
    fireEvent.click(screen.getByRole("button", { name: "Task Progress Center" }));
    expect(await screen.findByText("任务进度中心")).toBeTruthy();

    // 跑中:标题 + 进度% + 深析类的阶段流(当前步「分析」在 DOM)
    expect(screen.getByText("video深析 · youtube/@gearhead")).toBeTruthy();
    expect(screen.getByText("42%")).toBeTruthy();
    expect(screen.getByText("分析")).toBeTruthy();
    expect(screen.getByText("落库")).toBeTruthy();

    // 排队:位次 + ETA 分钟
    expect(screen.getByText("第 1 位")).toBeTruthy();
    expect(screen.getByText("账号分析 · tiktok/@lensqueen")).toBeTruthy();
    expect(screen.getByText("约 7 分钟")).toBeTruthy();

    // 最近完成:done 与 failed 各一条
    expect(screen.getByText("评论采集 · instagram/@filmlook")).toBeTruthy();
    expect(screen.getByText("报告生成 · 周报 W27")).toBeTruthy();
  });

  it("闲态:安静收起成图标按钮(无跑中文案);点开抽屉显示空闲文案", async () => {
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 0, queued: 0, active_total: 0, recent_total: 0 },
      running: [], queued: [], recent_done: [],
    }));
    render(React.createElement(TopProgressCenter));

    const btn = await screen.findByRole("button", { name: "Task Progress Center" });
    expect(btn).toBeTruthy();
    expect(screen.queryByText(/跑中/)).toBeNull();

    fireEvent.click(btn);
    expect(await screen.findByText("队列空闲,没有在跑的任务")).toBeTruthy();
  });

  it("首拉失败:不炸,保持安静图标态", async () => {
    fetchProgressCenter.mockRejectedValue(new Error("network down"));
    render(React.createElement(TopProgressCenter));
    expect(await screen.findByRole("button", { name: "Task Progress Center" })).toBeTruthy();
    expect(screen.queryByText(/跑中/)).toBeNull();
  });
});
