// 会话 1106 复刻(2026-08-22「搜索完成后新发现区不显示」):
// ① 召回先到、发现未到的窗口,契约带 orchestration_pending → 不判终态、不停轮询、横幅仍「正在查找」;
// ② 18 条 new_creator 快照不带 followers → 全部上墙,卡面标「粉丝数待核」;
// ③ 会话完成后横幅切完成态摘要(本次全网新发现 N 人…),不再挂「正在查找」。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import { RecallMiniItem } from "./SmartKolInputPanel.Sections";
import {
  discoveryItemsFromSession,
  isSearchSessionTerminal,
  reachFloorDisplayFromSession,
  searchSessionProgress,
  sessionDiscoveryTally,
  sessionStatusBanner,
} from "./SmartKolInputPanel.derivers";

const PLATFORMS = ["tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "tiktok", "youtube", "youtube", "youtube", "youtube", "youtube", "youtube", "instagram", "instagram"];

// 1106 真实 payload 键形:avatar_url/avg_views/channel_name/channel_url/comments/handle/likes/market/platform/
// published/reach_status/relevance_*/sample_title/search_query/source/source_url/thumbnail_url/views —— 没有 followers/bio。
function newCreatorItem(index: number, platform: string, reachStatus = "ok") {
  const handle = `face_${index}`;
  return {
    id: 3844 + index,
    item_type: "new_creator",
    status: "identified",
    stage: "identified",
    rank: index + 1,
    kol_pool_id: null,
    source_url: `https://www.${platform}.com/@${handle}`,
    payload: {
      source: "platform_discovery",
      platform,
      handle,
      channel_name: `Face ${index}`,
      channel_url: `https://www.${platform}.com/@${handle}`,
      source_url: `https://www.${platform}.com/@${handle}`,
      avatar_url: "",
      thumbnail_url: "",
      views: platform === "youtube" ? 0 : 1200,
      likes: platform === "youtube" ? 0 : 40,
      comments: platform === "youtube" ? 0 : 3,
      avg_views: 0,
      published: "2026-08-01T00:00:00Z",
      search_query: "Sony E-mount 35mm f1.2",
      market: "US",
      relevance_score: 0.6,
      relevance_tier: "高",
      relevance_hits: ["lens"],
      reach_status: reachStatus,
      sample_title: "35mm f1.2 test",
    },
  };
}

function contract(overrides: Record<string, unknown>) {
  const stage = (requested: number, successful: number) => ({
    key: "x", population: 30, requested, successful, terminal: successful, remaining: requested - successful,
    state: requested ? (successful >= requested ? "ready" : "pending") : "not_requested",
    counts: { ready: successful, queued: 0, running: 0, active: 0, partial: 0, failed: 0, skipped: 0, not_requested: 30 - requested, unknown: 0 },
  });
  return {
    schema: "kol_search_progress_v1",
    claim_status: "observed_execution_only",
    state: "ready",
    requested_units: 30,
    successful_units: 30,
    terminal_units: 30,
    queued_units: 0,
    running_units: 0,
    active_units: 0,
    failed_units: 0,
    blocked_by_worker: false,
    orchestration_pending: false,
    full_analysis_complete: false,
    stages: { search: stage(30, 30), profile: stage(0, 0), video: stage(0, 0), comments: stage(0, 0), audience: stage(0, 0) },
    worker: { observed: true, online: true, state: "online" },
    ...overrides,
  };
}

function session1106(overrides: Partial<VkpiKolSearchHistoryItem> & Record<string, unknown>): VkpiKolSearchHistoryItem {
  const items = PLATFORMS.map((platform, index) => newCreatorItem(index, platform, index === 0 ? "analyzing" : "ok"));
  return {
    id: 1106,
    query_text: "…35 1.2 的用户",
    query_type: "text_recall",
    status: "ready",
    result_summary: {
      phase: "complete",
      complete: true,
      progress: { base: 63, total: 15, requested_tasks_terminal: false, base_complete: true, complete: true },
      new_discovery: {
        kind: "platform_discovery",
        status: "ready",
        counts: { new_creators: 18, auto_enrolled: 18, existing_matches: 15, filtered_low_reach: 34, analyzing: 1 },
      },
      smart_search_profile_advance_job: { status: "ready", advance_status: "ready", advance_counts: { ready: 0, failed: 0, partial: 15 } },
    },
    items,
    item_count: items.length,
    reach_floor_display: {
      enabled: true, min_followers: 1000, hidden_low_reach: 0, hidden_analyzing: 0, visible_analyzing: 1,
      by_type: { new_creator: { hidden_low_reach: 0, hidden_analyzing: 0, visible_analyzing: 1 } },
    },
    ...overrides,
  } as unknown as VkpiKolSearchHistoryItem;
}

describe("session 1106 replay · discovery visibility", () => {
  it("shows all 18 follower-less new faces and tallies them", () => {
    const session = session1106({ progress_contract: contract({}) });
    const faces = discoveryItemsFromSession(session);
    expect(faces).toHaveLength(18);
    expect(faces.every((face) => face.followers == null)).toBe(true);
    expect(sessionDiscoveryTally(session)).toEqual({ newFaces: 18, existing: 15, recall: 0 });
    expect(reachFloorDisplayFromSession(session)?.discovery.pendingFollowers).toBe(1);
  });

  it("renders「粉丝数待核」on a discovery card without followers (reach_status=analyzing or legacy snapshot)", () => {
    const [analyzing, legacyOk] = discoveryItemsFromSession(session1106({}));
    render(<RecallMiniItem index={1} item={analyzing} />);
    expect(screen.getByTestId("candidate-followers-pending").textContent).toBe("粉丝数待核");
    render(<RecallMiniItem index={2} item={legacyOk} />);
    expect(screen.getAllByTestId("candidate-followers-pending")).toHaveLength(2);
  });

  it("does not show「粉丝数待核」once followers are known", () => {
    const [face] = discoveryItemsFromSession(session1106({}));
    render(<RecallMiniItem index={1} item={{ ...face, followers: 7020 }} />);
    expect(screen.queryByTestId("candidate-followers-pending")).toBeNull();
    expect(screen.getByTestId("candidate-observed-metrics").textContent).toContain("7.0K");
  });

  it("retries a recovered avatar when polling replaces the failed URL", () => {
    const [face] = discoveryItemsFromSession(session1106({}));
    const stale = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=1";
    const refreshed = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=9999999999";
    const { container, rerender } = render(
      <RecallMiniItem index={1} item={{ ...face, avatar_url: stale }} />,
    );
    fireEvent.error(container.querySelector("img") as HTMLImageElement);
    expect(container.querySelector("img")).toBeNull();

    rerender(<RecallMiniItem index={1} item={{ ...face, avatar_url: refreshed }} />);
    expect(container.querySelector("img")?.getAttribute("src")).toContain(encodeURIComponent(refreshed));
  });

  it("recall-only window with orchestration_pending is not terminal and keeps polling", () => {
    const recallItems = Array.from({ length: 30 }, (_, index) => ({
      id: index + 1, item_type: "recall_candidate", status: "ready", stage: "identified", kol_pool_id: 1000 + index, payload: { platform: "youtube", handle: `r${index}` },
    }));
    const session = session1106({
      status: "running",
      items: recallItems,
      result_summary: {
        phase: "base",
        progress: { base: 30, total: 30, requested_tasks_terminal: false, base_complete: true },
        smart_search_profile_advance_job: { status: "running" },
      },
      progress_contract: contract({ state: "running", orchestration_pending: true, orchestration_pending_basis: "session_running_and_orchestrator_declares_more_tasks" }),
    });
    expect(isSearchSessionTerminal(session)).toBe(false);
    const progress = searchSessionProgress(session);
    expect(progress.requiredTasksComplete).toBe(false);
    expect(progress.phase).toBe("discovering");
    expect(progress.phaseLabel).toBe("全网发现中");
    expect(sessionStatusBanner(session, "running", {}, true)?.label).toBe("正在查找");
  });

  it("shows a Worker blockage instead of falling back to queued", () => {
    const session = session1106({
      status: "running",
      progress_contract: contract({
        state: "blocked_by_worker",
        blocked_by_worker: true,
        queued_units: 3,
        worker: { observed: true, online: false, state: "offline" },
      }),
    });
    const banner = sessionStatusBanner(session, "running", {}, true);
    expect(banner).toMatchObject({ tone: "info", label: "等待 Worker 恢复" });
    expect(banner?.note).toContain("Worker");
    expect(banner?.label).not.toBe("已排队");
  });

  it("legacy contract without the pending flag still reads the same window as terminal (documents the old trap)", () => {
    const recallItems = Array.from({ length: 30 }, (_, index) => ({
      id: index + 1, item_type: "recall_candidate", status: "ready", stage: "identified", kol_pool_id: 1000 + index, payload: {},
    }));
    const session = session1106({ status: "running", items: recallItems, progress_contract: contract({ state: "ready" }) });
    // 旧后端不带 orchestration_pending 时前端只能信契约单元——这里只是记录旧行为,修复落在后端契约。
    expect(isSearchSessionTerminal(session)).toBe(true);
  });

  it("switches the banner to a completion summary once the session is terminal, even if the job status is stale", () => {
    const session = session1106({ progress_contract: contract({}) });
    const banner = sessionStatusBanner(session, "running", { ready: 0, failed: 0 }, true);
    expect(banner?.tone).toBe("ok");
    expect(banner?.label).toBe("已找完");
    expect(banner?.note).toBe("本次全网新发现 18 人 · 库内已有 15 人,见下方。");
    // 完成态不再受 displayedSessionId 恒真的 polling 参数影响
    expect(sessionStatusBanner(session, "", {}, true)?.label).toBe("已找完");
  });

  it("partial sessions keep the warn tone but carry the tally", () => {
    const session = session1106({ status: "partial", progress_contract: contract({ state: "partial", failed_units: 3 }) });
    const banner = sessionStatusBanner(session, "partial", { ready: 0, failed: 0, partial: 15 }, false);
    expect(banner?.tone).toBe("warn");
    expect(banner?.note).toContain("本次全网新发现 18 人 · 库内已有 15 人");
  });

  it("closes a cancelled session without calling it successful or leaving it searching", () => {
    const session = session1106({
      status: "cancelled",
      progress_contract: contract({
        state: "cancelled",
        requested_tasks_terminal: true,
        requested_tasks_successful: false,
        successful_units: 12,
        terminal_units: 30,
      }),
    });

    expect(isSearchSessionTerminal(session)).toBe(true);
    expect(sessionStatusBanner(session, "running", {}, true)).toMatchObject({
      tone: "warn",
      label: "本轮已取消",
    });
    expect(sessionStatusBanner(session, "running", {}, true)?.note).toContain("后台不会继续运行");
  });

  it("lets a legacy terminal session override a stale nested running marker", () => {
    const cancelled = session1106({
      status: "cancelled",
      result_summary: {
        smart_search_profile_advance_job: { status: "running", advance_status: "running" },
      },
    });
    const failed = session1106({
      status: "failed",
      items: [],
      result_summary: {
        smart_search_profile_advance_job: { status: "running", advance_status: "running", error: "provider stopped" },
      },
    });

    expect(sessionStatusBanner(cancelled, "running", {}, true)?.label).toBe("本轮已取消");
    expect(sessionStatusBanner(failed, "running", {}, true)).toMatchObject({
      tone: "error",
      label: "这次没找到结果",
      note: "失败原因:provider stopped",
    });
  });

  it("empty completion stays honest", () => {
    const session = session1106({ items: [], result_summary: { phase: "complete", new_discovery: { counts: { new_creators: 0, existing_matches: 0 } } }, progress_contract: contract({}) });
    expect(sessionStatusBanner(session, "ready", {}, false)?.note).toBe("这次没有新的人选,可换个描述再试。");
  });
});
