import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getAnalysisCache = vi.fn();
vi.mock("../../../services/vkpi/projects-api", () => ({
  getAnalysisCache: (...args: unknown[]) => getAnalysisCache(...args),
}));

import { ProjectDetailDrawer, projectAnalysisCachePresentation } from "./ProjectDetailDrawer";

describe("ProjectDetailDrawer analysis cache state", () => {
  beforeEach(() => getAnalysisCache.mockReset());

  it("区分 ready/stale/quality_incomplete 而不只看 entry", () => {
    expect(projectAnalysisCachePresentation("ready")).toMatchObject({ label: "已缓存", displayEntry: true });
    expect(projectAnalysisCachePresentation("stale")).toMatchObject({ label: "缓存已过期", displayEntry: true });
    expect(projectAnalysisCachePresentation("quality_incomplete")).toMatchObject({ label: "结果质量未通过", displayEntry: false });
    expect(projectAnalysisCachePresentation("legacy_unverified")).toMatchObject({ label: "历史结果待核验", displayEntry: false });
  });

  it("response.state=quality_incomplete 时不展示未合格 payload", async () => {
    getAnalysisCache.mockResolvedValue({
      target_type: "project",
      target_id: "7",
      state: "quality_incomplete",
      entry: {
        target_type: "project",
        target_id: "7",
        derive_method: "video_analysis_final_v1",
        status: "quality_incomplete",
        result: { summary: "DO NOT RENDER" },
      },
    });

    render(
      <ProjectDetailDrawer
        detail={{ project: { id: 7, project_name: "Quality project" } } as never}
        apiToken="token"
        viewMode="manager"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText("结果质量未通过")).toBeInTheDocument();
    expect(screen.getByText(/待重试或人工复核/)).toBeInTheDocument();
    expect(screen.queryByText(/DO NOT RENDER/)).not.toBeInTheDocument();
  });

  it("response.state=legacy_unverified 时不展示历史 payload", async () => {
    getAnalysisCache.mockResolvedValue({
      target_type: "video",
      target_id: "7",
      state: "legacy_unverified",
      terminal: true,
      entry: { target_type: "video", target_id: "7", derive_method: "video_analysis_final_v1", status: "ready", result: { summary: "LEGACY PAYLOAD" } },
    });
    render(
      <ProjectDetailDrawer
        detail={{ project: { id: 7, project_name: "Legacy project" } } as never}
        apiToken="token"
        viewMode="manager"
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText("历史结果待核验")).toBeInTheDocument();
    expect(screen.queryByText(/LEGACY PAYLOAD/)).not.toBeInTheDocument();
  });
});
