import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import { ProgressiveSearchStageCard, runtimeSurfaceFromLocation } from "./SmartKolInputPanel.Progress";

function progress(overrides: Partial<SearchSessionProgress> = {}): SearchSessionProgress {
  return {
    phase: "enriching",
    phaseLabel: "后台深析中",
    target: 15,
    basicVisible: 15,
    profileReady: 4,
    profileCompleted: 5,
    profileSucceeded: 4,
    profileFailed: 1,
    profileRemaining: 10,
    currentItem: {
      itemId: 8,
      rank: 5,
      handle: "camera_creator",
      profileUrl: "https://example.test/camera_creator",
      status: "running",
      profileStatus: "running",
    },
    deepReady: 0,
    deepPartial: 0,
    failed: 1,
    accounted: 1,
    downstreamTracked: true,
    video: { ready: 2, active: 2, failed: 0, notRequested: 11 },
    comments: { ready: 1, active: 3, failed: 1, notRequested: 10 },
    audience: { ready: 0, active: 4, failed: 0, notRequested: 11 },
    completionContractExplicit: true,
    baseComplete: true,
    requestedTasksTerminal: false,
    fullAnalysisComplete: false,
    decisionEligible: false,
    requiredTasksComplete: false,
    contract: null,
    ...overrides,
  };
}

function contractStage(overrides: Record<string, unknown> = {}) {
  return {
    key: "stage",
    tracked: true,
    population: 30,
    requested: 30,
    successful: 0,
    terminal: 0,
    remaining: 30,
    dataReady: 0,
    state: "queued",
    counts: {
      ready: 0,
      queued: 0,
      running: 0,
      active: 0,
      partial: 0,
      failed: 0,
      skipped: 0,
      notRequested: 0,
    },
    ...overrides,
  };
}

describe("SmartKolInputPanel progressive stage card", () => {
  it("shows base results immediately and names the currently processed KOL", () => {
    render(<ProgressiveSearchStageCard progress={progress()} />);

    expect(screen.getByTestId("kol-progress-base")).toHaveTextContent("15/15 可查看");
    expect(screen.getByTestId("kol-progress-profile")).toHaveTextContent("5/15 已处理");
    expect(screen.getByTestId("kol-progress-profile")).toHaveTextContent("成功 4 · 失败 1 · 待处理 10");
    expect(screen.getByTestId("kol-progress-current-item")).toHaveTextContent("当前处理 · #5 · camera_creator · running");
    expect(screen.getByText("基础结果先展示，完整分析继续后台补全")).toBeInTheDocument();
  });

  it("renders video, comments, and audience as separate evidence stages", () => {
    render(<ProgressiveSearchStageCard progress={progress()} />);

    expect(screen.getByTestId("kol-progress-stage-video")).toHaveTextContent("完成 2 · 进行中 2 · 未请求 11");
    expect(screen.getByTestId("kol-progress-stage-comments")).toHaveTextContent("完成 1 · 进行中 3 · 失败 1 · 未请求 10");
    expect(screen.getByTestId("kol-progress-stage-audience")).toHaveTextContent("进行中 4 · 未请求 11");
  });

  it("warns that not-requested work is not full analysis even after requested tasks stop", () => {
    render(<ProgressiveSearchStageCard progress={progress({
      phase: "complete",
      phaseLabel: "已请求阶段已结束",
      requestedTasksTerminal: true,
      requiredTasksComplete: true,
      currentItem: null,
    })} />);

    expect(screen.getByTestId("kol-progress-strict-status")).toHaveTextContent("已请求阶段已结束");
    expect(screen.getByText("已请求阶段结束 · 尚非完整分析")).toBeInTheDocument();
    expect(screen.getByTestId("kol-progress-not-requested-warning")).toHaveTextContent("未请求 32 项不计入完整分析");
    expect(screen.queryByText("可进入决策")).not.toBeInTheDocument();
  });

  it("shows decision-ready only when the strict decision flag is true", () => {
    render(<ProgressiveSearchStageCard progress={progress({
      phase: "complete",
      phaseLabel: "完整分析已完成",
      video: { ready: 15, active: 0, failed: 0, notRequested: 0 },
      comments: { ready: 15, active: 0, failed: 0, notRequested: 0 },
      audience: { ready: 15, active: 0, failed: 0, notRequested: 0 },
      profileCompleted: 15,
      profileSucceeded: 15,
      profileFailed: 0,
      profileRemaining: 0,
      currentItem: null,
      requestedTasksTerminal: true,
      fullAnalysisComplete: true,
      decisionEligible: true,
      requiredTasksComplete: true,
    })} />);

    expect(screen.getByTestId("kol-progress-strict-status")).toHaveTextContent("可进入决策");
    expect(screen.getByText("决策证据已就绪")).toBeInTheDocument();
    expect(screen.queryByTestId("kol-progress-not-requested-warning")).not.toBeInTheDocument();
  });

  it("uses the versioned contract for 30 candidates, stage truth, worker blockage, and durable-success percent", () => {
    render(<ProgressiveSearchStageCard progress={progress({
      phase: "blocked",
      phaseLabel: "Worker 阻塞",
      target: 30,
      contract: {
        schema: "kol_search_progress_v1",
        claimStatus: "observed_execution_only",
        state: "blocked_by_worker",
        requestedUnits: 65,
        successfulUnits: 40,
        terminalUnits: 42,
        queuedUnits: 8,
        runningUnits: 0,
        activeUnits: 5,
        failedUnits: 2,
        progressPct: 61.5,
        terminalPct: 64.6,
        blockedByWorker: true,
        fullAnalysisComplete: false,
        fullAnalysisExecutionComplete: false,
        fullAnalysisObservable: false,
        observedAt: "2026-08-04T01:00:00Z",
        worker: {
          observed: true,
          state: "offline",
          online: false,
          onlineCount: 0,
          expectedCount: 16,
          capacityReady: false,
          latestHeartbeatAt: "2026-08-04T00:55:00Z",
          shaAligned: null,
        },
        stages: {
          search: contractStage({ key: "search", successful: 30, terminal: 30, remaining: 0, dataReady: 30, state: "ready", counts: { ready: 30, queued: 0, running: 0, active: 0, partial: 0, failed: 0, skipped: 0, notRequested: 0 } }),
          profile: contractStage({ key: "profile", successful: 10, terminal: 12, remaining: 18, dataReady: 10, state: "active", counts: { ready: 10, queued: 8, running: 0, active: 0, partial: 2, failed: 0, skipped: 0, notRequested: 0 } }),
          video: contractStage({ key: "video", requested: 0, successful: 0, terminal: 0, remaining: 0, dataReady: 0, state: "not_requested", counts: { ready: 0, queued: 0, running: 0, active: 0, partial: 0, failed: 0, skipped: 0, notRequested: 30 } }),
          comments: contractStage({ key: "comments", requested: 5, successful: 0, terminal: 0, remaining: 5, dataReady: null, state: "active", counts: { ready: 0, queued: 0, running: 0, active: 5, partial: 0, failed: 0, skipped: 0, notRequested: 25 } }),
          audience: contractStage({ key: "audience", requested: 0, successful: 0, terminal: 0, remaining: 0, dataReady: 0, state: "not_requested", counts: { ready: 0, queued: 0, running: 0, active: 0, partial: 0, failed: 0, skipped: 0, notRequested: 30 } }),
        },
      },
    })} />);

    expect(screen.getByTestId("kol-progress-base")).toHaveTextContent("30/30 已返回");
    expect(screen.getByTestId("kol-truth-progress-profile")).toHaveTextContent("10/30 成功");
    expect(screen.getByTestId("kol-truth-progress-profile")).toHaveTextContent("Worker 阻塞 8");
    expect(screen.getByTestId("kol-truth-progress-video")).toHaveTextContent("未请求");
    expect(screen.getByTestId("kol-truth-progress-comments")).toHaveTextContent("Worker 阻塞 5");
    expect(screen.getByTestId("kol-progress-worker")).toHaveTextContent("Worker 0/16 · 离线");
    expect(screen.getByTestId("kol-progress-success-pct")).toHaveTextContent("已请求成功 61.5%");
    expect(screen.getByTestId("kol-progress-terminal-pct")).toHaveTextContent("已结束 64.6%");
    expect(screen.getByText("排队 8 · 不计完成")).toBeInTheDocument();
    expect(screen.getByText("处理中/状态待确认 5 · 不计完成")).toBeInTheDocument();
  });

  it("does not render a percentage when the contract has no requested-unit denominator", () => {
    render(<ProgressiveSearchStageCard progress={progress({
      contract: {
        schema: "kol_search_progress_v1",
        claimStatus: "observed_execution_only",
        state: "planned",
        requestedUnits: null,
        successfulUnits: null,
        terminalUnits: null,
        queuedUnits: null,
        runningUnits: null,
        activeUnits: null,
        failedUnits: null,
        progressPct: null,
        terminalPct: null,
        blockedByWorker: false,
        fullAnalysisComplete: false,
        fullAnalysisExecutionComplete: false,
        fullAnalysisObservable: false,
        observedAt: "",
        worker: { observed: false, state: "unknown", online: null, onlineCount: null, expectedCount: null, capacityReady: null, latestHeartbeatAt: "", shaAligned: null },
        stages: {
          search: contractStage({ key: "search", population: null, requested: null, successful: null, terminal: null, remaining: null, dataReady: 4, state: "pending", counts: { ready: null, queued: null, running: null, active: null, partial: null, failed: null, skipped: null, notRequested: null } }),
          profile: contractStage({ key: "profile", tracked: false, population: null, requested: null, successful: null, terminal: null, remaining: null, dataReady: null, state: "", counts: { ready: null, queued: null, running: null, active: null, partial: null, failed: null, skipped: null, notRequested: null } }),
          video: contractStage({ key: "video", tracked: false, population: null, requested: null, successful: null, terminal: null, remaining: null, dataReady: null, state: "", counts: { ready: null, queued: null, running: null, active: null, partial: null, failed: null, skipped: null, notRequested: null } }),
          comments: contractStage({ key: "comments", tracked: false, population: null, requested: null, successful: null, terminal: null, remaining: null, dataReady: null, state: "", counts: { ready: null, queued: null, running: null, active: null, partial: null, failed: null, skipped: null, notRequested: null } }),
          audience: contractStage({ key: "audience", tracked: false, population: null, requested: null, successful: null, terminal: null, remaining: null, dataReady: null, state: "", counts: { ready: null, queued: null, running: null, active: null, partial: null, failed: null, skipped: null, notRequested: null } }),
        },
      },
    })} />);

    expect(screen.getByTestId("kol-progress-base")).toHaveTextContent("已返回 4");
    expect(screen.queryByTestId("kol-progress-success-pct")).not.toBeInTheDocument();
    expect(screen.queryByTestId("kol-progress-terminal-pct")).not.toBeInTheDocument();
    expect(screen.getByTestId("kol-progress-worker")).toHaveTextContent("Worker 未观测");
  });

  it("labels local and cloud API surfaces from the effective request origin without claiming deployment parity", () => {
    expect(runtimeSurfaceFromLocation("http://127.0.0.1:5173/?cockpit=kol-pool")).toMatchObject({ kind: "local", label: "本地后端" });
    expect(runtimeSurfaceFromLocation("https://www.viltroxtest.com/", "https://api.viltroxtest.com")).toMatchObject({ kind: "cloud", label: "云端后端", host: "api.viltroxtest.com" });
    expect(runtimeSurfaceFromLocation("file:///tmp/report.html")).toMatchObject({ kind: "unknown", label: "后端位置未知" });
  });
});
