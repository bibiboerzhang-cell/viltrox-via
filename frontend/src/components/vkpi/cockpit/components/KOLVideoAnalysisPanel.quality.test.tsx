import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getKolVideoAnalysisBatch = vi.fn();
vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolVideoAnalysisBatch: (...args: unknown[]) => getKolVideoAnalysisBatch(...args),
}));

import { detailBundleAnalysisItems, videoAnalysisPollSnapshot } from "./KOLDetailDrawer.helpers";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";

describe("KOL final_v1 quality state", () => {
  beforeEach(() => {
    getKolVideoAnalysisBatch.mockReset();
  });

  it("detail bundle 透传终态/原因/job，且不把未合格 entry 当 ready", () => {
    const [bundle] = detailBundleAnalysisItems({
      video_analysis: {
        items: [{
          video: { evidence_id: 99, title: "Quality gated" },
          state: "quality_incomplete",
          reason: "final_v1_quality_incomplete",
          analysis_job: { state: "quality_incomplete", stage: "data_quality" },
          final_entry: { status: "quality_incomplete", result: { summary: "DO NOT RENDER" } },
        }],
      },
    });

    expect(bundle).toMatchObject({
      state: "quality_incomplete",
      reason: "final_v1_quality_incomplete",
      analysisJob: { state: "quality_incomplete", stage: "data_quality" },
      finalEntry: null,
    });
    expect(videoAnalysisPollSnapshot({ target_id: 99, state: "quality_incomplete" })).toMatchObject({
      ready: false,
      terminal: true,
      state: "quality_incomplete",
    });
  });

  it("显示独立质量终态，不渲染为已分析且不重复探测", () => {
    const bundles = detailBundleAnalysisItems({
      video_analysis: {
        items: [{
          video: { evidence_id: 99, title: "Quality gated" },
          state: "quality_incomplete",
          reason: "final_v1_quality_incomplete",
          final_entry: { status: "quality_incomplete", result: { summary: "DO NOT RENDER" } },
        }],
      },
    });

    render(
      <KOLVideoAnalysisPanel
        apiToken="token"
        videos={[{ evidence_id: 99, title: "Quality gated" }]}
        preloadedBundles={bundles}
        summary={{ evidence_count: 1, ready_count: 0, quality_incomplete_count: 1 }}
      />,
    );

    expect(screen.getAllByText("结果质量未通过").length).toBeGreaterThan(0);
    expect(screen.getByText(/待重试或人工复核/)).toBeInTheDocument();
    expect(screen.queryByText("DO NOT RENDER")).not.toBeInTheDocument();
    expect(getKolVideoAnalysisBatch).not.toHaveBeenCalled();
  });

  it("把纯 stale 视为终态，显示历史结果过期且不渲染或重复探测", () => {
    const bundles = detailBundleAnalysisItems({
      video_analysis: {
        items: [{
          video: { evidence_id: 100, title: "Stale result" },
          state: "stale",
          reason: "analysis_cache_stale",
          final_entry: { status: "ready", result: { summary: "EXPIRED PAYLOAD" } },
        }],
      },
    });

    expect(bundles[0]).toMatchObject({
      state: "stale",
      reason: "analysis_cache_stale",
      finalEntry: null,
    });
    expect(videoAnalysisPollSnapshot({ target_id: 100, state: "stale" })).toMatchObject({
      ready: false,
      terminal: true,
      state: "stale",
    });

    render(
      <KOLVideoAnalysisPanel
        apiToken="token"
        videos={[{ evidence_id: 100, title: "Stale result" }]}
        preloadedBundles={bundles}
        summary={{ evidence_count: 1, ready_count: 0 }}
      />,
    );

    expect(screen.getAllByText("历史分析已过期").length).toBeGreaterThan(0);
    expect(screen.getByText(/重新发起分析/)).toBeInTheDocument();
    expect(screen.queryByText("EXPIRED PAYLOAD")).not.toBeInTheDocument();
    expect(getKolVideoAnalysisBatch).not.toHaveBeenCalled();
  });

  it("把 legacy_unverified 视为终态，不渲染历史 payload 或重复探测", () => {
    const bundles = detailBundleAnalysisItems({
      video_analysis: { items: [{
        video: { evidence_id: 101, title: "Legacy result" },
        state: "legacy_unverified",
        reason: "final_v1_cache_legacy_unverified",
        final_entry: { status: "ready", result: { summary: "LEGACY PAYLOAD" } },
      }] },
    });
    expect(bundles[0]).toMatchObject({ state: "legacy_unverified", finalEntry: null });
    expect(videoAnalysisPollSnapshot({ target_id: 101, state: "legacy_unverified" })).toMatchObject({
      ready: false, terminal: true, state: "legacy_unverified",
    });

    render(<KOLVideoAnalysisPanel apiToken="token" videos={[{ evidence_id: 101 }]} preloadedBundles={bundles} />);

    expect(screen.getAllByText("历史结果待核验").length).toBeGreaterThan(0);
    expect(screen.queryByText("LEGACY PAYLOAD")).not.toBeInTheDocument();
    expect(getKolVideoAnalysisBatch).not.toHaveBeenCalled();
  });
});
