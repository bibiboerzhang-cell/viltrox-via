// SmartKolInputPanel 搜索进度合同与会话阶段纯派生。
// 只读取服务端观测字段；排队/运行/失败不计入成功进度。
import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  asRecord,
  cleanText,
  terminalSessionStatus,
  type Row,
} from "./SmartKolInputPanel.helpers";

function sessionItemsForProgress(session: VkpiKolSearchHistoryItem): Row[] {
  const items = Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.active_items) && session.active_items.length
      ? session.active_items
      : Array.isArray(session.items_preview)
        ? session.items_preview
        : [];
  return items.map((item) => asRecord(item));
}
export type SearchStageProgress = {
  ready: number;
  active: number;
  failed: number;
  notRequested: number;
  blocked?: number;
};

export type SearchProgressContractStage = {
  key: string;
  tracked: boolean;
  population: number | null;
  requested: number | null;
  successful: number | null;
  terminal: number | null;
  remaining: number | null;
  dataReady: number | null;
  state: string;
  counts: {
    ready: number | null;
    queued: number | null;
    running: number | null;
    active: number | null;
    partial: number | null;
    failed: number | null;
    skipped: number | null;
    notRequested: number | null;
  };
};

export type SearchProgressContractWorker = {
  observed: boolean;
  state: string;
  online: boolean | null;
  onlineCount: number | null;
  expectedCount: number | null;
  capacityReady: boolean | null;
  latestHeartbeatAt: string;
  shaAligned: boolean | null;
};

export type SearchProgressContractView = {
  schema: "kol_search_progress_v1";
  claimStatus: string;
  state: string;
  requestedUnits: number | null;
  successfulUnits: number | null;
  terminalUnits: number | null;
  queuedUnits: number | null;
  runningUnits: number | null;
  activeUnits: number | null;
  failedUnits: number | null;
  progressPct: number | null;
  terminalPct: number | null;
  blockedByWorker: boolean;
  fullAnalysisComplete: boolean;
  fullAnalysisExecutionComplete: boolean;
  fullAnalysisObservable: boolean;
  observedAt: string;
  stages: Record<"search" | "profile" | "video" | "comments" | "audience", SearchProgressContractStage>;
  worker: SearchProgressContractWorker;
};

export type SearchCurrentItem = {
  itemId: number | null;
  rank: number | null;
  handle: string;
  profileUrl: string;
  status: string;
  profileStatus: string;
};

export type SearchSessionProgress = {
  phase: "queued" | "discovering" | "profiling" | "enriching" | "blocked" | "complete" | "partial" | "failed";
  phaseLabel: string;
  target: number;
  basicVisible: number;
  profileReady: number;
  profileCompleted: number;
  profileSucceeded: number;
  profileFailed: number;
  profileRemaining: number;
  currentItem: SearchCurrentItem | null;
  deepReady: number;
  deepPartial: number;
  failed: number;
  accounted: number;
  downstreamTracked: boolean;
  video: SearchStageProgress;
  comments: SearchStageProgress;
  audience: SearchStageProgress;
  /** True only when the backend emitted at least one strict completion-contract field. */
  completionContractExplicit: boolean;
  baseComplete: boolean;
  /** Every requested stage is terminal. This is not the same as full analysis. */
  requestedTasksTerminal: boolean;
  /** Strict backend truth: video + comments + audience all succeeded for every target. */
  fullAnalysisComplete: boolean;
  /** Strict backend truth: the result has enough complete evidence for a decision. */
  decisionEligible: boolean;
  /** Backward-compatible alias used by the polling loop. */
  requiredTasksComplete: boolean;
  /** 新服务端统一合同；null 表示旧会话，只能展示无百分比的降级阶段。 */
  contract: SearchProgressContractView | null;
};

function contractNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function contractPercent(value: unknown, requested: number | null): number | null {
  if (requested == null || requested <= 0) return null;
  const parsed = contractNumber(value);
  return parsed == null ? null : Math.max(0, Math.min(100, parsed));
}

function progressContractStage(key: SearchProgressContractStage["key"], value: unknown): SearchProgressContractStage {
  const stage = asRecord(value);
  const counts = asRecord(stage.counts);
  const count = (name: string) => contractNumber(counts[name]);
  return {
    key,
    tracked: Object.keys(stage).length > 0,
    population: contractNumber(stage.population),
    requested: contractNumber(stage.requested),
    successful: contractNumber(stage.successful),
    terminal: contractNumber(stage.terminal),
    remaining: contractNumber(stage.remaining),
    dataReady: contractNumber(stage.data_ready),
    state: cleanText(stage.state),
    counts: {
      ready: count("ready"),
      queued: count("queued"),
      running: count("running"),
      active: count("active"),
      partial: count("partial"),
      failed: count("failed"),
      skipped: count("skipped"),
      notRequested: count("not_requested"),
    },
  };
}

/** Read only the versioned backend contract. A similarly-shaped legacy object is not upgraded. */
export function searchProgressContractFromSession(session: VkpiKolSearchHistoryItem | null): SearchProgressContractView | null {
  if (!session) return null;
  const summary = asRecord(session.result_summary);
  const direct = asRecord(session.progress_contract);
  const nested = asRecord(summary.progress_contract);
  const contract = cleanText(direct.schema) === "kol_search_progress_v1" ? direct : nested;
  if (cleanText(contract.schema) !== "kol_search_progress_v1") return null;
  const stages = asRecord(contract.stages);
  const requestedUnits = contractNumber(contract.requested_units);
  const worker = asRecord(contract.worker);
  return {
    schema: "kol_search_progress_v1",
    claimStatus: cleanText(contract.claim_status),
    state: cleanText(contract.state),
    requestedUnits,
    successfulUnits: contractNumber(contract.successful_units),
    terminalUnits: contractNumber(contract.terminal_units),
    queuedUnits: contractNumber(contract.queued_units),
    runningUnits: contractNumber(contract.running_units),
    activeUnits: contractNumber(contract.active_units),
    failedUnits: contractNumber(contract.failed_units),
    progressPct: contractPercent(contract.progress_pct, requestedUnits),
    terminalPct: contractPercent(contract.terminal_pct, requestedUnits),
    blockedByWorker: contract.blocked_by_worker === true,
    fullAnalysisComplete: contract.full_analysis_complete === true,
    fullAnalysisExecutionComplete: contract.full_analysis_execution_complete === true,
    fullAnalysisObservable: contract.full_analysis_observable === true,
    observedAt: cleanText(contract.observed_at),
    stages: {
      search: progressContractStage("search", stages.search),
      profile: progressContractStage("profile", stages.profile),
      video: progressContractStage("video", stages.video),
      comments: progressContractStage("comments", stages.comments),
      audience: progressContractStage("audience", stages.audience),
    },
    worker: {
      observed: worker.observed === true,
      state: cleanText(worker.state) || "unknown",
      online: typeof worker.online === "boolean" ? worker.online : null,
      onlineCount: contractNumber(worker.online_count),
      expectedCount: contractNumber(worker.expected_count),
      capacityReady: typeof worker.capacity_ready === "boolean" ? worker.capacity_ready : null,
      latestHeartbeatAt: cleanText(worker.latest_heartbeat_at),
      shaAligned: typeof worker.sha_aligned === "boolean" ? worker.sha_aligned : null,
    },
  };
}

function terminalSearchTaskStatus(value: unknown): boolean {
  const status = cleanText(value).toLowerCase();
  return terminalSessionStatus(status) || ["nothing_to_queue", "skipped", "not_requested", "ai_disabled"].includes(status);
}

function downstreamStageProgress(value: unknown): SearchStageProgress {
  const stage = asRecord(value);
  return {
    ready: Math.max(0, Number(stage.ready) || 0),
    active: Math.max(0, Number(stage.active) || 0),
    failed: Math.max(0, Number(stage.failed) || 0),
    notRequested: Math.max(0, Number(stage.not_requested ?? stage.notRequested) || 0),
  };
}

/** Derive a truthful staged view from existing backend summary/count fields; never invent totals. */
export function searchSessionProgress(session: VkpiKolSearchHistoryItem | null): SearchSessionProgress {
  const summary = asRecord(session?.result_summary);
  const progressContract = searchProgressContractFromSession(session);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  const discovery = asRecord(summary.new_discovery);
  const explicitProgress = asRecord(summary.progress || smartJob.progress || batch.progress);
  const explicitContractBoolean = (key: string): boolean | undefined => {
    const value = explicitProgress[key] ?? summary[key];
    return typeof value === "boolean" ? value : undefined;
  };
  const completionContractExplicit = [
    "base_complete",
    "requested_tasks_terminal",
    "full_analysis_complete",
    "decision_eligible",
  ].some((key) => explicitContractBoolean(key) !== undefined);
  const downstreamTracked = ["video", "comments", "audience"].some((key) => Object.keys(asRecord(explicitProgress[key])).length > 0);
  const video = downstreamStageProgress(explicitProgress.video);
  const comments = downstreamStageProgress(explicitProgress.comments);
  const audience = downstreamStageProgress(explicitProgress.audience);
  const downstreamStages = [video, comments, audience];
  const downstreamActive = downstreamStages.reduce((sum, stage) => sum + stage.active, 0);
  // `not_requested` is a terminal, intentional state (for example external AI is disabled by the
  // production-readiness gate).  It must not turn an otherwise complete base/profile/comments/
  // audience flow into a false failure.  The backend owns which tasks are required through
  // `required_tasks_complete`; only a real failed downstream task makes this view partial.
  const downstreamIncomplete = downstreamTracked && downstreamStages.some((stage) => stage.failed > 0);
  const downstreamNotRequested = downstreamTracked && downstreamStages.some((stage) => stage.notRequested > 0);
  const counts = asRecord(batch.counts || smartJob.advance_counts || explicitProgress.counts);
  // 新契约严格区分「档案补全」profile_* 与「完整分析」complete_*。完整分析只能读
  // complete_ready/complete_partial；仅旧后端缺 complete_* 时才回退旧 advance_counts。
  const profileReady = Math.max(0, Number(explicitProgress.profile_ready ?? counts.ready) || 0);
  const ready = Math.max(0, Number(explicitProgress.complete_ready ?? counts.ready) || 0);
  const partial = Math.max(0, Number(explicitProgress.complete_partial ?? counts.partial) || 0);
  const failed = Math.max(0, Number(counts.failed ?? explicitProgress.profile_failed) || 0)
    + Math.max(0, Number(counts.errors) || 0);
  const skipped = Math.max(0, Number(counts.skipped ?? explicitProgress.profile_skipped) || 0);
  const accounted = ready + partial + failed + skipped;
  const query = asRecord(summary.query);
  const recallItemCount = session
    ? sessionItemsForProgress(session).filter((item) => cleanText(item.item_type) !== "new_creator").length
    : 0;
  const target = Math.max(
    0,
    Number(explicitProgress.total) || 0,
    Number(batch.selected) || 0,
    Number(smartJob.recall_returned) || 0,
    Number(query.limit) || 0,
    accounted,
    recallItemCount,
  );
  const explicitBase = Math.max(0, Number(explicitProgress.base) || 0);
  const basicVisible = Math.min(
    target || explicitBase || Number(summary.items_written) || recallItemCount,
    Math.max(explicitBase, recallItemCount, Number(summary.items_written) || 0),
  );
  const profileFailed = Math.max(0, Number(explicitProgress.profile_failed ?? counts.failed) || 0);
  const hasProfileCompleted = Object.prototype.hasOwnProperty.call(explicitProgress, "profile_completed");
  const rawProfileCompleted = hasProfileCompleted
    ? Math.max(0, Number(explicitProgress.profile_completed) || 0)
    : Math.max(0, profileReady + profileFailed);
  const profileCompleted = target > 0 ? Math.min(target, rawProfileCompleted) : rawProfileCompleted;
  const hasProfileSucceeded = Object.prototype.hasOwnProperty.call(explicitProgress, "profile_succeeded");
  const rawProfileSucceeded = hasProfileSucceeded
    ? Math.max(0, Number(explicitProgress.profile_succeeded) || 0)
    : Math.max(0, profileCompleted - profileFailed);
  const profileSucceeded = target > 0 ? Math.min(target, rawProfileSucceeded) : rawProfileSucceeded;
  const profileRemaining = Object.prototype.hasOwnProperty.call(explicitProgress, "profile_remaining")
    ? Math.max(0, Number(explicitProgress.profile_remaining) || 0)
    : Math.max(0, target - profileCompleted);
  const currentItemRow = asRecord(explicitProgress.current_item || batch.current_item);
  const currentItem = Object.keys(currentItemRow).length > 0
    ? {
        itemId: Number(currentItemRow.item_id) > 0 ? Number(currentItemRow.item_id) : null,
        rank: Number(currentItemRow.rank) > 0 ? Number(currentItemRow.rank) : null,
        handle: cleanText(currentItemRow.handle),
        profileUrl: cleanText(currentItemRow.profile_url),
        status: cleanText(currentItemRow.status),
        profileStatus: cleanText(currentItemRow.profile_status),
      }
    : null;
  const taskStatuses = [
    Object.keys(smartJob).length ? cleanText(smartJob.advance_status || smartJob.status) : "",
    Object.keys(batch).length ? cleanText(batch.status) : "",
    Object.keys(discovery).length ? cleanText(discovery.status) : "",
  ].filter(Boolean);
  const explicitPhase = cleanText(explicitProgress.phase || summary.phase || smartJob.phase || batch.phase).toLowerCase();
  const explicitPhaseTerminal = ["failed", "blocked", "complete", "completed", "ready", "done", "partial"].includes(explicitPhase);
  const explicitPhaseActive = ["queued", "discovering", "discovery", "profiling", "profile", "enriching", "materializing", "analysis", "running"].includes(explicitPhase);
  const backendRequestedTasksTerminal = explicitContractBoolean("requested_tasks_terminal")
    ?? explicitContractBoolean("required_tasks_complete");
  const requiredTasksComplete = typeof backendRequestedTasksTerminal === "boolean"
    ? backendRequestedTasksTerminal && downstreamActive === 0
    : downstreamActive > 0 || explicitPhaseActive
      ? false
      : explicitPhaseTerminal
        ? true
        : taskStatuses.length
          ? taskStatuses.every(terminalSearchTaskStatus)
          : (target > 0 && accounted >= target) || Boolean(session && terminalSessionStatus(session.status));
  const baseComplete = explicitContractBoolean("base_complete") ?? (target > 0 && basicVisible >= target);
  const requestedTasksTerminal = requiredTasksComplete;
  // Do not infer these strict states from legacy `complete`/`required_tasks_complete`: those fields
  // only mean that requested work stopped, and may include `not_requested` downstream stages.
  const strictStagesReady = downstreamTracked && target > 0 && downstreamStages.every(
    (stage) => stage.ready >= target && stage.active === 0 && stage.failed === 0 && stage.notRequested === 0,
  );
  const fullAnalysisComplete = explicitContractBoolean("full_analysis_complete") === true && strictStagesReady;
  const decisionEligible = explicitContractBoolean("decision_eligible") === true
    && fullAnalysisComplete
    && profileFailed === 0
    && profileSucceeded >= target;
  const discoveryStatus = cleanText(discovery.status).toLowerCase();
  let phase: SearchSessionProgress["phase"] = "queued";
  if (downstreamActive > 0) phase = "enriching";
  else if (fullAnalysisComplete) phase = "complete";
  else if (["failed", "blocked"].includes(explicitPhase)) phase = "failed";
  else if (["complete", "completed", "ready", "done"].includes(explicitPhase)) phase = downstreamIncomplete ? "partial" : "complete";
  else if (explicitPhase === "partial") phase = "partial";
  else if (["discovering", "discovery"].includes(explicitPhase)) phase = "discovering";
  else if (["enriching", "materializing", "analysis"].includes(explicitPhase)) phase = "enriching";
  else if (["profiling", "profile"].includes(explicitPhase)) phase = "profiling";
  else if (failed > 0 && ready + partial === 0 && requiredTasksComplete) phase = "failed";
  else if (requiredTasksComplete && target > 0 && ready >= target) phase = "complete";
  else if (requiredTasksComplete && (partial > 0 || failed > 0)) phase = "partial";
  else if (Object.keys(discovery).length > 0 && !terminalSearchTaskStatus(discoveryStatus)) phase = "discovering";
  else if (accounted > 0 && accounted >= target && !requiredTasksComplete) phase = "enriching";
  else if (accounted > 0 || recallItemCount > 0) phase = "profiling";
  const phaseLabel: Record<SearchSessionProgress["phase"], string> = {
    queued: "等待开始",
    discovering: "全网发现中",
    profiling: "基础资料补全中",
    enriching: "后台深析中",
    blocked: "Worker 阻塞",
    complete: fullAnalysisComplete
      ? "完整分析已完成"
      : completionContractExplicit && requestedTasksTerminal
        ? "已请求阶段已结束"
        : downstreamNotRequested
          ? "基础数据已完成"
          : requestedTasksTerminal
            ? "已请求阶段已结束"
            : "分析已结束",
    partial: "阶段结果可查看",
    failed: "分析未完成",
  };
  if (progressContract) {
    const contractStage = (key: keyof SearchProgressContractView["stages"]): SearchStageProgress => {
      const stage = progressContract.stages[key];
      const activeCount = (stage.counts.queued ?? 0) + (stage.counts.running ?? 0) + (stage.counts.active ?? 0);
      return {
        ready: stage.successful ?? 0,
        active: activeCount,
        failed: (stage.counts.failed ?? 0) + (stage.counts.partial ?? 0),
        notRequested: stage.counts.notRequested ?? 0,
        blocked: progressContract.blockedByWorker ? activeCount : 0,
      };
    };
    const profileStage = progressContract.stages.profile;
    const searchStage = progressContract.stages.search;
    const contractActive = (progressContract.queuedUnits ?? 0)
      + (progressContract.runningUnits ?? 0)
      + (progressContract.activeUnits ?? 0);
    const contractTerminal = progressContract.requestedUnits != null
      && progressContract.requestedUnits > 0
      && progressContract.terminalUnits != null
      && progressContract.terminalUnits >= progressContract.requestedUnits
      && contractActive === 0;
    const contractPhase: SearchSessionProgress["phase"] = progressContract.blockedByWorker
      ? "blocked"
      : ["running", "active"].includes(progressContract.state)
        ? "enriching"
        : progressContract.state === "queued" || progressContract.state === "planned"
          ? "queued"
          : progressContract.state === "ready"
            ? "complete"
            : progressContract.state === "partial"
              ? "partial"
              : ["failed", "cancelled", "canceled"].includes(progressContract.state)
                ? "failed"
                : phase;
    const profileFailedContract = (profileStage.counts.failed ?? 0) + (profileStage.counts.partial ?? 0);
    const targetContract = searchStage.population ?? searchStage.requested ?? target;
    const requestedDownstream = ["video", "comments", "audience"]
      .some((key) => (progressContract.stages[key as "video" | "comments" | "audience"].requested ?? 0) > 0);
    return {
      phase: contractPhase,
      phaseLabel: contractPhase === "blocked"
        ? "Worker 阻塞"
        : contractPhase === "complete" && progressContract.fullAnalysisComplete
          ? "完整分析已完成"
          : phaseLabel[contractPhase],
      target: targetContract,
      basicVisible: searchStage.dataReady ?? searchStage.successful ?? 0,
      profileReady: profileStage.dataReady ?? profileStage.successful ?? 0,
      profileCompleted: profileStage.terminal ?? 0,
      profileSucceeded: profileStage.successful ?? 0,
      profileFailed: profileFailedContract,
      profileRemaining: profileStage.remaining ?? 0,
      currentItem,
      deepReady: progressContract.fullAnalysisComplete ? targetContract : 0,
      deepPartial: 0,
      failed: progressContract.failedUnits ?? 0,
      accounted: progressContract.terminalUnits ?? 0,
      downstreamTracked: requestedDownstream,
      video: contractStage("video"),
      comments: contractStage("comments"),
      audience: contractStage("audience"),
      completionContractExplicit: true,
      baseComplete: Boolean(
        searchStage.requested != null
        && searchStage.requested > 0
        && searchStage.successful != null
        && searchStage.successful >= searchStage.requested
      ),
      requestedTasksTerminal: contractTerminal,
      fullAnalysisComplete: progressContract.fullAnalysisComplete,
      // v1 contract intentionally does not assert business decision eligibility.
      decisionEligible: false,
      requiredTasksComplete: contractTerminal,
      contract: progressContract,
    };
  }
  return {
    phase,
    phaseLabel: phaseLabel[phase],
    target,
    basicVisible,
    profileReady,
    profileCompleted,
    profileSucceeded,
    profileFailed,
    profileRemaining,
    currentItem,
    deepReady: ready,
    deepPartial: partial,
    failed,
    accounted,
    downstreamTracked,
    video,
    comments,
    audience,
    completionContractExplicit,
    baseComplete,
    requestedTasksTerminal,
    fullAnalysisComplete,
    decisionEligible,
    requiredTasksComplete,
    contract: null,
  };
}
