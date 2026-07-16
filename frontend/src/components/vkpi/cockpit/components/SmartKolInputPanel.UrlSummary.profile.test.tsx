import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VkpiKolUrlDeepCrawlResponse } from "../../../../domains/kol";

const api = vi.hoisted(() => ({
  deepCrawlKolUrl: vi.fn(),
  enqueueAllKolVideos: vi.fn(),
  getKolPoolAccountDossier: vi.fn(),
  getKolPoolDetailBundle: vi.fn(),
  getKolPoolItem: vi.fn(),
  getKolRecommendationCard: vi.fn(),
  getKolVideoAnalysisCache: vi.fn(),
  listKolPoolVideoComments: vi.fn(),
  translateBio: vi.fn(),
}));

vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  deepCrawlKolUrl: (...args: unknown[]) => api.deepCrawlKolUrl(...args),
  enqueueAllKolVideos: (...args: unknown[]) => api.enqueueAllKolVideos(...args),
  getKolPoolAccountDossier: (...args: unknown[]) => api.getKolPoolAccountDossier(...args),
  getKolPoolDetailBundle: (...args: unknown[]) => api.getKolPoolDetailBundle(...args),
  getKolPoolItem: (...args: unknown[]) => api.getKolPoolItem(...args),
  getKolRecommendationCard: (...args: unknown[]) => api.getKolRecommendationCard(...args),
  getKolVideoAnalysisCache: (...args: unknown[]) => api.getKolVideoAnalysisCache(...args),
  listKolPoolVideoComments: (...args: unknown[]) => api.listKolPoolVideoComments(...args),
  translateBio: (...args: unknown[]) => api.translateBio(...args),
}));

import { UrlSummary } from "./SmartKolInputPanel.UrlSummary";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function profileResult(id: number, handle: string): VkpiKolUrlDeepCrawlResponse {
  const url = `https://www.youtube.com/@${handle}`;
  return {
    execute: true,
    url: { input: url, normalized: url },
    url_type: "profile",
    platform: "youtube",
    handle,
    in_pool: true,
    matched_kol_pool_id: id,
    profile_flow: { status: "ready", operation: "reuse_recent_profile", kol_pool_id: id },
  } as VkpiKolUrlDeepCrawlResponse;
}

function renderSummary(result: VkpiKolUrlDeepCrawlResponse) {
  return render(
    <UrlSummary
      result={result}
      apiToken="token"
      canExecute
      isExecuting={false}
      onExecute={() => undefined}
      onOpenProfile={() => undefined}
    />,
  );
}

describe("SmartKolInputPanel URL profile hydration and refresh", () => {
  beforeEach(() => {
    api.deepCrawlKolUrl.mockReset();
    api.enqueueAllKolVideos.mockReset();
    api.getKolPoolAccountDossier.mockReset().mockResolvedValue({
      status: "ready",
      coverage: { video_evidence_count: 0, analyzed_final_v1_count: 0, qa_count: 0, deep_result_count: 0 },
      videos: [],
      gaps: ["video_evidence_missing"],
    });
    api.getKolPoolDetailBundle.mockReset().mockResolvedValue({
      status: "ready",
      item: { video_evidence: [] },
      video_analysis: { items: [], summary: { evidence_count: 0, ready_count: 0, pending_count: 0, qa_ready_count: 0 } },
      llm_deep_analysis: { status: "missing", count: 0 },
      diagnostics: { provider_calls: false, llm_calls: false, write_db: false },
    });
    api.getKolPoolItem.mockReset().mockResolvedValue({
      item: {
        id: 14060,
        platform: "instagram",
        handle: "decadentdepictions",
        display_name: "Decadent Depictions",
        avatar_url: "https://images.example/avatar.jpg",
        followers: 87271,
        posts_count: 64,
        bio: "Miami food and hospitality filmmaker",
        profile_url: "https://www.instagram.com/decadentdepictions/",
      },
    });
    api.getKolRecommendationCard.mockReset().mockResolvedValue({
      status: "ok",
      kol_pool_id: 14060,
      data_grade: "B",
      data_grade_score: 3,
      why_recommended: "12 条视频证据、深析 2 次。",
      signals: { videos: 12, analyses: 2, projects: 0 },
      note: "data_grade 是数据完整度/可信度档,不是推荐分。",
    });
    api.getKolVideoAnalysisCache.mockReset().mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "pending",
      entry: null,
    });
    api.listKolPoolVideoComments.mockReset().mockResolvedValue({ items: [], page: { total: 0 }, source: "pool_evidence" });
    api.translateBio.mockReset().mockResolvedValue({ translated: "" });
  });

  afterEach(() => cleanup());

  it("shows the stored account videos, analysis readiness, timestamps, and sampled-audience scope without another click", async () => {
    api.getKolPoolItem.mockResolvedValueOnce({
      item: {
        id: 13053,
        platform: "youtube",
        handle: "itijarve",
        display_name: "itijarve",
        followers: 29900,
        posts_count: 36,
        avg_views: null,
        avg_likes: null,
        avg_comments: null,
        engagement_rate: null,
        updated_at: "2026-07-14T15:30:00Z",
      },
      freshness: { last_refresh_at: "2026-07-14T15:30:00Z" },
    });
    api.getKolPoolDetailBundle.mockResolvedValueOnce({
      status: "ready",
      kol_pool_id: 13053,
      item: {
        id: 13053,
        pool_uid: "kol-youtube-itijarve",
        platform: "youtube",
        handle: "itijarve",
        avg_views: null,
        avg_likes: null,
        avg_comments: null,
        engagement_rate: null,
        updated_at: "2026-07-14T15:30:00Z",
        audience_estimated: {
          sample_size: 329,
          comments_scanned: 400,
          confidence: 0.78,
        },
      },
      llm_deep_analysis: { status: "ready", count: 2 },
      video_analysis: {
        summary: { evidence_count: 1, ready_count: 1, pending_count: 0, qa_ready_count: 1 },
        items: [{
          state: "ready",
          video: { evidence_id: 9001, title: "Viltrox field test" },
          final_entry: {
            status: "ready",
            result: {
              video_analysis_final_v1: {
                layer1_visual_content: {
                  content_summary: "Landscape lens field test with practical examples.",
                  scene_timeline: [{ timestamp: "00:12", what: "Sharpness comparison begins" }],
                },
                layer6_flags_and_scores: {
                  scores: { content_quality_score: 92, marketing_value_score: 88 },
                },
              },
            },
          },
        }],
      },
      diagnostics: { provider_calls: false, llm_calls: false, write_db: false },
    });
    api.getKolPoolAccountDossier.mockResolvedValueOnce({
      status: "ready",
      profile: { handle: "itijarve", followers: 29900, avg_views: null, engagement_rate: null },
      coverage: {
        video_evidence_count: 12,
        analyzed_final_v1_count: 1,
        qa_count: 1,
        deep_result_count: 2,
        content_quality_score_avg: 92,
        marketing_value_score_avg: 88,
      },
      judgment: { one_line_verdict: "内容质量均分 92 · 投放价值均分 88 · 已深析 1 条" },
      videos: [
        {
          evidence_id: 9001,
          channel_name: "Iti Järve",
          title: "Viltrox field test",
          content_url: "https://www.youtube.com/watch?v=abcdefghijk",
          thumbnail_url: "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg",
          view_count: 100000,
          like_count: 5000,
          comment_count: 200,
          publish_date: "2026-06-01T00:00:00Z",
          analysis: { has_final_v1: true, has_qa: true },
          has_final_v1_cache: true,
          has_keyframe_qa_cache: true,
        },
        {
          evidence_id: 9002,
          channel_name: "Iti Järve",
          title: "Camera travel setup",
          content_url: "https://www.youtube.com/watch?v=lmnopqrstuv",
          view_count: 40000,
          like_count: 2000,
          comment_count: 80,
        },
        ...Array.from({ length: 9 }, (_, index) => ({
          evidence_id: 9100 + index,
          channel_name: "Iti Järve",
          title: `Archive video ${index + 1}`,
          content_url: `https://www.youtube.com/watch?v=archive${index + 1}`,
          view_count: 7000,
          like_count: 300,
          comment_count: 20,
        })),
        {
          evidence_id: 9200,
          channel_name: "Iti Järve",
          title: "Archive video 10",
          content_url: "https://www.youtube.com/watch?v=archive-last",
          view_count: 12435,
          like_count: 704,
          comment_count: 63,
        },
      ],
      diagnostics: { provider_calls: false, llm_calls: false, write_db: false },
    });

    renderSummary(profileResult(13053, "ItiJarve"));

    await waitFor(() => expect(screen.getByTestId("account-url-inline-overview")).toBeTruthy());
    await waitFor(() => expect(screen.getAllByText("Iti Järve").length).toBeGreaterThan(0));
    expect(screen.getByText("已入库样本 12 条 · 当前展示 6 条")).toBeTruthy();
    expect(screen.getByText("KPI 口径：已入库 12 条视频样本聚合 · 非平台全量实时值")).toBeTruthy();
    expect(screen.getByText("215K")).toBeTruthy();
    expect(screen.getByText("18K")).toBeTruthy();
    expect(screen.getByText("867")).toBeTruthy();
    expect(screen.getByText("44")).toBeTruthy();
    expect(screen.getByText("5.07%")).toBeTruthy();
    expect(screen.getAllByText("Viltrox field test").length).toBeGreaterThan(0);
    expect(screen.getByText("100K 播放")).toBeTruthy();
    expect(screen.getByText("内容 92")).toBeTruthy();
    expect(screen.getByText("投放 88")).toBeTruthy();
    expect(screen.getByText("00:12")).toBeTruthy();
    expect(screen.getByText("Sharpness comparison begins")).toBeTruthy();
    expect(screen.getByText("评论者样本估算（非全体粉丝）")).toBeTruthy();
    expect(screen.getByText("样本 329")).toBeTruthy();
    expect(screen.getByText("扫描评论 400")).toBeTruthy();
    expect(screen.getByText("置信 78%")).toBeTruthy();
    expect(api.getKolPoolDetailBundle).toHaveBeenCalledWith("token", 13053, { videoLimit: 10, llmLimit: 6 });
    expect(api.getKolPoolAccountDossier).toHaveBeenCalledWith("token", 13053);
    expect(api.deepCrawlKolUrl).not.toHaveBeenCalled();
    expect(api.enqueueAllKolVideos).not.toHaveBeenCalled();
  });

  it("refetches the read-only account bundle when the same KOL pipeline status advances", async () => {
    const withStatus = (status: string, representativeStatus: string, queued: number, ready: number) => {
      const result = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
        profile_flow: Record<string, unknown>;
      };
      result.profile_flow = {
        ...result.profile_flow,
        status,
        job_id: 771,
        representative_video_analysis: { status: representativeStatus, queued, ready },
      };
      return result;
    };

    const view = renderSummary(withStatus("queued", "queued", 2, 0));
    await waitFor(() => expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getKolPoolAccountDossier).toHaveBeenCalledTimes(1));

    view.rerender(
      <UrlSummary
        result={withStatus("running", "running", 2, 1)}
        apiToken="token"
        canExecute
        isExecuting={false}
        onExecute={() => undefined}
      />,
    );
    await waitFor(() => expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getKolPoolAccountDossier).toHaveBeenCalledTimes(2));

    view.rerender(
      <UrlSummary
        result={withStatus("ready", "ready", 2, 2)}
        apiToken="token"
        canExecute
        isExecuting={false}
        onExecute={() => undefined}
      />,
    );
    await waitFor(() => expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(api.getKolPoolAccountDossier).toHaveBeenCalledTimes(3));
    expect(screen.getByTestId("profile-crawl-status")).toHaveTextContent("资料已抓取并入库");
  });

  it("does not let an older same-KOL account read overwrite a newer progress refresh", async () => {
    const oldDetail = deferred<any>();
    const oldDossier = deferred<any>();
    api.getKolPoolDetailBundle
      .mockImplementationOnce(() => oldDetail.promise)
      .mockResolvedValue({
        status: "ready",
        item: { id: 13053, handle: "itijarve" },
        video_analysis: { items: [], summary: { evidence_count: 0, ready_count: 0, pending_count: 0, qa_ready_count: 0 } },
        llm_deep_analysis: { status: "missing", count: 0 },
      });
    api.getKolPoolAccountDossier
      .mockImplementationOnce(() => oldDossier.promise)
      .mockResolvedValue({
        status: "ready",
        profile: { handle: "itijarve" },
        coverage: { video_evidence_count: 0, analyzed_final_v1_count: 0, qa_count: 0, deep_result_count: 0 },
        videos: [],
        judgment: { one_line_verdict: "Newest dossier verdict" },
      });

    const queued = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
      profile_flow: Record<string, unknown>;
    };
    queued.profile_flow = {
      ...queued.profile_flow,
      status: "queued",
      representative_video_analysis: { status: "queued", queued: 2, ready: 0 },
    };
    const view = renderSummary(queued);
    await waitFor(() => expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(1));

    const running = {
      ...queued,
      profile_flow: {
        ...queued.profile_flow,
        status: "running",
        representative_video_analysis: { status: "running", queued: 2, ready: 1 },
      },
    } as VkpiKolUrlDeepCrawlResponse;
    view.rerender(
      <UrlSummary result={running} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    await waitFor(() => expect(screen.getAllByText("Newest dossier verdict").length).toBeGreaterThan(0));

    await act(async () => {
      oldDetail.resolve({
        status: "ready",
        item: { id: 13053, handle: "itijarve" },
        video_analysis: { items: [], summary: { evidence_count: 0, ready_count: 0, pending_count: 0, qa_ready_count: 0 } },
        llm_deep_analysis: { status: "missing", count: 0 },
      });
      oldDossier.resolve({
        status: "ready",
        profile: { handle: "itijarve" },
        coverage: { video_evidence_count: 0, analyzed_final_v1_count: 0, qa_count: 0, deep_result_count: 0 },
        videos: [],
        judgment: { one_line_verdict: "Stale dossier verdict" },
      });
      await Promise.all([oldDetail.promise, oldDossier.promise]);
    });

    expect(screen.getAllByText("Newest dossier verdict").length).toBeGreaterThan(0);
    expect(screen.queryByText("Stale dossier verdict")).toBeNull();
  });

  it("keeps one fixed 30-minute account polling deadline across same-operation progress rerenders", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T12:00:00Z"));
    const withStatus = (status: string, ready: number) => {
      const result = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
        profile_flow: Record<string, unknown>;
        search_session: Record<string, unknown>;
      };
      result.search_session = { id: 9001, item_status: status };
      result.profile_flow = {
        ...result.profile_flow,
        status,
        job_id: 771,
        representative_video_analysis: { status, queued: 2, ready },
      };
      return result;
    };

    const view = renderSummary(withStatus("queued", 0));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(1);

    vi.setSystemTime(new Date("2026-07-15T12:29:59Z"));
    view.rerender(
      <UrlSummary
        result={withStatus("running", 1)}
        apiToken="token"
        canExecute
        isExecuting={false}
        onExecute={() => undefined}
      />,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(2);

    vi.setSystemTime(new Date("2026-07-15T12:30:01Z"));
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    const callsAtDeadline = api.getKolPoolDetailBundle.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(60 * 60_000);
      await Promise.resolve();
    });
    expect(api.getKolPoolDetailBundle).toHaveBeenCalledTimes(callsAtDeadline);
    vi.useRealTimers();
  });

});
