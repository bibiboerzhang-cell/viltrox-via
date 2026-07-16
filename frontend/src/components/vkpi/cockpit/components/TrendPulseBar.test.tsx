import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const apiFetch = vi.fn();

vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { TrendPulseBar } from "./TrendPulseBar";

beforeEach(() => {
  apiFetch.mockReset();
});

describe("TrendPulseBar 取数状态与证据口径", () => {
  it("loading:明确显示聚合中,不伪装成空态", () => {
    apiFetch.mockReturnValue(new Promise(() => undefined));
    render(<TrendPulseBar apiToken="tok" />);

    expect(screen.getByLabelText("近期市场热词")).toHaveAttribute("data-state", "loading");
    expect(screen.getByText("聚合中")).toBeInTheDocument();
    expect(screen.getByText("正在聚合真实行业帖…")).toBeInTheDocument();
  });

  it("ready:兼容当前 API shape,显示平台/窗口/标签证据命中", async () => {
    apiFetch.mockResolvedValue({
      generated_at: "2026-07-16T12:00:00Z",
      trends: [
        { hashtag: "cinematic", platform: "youtube", post_count: 3, engagement: 180 },
        { hashtag: "portrait", platform: "instagram", post_count: 2, engagement: 70 },
      ],
      summary: { total_hashtags: 7, window_days: 14, top: "cinematic" },
    });

    render(<TrendPulseBar apiToken="tok" />);

    expect(await screen.findByText("7 个热词")).toBeInTheDocument();
    expect(screen.getByLabelText("近期市场热词")).toHaveAttribute("data-state", "ready");
    expect(screen.getByLabelText("热词取数口径")).toHaveTextContent("行业帖 · 2 平台 · 近 14 天 · 标签命中 5 次");
    expect(screen.getByText("#cinematic")).toBeInTheDocument();
    expect(screen.getByText("youtube")).toBeInTheDocument();
    expect(screen.getByText("instagram")).toBeInTheDocument();
  });

  it("ready:兼容新增的精确证据数和来源字段", async () => {
    apiFetch.mockResolvedValue({
      trends: [{ hashtag: "filmmaking", platform: "youtube", post_count: 4 }],
      summary: {
        total_hashtags: 1,
        window_days: 7,
        source_label: "竞品官方帖",
        evidence_count: 38,
      },
    });

    render(<TrendPulseBar apiToken="tok" />);

    expect(await screen.findByText("1 个热词")).toBeInTheDocument();
    expect(screen.getByLabelText("热词取数口径")).toHaveTextContent("竞品官方帖 · 1 平台 · 近 7 天 · 证据 38 条");
  });

  it("empty:显示真实 0 证据和窗口,不留大空白", async () => {
    apiFetch.mockResolvedValue({
      trends: [],
      summary: { total_hashtags: 0, window_days: 14, evidence_count: 0 },
    });

    render(<TrendPulseBar apiToken="tok" />);

    expect(await screen.findByText("暂无热词")).toBeInTheDocument();
    expect(screen.getByLabelText("近期市场热词")).toHaveAttribute("data-state", "empty");
    expect(screen.getByLabelText("热词取数口径")).toHaveTextContent("行业帖 · 近 14 天 · 证据 0 条");
    expect(screen.getByText("真实行业帖入库后自动出现")).toBeInTheDocument();
  });

  it("error:请求失败显式告警,不冒充暂无热词", async () => {
    apiFetch.mockRejectedValue(new Error("HTTP 500"));

    render(<TrendPulseBar apiToken="tok" />);

    expect(await screen.findByText("热词源暂不可用")).toBeInTheDocument();
    expect(screen.getByLabelText("近期市场热词")).toHaveAttribute("data-state", "error");
    expect(screen.getByText("HTTP 500")).toBeInTheDocument();
    expect(screen.queryByText("暂无热词")).not.toBeInTheDocument();
  });

  it("fulfilled error shape 也进入 error 态", async () => {
    apiFetch.mockResolvedValue({ status: "error", reason: "industry_posts_unavailable" });

    render(<TrendPulseBar apiToken="tok" />);

    expect(await screen.findByText("industry_posts_unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("近期市场热词")).toHaveAttribute("data-state", "error");
  });

  it("无 token 时不渲染也不发请求", async () => {
    const { container } = render(<TrendPulseBar apiToken="" />);
    expect(container.textContent).toBe("");
    await waitFor(() => expect(apiFetch).not.toHaveBeenCalled());
  });
});
