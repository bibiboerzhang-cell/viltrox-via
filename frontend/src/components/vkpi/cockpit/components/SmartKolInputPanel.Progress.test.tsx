import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import { ProgressiveSearchStageCard } from "./SmartKolInputPanel.Progress";

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
});
