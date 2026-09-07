import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const domain = vi.hoisted(() => ({
  deepCrawlKolUrl: vi.fn(), getKolSearchSession: vi.fn(), listKolSearchHistory: vi.fn(),
  smartKolSearch: vi.fn(), smartKolSearchProfileAdvanceJob: vi.fn(),
}));
const service = vi.hoisted(() => ({
  archiveAllKolSearchHistory: vi.fn(), archiveKolSearchHistorySession: vi.fn(),
  restoreKolSearchHistorySession: vi.fn(), approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(), favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(), listKolPoolFavorites: vi.fn(), resolveKolPool: vi.fn(),
}));
vi.mock("../../../../domains/kol", () => domain);
vi.mock("../../../../services/vkpi/kolPool-api", () => service);
import { SmartKolInputPanel } from "./SmartKolInputPanel";
import { useSmartKolInputPanelController } from "./SmartKolInputPanel.controller";
import { onlineQualifiedSummaryFromSession } from "./SmartKolInputPanel.OnlineQualified";
import { isSearchSessionTerminal, searchSessionProgress } from "./SmartKolInputPanel.progress-derivers";
import { ProgressiveSearchStageCard } from "./SmartKolInputPanel.Progress";

function queued(id = 81) {
  return {
    status: "queued", mode: "text", query_type: "text_recall",
    branch: "kol_recall_profile_advance_pipeline",
    search_session: { id, query_text: "street photographers", query_type: "text_recall", status: "running", items: [] },
    search_lanes: { local: { status: "queued", returned_count: null }, online: { status: "queued", returned_count: null } },
  };
}
function search() {
  fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: "street photographers" } });
  fireEvent.click(screen.getByTestId("smart-kol-run"));
}

function acceptedSnapshot() {
  const identity = { kol_pool_id: 91, canonical_fingerprint: "a".repeat(64),
    snapshot_id: "accepted-1", snapshot_revision: 1, server_rank: 1, global_unique_rank: 31 };
  return {
    ...queued().search_session,
    items: [{ id: 91, item_type: "online_qualified_candidate", kol_pool_id: 91, status: "ready", rank: 1,
      payload: { ...identity, schema: "smart_online_net_new_qualified_v1", origin_lane: "online",
        source: "platform_discovery_strict", qualification_status: "accepted", handle: "verified-creator",
        platform: "youtube", followers: 12000, profile_type: "creator",
        qualification_evidence: { ...identity, schema: "smart_local_gate_evidence_v2", passed: true,
          account_quality: { passed: true, verdict: "creator" }, followers: { passed: true, value: 12000 },
          activity: { passed: true }, market: { passed: true }, language: { passed: true },
          profile_type: { passed: true }, platform: { passed: true },
          relevance: { passed: true, evidence: [{ field: "bio", term: "street", source: "server_profile_evidence" }] },
        },
      },
    }],
    result_summary: { online_qualification: {
      schema: "smart_online_net_new_qualified_v1", policy_version: 1, server_owned: true,
      origin_lane: "online", source: "platform_discovery_strict", status: "running", terminal: false,
      snapshot_complete: true, snapshot_revision: 1, snapshot_id: "accepted-1", target_count: 30,
      net_new_accepted_count: 1, returned_count: 1,
    } },
  };
}

function failedSnapshot(complete = false) {
  const accepted = acceptedSnapshot();
  return { ...accepted, status: "failed", result_summary: {
    ...accepted.result_summary, phase: "failed", search_execution_id: "attempt-one", search_execution_job_id: 17,
    smart_search_profile_advance_job: { status: "failed", job_id: 17, execution_id: "attempt-one", reason: "search_pipeline_cancelled" },
    progress: { phase: complete ? "complete" : "running", total: 1, base: 1,
      requested_tasks_terminal: complete, required_tasks_complete: complete,
      full_analysis_complete: complete, decision_eligible: complete, profile_succeeded: 1,
      video: { ready: 1 }, comments: { ready: 1 }, audience: { ready: 1 } },
  } };
}

describe("single-submission hybrid search", () => {
  beforeEach(() => {
    window.sessionStorage.clear(); window.localStorage.clear();
    Object.values(domain).forEach((mock) => mock.mockReset());
    Object.values(service).forEach((mock) => mock.mockReset());
    domain.listKolSearchHistory.mockResolvedValue({ items: [] });
    service.listKolPoolFavorites.mockResolvedValue({ items: [], total: 0 });
    domain.smartKolSearch.mockResolvedValue(queued());
    domain.getKolSearchSession.mockReturnValue(new Promise(() => {}));
  });

  it("shows local timeout as unknown count while online continues, without a second POST", async () => {
    domain.getKolSearchSession.mockResolvedValue({
      ...queued().search_session,
      result_summary: { search_lanes: {
        local: { status: "timeout", returned_count: null }, online: { status: "running", returned_count: null },
      } },
    });
    render(<SmartKolInputPanel apiToken="token" accountId="a" />);
    search();
    await waitFor(() => expect(screen.getByTestId("smart-kol-local-lane")).toHaveTextContent("超时 · 数量待确认"));
    expect(screen.getByTestId("smart-kol-online-lane")).toHaveTextContent("查找中");
    expect(screen.queryByTestId("smart-kol-recall-ready")).toBeNull();
    expect(screen.getByTestId("smart-kol-run")).not.toBeDisabled();
    expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("restores a running session after remount by GET only", async () => {
    const view = render(<SmartKolInputPanel apiToken="token" accountId="a" />);
    search();
    await screen.findByTestId("smart-kol-search-lanes");
    await waitFor(() => expect(domain.getKolSearchSession).toHaveBeenCalledWith("token", 81));
    view.unmount();
    render(<SmartKolInputPanel apiToken="token" accountId="a" />);
    await waitFor(() => expect(domain.getKolSearchSession).toHaveBeenCalledTimes(2));
    expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("shows blocked audience access even without local results and never resubmits", async () => {
    domain.getKolSearchSession.mockResolvedValue({
      ...queued().search_session, status: "partial", items: [],
      result_summary: { search_lanes: { local: { status: "timeout", returned_count: null }, online: { status: "blocked", returned_count: null } },
        online_qualification: { schema: "smart_online_net_new_qualified_v1", policy_version: 1, server_owned: true,
          origin_lane: "online", source: "platform_discovery_strict", status: "blocked", terminal: true,
          snapshot_complete: true, snapshot_revision: 1, snapshot_id: "blocked-snapshot", target_count: 30,
          net_new_accepted_count: 0, returned_count: 0, provider_calls: 0, evaluated_count: 0, exhausted: false,
          round_gate: { stopped_by: "audience_evidence_source_unavailable" },
          shortfall_reasons: { audience_evidence_source_unavailable: 1 },
        },
      },
    });
    render(<SmartKolInputPanel apiToken="token" accountId="a" />);
    search();
    await waitFor(() => expect(screen.getByTestId("online-source-blocked")).toHaveTextContent("未接入可信受众证据源"));
    expect(screen.queryByText("本轮已结束，没有已通过联网严格验收的候选。")).toBeNull();
    expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("an uncertain/failed acceptance never triggers a paid retry automatically", async () => {
    domain.smartKolSearch.mockRejectedValue(new Error("受理状态未知"));
    render(<SmartKolInputPanel apiToken="token" />);
    search();
    await screen.findByText("受理状态未知");
    expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });

  it("keeps accepted people across failed GETs, pause and GET-only resume of the original job", async () => {
    vi.useFakeTimers();
    try {
      domain.getKolSearchSession.mockResolvedValueOnce(acceptedSnapshot()).mockRejectedValue(new Error("离线"));
      const { result, unmount } = renderHook(() => useSmartKolInputPanelController({ apiToken: "token" }));
      await act(async () => { result.current.setInput("street photographers"); });
      await act(async () => { await result.current.run(); });
      const summary = () => onlineQualifiedSummaryFromSession(result.current.activeSearchSession);
      expect(summary().qualified).toBe(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
      expect(summary().rows[0].item.handle).toBe("verified-creator");
      await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 1000); });
      expect(result.current.isSessionPollPaused).toBe(true);
      expect(summary().qualified).toBe(1);
      domain.getKolSearchSession.mockResolvedValue(acceptedSnapshot());
      await act(async () => { result.current.resumeSearchPolling(); });
      expect(result.current.isSessionPollPaused).toBe(false);
      expect(domain.getKolSearchSession).toHaveBeenLastCalledWith("token", 81);
      expect(summary().qualified).toBe(1);
      expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
      expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([false, true])("confirmed orchestration failure overrides stale completion=%s without discarding accepted people", (complete) => {
    const failed = failedSnapshot(complete);
    expect(onlineQualifiedSummaryFromSession(failed).qualified).toBe(1);
    expect(isSearchSessionTerminal(failed)).toBe(true);
    expect(searchSessionProgress(failed)).toMatchObject({ phase: "failed", requiredTasksComplete: complete, observationTerminal: true,
      fullAnalysisComplete: false, decisionEligible: false });
  });

  it("does not let a stale attempt override an observed running unit", () => {
    const failed = failedSnapshot();
    failed.result_summary.search_execution_id = "replacement-attempt";
    const session = { ...failed, progress_contract: { schema: "kol_search_progress_v1", state: "running",
      requested_units: 2, successful_units: 1, running_units: 1, requested_tasks_terminal: false } };
    expect(isSearchSessionTerminal(session)).toBe(false);
    expect(searchSessionProgress(session)).toMatchObject({ phase: "enriching", requiredTasksComplete: false });
    expect(searchSessionProgress(session).observationTerminal).not.toBe(true);
  });

  it("stops waiting for confirmed interruption while preserving unresolved units and accepted rows", async () => {
    vi.useFakeTimers();
    try {
      const session = { ...failedSnapshot(), progress_contract: {
        schema: "kol_search_progress_v1", state: "failed", orchestration_interrupted: true, observation_terminal: true,
        requested_units: 2, successful_units: 1, terminal_units: 1, running_units: 1,
        requested_tasks_terminal: false, requested_tasks_successful: false,
        stages: { search: { population: 1, requested: 1, successful: 1, data_ready: 1 },
          profile: { requested: 1, successful: 0, counts: { running: 1 } } },
      } };
      domain.getKolSearchSession.mockResolvedValue(session);
      const { result, unmount } = renderHook(() => useSmartKolInputPanelController({ apiToken: "token" }));
      await act(async () => { result.current.setInput("street photographers"); });
      await act(async () => { await result.current.run(); });
      const progress = searchSessionProgress(result.current.activeSearchSession);
      expect(progress).toMatchObject({ phase: "failed", observationTerminal: true, requiredTasksComplete: false });
      expect(progress.contract?.runningUnits).toBe(1);
      render(<ProgressiveSearchStageCard progress={progress} />);
      expect(screen.getByTestId("kol-progress-strict-status")).toHaveTextContent("本次查找已中止");
      expect(screen.getByTestId("kol-progress-state-icon")).toHaveAttribute("data-state", "terminal");
      expect(screen.getByText(/未结束子任务与费用仍待核对/)).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(40_000); });
      expect(result.current.isSessionPolling).toBe(false);
      expect(result.current.isSessionPollPaused).toBe(false);
      expect(onlineQualifiedSummaryFromSession(result.current.activeSearchSession).qualified).toBe(1);
      const readCount = domain.getKolSearchSession.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(domain.getKolSearchSession).toHaveBeenCalledTimes(readCount);
      expect(domain.smartKolSearch).toHaveBeenCalledTimes(1);
      expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
      unmount();
    } finally { vi.useRealTimers(); }
  });

  it("ignores an old queue response after an explicit new network action", async () => {
    let resolveOld!: (value: unknown) => void;
    domain.smartKolSearch.mockReturnValue(new Promise((resolve) => { resolveOld = resolve; }));
    domain.smartKolSearchProfileAdvanceJob.mockResolvedValue({ status: "queued", search_session: { ...queued(82).search_session } });
    render(<SmartKolInputPanel apiToken="token" />);
    search();
    fireEvent.click(screen.getByTestId("smart-kol-fresh-network"));
    await waitFor(() => expect(domain.getKolSearchSession).toHaveBeenCalledWith("token", 82));
    await act(async () => { resolveOld(queued(999)); });
    expect(domain.smartKolSearchProfileAdvanceJob).toHaveBeenCalledTimes(1);
    expect(domain.getKolSearchSession).not.toHaveBeenCalledWith("token", 999);
  });

  it("a local-only platform selection submits saved with both paid flags false", async () => {
    domain.smartKolSearch.mockResolvedValue({ status: "ready", mode: "text", result: { items: [], diagnostics: {} } });
    const { result } = renderHook(() => useSmartKolInputPanelController({ apiToken: "token" }));
    act(() => { result.current.setInput("street photographers"); result.current.setDiscoveryPlatforms(["facebook"]); });
    await act(async () => { await result.current.run(); });
    expect(domain.smartKolSearch.mock.calls[0][2]).toMatchObject({
      searchMode: "saved", includeNewDiscovery: false, executeNewDiscovery: false, newDiscoveryPlatforms: [],
    });
    expect(domain.smartKolSearchProfileAdvanceJob).not.toHaveBeenCalled();
  });
});
