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

describe("SmartKolInputPanel URL result mapping · 中国平台", () => {
  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    api.getKolPoolAccountDossier.mockResolvedValue({ status: "ready", coverage: {}, videos: [], gaps: [] });
    api.getKolPoolDetailBundle.mockResolvedValue({ status: "ready", item: {}, video_analysis: { items: [], summary: {} }, llm_deep_analysis: { status: "missing", count: 0 }, diagnostics: {} });
    api.getKolPoolItem.mockResolvedValue({ item: {} });
    api.getKolRecommendationCard.mockResolvedValue({ status: "ok" });
    api.getKolVideoAnalysisCache.mockResolvedValue({ target_type: "video_evidence", target_id: "0", state: "pending", entry: null });
    api.listKolPoolVideoComments.mockResolvedValue({ items: [], page: { total: 0, next_offset: 0 }, source: "pool_evidence" });
    api.translateBio.mockResolvedValue({ translated: "" });
  });

  afterEach(() => {
    cleanup();
  });

  it("中国平台视频终态:显示仅内容分析横幅+摘要,不显示失败/假排队/建档提示", async () => {
    // 面板自取缓存:给终态响应(not_requested),避免挂 6s 轮询定时器。
    api.getKolVideoAnalysisCache.mockResolvedValue({
      target_type: "cn_platform_video",
      target_id: "bilibili:BV1S6Kr6mEgi",
      state: "not_requested",
      entry: null,
    } as unknown as VkpiKolVideoAnalysisCacheResponse);
    renderSummary(videoResult({
      execute: true,
      in_pool: false,
      matched_kol_pool_id: null,
      platform: "bilibili",
      video_id: "BV1S6Kr6mEgi",
      url: {
        input: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
        normalized: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
      },
      creator_identity: { platform: "bilibili", display_name: "IPx的粉红豹" },
      video_metadata: {
        platform: "bilibili",
        title: "《 双 枪 牛 仔 》",
        content_url: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
        view_count: 3005478,
        duration_seconds: 62,
      },
      video_flow: {
        status: "cn_platform_video",
        operation: "cn_platform_video_analysis",
        cn_platform_video: true,
        job_status: "done",
        ai_analysis: { state: "ready", reason: "cn_platform_video_analysis" },
        cn_analysis: { content_summary: "动物配音搞笑短片", scores: { content_quality: 92 } },
      },
    }));
    expect(screen.getByText(/仅做内容分析，不建人选档案/)).toBeTruthy();
    expect(screen.getByText(/动物配音搞笑短片/)).toBeTruthy();
    expect(screen.getByText(/中国平台视频 · 内容分析完成，未建档/)).toBeTruthy();
    expect(screen.queryByText(/分析失败/)).toBeNull();
    expect(screen.queryByText(/AI 深析与时间戳正在排队/)).toBeNull();
    expect(screen.queryByText(/没识别到创作者/)).toBeNull();
    // 等面板异步读缓存落定(act 收口);缓存缺行时呈现诚实态,依然无假排队。
    await waitFor(() => expect(screen.getByTestId("video-decision-overview")).toBeTruthy());
  });

  it("中国平台视频终态:渲染完整六层深析面板(分镜/评分/情绪),摘要行保留", async () => {
    // final_v1 结构按本地 vkpi_analysis_cache id=15187(bilibili:BV1S6Kr6mEgi)真实行裁剪:
    // 六层平铺在 result 根部(CN _shape_cn_final_v1 落库形状),非嵌套 video_analysis_final_v1。
    api.getKolVideoAnalysisCache.mockImplementation(async (
      _token: string,
      targetId: string,
      method: string,
    ): Promise<VkpiKolVideoAnalysisCacheResponse> => {
      if (method.includes("keyframe")) {
        return { target_type: "cn_platform_video", target_id: targetId, state: "not_requested", entry: null } as VkpiKolVideoAnalysisCacheResponse;
      }
      return {
        target_type: "cn_platform_video",
        target_id: targetId,
        state: "ready",
        entry: {
          target_type: "cn_platform_video",
          target_id: targetId,
          derive_method: "video_analysis_final_v1",
          model: "gemini-2.5-flash",
          status: "ready",
          updated_at: "2026-07-21T02:28:30.729995+00:00",
          result: {
            schema_version: "video_analysis_final_v1",
            status: "completed",
            model: "gemini-2.5-flash",
            target_type: "cn_platform_video",
            target_id: "bilibili:BV1S6Kr6mEgi",
            layer1_visual_content: {
              content_summary: "视频是一个有趣的动物配音动画，一只狗和一只猫被配上拟人化的口型和对话。",
              scene_timeline: [
                { timestamp: "00:00", what: "狗盯着一个模糊的物体，眼神惊恐。", why_it_matters: "引入主要角色和核心冲突。" },
                { timestamp: "00:08", what: "猫出现，并被PS上拟人化口型，与狗对视。", why_it_matters: "奠定视频的幽默基调。" },
              ],
              brand_exposure: "无Viltrox品牌露出。",
            },
            layer2_viewer_emotion: {
              viewer_heart_score: 88,
              one_sentence_viewer_reaction: "太可爱太搞笑了，这狗和猫的表情和歌词完美配合！",
            },
            layer3_three_values: {
              channel_value: { score: 80, evidence: "该创作者展现了极强的内容创意、剪辑和配音能力。", confidence: 0.8 },
            },
            layer4_attribution: { product_contribution: "0%", kol_craft_contribution: "100%。" },
            layer5_recommendations: {
              cooperation_recommendation: { action: "接触并考虑合作", confidence: 0.85 },
            },
            layer6_flags_and_scores: {
              scores: { content_quality_score: 92, marketing_value_score: 25, viewer_heart_score: 88 },
              final_verdict: "这条视频本身对Viltrox的直接营销价值为零，但创作者制作水准极高，值得接触。",
              risk_flags: ["产品无关性：本视频内容与摄影器材完全无关。"],
            },
            viltrox_fit_score_untouched: true,
          },
        },
      } as unknown as VkpiKolVideoAnalysisCacheResponse;
    });
    renderSummary(videoResult({
      execute: true,
      in_pool: false,
      matched_kol_pool_id: null,
      platform: "bilibili",
      video_id: "BV1S6Kr6mEgi",
      url: {
        input: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
        normalized: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
      },
      creator_identity: { platform: "bilibili", display_name: "IPx的粉红豹" },
      video_metadata: {
        platform: "bilibili",
        title: "《 双 枪 牛 仔 》",
        content_url: "https://www.bilibili.com/video/BV1S6Kr6mEgi",
        view_count: 3005478,
        duration_seconds: 62,
      },
      video_flow: {
        status: "cn_platform_video",
        operation: "cn_platform_video_analysis",
        cn_platform_video: true,
        job_status: "done",
        ai_analysis: { state: "ready", reason: "cn_platform_video_analysis" },
        cn_analysis: { content_summary: "动物配音搞笑短片", scores: { content_quality: 92 } },
      },
    }));
    // 读取按 cn_platform_video 缓存键发起(target_id=<platform>:<video_id>)。
    await waitFor(() => {
      expect(api.getKolVideoAnalysisCache.mock.calls.some((call) => (
        call[1] === "bilibili:BV1S6Kr6mEgi"
        && call[2] === "video_analysis_final_v1"
        && (call[3] as { targetType?: string } | undefined)?.targetType === "cn_platform_video"
      ))).toBe(true);
    });
    // 六层富面板:分镜时间线 + 双评分 + 观众情绪 + 结论。
    await waitFor(() => expect(screen.getByText("分镜时间线")).toBeTruthy());
    expect(screen.getByText("00:08")).toBeTruthy();
    expect(screen.getByText("猫出现，并被PS上拟人化口型，与狗对视。")).toBeTruthy();
    expect(screen.getByText("内容质量")).toBeTruthy();
    expect(screen.getAllByText("92").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("25").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/太可爱太搞笑了/)).toBeTruthy();
    expect(screen.getByTestId("video-decision-overview")).toBeTruthy();
    // 摘要横幅保留;仍不出现假排队/建档文案。
    expect(screen.getByText(/仅做内容分析，不建人选档案/)).toBeTruthy();
    expect(screen.getByText(/动物配音搞笑短片/)).toBeTruthy();
    expect(screen.queryByText(/AI 深析与时间戳正在排队/)).toBeNull();
    expect(screen.queryByText(/分析失败/)).toBeNull();
  });

  it("中国平台视频媒体降级:仅元数据,不渲染富面板,不标失败", async () => {
    api.getKolVideoAnalysisCache.mockClear();
    renderSummary(videoResult({
      execute: true,
      in_pool: false,
      matched_kol_pool_id: null,
      platform: "xiaohongshu",
      video_id: "64608fa90000000027003d64",
      url: {
        input: "https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=T",
        normalized: "https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=T",
      },
      creator_identity: { platform: "xiaohongshu", display_name: "小青柑" },
      video_metadata: { platform: "xiaohongshu", title: "图文笔记", media_kind: "image" },
      video_flow: {
        status: "cn_platform_video",
        operation: "cn_platform_video_analysis",
        cn_platform_video: true,
        media_degraded: true,
        media_degraded_reason: "note_has_no_video_image_only",
        job_status: "done",
        ai_analysis: { state: "skipped", reason: "media_unavailable_metadata_only" },
      },
    }));
    expect(screen.getByText(/仅做内容分析，不建人选档案/)).toBeTruthy();
    expect(screen.getByText(/本次仅保留元数据/)).toBeTruthy();
    expect(screen.queryByText(/分析失败/)).toBeNull();
    // 媒体降级=仅元数据:不渲染富面板,也不发起 cn 缓存读取。
    expect(screen.queryByTestId("video-decision-overview")).toBeNull();
    expect(screen.queryByText("分镜时间线")).toBeNull();
    expect(api.getKolVideoAnalysisCache.mock.calls.some((call) => (
      (call[3] as { targetType?: string } | undefined)?.targetType === "cn_platform_video"
    ))).toBe(false);
  });
});
