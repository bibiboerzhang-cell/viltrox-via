import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AIIntelligenceCard } from "./AIIntelligenceCard";
import { SignalsAlertsCard } from "./SignalsAlertsCard";

const baseInsight = {
  freshnessStatus: "fresh",
  updatedLabel: "新鲜 · 2 小时前",
  todayDecision: {
    text: "优先复核外部样例",
    reason: "来源可回跳",
    primaryAction: "查看来源",
    secondaryAction: "稍后处理",
  },
  strengthen: [{ text: "加强真实样例" }],
  weaken: [],
  todayContent: ["发布前人工复核"],
  recommendedVideos: [],
  sources: [],
};

function spanStyle(span: number) {
  return { "--vkpi-module-span": span } as React.CSSProperties;
}

describe("external evidence dashboard cards", () => {
  it("将 4/6/8/12 列映射为 compact/standard/standard/focus", () => {
    const spans = [4, 6, 8, 12];
    const { container } = render(
      <>
        {spans.map((span) => (
          <section key={span} className="vkpi-board-module" style={spanStyle(span)}>
            <AIIntelligenceCard insight={baseInsight} onApprove={vi.fn()} />
          </section>
        ))}
      </>,
    );

    const cards = Array.from(container.querySelectorAll(".ai-evidence-card"));
    expect(cards.map((card) => card.getAttribute("data-layout-variant"))).toEqual([
      "compact",
      "standard",
      "standard",
      "focus",
    ]);
    expect(cards.map((card) => card.getAttribute("data-column-span"))).toEqual(["4", "6", "8", "12"]);
  });

  it("六列响应式棋盘会把原 12 列 focus 卡降为 standard", () => {
    const { container } = render(
      <div className="vkpi-editable-board" style={{ gridTemplateColumns: "repeat(6, 100px)" }}>
        <section className="vkpi-board-module" style={spanStyle(12)}>
          <AIIntelligenceCard insight={baseInsight} onApprove={vi.fn()} />
        </section>
      </div>,
    );

    expect(container.querySelector(".ai-evidence-card")).toHaveAttribute("data-layout-variant", "standard");
  });

  it("AI 卡明确展示外部样例元数据与无市场来源状态", () => {
    const insight = {
      ...baseInsight,
      recommendedVideos: [{
        evidence_id: 42,
        platform: "youtube",
        title: "Night portrait short",
        creator_name: "Lens Author",
        content_url: "https://youtube.com/shorts/demo42",
        thumbnail_url: "https://img.example/demo42.jpg",
        published_at: "2026-07-09",
        freshness_label: "1 天前",
        content_origin: "external",
        source_refs: [{ table: "vkpi_kol_video_evidence", id: 42 }],
      }],
    };
    render(<AIIntelligenceCard insight={insight} onApprove={vi.fn()} />);

    expect(screen.getAllByText("外部市场样例").length).toBeGreaterThan(0);
    expect(screen.getByText("YouTube")).toBeTruthy();
    expect(screen.getByText("Shorts")).toBeTruthy();
    expect(screen.getByText("Lens Author")).toBeTruthy();
    expect(screen.getByText("2026-07-09")).toBeTruthy();
    expect(screen.getByText("vkpi_kol_video_evidence:42")).toBeTruthy();
    expect(screen.getByRole("link", { name: /打开原帖/ })).toHaveAttribute("href", "https://youtube.com/shorts/demo42");
    expect(screen.getByText("未保留市场来源")).toBeTruthy();
  });

  it("AI 卡优先使用已缓存缩略图", () => {
    const insight = {
      ...baseInsight,
      recommendedVideos: [{
        platform: "instagram",
        content_url: "https://www.instagram.com/reel/demo/",
        content_origin: "external",
        cached_thumbnail_url: "/api/vkpi-media/image-cache/cached-demo",
        thumbnail_url: "https://scontent-ord5-3.cdninstagram.com/raw.jpg",
      }],
    };
    const { container } = render(<AIIntelligenceCard insight={insight} onApprove={vi.fn()} />);

    expect(container.querySelector("img")).toHaveAttribute("src", "/api/vkpi-media/image-cache/cached-demo");
  });

  it("AI 卡不直出 Instagram CDN，代理失败后仅显示诚实占位", () => {
    const rawThumbnail = "https://scontent-ord5-3.cdninstagram.com/raw.jpg";
    const insight = {
      ...baseInsight,
      recommendedVideos: [{
        platform: "instagram",
        content_url: "https://www.instagram.com/reel/demo/",
        content_origin: "external",
        thumbnail_url: rawThumbnail,
      }],
    };
    const { container } = render(<AIIntelligenceCard insight={insight} onApprove={vi.fn()} />);

    const image = container.querySelector("img");
    expect(image).toHaveAttribute(
      "src",
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(rawThumbnail)}`,
    );
    expect(image).not.toHaveAttribute("src", rawThumbnail);

    fireEvent.error(image as HTMLImageElement);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("img", { name: "缩略图加载失败，未使用原始平台图片" })).toBeTruthy();
    expect(screen.getByText("缩略图不可用")).toBeTruthy();
  });

  it("AI Today 可触发真实重新生成，降级时明确保留旧快照", () => {
    const onRegenerate = vi.fn();
    const { container } = render(<AIIntelligenceCard
      insight={baseInsight}
      onApprove={vi.fn()}
      onRegenerate={onRegenerate}
      regenerationState={{
        phase: "degraded",
        message: "新结果未通过就绪门禁；继续显示上一份快照。",
      }}
    />);

    expect(screen.getByText("优先复核外部样例")).toBeInTheDocument();
    expect(container.querySelector('[data-ai-regeneration-phase="degraded"]'))
      .toHaveTextContent("继续显示上一份快照");
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("AI Today 持久显示最新失败尝试的时间、状态与原因,厂商名与机器码只进溯源 title", () => {
    const { container } = render(<AIIntelligenceCard
      insight={{
        ...baseInsight,
        latestAttempt: {
          attemptedLabel: "2 分钟前",
          status: "invalid",
          provider: "anthropic",
          reason: "invalid_result_contract",
          generationStatus: "all_providers_failed",
        },
      }}
      onApprove={vi.fn()}
    />);

    const attempt = container.querySelector('[data-ai-latest-attempt-status="invalid"]');
    expect(attempt).toHaveTextContent("最近生成尝试 · 2 分钟前 · 合同未通过");
    expect(attempt).toHaveTextContent("结果合同未通过");
    expect(attempt).toHaveTextContent("所有可用通道均未成功");
    // 红线2:厂商名与原始机器码不上卡面,但信息不删——完整原始串下沉到 title。
    expect(attempt?.textContent || "").not.toMatch(/anthropic|invalid_result_contract|all_providers_failed/);
    expect(attempt).toHaveAttribute(
      "title",
      "anthropic · invalid_result_contract · all_providers_failed",
    );
  });

  it("AI Today 展示 Claude 基于热点证据产出的产品、内容与视频建议", () => {
    render(<AIIntelligenceCard
      insight={{
        ...baseInsight,
        productRecommendations: ["EVO · 适合轻量旅行创作者"],
        contentRecommendations: ["YouTube · 发布镜头对比与拍摄参数"],
        videoRecommendations: ["用已回溯外部样例说明弱光构图"],
      }}
      onApprove={vi.fn()}
    />);

    expect(screen.getByText("产品推荐")).toBeInTheDocument();
    expect(screen.getByText("EVO · 适合轻量旅行创作者")).toBeInTheDocument();
    expect(screen.getByText("内容打法")).toBeInTheDocument();
    expect(screen.getByText("YouTube · 发布镜头对比与拍摄参数")).toBeInTheDocument();
    expect(screen.getByText("视频推荐理由")).toBeInTheDocument();
    expect(screen.getByText("用已回溯外部样例说明弱光构图")).toBeInTheDocument();
  });

  it("Signals 卡覆盖四个平台并诚实显示缺失新鲜度和来源", () => {
    const platforms = [
      { id: "yt", platform: "youtube", url: "https://youtube.com/shorts/yt", format: "Shorts", author: "YT Author" },
      { id: "tt", platform: "tiktok", url: "https://tiktok.com/@a/video/2", format: "短视频", author: "TT Author" },
      { id: "ig", platform: "instagram", url: "https://instagram.com/reel/ig", format: "Reel", author: "IG Author" },
      { id: "fb", platform: "facebook", url: "", format: "格式未记录", author: "FB Author" },
    ];
    const alerts = platforms.map((item) => ({
      id: item.id,
      severity: "medium",
      title: `${item.platform} market post`,
      desc: "外部内容样例摘要",
      time: "时间未记录",
      totalMentions: 1,
      trendPct: "证据可查",
      sources: [{
        name: `${item.platform} source`,
        platform: item.platform,
        url: item.url,
        author: item.author,
        published_at: "2026-07-09",
        content_origin: "external",
      }],
    }));

    const { container } = render(
      <section className="vkpi-board-module" style={spanStyle(8)}>
        <SignalsAlertsCard alerts={alerts} onAlertClick={vi.fn()} onViewAll={vi.fn()} />
      </section>,
    );

    expect(container.querySelector(".signals-evidence-card")).toHaveAttribute("data-layout-variant", "standard");
    expect(screen.getByText("更新时间未记录")).toBeTruthy();
    expect(screen.getAllByText("外部市场样例")).toHaveLength(4);
    expect(screen.getByText(/平台 YouTube/)).toBeTruthy();
    expect(screen.getByText(/格式 Shorts/)).toBeTruthy();
    expect(screen.getByText(/平台 TikTok/)).toBeTruthy();
    expect(screen.getByText(/平台 Instagram/)).toBeTruthy();
    expect(screen.getByText(/平台 Facebook/)).toBeTruthy();
    expect(screen.getAllByText(/无原始链接/).length).toBeGreaterThan(0);
    expect(screen.getByText(/3\/4 条信号可回源/)).toBeTruthy();
  });
});
