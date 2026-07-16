import { describe, expect, it } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  advanceStatusLabel,
  historyStatusMeta,
  isSearchSessionTerminal,
  mergeKolSearchSessionSnapshots,
  mergeSearchSnapshotItems,
  searchSessionProgress,
  urlResultFromSession,
} from "./SmartKolInputPanel.derivers";

describe("SmartKolInputPanel history status labels", () => {
  it("does not present a partial terminal session as fully complete", () => {
    expect(advanceStatusLabel("partial")).toBe("部分完成");
    expect(historyStatusMeta("partial")).toMatchObject({ label: "部分完成", dot: "#fbbf24" });
  });
});

describe("SmartKolInputPanel progressive search snapshots", () => {
  it("keeps ready and non-empty fields when a later pool-id snapshot is sparse", () => {
    const merged = mergeSearchSnapshotItems(
      [{ kol_pool_id: 42, status: "ready", payload: { platform: "youtube", handle: "creator", avatar_url: "avatar.jpg", bio: "rich bio", tags: ["camera", "portrait"] } }],
      [{ kol_pool_id: 42, status: "partial", payload: { platform: "youtube", handle: "creator", avatar_url: "", followers: 12000, tags: ["camera"] } }],
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].status).toBe("ready");
    expect(merged[0].payload).toMatchObject({ avatar_url: "avatar.jpg", bio: "rich bio", followers: 12000, tags: ["camera", "portrait"] });
  });

  it("falls back to normalized platform and handle when the pool id is not available", () => {
    const merged = mergeSearchSnapshotItems(
      [{ status: "identified", payload: { platform: "TikTok", handle: "@Creator", avatar_url: "avatar.jpg" } }],
      [{ status: "partial", payload: { platform: "tiktok", handle: "creator", followers: 8000 } }],
    );

    expect(merged).toHaveLength(1);
    expect(merged[0].payload).toMatchObject({ avatar_url: "avatar.jpg", followers: 8000 });
  });

  it("keeps prior KOL rows while accepting fresh phase and count metadata", () => {
    const previous: VkpiKolSearchHistoryItem = {
      id: 7,
      status: "running",
      items: [{ kol_pool_id: 42, status: "ready", payload: { platform: "youtube", handle: "creator", bio: "rich bio" } }],
      result_summary: { smart_search_profile_advance_job: { status: "running", advance_counts: { ready: 1 } } },
    };
    const incoming: VkpiKolSearchHistoryItem = {
      id: 7,
      status: "partial",
      items: [],
      result_summary: { smart_search_profile_advance_job: { status: "partial", advance_counts: { ready: 1, partial: 14 } } },
    };

    const merged = mergeKolSearchSessionSnapshots(previous, incoming);
    expect(merged.items).toHaveLength(1);
    expect(merged.items?.[0].payload).toMatchObject({ bio: "rich bio" });
    expect(merged.result_summary).toMatchObject({
      smart_search_profile_advance_job: { status: "partial", advance_counts: { ready: 1, partial: 14 } },
    });
  });

  it("derives a truthful 15-person basic/deep stage from backend counts", () => {
    const progress = searchSessionProgress({
      status: "partial",
      result_summary: {
        items_written: 15,
        query: { limit: 15 },
        new_discovery: { status: "ready" },
        profile_batch_advance: { status: "partial", selected: 15 },
        smart_search_profile_advance_job: {
          status: "partial",
          advance_status: "partial",
          recall_returned: 15,
          advance_counts: { ready: 3, partial: 11, failed: 1, errors: 0, skipped: 0 },
        },
      },
    });

    expect(progress).toMatchObject({
      phase: "partial",
      target: 15,
      basicVisible: 15,
      deepReady: 3,
      deepPartial: 11,
      failed: 1,
      requiredTasksComplete: true,
    });
  });

  it("reads the direct backend progress contract for base and profile outcomes", () => {
    const progress = searchSessionProgress({
      status: "running",
      result_summary: {
        smart_search_profile_advance_job: { status: "partial", advance_status: "partial" },
        new_discovery: { status: "ready" },
        progress: {
          phase: "enriching",
          base: 15,
          total: 15,
          profile_ready: 11,
          profile_partial: 7,
          profile_failed: 1,
          profile_skipped: 0,
          complete_ready: 4,
          complete_partial: 3,
        },
      },
    });

    expect(progress).toMatchObject({
      phase: "enriching",
      target: 15,
      basicVisible: 15,
      profileReady: 11,
      deepReady: 4,
      deepPartial: 3,
      failed: 1,
      accounted: 8,
      requiredTasksComplete: false,
    });
  });

  it("maps incremental profile counters and the current KOL without treating base data as full analysis", () => {
    const progress = searchSessionProgress({
      status: "running",
      result_summary: {
        phase: "profile",
        progress: {
          base: 15,
          total: 15,
          profile_ready: 3,
          profile_completed: 4,
          profile_succeeded: 3,
          profile_failed: 1,
          profile_remaining: 11,
          current_item: {
            item_id: 77,
            rank: 4,
            handle: "camera_creator",
            profile_url: "https://example.test/camera_creator",
            status: "partial",
            profile_status: "crawl_failed",
          },
          base_complete: true,
          requested_tasks_terminal: false,
          full_analysis_complete: false,
          decision_eligible: false,
        },
      },
    });

    expect(progress).toMatchObject({
      phase: "profiling",
      target: 15,
      basicVisible: 15,
      baseComplete: true,
      profileCompleted: 4,
      profileSucceeded: 3,
      profileFailed: 1,
      profileRemaining: 11,
      currentItem: {
        itemId: 77,
        rank: 4,
        handle: "camera_creator",
        profileStatus: "crawl_failed",
      },
      completionContractExplicit: true,
      requestedTasksTerminal: false,
      fullAnalysisComplete: false,
      decisionEligible: false,
      requiredTasksComplete: false,
    });
  });

  it("does not let not-requested downstream work masquerade as complete analysis", () => {
    const progress = searchSessionProgress({
      status: "ready",
      result_summary: {
        phase: "complete",
        progress: {
          base: 2,
          total: 2,
          profile_ready: 2,
          profile_completed: 2,
          profile_succeeded: 2,
          profile_failed: 0,
          base_complete: true,
          requested_tasks_terminal: true,
          // Even a contradictory stale/buggy truth bit cannot override visible not_requested work.
          full_analysis_complete: true,
          decision_eligible: true,
          video: { ready: 0, active: 0, failed: 0, not_requested: 2 },
          comments: { ready: 2, active: 0, failed: 0, not_requested: 0 },
          audience: { ready: 2, active: 0, failed: 0, not_requested: 0 },
        },
      },
    });

    expect(progress).toMatchObject({
      phase: "complete",
      phaseLabel: "已请求阶段已结束",
      requestedTasksTerminal: true,
      fullAnalysisComplete: false,
      decisionEligible: false,
      video: { notRequested: 2 },
    });
  });

  it("exposes decision eligibility only from the strict backend contract", () => {
    const progress = searchSessionProgress({
      status: "ready",
      result_summary: {
        phase: "complete",
        progress: {
          base: 1,
          total: 1,
          profile_ready: 1,
          profile_completed: 1,
          profile_succeeded: 1,
          base_complete: true,
          requested_tasks_terminal: true,
          full_analysis_complete: true,
          decision_eligible: true,
          video: { ready: 1, active: 0, failed: 0, not_requested: 0 },
          comments: { ready: 1, active: 0, failed: 0, not_requested: 0 },
          audience: { ready: 1, active: 0, failed: 0, not_requested: 0 },
        },
      },
    });

    expect(progress).toMatchObject({
      phase: "complete",
      phaseLabel: "完整分析已完成",
      fullAnalysisComplete: true,
      decisionEligible: true,
    });
  });

  it("keeps polling while a downstream stage is active and exposes all four display stages", () => {
    const session: VkpiKolSearchHistoryItem = {
      status: "partial",
      result_summary: {
        phase: "partial",
        required_tasks_complete: false,
        progress: {
          base: 15,
          total: 15,
          profile_ready: 15,
          complete_ready: 14,
          complete_partial: 1,
          video: { ready: 10, active: 2, failed: 1, not_requested: 2 },
          comments: { ready: 4, active: 11, failed: 0, not_requested: 0 },
          audience: { ready: 2, active: 2, failed: 0, not_requested: 11 },
        },
      },
    };

    const progress = searchSessionProgress(session);
    expect(progress).toMatchObject({
      phase: "enriching",
      downstreamTracked: true,
      video: { ready: 10, active: 2, failed: 1, notRequested: 2 },
      comments: { ready: 4, active: 11, failed: 0, notRequested: 0 },
      audience: { ready: 2, active: 2, failed: 0, notRequested: 11 },
      requiredTasksComplete: false,
    });
    expect(isSearchSessionTerminal(session)).toBe(false);
  });

  it("stops polling a terminal partial result without calling it complete", () => {
    const session: VkpiKolSearchHistoryItem = {
      status: "partial",
      result_summary: {
        phase: "partial",
        required_tasks_complete: true,
        progress: {
          base: 1,
          total: 1,
          profile_ready: 1,
          complete_ready: 0,
          complete_partial: 1,
          video: { ready: 1, active: 0, failed: 0, not_requested: 0 },
          comments: { ready: 0, active: 0, failed: 1, not_requested: 0 },
          audience: { ready: 0, active: 0, failed: 0, not_requested: 1 },
        },
      },
    };

    const progress = searchSessionProgress(session);
    expect(progress.phase).toBe("partial");
    expect(progress.requiredTasksComplete).toBe(true);
    expect(isSearchSessionTerminal(session)).toBe(true);
  });

  it("treats intentionally disabled AI stages as a complete base-data flow", () => {
    const session: VkpiKolSearchHistoryItem = {
      status: "ready",
      result_summary: {
        phase: "complete",
        required_tasks_complete: true,
        smart_search_profile_advance_job: {
          status: "ready",
          advance_status: "ready",
          content_fit_status: "ai_disabled",
        },
        progress: {
          base: 15,
          total: 15,
          profile_ready: 15,
          complete_ready: 15,
          complete_partial: 0,
          video: { ready: 0, active: 0, failed: 0, not_requested: 15 },
          comments: { ready: 15, active: 0, failed: 0, not_requested: 0 },
          audience: { ready: 15, active: 0, failed: 0, not_requested: 0 },
        },
      },
    };

    const progress = searchSessionProgress(session);
    expect(progress).toMatchObject({
      phase: "complete",
      phaseLabel: "基础数据已完成",
      requiredTasksComplete: true,
      video: { ready: 0, active: 0, failed: 0, notRequested: 15 },
    });
    expect(isSearchSessionTerminal(session)).toBe(true);
  });

  it("restores profile data written under profile_execute in account URL history", () => {
    const result = urlResultFromSession({
      id: 993,
      query_text: "https://www.youtube.com/@ItiJarve",
      query_type: "url_profile",
      status: "ready",
      items: [{
        item_type: "url_profile",
        status: "ready",
        kol_pool_id: 13053,
        source_url: "https://www.youtube.com/@ItiJarve",
        payload: {
          platform: "youtube",
          handle: "itijarve",
          profile_execute: {
            status: "ready",
            operation: "update",
            profile_data: {
              avatar_url: "https://images.example/iti.jpg",
              followers: 24000,
              posts_count: 87,
              bio: "Camera creator",
            },
          },
          profile_flow: {
            status: "ready",
            operation: "reuse_recent_profile",
            profile_data: { bio: "Latest camera creator bio" },
          },
        },
      }],
    });

    expect(result?.profile_flow).toMatchObject({
      status: "ready",
      operation: "reuse_recent_profile",
      profile_data: {
        avatar_url: "https://images.example/iti.jpg",
        followers: 24000,
        posts_count: 87,
        bio: "Latest camera creator bio",
      },
    });
  });
});
