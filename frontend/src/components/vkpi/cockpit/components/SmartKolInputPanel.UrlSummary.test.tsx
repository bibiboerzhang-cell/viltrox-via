import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VkpiKolUrlDeepCrawlResponse } from "../../../../domains/kol";
import type { VkpiKolVideoAnalysisCacheResponse } from "../../../../services/vkpi/kolPool-api";

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

function videoResult(overrides: Record<string, unknown> = {}): VkpiKolUrlDeepCrawlResponse {
  return {
    execute: true,
    url: {
      input: "https://www.instagram.com/reel/DX8prCJOe6V/",
      normalized: "https://www.instagram.com/p/DX8prCJOe6V/",
    },
    url_type: "video",
    platform: "instagram",
    video_id: "DX8prCJOe6V",
    in_pool: true,
    matched_kol_pool_id: 14060,
    creator_identity: {
      handle: "decadentdepictions",
      display_name: "Decadent Depictions",
      platform: "instagram",
      profile_url: "https://www.instagram.com/decadentdepictions/",
    },
    video_metadata: {
      title: "Flavor in motion",
      description: "Cinematic food story",
      content_url: "https://www.instagram.com/p/DX8prCJOe6V/",
      thumbnail_url: "https://images.example/poster.jpg",
      view_count: 397706,
      like_count: 57925,
      comment_count: 427,
      duration_seconds: 59,
      publish_date: "2026-05-05T07:00:19.000Z",
    },
    video_flow: {
      status: "queued",
      job_status: "queued",
      evidence_id: 3951,
    },
    ...overrides,
  } as VkpiKolUrlDeepCrawlResponse;
}

function renderSummary(
  result: VkpiKolUrlDeepCrawlResponse,
  onOpenProfile = vi.fn(),
  onLocalEvaluation?: () => void,
) {
  return render(
    <UrlSummary
      result={result}
      apiToken="token"
      canExecute
      isExecuting={false}
      onExecute={() => undefined}
      onLocalEvaluation={onLocalEvaluation}
      onOpenProfile={onOpenProfile}
    />,
  );
}

describe("SmartKolInputPanel URL result mapping", () => {
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
    api.listKolPoolVideoComments.mockReset().mockResolvedValue({
      items: [],
      page: { total: 0, next_offset: 0 },
      source: "pool_evidence",
    });
    api.translateBio.mockReset().mockResolvedValue({ translated: "" });
  });

  afterEach(() => cleanup());

  it("keeps the analysis-cache contract aligned with truthful empty states", () => {
    const states: VkpiKolVideoAnalysisCacheResponse["state"][] = ["not_requested", "unknown"];
    expect(states).toEqual(["not_requested", "unknown"]);
  });

  it("shows the durable four-stage video URL resolver progress", () => {
    renderSummary(videoResult({
      matched_kol_pool_id: null,
      video_flow: {
        status: "queued",
        operation: "video_url_resolve_queue",
        evidence_id: null,
        resolution_progress: {
          status: "running",
          base_status: "pending",
          current_step: "identify_creator",
          steps: [
            { key: "resolve_video", label: "解析视频", status: "ready" },
            { key: "identify_creator", label: "识别作者", status: "running" },
            { key: "cache_media", label: "缓存媒体", status: "pending" },
            { key: "ai_analysis", label: "AI分析", status: "pending" },
          ],
        },
      },
    }));

    const progress = screen.getByTestId("video-url-resolution-progress");
    expect(within(progress).getByText("解析视频")).toBeTruthy();
    expect(within(progress).getByText("识别作者")).toBeTruthy();
    expect(within(progress).getByText("缓存媒体")).toBeTruthy();
    expect(within(progress).getByText("AI分析")).toBeTruthy();
    expect(within(progress).getAllByText("进行中")).toHaveLength(1);
  });

  it("shows a useful video and creator summary before the R2 file is playable", async () => {
    renderSummary(videoResult());

    expect(screen.getByText("Flavor in motion")).toBeTruthy();
    expect(screen.getByText("398K 播放")).toBeTruthy();
    expect(screen.getByText("58K 点赞")).toBeTruthy();
    expect(screen.getByText("427 评论")).toBeTruthy();
    expect(screen.getByText("0:59")).toBeTruthy();
    expect(screen.getByText(/2026.*05.*05/)).toBeTruthy();
    expect(screen.getByText("视频缓存未就绪，暂时不可播放")).toBeTruthy();
    expect(screen.getByRole("link", { name: /打开 Instagram 原帖/ })).toHaveAttribute("href", "https://www.instagram.com/p/DX8prCJOe6V/");
    expect(screen.getByRole("img", { name: /Flavor in motion · 视频封面/ })).toBeTruthy();
    expect(screen.queryByText("视频分析完成，已入库")).toBeNull();
    expect(screen.getByText("视频分析与推荐进度")).toBeTruthy();
    expect(screen.getByText("当前视频投放价值")).toBeTruthy();
    expect(within(screen.getByTestId("video-decision-overview")).getByText("未形成")).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("87K 粉")).toBeTruthy();
      expect(screen.getByText("64 帖")).toBeTruthy();
      expect(screen.getByText("Miami food and hospitality filmmaker")).toBeTruthy();
    });
    expect(screen.getByText("AI 深析与时间戳正在后台生成")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("平台评论指标 427")).toBeTruthy());
    expect(screen.getByText("已读取评论样本 0")).toBeTruthy();
    expect(screen.getByText(/平台显示 427 条评论，但本地尚无该视频的评论正文样本/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("B")).toBeTruthy());
    expect(screen.getByText("12 条视频证据、深析 2 次。")).toBeTruthy();
  });

  it("maps persisted comment evidence separately from the platform comment metric", async () => {
    api.listKolPoolVideoComments.mockResolvedValueOnce({
      items: [
        { id: 11, author_handle: "viewer_one", comment_text: "The lighting breakdown is excellent.", like_count: 18 },
        { id: 12, author_handle: "viewer_two", comment_text: "Which lens was used here?", like_count: 3 },
      ],
      page: { total: 2, next_offset: 0 },
      source: "kol_comments_bridge",
    });

    renderSummary(videoResult());

    const samples = await screen.findByTestId("video-comment-samples");
    expect(within(samples).getByText("平台评论指标 427")).toBeTruthy();
    expect(within(samples).getByText("已读取评论样本 2")).toBeTruthy();
    expect(within(samples).getByText("The lighting breakdown is excellent.")).toBeTruthy();
    expect(within(samples).getByText("Which lens was used here?")).toBeTruthy();
    expect(within(samples).getByText(/来源：账号评论桥接/)).toBeTruthy();
  });

  it("sends an origin referrer with the YouTube embed player", async () => {
    renderSummary(videoResult({
      platform: "youtube",
      video_id: "abcdefghijk",
      creator_identity: {
        handle: "camera-creator",
        display_name: "Camera Creator",
        platform: "youtube",
        profile_url: "https://www.youtube.com/@camera-creator",
      },
      video_metadata: {
        title: "YouTube camera review",
        content_url: "https://www.youtube.com/watch?v=abcdefghijk",
      },
    }));

    const player = screen.getByTitle("YouTube camera review");
    expect(player.tagName).toBe("IFRAME");
    expect(player).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    await waitFor(() => {
      expect(api.getKolPoolItem).toHaveBeenCalled();
      expect(api.getKolVideoAnalysisCache).toHaveBeenCalled();
    });
  });

  it("does not relabel a video post URL as the creator profile URL", () => {
    const source = "https://www.instagram.com/p/no-profile-fallback/";
    renderSummary(videoResult({
      url: { input: source, normalized: source },
      matched_kol_pool_id: 0,
      creator_identity: { handle: "creator-without-profile", platform: "instagram" },
      video_metadata: { title: "Post without creator profile", content_url: source },
      video_flow: { status: "queued", evidence_id: 0 },
    }));

    expect(screen.queryByText(source)).toBeNull();
    expect(screen.getByRole("link", { name: /打开 Instagram 原帖/ })).toHaveAttribute("href", source);
  });

  it("does not retry a failed proxied Instagram poster through the raw CDN", async () => {
    const rawPoster = "https://scontent-ord5-3.cdninstagram.com/v/t51.82787-15/poster.jpg";
    renderSummary(videoResult({
      video_metadata: {
        title: "Proxy-only poster",
        content_url: "https://www.instagram.com/p/DX8prCJOe6V/",
        thumbnail_url: rawPoster,
      },
    }));

    const poster = screen.getByRole("img", { name: "Proxy-only poster · 视频封面" });
    expect(poster.getAttribute("src")).toContain("/api/admin/vkpi/media/image-proxy?url=");
    expect(poster.getAttribute("src")).not.toBe(rawPoster);
    await waitFor(() => {
      expect(api.getKolPoolItem).toHaveBeenCalled();
      expect(api.getKolVideoAnalysisCache).toHaveBeenCalled();
    });

    fireEvent.error(poster);

    await waitFor(() => expect(screen.queryByRole("img", { name: "Proxy-only poster · 视频封面" })).toBeNull());
    expect(screen.getByText("封面暂不可用")).toBeTruthy();
    expect(document.querySelector(`img[src="${rawPoster}"]`)).toBeNull();
  });

  it("keeps the R2 player and renders ready analysis with timestamps", async () => {
    api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, _evidenceId: string, method: string) => {
      if (method.includes("keyframe")) {
        return { target_type: "video_evidence", target_id: "3951", state: "pending", entry: null };
      }
      return {
        target_type: "video_evidence",
        target_id: "3951",
        state: "ready",
        cached_video_url: "https://r2.example/DX8prCJOe6V.mp4",
        entry: {
          target_type: "video_evidence",
          target_id: "3951",
          derive_method: "video_analysis_final_v1",
          status: "ready",
          result: {
            video_analysis_final_v1: {
              layer1_visual_content: {
                content_summary: "A cinematic hospitality sequence.",
                scene_timeline: [{ timestamp: "00:03", what: "Food preparation close-up" }],
              },
              layer6_flags_and_scores: { scores: { content_quality_score: 88, marketing_value_score: 74 } },
            },
          },
        },
      };
    });

    renderSummary(videoResult());

    const player = await screen.findByTestId("cached-video-player");
    expect(player?.getAttribute("src")).toBe("https://r2.example/DX8prCJOe6V.mp4");
    expect(screen.getByText("缓存视频加载中")).toBeTruthy();
    expect(screen.queryByText("缓存视频可播放")).toBeNull();
    fireEvent.loadedMetadata(player);
    expect(screen.getByText("缓存视频可播放")).toBeTruthy();
    expect(screen.getByText("A cinematic hospitality sequence.")).toBeTruthy();
    expect(screen.getByText("00:03")).toBeTruthy();
    expect(screen.getByText("Food preparation close-up")).toBeTruthy();
    expect(screen.getByText("视频模型分 · outcome 未验证")).toBeTruthy();
    expect(screen.getByTestId("business-outcome-status")).toHaveTextContent("业务 outcome：未验证");
    expect(screen.getAllByText("74").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("video-analysis-evaluation-notice")).toBeNull();
  });

  it("does not call a cache URL playable until media validation and falls back to the source after failure", async () => {
    renderSummary(videoResult({
      video_flow: {
        status: "queued",
        evidence_id: 0,
        cached_video_url: "https://r2.example/unreadable.mp4",
      },
    }));

    const player = screen.getByTestId("cached-video-player");
    expect(screen.getByText("缓存视频加载中")).toBeTruthy();
    expect(screen.queryByText("缓存视频可播放")).toBeNull();

    fireEvent.error(player);

    expect(screen.queryByTestId("cached-video-player")).toBeNull();
    expect(screen.getByText("缓存视频加载失败")).toBeTruthy();
    expect(screen.getByText("缓存视频加载失败，请打开原帖查看")).toBeTruthy();
    expect(screen.getByRole("link", { name: /打开 Instagram 原帖/ })).toHaveAttribute(
      "href",
      "https://www.instagram.com/p/DX8prCJOe6V/",
    );
    await waitFor(() => expect(api.getKolPoolItem).toHaveBeenCalled());
  });

  it("keeps polling the independent QA stage after the main analysis is ready", async () => {
    vi.useFakeTimers();
    let qaCalls = 0;
    try {
      api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, _evidenceId: string, method: string) => {
        if (method.includes("keyframe")) {
          qaCalls += 1;
          if (qaCalls === 1) return { target_type: "video_evidence", target_id: "3951", state: "queued", entry: null };
          return {
            target_type: "video_evidence",
            target_id: "3951",
            state: "ready",
            entry: {
              target_type: "video_evidence",
              target_id: "3951",
              derive_method: "video_analysis_final_v1_keyframe_qa",
              status: "ready",
              result: { qa_pass: true, confidence: 0.9, summary: "Frame evidence verified" },
            },
          };
        }
        return {
          target_type: "video_evidence",
          target_id: "3951",
          state: "ready",
          entry: {
            target_type: "video_evidence",
            target_id: "3951",
            derive_method: "video_analysis_final_v1",
            status: "ready",
            result: { layer1_visual_content: { content_summary: "Main analysis ready first" } },
          },
        };
      });

      renderSummary(videoResult());
      await act(async () => { await Promise.resolve(); });
      expect(screen.getByText("Main analysis ready first")).toBeTruthy();

      await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
      expect(screen.getByText("关键帧 QA 通过")).toBeTruthy();
      expect(screen.getByText("Frame evidence verified")).toBeTruthy();
      expect(qaCalls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ["evaluation_only", { evaluation_only: true, claim_status: "validated" }],
    ["descriptive_only", { execution_class: "local_evaluation", production_authorized: false, claim_status: "descriptive_only" }],
  ])("labels a ready %s result as local descriptive evidence", async (_case, authorization) => {
    api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, _evidenceId: string, method: string) => {
      if (method.includes("keyframe")) {
        return { target_type: "video_evidence", target_id: "3951", state: "pending", entry: null };
      }
      return {
        target_type: "video_evidence",
        target_id: "3951",
        state: "ready",
        entry: {
          target_type: "video_evidence",
          target_id: "3951",
          derive_method: "video_analysis_final_v1",
          status: "ready",
          result: {
            ...authorization,
            video_analysis_final_v1: {
              layer1_visual_content: { content_summary: "Evaluation-only analysis" },
            },
          },
        },
      };
    });

    renderSummary(videoResult());

    await waitFor(() => expect(screen.getByTestId("video-analysis-evaluation-notice")).toBeTruthy());
    expect(screen.getByText("本地评估 · 描述性结论 · 非生产授权")).toBeTruthy();
    expect(screen.getByText("来源: 本地评估 final_v1（非生产）")).toBeTruthy();
    expect(screen.getByText("Evaluation-only analysis")).toBeTruthy();
  });

  it("does not warn on a production-authorized ready result", async () => {
    api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, _evidenceId: string, method: string) => {
      if (method.includes("keyframe")) {
        return { target_type: "video_evidence", target_id: "3951", state: "pending", entry: null };
      }
      return {
        target_type: "video_evidence",
        target_id: "3951",
        state: "ready",
        entry: {
          target_type: "video_evidence",
          target_id: "3951",
          derive_method: "video_analysis_final_v1",
          status: "ready",
          result: {
            evaluation_only: false,
            production_authorized: true,
            claim_status: "descriptive_only",
            model_readiness_status: "production_ready",
            layer1_visual_content: { content_summary: "Production-authorized analysis" },
          },
        },
      };
    });

    renderSummary(videoResult());

    await waitFor(() => expect(screen.getByText("Production-authorized analysis")).toBeTruthy());
    expect(screen.queryByTestId("video-analysis-evaluation-notice")).toBeNull();
  });

  it("stops polling and displays the real terminal analysis reason", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "blocked",
      entry: null,
      analysis_job: {
        state: "blocked",
        reason: "model_binding_blocked",
        reason_detail: "模型尚未通过 runtime probe 与 actual evaluation",
        provider: "google",
        stage: "dispatch",
      },
    });

    renderSummary(videoResult());

    await waitFor(() => expect(screen.getByText("AI 分析未完成")).toBeTruthy());
    expect(screen.getByText("模型尚未通过 runtime probe 与 actual evaluation")).toBeTruthy();
    expect(screen.queryByText("AI 深析与时间戳正在后台生成")).toBeNull();
    expect(screen.queryByTestId("video-analysis-evaluation-notice")).toBeNull();
    const mainCalls = api.getKolVideoAnalysisCache.mock.calls.filter((call) => call[2] === "video_analysis_final_v1");
    expect(mainCalls).toHaveLength(1);
  });

  it("humanizes a terminal machine code when the backend has no reason_detail", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "blocked",
      entry: null,
      analysis_job: {
        state: "blocked",
        reason: "budget_guard_blocked",
        provider: "google",
        stage: "dispatch",
      },
    });

    renderSummary(videoResult());

    expect(await screen.findByText("模型预算尚未授权，本轮不会调用外部模型。")).toBeTruthy();
    expect(screen.getByText(/诊断码 budget_guard_blocked/)).toBeTruthy();
    expect(screen.queryByText(/^budget_guard_blocked$/)).toBeNull();
  });

  it("shows a separate DEV-only local evaluation action for model readiness blocks", async () => {
    const onLocalEvaluation = vi.fn();
    renderSummary(videoResult({
      video_flow: {
        status: "blocked",
        job_status: "blocked",
        job_last_error: "budget_guard_blocked: model_binding_blocked readiness_not_production_ready",
        evidence_id: 3951,
      },
    }), vi.fn(), onLocalEvaluation);

    const action = screen.getByTestId("video-local-evaluation-action");
    expect(action).toHaveTextContent("本地评估此视频（非生产）");
    await waitFor(() => expect(api.getKolVideoAnalysisCache).toHaveBeenCalled());
    await waitFor(() => expect(api.getKolPoolItem).toHaveBeenCalled());
    fireEvent.click(action);
    expect(onLocalEvaluation).toHaveBeenCalledTimes(1);
  });

  it("stops polling when analysis was not requested and labels it truthfully", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "not_requested",
      entry: null,
      analysis_job: {
        state: "not_requested",
        reason: "analysis_not_requested",
        reason_detail: "当前只有视频基础数据，尚未提交深析任务",
      },
    });

    renderSummary(videoResult());

    await waitFor(() => expect(screen.getByText("AI 深析尚未入队")).toBeTruthy());
    expect(screen.getByText("当前只有视频基础数据，尚未提交深析任务")).toBeTruthy();
    expect(screen.queryByText("AI 深析与时间戳正在后台生成")).toBeNull();
    const mainCalls = api.getKolVideoAnalysisCache.mock.calls.filter((call) => call[2] === "video_analysis_final_v1");
    expect(mainCalls).toHaveLength(1);
  });

  it("shows video base data as complete when production AI is disabled", async () => {
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "video_evidence",
      target_id: "3951",
      state: "not_requested",
      entry: null,
      analysis_job: {
        state: "not_requested",
        reason: "ai_disabled",
        reason_detail: "外部模型尚未通过生产就绪闸门，本轮不会调用",
      },
    });

    renderSummary(videoResult({
      video_flow: {
        status: "ready",
        job_status: "not_requested",
        evidence_id: 3951,
        ai_analysis: {
          state: "not_requested",
          reason: "ai_disabled",
          gate_reason: "model_binding_blocked",
          provider_calls_allowed: false,
        },
      },
    }));

    expect(screen.getByText("视频基础数据已入库 · AI 深析未启用")).toBeTruthy();
    expect(await screen.findByText("AI 深析未启用")).toBeTruthy();
    expect(screen.getByText("外部模型尚未通过生产就绪闸门，本轮不会调用")).toBeTruthy();
    expect(screen.queryByText("视频分析完成，已入库")).toBeNull();
    expect(screen.queryByText("重试分析")).toBeNull();
  });

  it.each([
    ["queued", "资料抓取已排队"],
    ["running", "资料抓取进行中..."],
    ["partial", "资料部分完成，等待补齐"],
    ["ready", "资料已抓取并入库"],
  ])("maps profile %s without claiming storage early", async (status, expected) => {
    const result = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
      profile_flow: Record<string, unknown>;
    };
    result.profile_flow = { ...result.profile_flow, status };

    renderSummary(result);

    expect(screen.getByTestId("profile-crawl-status")).toHaveTextContent(expected);
    if (status !== "ready") expect(screen.queryByText("资料已抓取并入库")).toBeNull();
    expect(screen.queryByText("已自动抓资料入库")).toBeNull();
    await waitFor(() => expect(screen.queryByText("正在读取已入库视频与分析…")).toBeNull());
  });

  it("shows a retry action instead of a false success label when profile crawl failed", async () => {
    const result = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
      profile_flow: Record<string, unknown>;
    };
    result.profile_flow = { ...result.profile_flow, status: "profile_crawl_failed" };

    renderSummary(result);

    expect(screen.getByRole("button", { name: "重试抓资料" })).toBeTruthy();
    expect(screen.queryByTestId("profile-crawl-status")).toBeNull();
    expect(screen.queryByText(/已抓取.*入库/)).toBeNull();
    await waitFor(() => expect(screen.queryByText("正在读取已入库视频与分析…")).toBeNull());
  });

  it("labels an account URL as base-ready when representative-video AI is disabled", async () => {
    const result = profileResult(13053, "ItiJarve") as VkpiKolUrlDeepCrawlResponse & {
      profile_flow: Record<string, unknown>;
    };
    result.profile_flow = {
      ...result.profile_flow,
      representative_video_analysis: {
        status: "ai_disabled",
        queued: 0,
        ai_analysis: {
          state: "not_requested",
          reason: "ai_disabled",
          provider_calls_allowed: false,
        },
      },
    };

    renderSummary(result);

    expect(screen.getByText("资料已抓取并入库 · AI 深析未启用")).toBeTruthy();
    // AccountUrlInlineOverview is React.lazy loaded. In the full Vitest shard the
    // module can resolve one render later than it does in this file alone, so a
    // synchronous query races Suspense and also leaves its commit outside act().
    expect(await screen.findByTestId("account-decision-summary")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发现历史视频" })).toBeTruthy();
    expect(screen.queryByText(/账号深度分析进行中/)).toBeNull();
    await waitFor(() => expect(api.getKolPoolItem).toHaveBeenCalledWith("token", 13053, false));
    await waitFor(() => expect(api.getKolPoolAccountDossier).toHaveBeenCalledWith("token", 13053));
  });

  it("hydrates an account URL from the matched pool item without requiring raw-field expansion", async () => {
    const onOpen = vi.fn();
    api.getKolPoolItem.mockResolvedValueOnce({
      item: {
        id: 13053,
        platform: "youtube",
        handle: "itijarve",
        display_name: "Iti Jarve",
        avatar_url: "https://images.example/iti.jpg",
        followers: 87271,
        posts_count: 64,
        bio: "Camera creator and landscape filmmaker",
        profile_url: "https://www.youtube.com/@ItiJarve",
        email: "private@example.com",
        raw_platform_data: "private-raw-payload",
        token: "top-secret-token",
        viltrox_fit_score: 99,
      },
    });
    renderSummary({
      execute: true,
      url: { input: "https://www.youtube.com/@ItiJarve", normalized: "https://www.youtube.com/@ItiJarve" },
      url_type: "profile",
      platform: "youtube",
      handle: "itijarve",
      in_pool: true,
      matched_kol_pool_id: 13053,
      profile_flow: {
        status: "ready",
        operation: "reuse_recent_profile",
        kol_pool_id: 13053,
        profile_data: { followers: 0, posts_count: "", bio: null },
      },
    }, onOpen);

    await waitFor(() => expect(api.getKolPoolItem).toHaveBeenCalledWith("token", 13053, false));
    expect(screen.getByText("87K 粉")).toBeTruthy();
    expect(screen.getByText("64 帖")).toBeTruthy();
    expect(screen.getAllByText("Camera creator and landscape filmmaker").length).toBeGreaterThan(0);
    expect(screen.getByText(/原始字段 \d+/)).toBeTruthy();
    expect(screen.queryByText("private@example.com")).toBeNull();
    expect(screen.queryByText("private-raw-payload")).toBeNull();
    expect(screen.queryByText("top-secret-token")).toBeNull();
    // Fit is an explicit decision field and may be shown only inside the labelled recommendation block;
    // private/raw fields remain filtered from the profile card.
    expect(screen.getByText("账号 Fit（库内模型/规则）")).toBeTruthy();
    expect(screen.getByText("99")).toBeTruthy();
    fireEvent.click(screen.getByText("查看详情 →"));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });


  it("clears a polled R2 URL when switching between history results", async () => {
    api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, evidenceId: string, method: string) => {
      if (evidenceId === "102") return new Promise(() => undefined);
      return {
        target_type: "video_evidence",
        target_id: evidenceId,
        state: "pending",
        entry: null,
        cached_video_url: method === "video_analysis_final_v1" ? "https://r2.example/first.mp4" : null,
      };
    });
    const first = videoResult({
      video_id: "first",
      matched_kol_pool_id: 0,
      video_flow: { status: "queued", evidence_id: 101 },
      video_metadata: { title: "First video", content_url: "https://www.instagram.com/p/first/", thumbnail_url: "https://images.example/first.jpg" },
    });
    const second = videoResult({
      video_id: "second",
      matched_kol_pool_id: 0,
      video_flow: { status: "queued", evidence_id: 102 },
      video_metadata: { title: "Second video", content_url: "https://www.instagram.com/p/second/", thumbnail_url: "https://images.example/second.jpg" },
    });

    const view = renderSummary(first);
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toBe("https://r2.example/first.mp4"));

    view.rerender(
      <UrlSummary result={second} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    expect(document.querySelector('video[src="https://r2.example/first.mp4"]')).toBeNull();
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText("Second video")).toBeTruthy();
    expect(screen.getByText("视频缓存未就绪，暂时不可播放")).toBeTruthy();
  });

  it("removes the previous evidence analysis on the first frame after a history switch", async () => {
    api.getKolVideoAnalysisCache.mockImplementation(async (_token: string, evidenceId: string, method: string) => {
      if (evidenceId === "202") return new Promise(() => undefined);
      if (method.includes("keyframe")) {
        return { target_type: "video_evidence", target_id: evidenceId, state: "pending", entry: null };
      }
      return {
        target_type: "video_evidence",
        target_id: evidenceId,
        state: "ready",
        entry: {
          target_type: "video_evidence",
          target_id: evidenceId,
          derive_method: "video_analysis_final_v1",
          status: "ready",
          result: {
            video_analysis_final_v1: {
              layer1_visual_content: {
                content_summary: "Evidence A analysis",
                scene_timeline: [{ timestamp: "00:17", what: "Evidence A scene" }],
              },
              layer6_flags_and_scores: { scores: { content_quality_score: 91 } },
            },
          },
        },
      };
    });
    const first = videoResult({
      matched_kol_pool_id: 0,
      video_flow: { status: "ready", evidence_id: 201 },
      video_metadata: { title: "Evidence A video", content_url: "https://www.instagram.com/p/evidence-a/" },
    });
    const second = videoResult({
      matched_kol_pool_id: 0,
      video_flow: { status: "queued", evidence_id: 202 },
      video_metadata: { title: "Evidence B video", content_url: "https://www.instagram.com/p/evidence-b/" },
    });

    const view = renderSummary(first);
    await waitFor(() => expect(screen.getByText("Evidence A analysis")).toBeTruthy());
    expect(screen.getByText("00:17")).toBeTruthy();
    expect(screen.getByText("91")).toBeTruthy();

    view.rerender(
      <UrlSummary result={second} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    expect(screen.queryByText("Evidence A analysis")).toBeNull();
    expect(screen.queryByText("00:17")).toBeNull();
    expect(screen.queryByText("Evidence A scene")).toBeNull();
    expect(screen.queryByText("91")).toBeNull();
    expect(screen.getByText("Evidence B video")).toBeTruthy();
  });

  it("accepts only an exact local video-cache digest route", () => {
    const digestUrl = `/api/vkpi-media/video-cache/${"a".repeat(64)}`;
    const view = renderSummary(videoResult({
      matched_kol_pool_id: 0,
      video_flow: { status: "queued", evidence_id: 0, cached_video_url: digestUrl },
    }));

    expect(document.querySelector("video")?.getAttribute("src")).toBe(digestUrl);
    expect(screen.getByText("缓存视频加载中")).toBeTruthy();
    expect(screen.queryByText("缓存视频可播放")).toBeNull();
    fireEvent.canPlay(screen.getByTestId("cached-video-player"));
    expect(screen.getByText("缓存视频可播放")).toBeTruthy();

    view.rerender(
      <UrlSummary
        result={videoResult({ matched_kol_pool_id: 0, video_flow: { status: "queued", evidence_id: 0, cached_video_url: "/api/vkpi-media/video-cache/not-a-digest" } })}
        apiToken="token"
        canExecute
        isExecuting={false}
        onExecute={() => undefined}
      />,
    );
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText("视频缓存未就绪，暂时不可播放")).toBeTruthy();
  });

  it("rejects unsafe poster, source, profile, and cached-video URLs", () => {
    renderSummary(videoResult({
      url: { input: "javascript:alert(1)", normalized: "//evil.example/post" },
      matched_kol_pool_id: 0,
      in_pool: false,
      creator_identity: {
        handle: "unsafe-creator",
        avatar_url: "data:image/svg+xml,unsafe",
        profile_url: "javascript:alert(2)",
      },
      video_metadata: {
        title: "Unsafe links",
        content_url: "https://user:password@evil.example/post",
        thumbnail_url: "data:image/png;base64,unsafe",
      },
      video_flow: {
        status: "queued",
        evidence_id: 0,
        cached_video_url: "/api/vkpi-media/video-cache/not-a-digest",
      },
    }));

    expect(screen.getByText("封面暂不可用")).toBeTruthy();
    expect(document.querySelector("video")).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(document.querySelector('a[href^="//"]')).toBeNull();
    expect(document.querySelector('img[src^="data:"]')).toBeNull();
    expect(screen.queryByRole("link", { name: /查看原帖|打开 Instagram 原帖/ })).toBeNull();
  });

  it("does not show the previous hydrated KOL on the first frame after a history switch", async () => {
    const bProfile = deferred<{ item: Record<string, unknown> }>();
    api.getKolPoolItem.mockImplementation((_token: string, kolId: number) => {
      if (kolId === 101) {
        return Promise.resolve({
          item: { id: 101, platform: "youtube", handle: "creator-a", followers: 111111, bio: "Creator A hydrated bio" },
        });
      }
      return bProfile.promise;
    });

    const view = renderSummary(profileResult(101, "creator-a"));
    await waitFor(() => expect(screen.getByText("111K 粉")).toBeTruthy());

    view.rerender(
      <UrlSummary result={profileResult(202, "creator-b")} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    expect(screen.queryByText("111K 粉")).toBeNull();
    expect(screen.queryByText("Creator A hydrated bio")).toBeNull();
    expect(screen.getAllByText("creator-b").length).toBeGreaterThan(0);

    await act(async () => {
      bProfile.resolve({ item: { id: 202, platform: "youtube", handle: "creator-b", followers: 222222, bio: "Creator B hydrated bio" } });
      await bProfile.promise;
    });
    await waitFor(() => expect(screen.getByText("222K 粉")).toBeTruthy());
  });

  it("keeps full-video discovery state isolated by KOL and request", async () => {
    const crawlA = deferred<any>();
    const crawlB = deferred<any>();
    api.deepCrawlKolUrl
      .mockImplementationOnce(() => crawlA.promise)
      .mockImplementationOnce(() => crawlB.promise);
    api.enqueueAllKolVideos.mockImplementation(async (_token: string, kolId: number) => ({ queued: kolId === 101 ? 11 : 22 }));

    const view = renderSummary(profileResult(101, "creator-a"));
    const startA = await screen.findByRole("button", { name: "发现历史视频并批量分析" });
    fireEvent.click(startA);
    expect(screen.getByRole("button", { name: "发现中…" })).toBeDisabled();

    view.rerender(
      <UrlSummary result={profileResult(202, "creator-b")} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    const startB = screen.getByRole("button", { name: "发现历史视频并批量分析" });
    expect(startB).not.toBeDisabled();
    expect(screen.queryByText(/已发现并入队:11/)).toBeNull();
    fireEvent.click(startB);
    expect(screen.getByRole("button", { name: "发现中…" })).toBeDisabled();

    await act(async () => {
      crawlA.resolve({ profile_flow: { kol_pool_id: 101 } });
      await crawlA.promise;
    });
    await waitFor(() => expect(api.enqueueAllKolVideos).toHaveBeenCalledWith("token", 101));
    expect(screen.getByRole("button", { name: "发现中…" })).toBeDisabled();
    expect(screen.queryByText(/已发现并入队:11/)).toBeNull();

    await act(async () => {
      crawlB.resolve({ profile_flow: { kol_pool_id: 202 } });
      await crawlB.promise;
    });
    await waitFor(() => expect(screen.getByText(/已发现并入队:22/)).toBeTruthy());
  });

});
