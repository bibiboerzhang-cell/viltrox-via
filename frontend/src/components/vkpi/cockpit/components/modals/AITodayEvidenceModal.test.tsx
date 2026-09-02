import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AITodayEvidenceModal } from "./AITodayEvidenceModal";

describe("AITodayEvidenceModal", () => {
  it("展示真实视频 ID、原链接、来源和过期警告", () => {
    render(
      <AITodayEvidenceModal
        insight={{
          isStale: true,
          freshnessLabel: "已过期 · 8 天前",
          snapshotDate: "2026-07-01",
          generatedAt: "2026-07-01T00:00:00Z",
          todayDecision: { text: "先复核再投放" },
          recommendedVideos: [{
            evidence_id: 77,
            kol_pool_id: 12,
            analysis_cache_id: 9,
            platform: "youtube",
            content_origin: "external",
            platform_video_id: "abc",
            title: "Cinematic lens test",
            creator_name: "Creator",
            content_url: "https://youtube.com/watch?v=abc",
            thumbnail_url: "https://img.example/thumb.jpg",
            published_at: "2026-07-01T12:00:00Z",
            view_count: 1000,
            fit_score: 88,
            source_refs: [{ table: "vkpi_kol_video_evidence", id: 77 }],
          }],
          sources: [{
            title: "Market source",
            url: "https://example.com/source",
            ledger_table: "vkpi_market_mentions",
            ledger_id: 5,
            relation_type: "brand_context",
            source_status: "expired",
          }],
        }}
        onClose={vi.fn()}
        onOpenKolPool={vi.fn()}
      />,
    );

    expect(screen.getByText("AI Today 证据")).toBeTruthy();
    expect(screen.getByText(/Evidence|vkpi_kol_video_evidence:77/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /原视频/ })).toEqual(expect.arrayContaining([
      expect.objectContaining({ href: "https://youtube.com/watch?v=abc" }),
    ]));
    expect(screen.getByRole("link", { name: /Market source/ })).toHaveAttribute("href", "https://example.com/source");
    expect(screen.getByText(/该决策快照已过期/)).toBeTruthy();
    expect(screen.getByText(/Google 引文/)).toBeTruthy();
    expect(screen.getAllByText("YouTube").length).toBeGreaterThan(0);
    expect(screen.getAllByText("视频").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Creator").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-07-01").length).toBeGreaterThan(0);
  });

  it("R2 视频失败时回退到缩略图并保留原视频入口", async () => {
    const { container } = render(
      <AITodayEvidenceModal
        insight={{
          todayDecision: { text: "验证缓存回退" },
          recommendedVideos: [{
            evidence_id: 88,
            platform: "youtube",
            content_origin: "external",
            title: "R2 fallback video",
            content_url: "https://youtube.com/watch?v=fallback",
            playback_url: "/api/vkpi-media/video-cache/demo",
            playback_source: "r2",
            thumbnail_url: "https://img.example/fallback.jpg",
            source_refs: [{ table: "vkpi_kol_video_evidence", id: 88 }],
          }],
          sources: [],
        }}
        onClose={vi.fn()}
        onOpenKolPool={vi.fn()}
      />,
    );

    expect(screen.getByText("R2 缓存")).toBeTruthy();
    const video = container.querySelector("video");
    expect(video).toBeTruthy();
    fireEvent.error(video as HTMLVideoElement);
    // 状态更新后的 DOM 断言一律等提交(慢 runner 竞态,同 ea20aa50)
    await waitFor(() => {
      expect(screen.getByRole("img", { name: "R2 fallback video" })).toHaveAttribute("src", "https://img.example/fallback.jpg");
    });
    expect(screen.getAllByRole("link", { name: /原视频/ }).every((link) => link.getAttribute("href") === "https://youtube.com/watch?v=fallback")).toBe(true);
    expect(screen.getByText(/该快照未保留原始市场来源/)).toBeTruthy();
  });

  it("明确区分四个平台的内容格式、作者、发布时间和来源缺失", () => {
    render(
      <AITodayEvidenceModal
        insight={{
          freshnessLabel: "新鲜 · 2 小时前",
          todayDecision: { text: "参考外部内容格式" },
          recommendedVideos: [
            {
              evidence_id: 1,
              platform: "youtube",
              content_origin: "external",
              title: "YT short",
              creator_name: "YT Author",
              content_url: "https://youtube.com/shorts/yt1",
              published_at: "2026-07-09",
              source_refs: [{ table: "vkpi_kol_video_evidence", id: 1 }],
            },
            {
              evidence_id: 2,
              platform: "tiktok",
              content_origin: "external",
              title: "TikTok clip",
              creator_name: "TikTok Author",
              content_url: "https://www.tiktok.com/@creator/video/2",
              published_at: "2026-07-08",
              source_refs: [{ table: "vkpi_kol_video_evidence", id: 2 }],
            },
            {
              evidence_id: 3,
              platform: "instagram",
              content_origin: "external",
              title: "IG reel",
              creator_name: "IG Author",
              content_url: "https://www.instagram.com/reel/ig3/",
              published_at: "2026-07-07",
              source_refs: [{ table: "vkpi_kol_video_evidence", id: 3 }],
            },
            {
              evidence_id: 4,
              platform: "facebook",
              content_origin: "external",
              title: "FB video",
              creator_name: "FB Author",
              content_url: "https://www.facebook.com/watch/videos/4",
              published_at: "2026-07-06",
              source_refs: [],
            },
          ],
          sources: [],
        }}
        onClose={vi.fn()}
        onOpenKolPool={vi.fn()}
      />,
    );

    expect(screen.getAllByText("YouTube").length).toBeGreaterThan(0);
    expect(screen.getAllByText("TikTok").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Instagram").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Facebook").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Shorts").length).toBeGreaterThan(0);
    expect(screen.getAllByText("短视频").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Reel").length).toBeGreaterThan(0);
    expect(screen.getAllByText("YT Author").length).toBeGreaterThan(0);
    expect(screen.getAllByText("证据表引用未记录").length).toBeGreaterThan(0);
  });
});
