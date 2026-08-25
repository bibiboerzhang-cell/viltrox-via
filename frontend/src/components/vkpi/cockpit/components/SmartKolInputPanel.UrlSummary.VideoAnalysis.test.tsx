import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getKolVideoAnalysisBatch: vi.fn(),
  getKolVideoAnalysisCache: vi.fn(),
}));

vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolVideoAnalysisBatch: (...args: unknown[]) => api.getKolVideoAnalysisBatch(...args),
  getKolVideoAnalysisCache: (...args: unknown[]) => api.getKolVideoAnalysisCache(...args),
}));

import { VideoSceneAnalysis } from "./SmartKolInputPanel.UrlSummary.VideoAnalysis";

function mainAnalysisCalls() {
  return api.getKolVideoAnalysisCache.mock.calls.filter((call) => call[2] === "video_analysis_final_v1");
}

describe("URL video analysis quality terminals", () => {
  beforeEach(() => {
    api.getKolVideoAnalysisBatch.mockReset();
    api.getKolVideoAnalysisCache.mockReset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("stops polling and never renders a quality_incomplete payload as ready", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "quality_incomplete",
      entry: {
        target_type: "video_evidence",
        target_id: "3951",
        derive_method: "video_analysis_final_v1",
        status: "quality_incomplete",
        result: { layer1_visual_content: { content_summary: "DO NOT RENDER" } },
      },
    });

    render(<VideoSceneAnalysis apiToken="token" evidenceId="3951" />);
    await act(async () => { await Promise.resolve(); });

    expect(screen.getAllByText("结果质量未通过").length).toBeGreaterThan(0);
    expect(screen.getByText(/待重试或人工复核/)).toBeTruthy();
    expect(screen.queryByText("DO NOT RENDER")).toBeNull();
    expect(mainAnalysisCalls()).toHaveLength(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(mainAnalysisCalls()).toHaveLength(1);
  });

  it("stops polling on a pure stale response and never renders the expired payload as ready", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "stale",
      entry: {
        target_type: "video_evidence",
        target_id: "3951",
        derive_method: "video_analysis_final_v1",
        status: "ready",
        result: { layer1_visual_content: { content_summary: "EXPIRED PAYLOAD" } },
      },
    });

    render(<VideoSceneAnalysis apiToken="token" evidenceId="3951" />);
    await act(async () => { await Promise.resolve(); });

    expect(screen.getAllByText("历史分析已过期").length).toBeGreaterThan(0);
    expect(screen.getByText(/重新发起分析/)).toBeTruthy();
    expect(screen.queryByText("EXPIRED PAYLOAD")).toBeNull();
    expect(mainAnalysisCalls()).toHaveLength(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(mainAnalysisCalls()).toHaveLength(1);
  });

  it("stops polling on legacy_unverified and never renders the historical payload", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video",
      target_id: "3951",
      state: "legacy_unverified",
      terminal: true,
      revalidation_required: true,
      entry: {
        target_type: "video",
        target_id: "3951",
        derive_method: "video_analysis_final_v1",
        status: "ready",
        result: { layer1_visual_content: { content_summary: "LEGACY PAYLOAD" } },
      },
    });

    render(<VideoSceneAnalysis apiToken="token" evidenceId="3951" />);
    await act(async () => { await Promise.resolve(); });

    expect(screen.getAllByText("历史结果待核验").length).toBeGreaterThan(0);
    expect(screen.queryByText("LEGACY PAYLOAD")).toBeNull();
    expect(mainAnalysisCalls()).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(mainAnalysisCalls()).toHaveLength(1);
  });
});
