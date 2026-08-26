// U1 顶栏任务进度中心渲染冒烟:mock 服务层,断言 忙态药丸/抽屉内容/闲态收起。
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
        masked: false, progress_pct: 42, progress_estimated: true, eta_seconds: 180,
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
    recent_llm: [],
    stage_flow: STAGE_FLOW,
    diagnostics: { worker_online: true },
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

    // 跑中:标题 + 进度% + 视频深析的专属阶段流。
    expect(screen.getByText("视频深度分析 · youtube/@gearhead")).toBeTruthy();
    expect(screen.getByText("≈42%")).toBeTruthy();
    expect(screen.getByText("模型深析")).toBeTruthy();
    expect(screen.getByText("分镜落库")).toBeTruthy();

    // 排队:位次 + ETA 分钟
    expect(screen.getByText("第 1 位")).toBeTruthy();
    expect(screen.getByText("账号分析 · tiktok/@lensqueen")).toBeTruthy();
    expect(screen.getByText("约 7 分钟")).toBeTruthy();

    // 最近完成:done 与 failed 各一条
    expect(screen.getByText("评论采集 · instagram/@filmlook")).toBeTruthy();
    expect(screen.getByText("报告生成 · 周报 W27")).toBeTruthy();
  });

  it("超出历史均时后显示诚实的不定进度，不长期伪装成 95%", async () => {
    const overdue = fixture().running[0];
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 1, queued: 0, active_total: 1, recent_total: 0 },
      running: [{
        ...overdue,
        progress_pct: null,
        progress_estimated: false,
        progress_overdue: true,
        progress_label: "已超历史均时",
        eta_seconds: null,
      }],
      queued: [],
      recent_done: [],
    }));
    const { container } = render(React.createElement(TopProgressCenter));

    expect(await screen.findByText("1 跑中")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Task Progress Center" }));
    expect(await screen.findByText("已超历史均时")).toBeTruthy();
    expect(screen.queryByText("95%")).toBeNull();
    expect(container.querySelector(".tpc-breath")).toBeTruthy();
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

  it("只有排队且后台没在跑时不显示跑中动画，并解释等待原因", async () => {
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 0, queued: 1, active_total: 1, recent_total: 0 },
      running: [],
      queued: [fixture().queued[0]],
      recent_done: [],
      diagnostics: { worker_online: false },
    }));
    const { container } = render(React.createElement(TopProgressCenter));

    expect(await screen.findByText("后台没在跑 · 1 等待")).toBeTruthy();
    expect(container.querySelector(".tpc-breath")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Task Progress Center" }));
    expect(await screen.findByText("后台未在运行，排队任务不会开始")).toBeTruthy();
    expect(screen.getByText("等待后台")).toBeTruthy();
  });

  it("首拉失败:不炸,并明确当前状态未知而非伪装成空闲", async () => {
    fetchProgressCenter.mockRejectedValue(new Error("network down"));
    render(React.createElement(TopProgressCenter));
    const button = await screen.findByRole("button", { name: "Task Progress Center" });
    expect(button).toBeTruthy();
    expect(screen.queryByText(/跑中/)).toBeNull();
    fireEvent.click(button);
    expect(await screen.findByText(/当前状态未知/)).toBeTruthy();
    expect(screen.queryByText("队列空闲,没有在跑的任务")).toBeNull();
  });

  it("retrying 只进排队区，各终态显示权威结果而非统一失败", async () => {
    const retrying = {
      ...fixture().queued[0],
      id: "retrying-1",
      status: "retrying",
      queue_position: 1,
    };
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 0, queued: 1, active_total: 1, recent_total: 4 },
      running: [],
      queued: [retrying],
      recent_done: [
        {
          id: "blocked-1", source: "apify_jobs", kind: "video深析", label: "video-1",
          status: "blocked", finished_at: "2026-07-07T07:45:00+00:00", has_error: false, masked: false,
          reason_code: "budget_blocked", reason_category: "budget",
        },
        {
          id: "cancelled-1", source: "ledger", kind: "账号分析", label: "profile-1",
          status: "cancelled", finished_at: "2026-07-07T07:44:00+00:00", has_error: false, masked: false,
        },
        {
          id: "partial-1", source: "ledger", kind: "评论采集", label: "comments-1",
          status: "partial_done", finished_at: "2026-07-07T07:43:00+00:00", has_error: false, masked: false,
        },
        {
          id: "triage-1", source: "apify_jobs", kind: "受众分析", label: "audience-1",
          status: "triage", finished_at: "2026-07-07T07:42:00+00:00", has_error: true, masked: false,
        },
      ],
      recent_llm: [],
    }));
    render(React.createElement(TopProgressCenter));

    expect(await screen.findByText("1 排队")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Task Progress Center" }));
    expect(await screen.findByText("等待重试")).toBeTruthy();
    expect(screen.getByText("最近结果")).toBeTruthy();
    expect(screen.getByText("已阻塞")).toBeTruthy();
    expect(screen.getByText("已取消")).toBeTruthy();
    expect(screen.getByText("部分完成")).toBeTruthy();
    expect(screen.getByText("待人工排查")).toBeTruthy();
    expect(screen.queryByText(/跑中/)).toBeNull();
  });

  it("按真实任务类型展示受众/QA 阶段，并单列模型分析回退记录", async () => {
    const base = fixture();
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 2, queued: 0, active_total: 2, recent_total: 1 },
      running: [
        {
          ...base.running[0],
          id: "audience-1",
          kind: "受众分析",
          job_type: "kol_audience_stats_refresh",
          stage: "thinking",
        },
        {
          ...base.running[0],
          id: "qa-1",
          kind: "视频QA",
          job_type: "video_analysis_final_v1_keyframe_qa",
          stage: "thinking",
          provider: "google",
          model: "gemini-2.5-pro",
          task_binding: "audit_video_analysis",
          phase: "evaluation",
          subphase: "provider_generation",
          attempt_index: 1,
          attempt_total: 2,
        },
      ],
      queued: [],
      recent_done: [],
      recent_llm: [{
        id: "llm-1",
        source: "llm_calls",
        kind: "LLM分析",
        job_type: "llm:audience_inference",
        label: "audience_inference",
        status: "failed",
        finished_at: "2026-07-07T07:45:00+00:00",
        has_error: true,
        masked: false,
        provider: "google",
        model: "gemini-2.5-pro",
        task_binding: "kol_audience_analysis",
        fallback_used: true,
        fallback_mode: "rule_v0",
        reason_code: "readiness_not_production_ready",
        phase: "qa",
        subphase: "provider_generation",
        attempt_index: 2,
        attempt_total: 2,
      }],
    }));
    render(React.createElement(TopProgressCenter));

    expect(await screen.findByText("2 跑中")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Task Progress Center" }));
    await waitFor(() => expect(fetchProgressCenter).toHaveBeenCalledTimes(2));

    expect(screen.getByText("受众推断")).toBeTruthy();
    expect(screen.getByText("画像落库")).toBeTruthy();
    expect(screen.getByText("模型复核")).toBeTruthy();
    expect(screen.getByText("QA 落库")).toBeTruthy();
    // 波 C·C3 门面去内部术语:不再写 LLM / Gateway 字面,分区仍单列
    expect(screen.getByText("模型分析记录")).toBeTruthy();
    expect(screen.getByText("已完成调用")).toBeTruthy();
    expect(screen.getByText("规则回退")).toBeTruthy();
    expect(screen.getByText(/不冒充模型结论/)).toBeTruthy();
    // 红线2:厂商名/模型 id 不上门面,只出通道角色;原标识下沉到 title/溯源。
    expect(screen.getAllByText("主通道").length).toBeGreaterThan(0);
    expect(screen.getByText("规则降级")).toBeTruthy();
    expect(screen.queryByText(/gemini|Google|OpenAI|Anthropic|Claude/i)).toBeNull();
    expect(screen.getAllByTitle("服务 google · 模型 gemini-2.5-pro").length).toBeGreaterThan(0);
    expect(screen.getByText("任务绑定 · 视频 AI 分析")).toBeTruthy();
    expect(screen.getByText("任务绑定 KOL 受众分析")).toBeTruthy();
    expect(screen.getByText("结果评估 · 模型生成 · 尝试 1/2")).toBeTruthy();
    expect(screen.getByText("证据 QA · 模型生成 · 尝试 2/2")).toBeTruthy();
  });

  it("在飞跟踪 schema 未就绪时明确提示在飞跟踪不可用(门面不提 migration 号)", async () => {
    fetchProgressCenter.mockResolvedValue(fixture({
      counts: { running: 0, queued: 0, active_total: 0, recent_total: 0 },
      running: [],
      queued: [],
      recent_done: [],
      recent_llm: [],
      diagnostics: {
        worker_online: true,
        llm_visibility: "gateway_outcomes_only_reservation_schema_unavailable",
        llm_reservation_schema_available: false,
      },
    }));
    render(React.createElement(TopProgressCenter));

    fireEvent.click(await screen.findByRole("button", { name: "Task Progress Center" }));
    expect(await screen.findByText(/在飞跟踪尚未启用/)).toBeTruthy();
    expect(screen.getByText(/只显示已完成的分析结果/)).toBeTruthy();
    expect(screen.queryByText(/migration 258/)).toBeNull();
  });
});
