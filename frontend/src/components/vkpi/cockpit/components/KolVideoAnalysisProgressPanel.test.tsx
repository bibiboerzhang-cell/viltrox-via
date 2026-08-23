import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getKolVideoAnalysisProgress: vi.fn(),
}));

vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolVideoAnalysisProgress: apiMocks.getKolVideoAnalysisProgress,
}));

import { KolVideoAnalysisProgressPanel } from "./KolVideoAnalysisProgressPanel";

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("KolVideoAnalysisProgressPanel · 账号级深析进度(F3/F7)", () => {
  beforeEach(() => {
    apiMocks.getKolVideoAnalysisProgress.mockReset();
  });
  afterEach(() => cleanup());

  it("旧后端 404 → 整块不渲染(不编进度)", async () => {
    apiMocks.getKolVideoAnalysisProgress.mockRejectedValue(Object.assign(new Error("404"), { status: 404 }));
    const { container } = render(<KolVideoAnalysisProgressPanel apiToken="tok" kolPoolId={42} />);
    await flush();
    expect(container.querySelector("[data-vkpi-video-progress]")).toBeNull();
  });

  it("进行中:完成/进行中计数 + eta_seconds 新口径;失败项带人话原因与动作", async () => {
    apiMocks.getKolVideoAnalysisProgress.mockResolvedValue({
      kol_pool_id: 42,
      state: "running",
      completed: 3,
      in_progress: 2,
      failed: 1,
      percent: 50,
      scope: { scope_total: 6 },
      eta_seconds: 240,
      items: [
        { evidence_id: 1, state: "ready" },
        { evidence_id: 2, state: "failed", title: "Lens review", failure_category: "authorization", failure_reason_human: "当前账号无权发起" },
      ],
    });
    const onReissue = vi.fn();
    render(<KolVideoAnalysisProgressPanel apiToken="tok" kolPoolId={42} onReissue={onReissue} />);
    await flush();
    const root = document.querySelector("[data-vkpi-video-progress]");
    expect(root?.getAttribute("data-vkpi-video-progress")).toBe("running");
    expect(root?.textContent).toContain("完成 3/6");
    expect(root?.textContent).toContain("未完成 1");
    expect(screen.getByText(/预计剩余/).textContent).toContain("约 4 分钟");
    expect(screen.getByText("当前账号无权发起")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从 MY KOL 重新发起" }));
    expect(onReissue).toHaveBeenCalledTimes(1);
  });

  it("ETA 缺失不显示;旧 estimated_remaining_seconds 不当 ETA", async () => {
    apiMocks.getKolVideoAnalysisProgress.mockResolvedValue({
      kol_pool_id: 42,
      state: "running",
      completed: 0,
      in_progress: 1,
      failed: 0,
      scope: { scope_total: 1 },
      eta: { estimated_remaining_seconds: 300 },
      items: [],
    });
    render(<KolVideoAnalysisProgressPanel apiToken="tok" kolPoolId={42} />);
    await flush();
    expect(screen.queryByText(/预计剩余/)).toBeNull();
  });
});
