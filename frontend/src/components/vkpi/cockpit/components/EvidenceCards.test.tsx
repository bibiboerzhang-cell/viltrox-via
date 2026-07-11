import React from "react";
import { render, screen } from "@testing-library/react";
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
