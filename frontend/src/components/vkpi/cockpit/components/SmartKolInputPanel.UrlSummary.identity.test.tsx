import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
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
import { ProfileInfoCard } from "./SmartKolInputPanel.Sections";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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

describe("SmartKolInputPanel URL result identity isolation", () => {
  beforeEach(() => {
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
    api.getKolPoolItem.mockReset().mockResolvedValue({ item: {} });
    api.getKolRecommendationCard.mockReset().mockResolvedValue({ status: "empty" });
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

  it("resets avatar failures when a creator or account avatar changes", () => {
    const firstVideo = videoResult({
      matched_kol_pool_id: 0,
      creator_identity: { handle: "avatar-a", platform: "instagram", avatar_url: "https://images.example/bad-video-avatar.jpg" },
      video_flow: { status: "queued", evidence_id: 0 },
    });
    const secondVideo = videoResult({
      matched_kol_pool_id: 0,
      creator_identity: { handle: "avatar-b", platform: "instagram", avatar_url: "https://images.example/good-video-avatar.jpg" },
      video_flow: { status: "queued", evidence_id: 0 },
    });
    const view = renderSummary(firstVideo);
    const badVideoAvatar = document.querySelector('img[src="https://images.example/bad-video-avatar.jpg"]');
    expect(badVideoAvatar).toBeTruthy();
    fireEvent.error(badVideoAvatar as Element);
    expect(document.querySelector('img[src="https://images.example/bad-video-avatar.jpg"]')).toBeNull();

    view.rerender(
      <UrlSummary result={secondVideo} apiToken="token" canExecute isExecuting={false} onExecute={() => undefined} />,
    );
    expect(document.querySelector('img[src="https://images.example/good-video-avatar.jpg"]')).toBeTruthy();

    cleanup();
    const account = render(
      <ProfileInfoCard data={{ handle: "account-a", avatar_url: "https://images.example/bad-account-avatar.jpg" }} apiToken="token" />,
    );
    const badAccountAvatar = document.querySelector('img[src="https://images.example/bad-account-avatar.jpg"]');
    expect(badAccountAvatar).toBeTruthy();
    fireEvent.error(badAccountAvatar as Element);
    expect(document.querySelector('img[src="https://images.example/bad-account-avatar.jpg"]')).toBeNull();
    account.rerender(
      <ProfileInfoCard data={{ handle: "account-b", avatar_url: "https://images.example/good-account-avatar.jpg" }} apiToken="token" />,
    );
    expect(document.querySelector('img[src="https://images.example/good-account-avatar.jpg"]')).toBeTruthy();
  });

  it("ignores an old bio translation promise after switching profiles", async () => {
    const translationA = deferred<{ translated: string }>();
    api.translateBio.mockReturnValueOnce(translationA.promise);
    const view = render(
      <ProfileInfoCard data={{ handle: "account-a", bio: "English bio A" }} apiToken="token" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "译中文" }));
    expect(screen.getByText("翻译中…")).toBeTruthy();

    view.rerender(
      <ProfileInfoCard data={{ handle: "account-b", bio: "English bio B" }} apiToken="token" />,
    );
    expect(screen.getByText("English bio B")).toBeTruthy();
    expect(screen.getByRole("button", { name: "译中文" })).not.toBeDisabled();

    await act(async () => {
      translationA.resolve({ translated: "A 的旧译文" });
      await translationA.promise;
    });
    expect(screen.queryByText("A 的旧译文")).toBeNull();
    expect(screen.getByText("English bio B")).toBeTruthy();
  });
});
