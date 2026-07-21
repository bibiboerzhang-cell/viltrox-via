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

  it("中国平台视频终态:显示仅内容分析横幅+摘要,不显示失败/假排队/建档提示", () => {
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
  });

  it("中国平台视频媒体降级:诚实提示只保留元数据,不标失败", () => {
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
  });
});
