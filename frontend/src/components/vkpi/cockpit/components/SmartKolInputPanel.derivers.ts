// SmartKolInputPanel 纯派生器：只读会话/payload，绝不写 viltrox_fit_score。
import {
  type VkpiKolRecallItem, type VkpiKolRecallResponse, type VkpiKolSearchHistoryItem, type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import {
  asRecord,
  cleanText,
  display,
  numberLabel,
  terminalSessionStatus,
  type Mode,
  type Row,
} from "./SmartKolInputPanel.helpers";
import { searchProgressContractFromSession } from "./SmartKolInputPanel.progress-derivers";
import { sanitizeSearchDisplayForCache } from "./SmartKolInputPanel.cachePrivacy";
import {
  recallCandidateDistribution,
  recallCandidateFacets,
  recallMatchEvidence,
} from "./SmartKolInputPanel.evidence";

export * from "./SmartKolInputPanel.progress-derivers";
export { sanitizeSearchDisplayForCache } from "./SmartKolInputPanel.cachePrivacy";

export const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";
// 刀2·流2 路A:贴账号 URL 自动分析的代表视频条数(dossier 据此出 LLM 账号分)。2 = 信号与成本/排队的折中;
// 想更深可继续发现历史视频并批量分析。代表视频走交互优先(并发A tier0)插队,不被批量饿死。
export const PROFILE_REP_VIDEO_LIMIT = 2;
// 搜索展示态持久化:把当前激活搜索的 ①②③ 显示态存进 sessionStorage,挂载时回填。
// 即便父级 90s/10min 刷新偶发重挂本面板(useState 归零),也能恢复结果,不让用户的搜索凭空消失。
const ACTIVE_SEARCH_DISPLAY_KEY = "vkpi:activeKolSearchDisplay";

// 搜索展示态的持久化形状(只存渲染所需,够回填 ①②③ 与轮询续接)。
export type PersistedSearchDisplay = {
  input: string;
  mode: Mode;
  recallResult: VkpiKolRecallResponse | null;
  urlResult: VkpiKolUrlDeepCrawlResponse | null;
  activeSearchSession: VkpiKolSearchHistoryItem | null;
  activeSearchSessionId: number | null;
};

export function readPersistedSearchDisplay(): PersistedSearchDisplay | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_SEARCH_DISPLAY_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedSearchDisplay;
    if (!parsed || typeof parsed !== "object") return null;
    const sanitized = sanitizeSearchDisplayForCache(parsed);
    // A terminal historical search may never poll again after page reload. Apply both the
    // contact-value cache projection and the field-level metric truth contract before render.
    const safeDisplay = {
      ...sanitized,
      recallResult: sanitized.recallResult
        ? sanitizeKolRecallSnapshot(sanitized.recallResult)
        : null,
    };
    window.sessionStorage.setItem(ACTIVE_SEARCH_DISPLAY_KEY, JSON.stringify(safeDisplay));
    return safeDisplay;
  } catch {
    return null;
  }
}

export function writePersistedSearchDisplay(value: PersistedSearchDisplay | null): void {
  if (typeof window === "undefined") return;
  try {
    if (!value) {
      window.sessionStorage.removeItem(ACTIVE_SEARCH_DISPLAY_KEY);
      return;
    }
    window.sessionStorage.setItem(
      ACTIVE_SEARCH_DISPLAY_KEY,
      JSON.stringify(sanitizeSearchDisplayForCache(value)),
    );
  } catch {
    // 配额/隐私模式失败时静默:持久化只是兜底,失败不影响实时搜索。
  }
}

export function recallTopItems(response: VkpiKolRecallResponse | null): VkpiKolRecallItem[] {
  if (!response) return [];
  // 30 人是筛选后的可见结果合同，不只是后端诊断数字。优先使用服务端已经
  // 排序好的 canonical items；旧响应没有 items 时才完整拼接两个兼容桶。
  const items = Array.isArray(response.items) ? response.items : [];
  if (items.length) return items;
  const buckets = asRecord(response.buckets);
  const names = ["creator", "reviewer", "unknown", ...Object.keys(buckets).filter((name) => !["creator", "reviewer", "unknown"].includes(name))];
  return names.flatMap((name) => Array.isArray(buckets[name]) ? buckets[name] as VkpiKolRecallItem[] : []);
}

export function historySessionId(value: unknown): number | undefined {
  const record = asRecord(value);
  const raw = record.id ?? record.session_id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export function sessionItems(session: VkpiKolSearchHistoryItem): Row[] {
  const items = Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.active_items) && session.active_items.length
      ? session.active_items
    : Array.isArray(session.items_preview)
      ? session.items_preview
      : [];
  return items.map((item) => asRecord(item));
}

function optionalSessionNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function recallBucket(value: unknown): string {
  return cleanText(value) || "unknown";
}

function replayMetricValue(payload: Row, field: string, replayComplete: boolean): number | null {
  const value = optionalSessionNumber(payload[field]);
  if (value == null || !replayComplete) return null;
  const truthFields = asRecord(asRecord(payload.data_truth).fields);
  const receipt = asRecord(truthFields[field]);
  return receipt.displayable === true ? value : null;
}

function bucketRecallItems(items: VkpiKolRecallItem[]): VkpiKolRecallResponse["buckets"] {
  const buckets: Record<string, VkpiKolRecallItem[]> = { creator: [], reviewer: [] };
  items.forEach((item) => {
    const bucket = recallBucket(item.bucket);
    (buckets[bucket] ||= []).push(item);
  });
  return buckets as VkpiKolRecallResponse["buckets"];
}

const RECALL_NUMERIC_TRUTH_FIELDS = [
  "followers",
  "avg_views",
  "avg_likes",
  "avg_comments",
  "engagement_rate",
  "real_er",
] as const;

function truthDisplayable(dataTruth: unknown, field: string): boolean {
  const fields = asRecord(asRecord(dataTruth).fields);
  return asRecord(fields[field]).displayable === true;
}

function sanitizeRepresentativeEvidence(value: unknown): Row[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const raw = asRecord(entry);
    const next: Row = { ...raw };
    for (const field of ["view_count", "like_count", "comment_count", "share_count"] as const) {
      if (!truthDisplayable(raw.data_truth, field)) next[field] = null;
    }
    const url = cleanText(raw.content_url || raw.url);
    const hasVisibleMetric = ["view_count", "like_count", "comment_count", "share_count"]
      .some((field) => {
        const numeric = Number(next[field]);
        return Number.isFinite(numeric) && numeric >= 0 && truthDisplayable(raw.data_truth, field);
      });
    return url && hasVisibleMetric ? [next] : [];
  });
}

/**
 * Re-apply the backend truth contract to any client-side snapshot before it reaches the UI.
 * This is a defense-in-depth boundary for older sessionStorage payloads; it never upgrades a
 * missing receipt into a fact and it does not mutate the durable history row.
 */
export function sanitizeKolRecallSnapshot(response: VkpiKolRecallResponse): VkpiKolRecallResponse {
  const items = recallTopItems(response).map((item) => {
    const row = item as unknown as Row;
    const next: Row = { ...row };
    const sourceFields: Row = { ...asRecord(row.source_fields) };
    RECALL_NUMERIC_TRUTH_FIELDS.forEach((field) => {
      const fieldWasPresent = Object.prototype.hasOwnProperty.call(row, field)
        || Object.prototype.hasOwnProperty.call(sourceFields, field);
      if (!truthDisplayable(row.data_truth, field) && Object.prototype.hasOwnProperty.call(row, field)) {
        next[field] = null;
      }
      if (fieldWasPresent) sourceFields[field] = next[field] ?? null;
    });
    if (!truthDisplayable(row.data_truth, "real_er")) {
      next.real_er_sample_n = null;
      next.real_er_computed_at = null;
      next.real_er_method = null;
    }
    next.exposure_potential = next.avg_views ?? null;
    next.representative_evidence = sanitizeRepresentativeEvidence(row.representative_evidence);

    const audienceReceipt = asRecord(asRecord(row.data_truth).fields).audience_estimated;
    if (
      asRecord(audienceReceipt).displayable !== true
      && Object.prototype.hasOwnProperty.call(sourceFields, "audience_preview")
    ) sourceFields.audience_preview = null;
    next.source_fields = sourceFields;
    return next as unknown as VkpiKolRecallItem;
  });
  return {
    ...response,
    items,
    buckets: bucketRecallItems(items),
  };
}

function meaningfulSnapshotValue(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return cleanText(value).length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value as Row).length > 0;
  return true;
}

const SESSION_STATUS_STRENGTH: Record<string, number> = {
  planned: 1,
  queued: 2,
  already_queued: 2,
  identified: 3,
  matched: 3,
  running: 4,
  processing: 4,
  partial: 5,
  failed: 5,
  blocked: 5,
  cancelled: 5,
  canceled: 5,
  ready: 6,
  done: 6,
};

function strongerSnapshotStatus(previous: unknown, incoming: unknown): unknown {
  const previousText = cleanText(previous).toLowerCase();
  const incomingText = cleanText(incoming).toLowerCase();
  if (!incomingText) return previous;
  if (!previousText) return incoming;
  return (SESSION_STATUS_STRENGTH[incomingText] ?? 0) >= (SESSION_STATUS_STRENGTH[previousText] ?? 0)
    ? incoming
    : previous;
}

/**
 * Merge a later polling snapshot without allowing sparse provider payloads to erase fields that
 * are already visible. Objects are merged recursively, empty arrays/strings are ignored, and a
 * ready/done state cannot regress to partial/running on an out-of-order response.
 */
export function mergeSearchSnapshotRecord(previous: Row, incoming: Row): Row {
  const merged: Row = { ...previous };
  Object.entries(incoming).forEach(([key, value]) => {
    if (key === "status" || key === "advance_status" || key === "last_status") {
      merged[key] = strongerSnapshotStatus(previous[key], value);
      return;
    }
    if (!meaningfulSnapshotValue(value)) return;
    const previousValue = previous[key];
    if (Array.isArray(value) && Array.isArray(previousValue)) {
      // Provider snapshots can temporarily return only the first N tags/posts. Keep the richer
      // array; equal-size incoming arrays may contain fresher values and are allowed through.
      if (value.length >= previousValue.length) merged[key] = value;
      return;
    }
    if (
      value && typeof value === "object" && !Array.isArray(value) &&
      previousValue && typeof previousValue === "object" && !Array.isArray(previousValue)
    ) {
      merged[key] = mergeSearchSnapshotRecord(asRecord(previousValue), asRecord(value));
      return;
    }
    merged[key] = value;
  });
  return merged;
}

// 重复卡修(2026-07-21 sky_vanya 案):同一会话项的身份键会随后端回填而「变形」——档案补全把
// kol_pool_id 写上后,旧单键法(pool 优先)从 profile:平台:handle 变成 pool:id,后到快照匹配不上
// 旧行 → 同一人被 push 成第二张卡。改产**全部别名键**(pool/profile/url/item),命中任一即合并,
// 合并后把新增别名(如补上的 pool id)也注册到同一行,身份变形不再裂成两行。
function searchSnapshotItemKeys(value: Row): string[] {
  const payload = asRecord(value.payload);
  const keys: string[] = [];
  const poolId = Number(value.kol_pool_id || payload.kol_pool_id || 0);
  if (Number.isFinite(poolId) && poolId > 0) keys.push(`pool:${poolId}`);
  const platform = cleanText(value.platform || payload.platform).toLowerCase();
  const handle = cleanText(value.handle || payload.handle || payload.channel_name)
    .toLowerCase()
    .replace(/^@/, "");
  if (platform && handle) keys.push(`profile:${platform}:${handle}`);
  const sourceUrl = cleanText(value.source_url || payload.source_url || payload.profile_url || payload.channel_url).toLowerCase();
  if (sourceUrl) keys.push(`url:${sourceUrl}`);
  const itemId = Number(value.id || 0);
  if (itemId > 0) keys.push(`item:${itemId}`);
  return keys;
}

export function mergeSearchSnapshotItems(previous: Row[], incoming: Row[]): Row[] {
  const merged = previous.map((item) => ({ ...item }));
  const indexByKey = new Map<string, number>();
  const registerAliases = (item: Row, index: number) => {
    searchSnapshotItemKeys(item).forEach((key) => {
      if (!indexByKey.has(key)) indexByKey.set(key, index);
    });
  };
  merged.forEach(registerAliases);
  incoming.forEach((item) => {
    const keys = searchSnapshotItemKeys(item);
    let existingIndex: number | undefined;
    for (const key of keys) {
      const idx = indexByKey.get(key);
      if (idx != null) { existingIndex = idx; break; }
    }
    if (existingIndex == null) {
      merged.push({ ...item });
      registerAliases(item, merged.length - 1);
      return;
    }
    merged[existingIndex] = mergeSearchSnapshotRecord(merged[existingIndex], item);
    // 合并后重新注册:身份变形补上的新别名(pool id/source_url)也指向同一行。
    registerAliases(merged[existingIndex], existingIndex);
  });
  return merged;
}

function mergeAuthoritativeSearchSnapshotItems(previous: Row[], incoming: Row[]): Row[] {
  // Preserve richer fields only for identities that remain in the authoritative list. Rows absent
  // from the incoming snapshot were deliberately hidden/removed and must not be unioned back in.
  const previousByKey = new Map<string, Row>();
  previous.forEach((item) => searchSnapshotItemKeys(item).forEach((key) => {
    if (!previousByKey.has(key)) previousByKey.set(key, item);
  }));
  return incoming.map((item) => {
    const previousItem = searchSnapshotItemKeys(item)
      .map((key) => previousByKey.get(key))
      .find((value): value is Row => Boolean(value));
    return previousItem ? mergeSearchSnapshotRecord(previousItem, item) : { ...item };
  });
}

function onlineSnapshotRevision(session: VkpiKolSearchHistoryItem | null): number {
  const contract = asRecord(asRecord(session?.result_summary).online_qualification);
  if (cleanText(contract.schema) !== "smart_online_net_new_qualified_v1") return 0;
  const revision = Number(contract.snapshot_revision);
  return Number.isInteger(revision) && revision > 0 ? revision : 0;
}

function preservePreviousOnlineLane(previous: Row[], merged: Row[]): Row[] {
  const notOnline = merged.filter((item) => cleanText(item.item_type) !== "online_qualified_candidate");
  const previousOnline = previous.filter((item) => cleanText(item.item_type) === "online_qualified_candidate");
  return [...notOnline, ...previousOnline];
}

/** Keep the richest version of every KOL while still accepting fresh phase/count metadata. */
export function mergeKolSearchSessionSnapshots(
  previous: VkpiKolSearchHistoryItem | null,
  incoming: VkpiKolSearchHistoryItem,
): VkpiKolSearchHistoryItem {
  if (!previous) return incoming;
  const merged = mergeSearchSnapshotRecord(previous as Row, incoming as Row) as VkpiKolSearchHistoryItem;
  const authoritative = incoming.items_snapshot_complete === true;
  const previousOnlineRevision = onlineSnapshotRevision(previous);
  const incomingOnlineRevision = onlineSnapshotRevision(incoming);
  const staleOnlineSnapshot = previousOnlineRevision > 0 && incomingOnlineRevision < previousOnlineRevision;
  const mergeCollection = (key: "items" | "active_items" | "items_preview") => {
    const previousItems = Array.isArray(previous[key]) ? previous[key]!.map(asRecord) : [];
    const incomingItems = Array.isArray(incoming[key]) ? incoming[key]!.map(asRecord) : [];
    if (!previousItems.length && !incomingItems.length) return;
    const nextItems = (
      authoritative
        ? mergeAuthoritativeSearchSnapshotItems(previousItems, incomingItems)
        : mergeSearchSnapshotItems(previousItems, incomingItems)
    );
    merged[key] = (staleOnlineSnapshot
      ? preservePreviousOnlineLane(previousItems, nextItems)
      : nextItems) as VkpiKolSearchHistoryItem[typeof key];
  };
  mergeCollection("items");
  mergeCollection("active_items");
  mergeCollection("items_preview");
  if (staleOnlineSnapshot) {
    const summary = asRecord(merged.result_summary);
    summary.online_qualification = asRecord(previous.result_summary).online_qualification;
    merged.result_summary = summary;
  }
  const mergedVisible = sessionItems(merged).length;
  merged.item_count = authoritative
    ? mergedVisible
    : Math.max(Number(previous.item_count) || 0, Number(incoming.item_count) || 0, mergedVisible);
  return merged;
}

const TRUTH_AUTHORITATIVE_RECALL_FIELDS = [
  "followers",
  "avg_views",
  "avg_likes",
  "avg_comments",
  "engagement_rate",
  "real_er",
  "real_er_sample_n",
  "real_er_computed_at",
  "real_er_method",
  "audience_estimated",
  "audience_estimated_json",
  "brand_collaborations_json",
  "brand_collaborations_factual_json",
  "representative_evidence",
  "data_truth",
] as const;

function mergeRecallItemLists(previous: VkpiKolRecallItem[], incoming: VkpiKolRecallItem[]): VkpiKolRecallItem[] {
  const incomingRows = incoming as unknown as Row[];
  const mergedRows = mergeSearchSnapshotItems(previous as unknown as Row[], incomingRows);
  const latestByIdentity = new Map<string, Row>();
  incomingRows.forEach((item) => searchSnapshotItemKeys(item).forEach((key) => latestByIdentity.set(key, item)));

  return mergedRows.map((item) => {
    const latest = searchSnapshotItemKeys(item)
      .map((key) => latestByIdentity.get(key))
      .find(Boolean);
    if (!latest) return item;

    // The generic polling merge deliberately ignores nulls so sparse provider snapshots cannot
    // erase useful fields. Truth-gated KOL metrics are the exception: a newer receipt may revoke
    // a legacy/default value, and that null must delete the stale browser/session copy.
    const replayIsIncomplete = latest.session_replay_complete === false;
    const hasTruthContract = replayIsIncomplete || Object.prototype.hasOwnProperty.call(latest, "data_truth");
    if (!hasTruthContract) return item;

    const next: Row = { ...item };
    TRUTH_AUTHORITATIVE_RECALL_FIELDS.forEach((field) => {
      if (replayIsIncomplete || Object.prototype.hasOwnProperty.call(latest, field)) {
        next[field] = latest[field] ?? null;
      }
    });

    // Candidate cards historically fell back to source_fields for average metrics. Keep those
    // aliases synchronized with the authoritative top-level projection so they cannot resurrect
    // a value that the latest truth contract suppressed.
    const nextSourceFields = { ...asRecord(next.source_fields) };
    for (const field of ["followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate", "real_er"] as const) {
      if (replayIsIncomplete || Object.prototype.hasOwnProperty.call(latest, field)) {
        nextSourceFields[field] = latest[field] ?? null;
      }
    }
    if (replayIsIncomplete) nextSourceFields.audience_preview = null;
    next.source_fields = nextSourceFields;
    return next;
  }) as unknown as VkpiKolRecallItem[];
}

function mergeAuthoritativeRecallItemLists(
  previous: VkpiKolRecallItem[],
  incoming: VkpiKolRecallItem[],
): VkpiKolRecallItem[] {
  const mergedByKey = new Map<string, VkpiKolRecallItem>();
  mergeRecallItemLists(previous, incoming).forEach((item) => {
    searchSnapshotItemKeys(item as unknown as Row).forEach((key) => mergedByKey.set(key, item));
  });
  return incoming.map((item) => {
    const merged = searchSnapshotItemKeys(item as unknown as Row)
      .map((key) => mergedByKey.get(key))
      .find(Boolean);
    return merged || item;
  });
}

export function mergeKolRecallSnapshots(
  previous: VkpiKolRecallResponse | null,
  incoming: VkpiKolRecallResponse,
): VkpiKolRecallResponse {
  if (!previous) return incoming;
  // Canonical ``items`` owns cross-bucket rank order.  Bucket-by-bucket merge
  // used to regroup creator/reviewer rows and silently discard ``unknown``. A complete
  // server snapshot is authoritative for membership and rank, while still retaining richer
  // fields for identities that remain present.
  const previousItems = recallTopItems(previous);
  const incomingItems = recallTopItems(incoming);
  const items = incoming.snapshot_complete === true
    ? mergeAuthoritativeRecallItemLists(previousItems, incomingItems)
    : mergeRecallItemLists(previousItems, incomingItems);
  return {
    ...previous,
    ...incoming,
    query: mergeSearchSnapshotRecord(asRecord(previous.query), asRecord(incoming.query)),
    ratio: mergeSearchSnapshotRecord(asRecord(previous.ratio), asRecord(incoming.ratio)) as unknown as VkpiKolRecallResponse["ratio"],
    diagnostics: mergeSearchSnapshotRecord(asRecord(previous.diagnostics), asRecord(incoming.diagnostics)),
    buckets: bucketRecallItems(items),
    items,
    ...((previous as unknown as Row).llm_query_plan && !(incoming as unknown as Row).llm_query_plan
      ? { llm_query_plan: (previous as unknown as Row).llm_query_plan }
      : {}),
  } as VkpiKolRecallResponse;
}

export function sessionAdvanceCounts(session: VkpiKolSearchHistoryItem | null): Row {
  const summary = asRecord(session?.result_summary);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return asRecord(batch.counts || smartJob.advance_counts);
}

// 【K3 正账】本次发现的真实自动入库数:后端 discover_new_creators 把 _auto_enroll_discoveries
// 的返回值记进 counts.auto_enrolled,attach_new_discovery_result 原样透传进会话
// result_summary.new_discovery.counts。旧会话/旧后端没有该键 → 返回 null(前端回退到概述文案)。
export function discoveryAutoEnrolledFromSession(session: VkpiKolSearchHistoryItem | null): number | null {
  const summary = asRecord(session?.result_summary);
  const counts = asRecord(asRecord(summary.new_discovery).counts);
  const value = counts.auto_enrolled;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// 品牌官号排除数(诚实信息):后端 discover_new_creators 把竞品品牌官方账号挡在发现结果外,
// counts.excluded_brand_official 记真实排除数;旧会话/旧后端无该键 → 0(静默不渲染,不编数字)。
export function discoveryBrandExcludedFromSession(session: VkpiKolSearchHistoryItem | null): number {
  const summary = asRecord(session?.result_summary);
  const counts = asRecord(asRecord(summary.new_discovery).counts);
  const value = Number(counts.excluded_brand_official);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

export function isSearchSessionTerminal(session: VkpiKolSearchHistoryItem): boolean {
  const contract = searchProgressContractFromSession(session);
  if (contract) {
    const active = (contract.queuedUnits ?? 0) + (contract.runningUnits ?? 0) + (contract.activeUnits ?? 0);
    if (active > 0 || contract.blockedByWorker) return false;
    if (contract.requestedUnits != null && contract.requestedUnits > 0) {
      return contract.terminalUnits != null && contract.terminalUnits >= contract.requestedUnits;
    }
    return ["ready", "partial", "failed", "cancelled", "canceled"].includes(contract.state);
  }
  const summary = asRecord(session.result_summary);
  const progress = asRecord(summary.progress);
  const requestedTasksTerminal = progress.requested_tasks_terminal
    ?? summary.requested_tasks_terminal
    ?? progress.required_tasks_complete
    ?? summary.required_tasks_complete;
  if (typeof requestedTasksTerminal === "boolean") return requestedTasksTerminal;
  if (terminalSessionStatus(session.status)) return true;
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return terminalSessionStatus(batch.status) || terminalSessionStatus(smartJob.status) || terminalSessionStatus(smartJob.advance_status);
}

// 触达展示闸折叠计数(2026-07-12 第二道闸):后端 get_session/list_history 按 pool 现值实时
// 重判,被隐藏的项不再下发,只给诚实计数——hidden_low_reach=低触达不展示(已入库仅不推荐)、
// hidden_analyzing=档案补全中(「分析后再 po」,补全回填达标后自动放出)。旧后端无该键 → null,
// 前端静默不渲染(graceful absence,不编数字)。by_type 与卡片框位一致:
// new_creator/existing_kol 归框3,recall_candidate 归框2。
export function reachFloorDisplayFromSession(session: VkpiKolSearchHistoryItem | null): {
  discovery: { lowReach: number; analyzing: number };
  recall: { lowReach: number; analyzing: number };
} | null {
  const raw = session?.reach_floor_display;
  if (!raw || typeof raw !== "object") return null;
  const info = asRecord(raw);
  const byType = asRecord(info.by_type);
  const bucket = (key: string) => {
    const entry = asRecord(byType[key]);
    return {
      lowReach: Number(entry.hidden_low_reach) || 0,
      analyzing: Number(entry.hidden_analyzing) || 0,
    };
  };
  const newCreator = bucket("new_creator");
  const existing = bucket("existing_kol");
  const recall = bucket("recall_candidate");
  return {
    discovery: {
      lowReach: newCreator.lowReach + existing.lowReach,
      analyzing: newCreator.analyzing + existing.analyzing,
    },
    recall: {
      lowReach: recall.lowReach,
      analyzing: recall.analyzing,
    },
  };
}

export function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
  const ranked: Array<{ rank: number; item: VkpiKolRecallItem }> = [];
  const legacyMissingFields = [
    "match_tier",
    "candidate_bucket",
    "why_fit",
    "evidence_quality",
    "representative_evidence",
    "robust_rank_score",
    "unknown_fields",
    "data_truth",
  ];
  sessionItems(session).forEach((item) => {
    const payload = asRecord(item.payload);
    // 三框:框2 只放库内召回。旧实现仅排除 new_creator，会把 URL/unknown
    // 编排项误画成 KOL；滚动升级时只额外兼容带明确 v2 recall schema 的行。
    const itemType = cleanText(item.item_type);
    if (itemType !== "recall_candidate" && cleanText(payload.session_payload_schema) !== "kol_recall_candidate_v2") return;
    const bucket = recallBucket(payload.bucket);
    const replayContract = cleanText(payload.session_payload_schema) || "legacy_incomplete";
    const replayComplete = replayContract === "kol_recall_candidate_v2" && payload.session_replay_complete === true;
    const missingReplayFields = replayComplete
      ? []
      : legacyMissingFields.filter((field) => !(field in payload));
    const persistedScore = optionalSessionNumber(item.score);
    const payloadSource = asRecord(payload.source_fields);
    const matchEvidence = recallMatchEvidence(payload.match_evidence);
    const replayMetrics = {
      followers: replayMetricValue(payload, "followers", replayComplete),
      avg_views: replayMetricValue(payload, "avg_views", replayComplete),
      avg_likes: replayMetricValue(payload, "avg_likes", replayComplete),
      avg_comments: replayMetricValue(payload, "avg_comments", replayComplete),
      engagement_rate: replayMetricValue(payload, "engagement_rate", replayComplete),
      real_er: replayMetricValue(payload, "real_er", replayComplete),
    };
    const replaySourceFields: Row = {
      ...payload,
      ...payloadSource,
      ...replayMetrics,
      session_replay_contract: replayContract,
      session_replay_complete: replayComplete,
      session_replay_missing_fields: missingReplayFields,
    };
    if (!replayComplete) replaySourceFields.audience_preview = null;
    const row = {
      ...payload,
      bucket,
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: cleanText(payload.handle || payload.display_name || payload.channel_name),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type, "unknown"),
      // 历史指标也必须通过与实时搜索相同的字段级 truth receipt。旧 v1 会话
      // 没有来源合同，只保留身份/排序，不继续展示当年的无来源数字。
      ...replayMetrics,
      data_truth: replayComplete ? payload.data_truth : null,
      representative_evidence: replayComplete && Array.isArray(payload.representative_evidence)
        ? payload.representative_evidence
        : [],
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      robust_rank_score: optionalSessionNumber(payload.robust_rank_score),
      retrieval_score: optionalSessionNumber(payload.retrieval_score),
      recall_rank_score: optionalSessionNumber(payload.recall_rank_score) ?? persistedScore,
      vector_score: optionalSessionNumber(payload.vector_score),
      display_rank_score: optionalSessionNumber(payload.display_rank_score),
      type_rank_score: optionalSessionNumber(payload.type_rank_score ?? payload.type_score),
      relevance_flags: Array.isArray(payload.relevance_flags) ? (payload.relevance_flags as unknown[]).map(cleanText).filter(Boolean) : [],
      relevance_tier_hint: cleanText(payload.relevance_tier_hint),
      match_evidence: matchEvidence,
      candidate_facets: recallCandidateFacets(payload.candidate_facets),
      qualification_evidence: asRecord(payload.qualification_evidence),
      server_rank: Number(payload.server_rank ?? payload.global_rank ?? item.rank ?? 0) || undefined,
      global_rank: Number(payload.global_rank ?? payload.server_rank ?? item.rank ?? 0) || undefined,
      type_label: cleanText(payload.type_label) || (bucket === "reviewer" ? "测评号" : bucket === "creator" ? "创作者" : "未分类"),
      creator_type_score: optionalSessionNumber(payload.creator_type_score),
      reviewer_type_score: optionalSessionNumber(payload.reviewer_type_score),
      recall_reason: (matchEvidence.length || replayComplete)
        ? cleanText(payload.recall_reason || payload.evidence || payload.sample_title)
        : "",
      why_fit: (matchEvidence.length || replayComplete) ? cleanText(payload.why_fit || payload.evidence) : "",
      // 三引擎展示信号透传(纯只读;后端在会话项 payload 写入则亮起,否则静默不渲染)。
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? replayMetrics.avg_views ?? 0) || null,
      session_replay_contract: replayContract,
      session_replay_complete: replayComplete,
      session_replay_missing_fields: missingReplayFields,
      source_fields: replaySourceFields,
    } as VkpiKolRecallItem;
    ranked.push({
      rank: Number(payload.server_rank ?? payload.global_rank ?? item.rank ?? 0) || Number.MAX_SAFE_INTEGER,
      item: row,
    });
  });
  ranked.sort((a, b) => a.rank - b.rank);
  const items = ranked.map((entry) => entry.item);
  const summary = asRecord(session.result_summary);
  const querySummary = asRecord(summary.query);
  const diagnostics = asRecord(summary.diagnostics);
  const replayContract = asRecord(summary.replay_contract);
  const llmQueryPlan = asRecord(summary.llm_query_plan);
  const buckets = bucketRecallItems(items);
  const creator = buckets.creator || [];
  const reviewer = buckets.reviewer || [];
  const unknown = buckets.unknown || [];
  const incompleteCount = items.filter((item) => item.session_replay_complete !== true).length;
  const replaySchema = cleanText(replayContract.schema);
  const replayComplete = replayContract.complete === true && incompleteCount === 0;
  // 空的旧会话没有 item 可供 incompleteCount 识别，不能因此伪装成 v2 完整回放。
  const resolvedReplayContract = replaySchema || (replayComplete ? "kol_recall_candidate_v2" : "legacy_incomplete");
  return {
    method: cleanText(summary.method) || "search_session_history",
    query: { query_text: display(querySummary.query_text || summary.query || session.query_text, "") },
    ratio: {
      creator_quota: Number(asRecord(summary.ratio).creator_quota ?? creator.length),
      reviewer_quota: Number(asRecord(summary.ratio).reviewer_quota ?? reviewer.length),
      policy: cleanText(asRecord(summary.ratio).policy) || "history",
      mixed_policy: cleanText(asRecord(summary.ratio).mixed_policy) || "history",
      dedupe: asRecord(summary.ratio).dedupe !== false,
    },
    items,
    buckets: {
      creator,
      reviewer,
      ...(unknown.length ? { unknown } : {}),
    },
    diagnostics: {
      ...diagnostics,
      candidate_count: Number(diagnostics.candidate_count ?? items.length),
      creator_returned: Number(diagnostics.creator_returned ?? creator.length),
      reviewer_returned: Number(diagnostics.reviewer_returned ?? reviewer.length),
      unknown_returned: Number(diagnostics.unknown_returned ?? unknown.length),
      returned_count: items.length,
      session_replay_contract: resolvedReplayContract,
      session_replay_complete: replayComplete,
      session_replay_incomplete_count: incompleteCount,
      session_replay_source_count: Number(replayContract.source_count ?? items.length),
      session_replay_persisted_count: Number(replayContract.persisted_count ?? items.length),
    },
    evaluation_status: asRecord(summary.evaluation_status),
    match_status: cleanText(summary.match_status),
    candidate_set_distribution: recallCandidateDistribution(summary.candidate_set_distribution),
    ...(Object.keys(asRecord(summary.local_qualification)).length
      ? { local_qualification: asRecord(summary.local_qualification) }
      : {}),
    ...(Object.keys(llmQueryPlan).length ? { llm_query_plan: llmQueryPlan } : {}),
    snapshot_complete: Boolean(
      (session as unknown as Row).recall_snapshot_complete === true
      || summary.recall_snapshot_complete === true,
    ),
  } satisfies VkpiKolRecallResponse;
}

// 滤掉零售/经销实体(B&H / Pro Audio / camera store 等)——不是真创作者,挂出来没意义。
export function looksLikeRetailer(item: any): boolean {
  const sf = (item && item.source_fields && typeof item.source_fields === "object") ? item.source_fields : {};
  const hay = `${item?.handle || ""} ${item?.display_name || ""} ${item?.why_fit || ""} ${sf.bio || sf.description || ""}`.toLowerCase();
  return /\bb\s*&\s*h\b|b\s*and\s*h|pro\s*audio|photo\s*video|camera\s*(store|house|shop|world|land)|rental|retailer|wholesale|distributor|旗舰店|专卖|经销/.test(hay);
}

// 三框·框3:从会话抽 new_creator(Apify+平台发现)项,带头像/用户名/平台。
// 重复卡修·渲染层兜底:同平台同 handle(小写去 @,退 profile_url)只出一张卡;后到重复项若带
// kol_pool_id 而首张缺,则把 pool id 并给首张(勾选可直连),位置保持首见序。
export function discoveryItemsFromSession(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  const all = discoveryItemsFromSessionRaw(session);
  const out: VkpiKolRecallItem[] = [];
  const indexByIdentity = new Map<string, number>();
  all.forEach((item) => {
    const handle = cleanText(item.handle).toLowerCase().replace(/^@/, "");
    const platform = cleanText(item.platform).toLowerCase();
    const identity = handle && handle !== "unknown"
      ? `${platform}:${handle}`
      : cleanText(item.profile_url).toLowerCase();
    const existingIndex = identity ? indexByIdentity.get(identity) : undefined;
    if (existingIndex == null) {
      if (identity) indexByIdentity.set(identity, out.length);
      out.push(item);
      return;
    }
    const kept = out[existingIndex];
    if (!Number(kept.kol_pool_id) && Number(item.kol_pool_id)) {
      out[existingIndex] = { ...kept, kol_pool_id: item.kol_pool_id };
    }
  });
  return out;
}

function discoveryItemsFromSessionRaw(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  if (!session) return [];
  const out: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
    const itemType = cleanText(item.item_type);
    if (itemType !== "new_creator" && itemType !== "existing_kol") return;
    const payload = asRecord(item.payload);
    out.push({
      bucket: "creator",
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || "creator", "creator"),
      // 粉丝身份只接受明确的 follower/subscriber 字段。avg_views/views 是内容表现，
      // 绝不能在缺粉丝时冒充粉丝数，否则会同时污染卡片身份和触达筛选。
      followers: Number(payload.followers || payload.follower_count || payload.subscriber_count || payload.subscribers || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: itemType === "existing_kol" ? "库内已有" : "全网发现",
      creator_type_score: 1,
      reviewer_type_score: 0,
      recall_reason: cleanText(payload.sample_title || payload.evidence),
      why_fit: cleanText(payload.why_fit || payload.sample_title),
      // 三引擎展示信号(发现项:exposure 用 avg_views/views;published/historical_match 透 source_fields 供新人/新鲜判定)。
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? payload.views ?? 0) || null,
      source_fields: payload,
    } as VkpiKolRecallItem);
  });
  return out;
}

export function urlResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolUrlDeepCrawlResponse | null {
  const item = sessionItems(session).find((entry) => cleanText(entry.item_type).startsWith("url_")) || sessionItems(session)[0];
  if (!item) return null;
  const payload = asRecord(item.payload);
  const summary = asRecord(session.result_summary);
  const videoFlow = asRecord(payload.video_flow);
  const profileFlow = asRecord(payload.profile_flow);
  const profileExecute = asRecord(payload.profile_execute);
  const executeProfileData = asRecord(profileExecute.profile_data);
  const flowProfileData = asRecord(profileFlow.profile_data);
  // 历史 item 的后台回填写入 payload.profile_execute；初始 URL attach 则写 payload.profile_flow。
  // 合并后保持 profile_flow 顶层优先，同时不能丢掉 execute 阶段补齐的头像/粉丝/帖数/简介。
  const mergedProfileData = { ...executeProfileData, ...flowProfileData };
  const mergedProfileFlow = {
    ...profileExecute,
    ...profileFlow,
    ...(Object.keys(mergedProfileData).length ? { profile_data: mergedProfileData } : {}),
  };
  const analysis = asRecord(payload.analysis || summary.analysis);
  const jobLastError = cleanText(payload.job_last_error || summary.job_last_error || payload.search_session_last_error);
  const jobStatus = cleanText(payload.job_status || payload.search_session_last_job_status || item.status);
  // 旧空壳会话的 item.status 可能是 unknown(dry-run 从未执行):当空处理,
  // 让 payload 里保存的真实 flow 状态(如 provider_refresh_pending)透出来。
  const rawItemStatus = cleanText(item.status || summary.item_status || videoFlow.status || profileFlow.status);
  const itemStatus = rawItemStatus === "unknown" ? "" : rawItemStatus;
  const urlType = cleanText(payload.url_type || session.query_type).includes("video") ? "video" : cleanText(payload.url_type || session.query_type).includes("profile") ? "profile" : "unknown";
  const terminal = terminalSessionStatus(session.status) || terminalSessionStatus(itemStatus);
  const nextAction = jobLastError
    ? "主任务已同步；部分富化/分析有错误，可查看错误并重试。"
    : terminal
      ? "任务已完成，结果已回填。"
      : "任务正在队列中运行，完成后会自动回填。";
  return {
    method: "search_session_history",
    // 诚实回放:只有摘要明说 execute 过、或 item 真在排队/运行,才算已执行。
    // 旧逻辑把「会话已终态」也当成已执行——dry-run 空壳会话(ready+从未执行)
    // 会被误标 execute=true → 前端渲染假「深析排队中」并禁用启动按钮。
    execute: Boolean(summary.execute || ["queued", "running", "already_queued"].includes(itemStatus)),
    url: {
      input: session.query_text,
      normalized: cleanText(item.source_url || session.query_text),
    },
    url_type: urlType,
    platform: cleanText(payload.platform),
    handle: cleanText(payload.handle),
    channel_id: cleanText(payload.channel_id),
    video_id: cleanText(payload.video_id),
    in_pool: Boolean(payload.in_pool || item.kol_pool_id),
    matched_kol_pool_id: Number(payload.matched_kol_pool_id || item.kol_pool_id) || null,
    next_action: nextAction,
    profile_flow: mergedProfileFlow,
    video_flow: {
      ...videoFlow,
      resolution_progress: asRecord(payload.video_url_resolution || videoFlow.resolution_progress),
      status: itemStatus || cleanText(videoFlow.status),
      evidence_id: Number(item.evidence_id || videoFlow.evidence_id) || null,
      job_last_error: jobLastError,
      job_status: jobStatus,
      analysis,
    },
    creator_identity: asRecord(payload.creator_identity),
    video_metadata: asRecord(payload.video_metadata),
    search_session: { id: session.id, session_id: session.id, status: session.status, item_status: itemStatus },
    safety: { viltrox_fit_score_untouched: Boolean(payload.viltrox_fit_score_untouched ?? summary.viltrox_fit_score_untouched) },
  };
}

// 全网发现状态码 → 人话(面向营销人,不暴露 queued/running 等内部状态码)。
export function advanceStatusLabel(value: unknown): string {
  const status = cleanText(value).toLowerCase();
  if (["ready", "done"].includes(status)) return "已完成";
  if (status === "partial") return "部分完成";
  if (["failed", "blocked"].includes(status)) return "未完成";
  if (status === "running") return "查找中";
  return "排队中";
}

// 异步会话状态横幅(诚实显示 排队/查找中/已完成/部分完成/未完成):只读后端真有的字段——
// 会话级 status(planned/running/ready/partial/failed/cancelled)+ result_summary
// .smart_search_profile_advance_job 的 {status, advance_status, advance_counts, error}。
// failed 时把后端 error 原文(异常串)收进 note,而非吞掉看似空白卡死。后端未单独返回「AI 规划
// 失败已退基础检索」原因字段(planner 失败被静默兜底),故只能据真实 status/error 说明,不编造。
type SessionBanner = { tone: "info" | "ok" | "warn" | "error"; label: string; note: string } | null;

export function sessionStatusBanner(
  session: VkpiKolSearchHistoryItem | null,
  advanceStatus: string,
  counts: Row,
  polling: boolean,
): SessionBanner {
  if (!session && !advanceStatus && !polling) return null;
  const summary = asRecord(session?.result_summary);
  const job = asRecord(summary.smart_search_profile_advance_job);
  const raw = cleanText(advanceStatus || job.advance_status || job.status || session?.status).toLowerCase();
  const jobError = cleanText(job.error);
  const ready = Number(counts.ready ?? 0);
  const failed = Number(counts.failed ?? 0) + Number(counts.errors ?? 0);
  if (["failed", "blocked"].includes(raw) && ready <= 0) {
    return { tone: "error", label: "这次没找到结果", note: jobError ? `失败原因:${jobError}` : "查找未能完成,可调整描述或换个区域重试。" };
  }
  if (["partial"].includes(raw) || (failed > 0 && ready > 0)) {
    return {
      tone: "warn",
      label: "已找到部分结果",
      note: failed > 0
        ? `下方结果可直接查看;另有 ${failed} 个没跑完,可稍后重试补齐。`
        : "下方结果可直接查看;部分人选还在补全,完成后会自动更新。",
    };
  }
  if (["ready", "done"].includes(raw)) {
    return { tone: "ok", label: "已找完", note: ready > 0 ? `共找到 ${ready} 个人选,见下方。` : "这次没有新的人选,可换个描述再试。" };
  }
  if (raw === "running" || polling) {
    return { tone: "info", label: "正在查找", note: "后台正从所选平台找人,找到的会随时显示,无需等待。" };
  }
  return { tone: "info", label: "已排队", note: "已进入后台查找队列,稍候会自动开始。" };
}

export function historyKindLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  if (type === "url_video") return "视频 URL";
  if (type === "url_profile") return "账号 URL";
  if (type === "text_recall") return "查找";
  return "历史";
}

// 历史标签可读化:文字搜索显查询语;URL 搜索把裸链接解析成「平台 @handle / 平台 帖id」,
// 让一堆长得一样的 instagram.com/p/... 能区分、看懂搜的是谁/什么(用户:历史要备注搜索信息)。
const _RESERVED_PATH = new Set(["p", "reel", "reels", "shorts", "video", "watch", "videos"]);

export function historyLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  // 数据里很多是 query_type='unknown' 且 query_text 形如「账号分析 · https://…」——
  // 去掉中文前缀、从文本里抠出 URL 再解析,而不是死认 query_type。
  const stripped = raw.replace(/^[一-龥A-Za-z]+\s*·\s*/, "").trim();
  const urlMatch = stripped.match(/https?:\/\/\S+/) || raw.match(/https?:\/\/\S+/);
  // 「账号分析」意图优先于路径启发式(如 /@x/shorts 是主页 Shorts 栏,不是单条视频)
  const isAccount = type === "url_profile" || /账号分析/.test(raw);
  const looksVideo = !isAccount && (type === "url_video" || /视频分析/.test(raw) || /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw));
  if (!urlMatch) return display(raw, "未命名"); // 纯文字搜索:原文
  try {
    const u = new URL(urlMatch[0]);
    const host = u.hostname.replace(/^www\./, "");
    const plat = host.includes("instagram")
      ? "IG"
      : host.includes("tiktok")
        ? "TikTok"
        : host.includes("youtu")
          ? "YouTube"
          : (host.split(".")[0] || "URL");
    const parts = u.pathname.split("/").filter(Boolean);
    const handleSeg = parts.find((p) => p.startsWith("@"));
    const firstNamed = parts[0] && !_RESERVED_PATH.has(parts[0]) ? parts[0] : "";
    // 账号/主页:有 @handle 或首段是用户名 → 「平台 @handle」
    if (!looksVideo && (handleSeg || firstNamed)) {
      return `${plat} @${(handleSeg || firstNamed).replace(/^@/, "")}`;
    }
    // 视频/帖:youtube 取 v 参数,其余取末段 id
    const vid = u.searchParams.get("v");
    const id = vid || parts[parts.length - 1] || firstNamed || "";
    return `${plat} ${looksVideo ? "视频" : "帖"} ${id}`.trim();
  } catch {
    return display(raw, "未命名");
  }
}

export function relativeTime(value: unknown): string {
  const s = cleanText(value);
  if (!s) return "";
  const t = Date.parse(s);
  if (!Number.isFinite(t)) return "";
  const diff = Date.now() - t;
  if (diff < 60000) return "刚刚";
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

export function historyKindMeta(session: VkpiKolSearchHistoryItem): { label: string; cls: string } {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  const video = { label: "视频", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100/90" };
  const account = { label: "账号", cls: "border-violet-300/30 bg-violet-400/[0.10] text-violet-100/90" };
  if (type === "url_video" || /视频分析/.test(raw)) return video;
  if (type === "url_profile" || /账号分析/.test(raw)) return account;
  if (type === "text_recall") return { label: "找人", cls: "border-cyan-300/30 bg-cyan-400/[0.10] text-cyan-100/90" };
  // query_type='unknown' 但文本含 URL:按视频路径/否则账号 兜底归类
  if (/https?:\/\//.test(raw)) return /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw) ? video : account;
  return { label: "历史", cls: "border-white/[0.1] bg-white/[0.03] text-slate-300" };
}

export function historyStatusMeta(value: unknown): { label: string; cls: string; dot: string } {
  const label = advanceStatusLabel(value);
  if (label === "已完成") return { label, cls: "text-emerald-300/85", dot: "#34d399" };
  if (label === "部分完成") return { label, cls: "text-amber-300/85", dot: "#fbbf24" };
  if (label === "查找中") return { label, cls: "text-amber-300/85", dot: "#fbbf24" };
  if (label === "未完成") return { label, cls: "text-rose-300/85", dot: "#fb7185" };
  return { label, cls: "text-slate-500", dot: "#64748b" };
}

// 历史卡状态:优先读会话级诚实终态。不支持的平台链接标「不支持」,
// 不再显示「部分完成/未完成」诱导用户等待或重试。
// (bilibili/抖音/小红书视频链接自 2026-07-20 起走「仅内容分析」通道,不再落 unsupported。)
export function historySessionStatusMeta(session: VkpiKolSearchHistoryItem): { label: string; cls: string; dot: string } {
  if (cleanText(asRecord(session.result_summary).result_state) === "unsupported") {
    return { label: "不支持", cls: "text-slate-500", dot: "#64748b" };
  }
  return historyStatusMeta(session.status || "ready");
}

// 问题5 UI:相关度按名次分档(列表已按分排序,名次=相关度强弱),裸 score 进 title 供细看,
// 避免向量分都 <0.5 时全显「相关」无区分。
// demote:后端 relevance_tier_hint==="demote"(如视频向产品×纯平面摄影候选)时,封顶为「中相关」,
// 绝不显「高相关」——纯展示分档,不动召回侧排序与任何评分字段。
export function relevanceTier(score: number, demote = false): { label: string; cls: string; dot: string } {
  // 按真实相关度分值(0-1)分档:高≥0.6 / 中≥0.3 / 相关。demote 封顶「中相关」绝不显「高相关」。纯展示。
  if (score >= 0.6 && !demote) return { label: "高相关", cls: "border-emerald-300/35 bg-emerald-400/[0.10] text-emerald-100", dot: "#34d399" };
  if (score >= 0.3) return { label: "中相关", cls: "border-cyan-300/30 bg-cyan-400/[0.07] text-cyan-100", dot: "#22d3ee" };
  return { label: "相关", cls: "border-white/[0.08] bg-white/[0.02] text-slate-400", dot: "#64748b" };
}

// 内容契合判定 → 徽章(纯展示信号,读会话项透传的 content_fit;绝不并入/改写 viltrox_fit_score）。
// 已深析才显;verdict ∈ {fit/partial_fit/not_fit} 映射 适合/一般/不适合,不可识别则不渲染。
export function contentFitBadge(value: unknown): { label: string; cls: string } | null {
  const verdict = cleanText(value).toLowerCase();
  if (verdict === "fit") return { label: "适合", cls: "border-emerald-300/35 bg-emerald-400/[0.12] text-emerald-100" };
  if (verdict === "not_fit") return { label: "不适合", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100" };
  if (verdict === "partial_fit") return { label: "一般", cls: "border-amber-300/30 bg-amber-400/[0.10] text-amber-100" };
  return null;
}

// 预估曝光(说人话):读会话项 exposure_potential / avg_views / views,折成 K/M 量级。
// 纯展示触达潜力(终极=提升曝光/市场),不参与任何评分。
export function exposureLabel(item: VkpiKolRecallItem): string {
  const self = item as unknown as Row;
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const raw = Number(
    self.exposure_potential ?? src.exposure_potential ?? src.avg_views ?? src.views ?? 0,
  );
  return numberLabel(raw);
}

// 新人/新鲜标(纯展示,优先新人裁令):
//  - newcomer:全网发现 new_creator(无 kol_pool_id 未入库)= 优先新人主源。
//  - fresh:近 90 天有新作(published 时间戳)。
//  - lowCollab:历史无合作记录(无 historical_match / cooperation 计数为 0)。
export function freshnessMarks(item: VkpiKolRecallItem): { newcomer: boolean; fresh: boolean; lowCollab: boolean } {
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const newcomer = !Number(item.kol_pool_id) && cleanText(item.type_label) === "全网发现";
  let fresh = false;
  const published = cleanText(src.published);
  if (published) {
    const ts = Date.parse(published);
    if (Number.isFinite(ts)) fresh = Date.now() - ts <= 90 * 24 * 3600 * 1000;
  }
  const coop = Number(src.history_cooperation_count ?? src.cooperation_count ?? 0);
  const lowCollab = newcomer || (!src.historical_match && coop <= 0);
  return { newcomer, fresh, lowCollab };
}

// YouTube search.list 无 @handle → 后端用 UC... channel_id 占 handle(保证非空 + 当 identity:
// profile_url 走 /channel/{id})。但 UC... 不是可读名,卡片应显示真频道名(channel_name→display_name)。
const YT_CHANNEL_ID_RE = /^UC[A-Za-z0-9_-]{20,}$/;
export function readableCreatorName(item: VkpiKolRecallItem): string {
  const handle = cleanText(item.handle);
  const displayName = cleanText(item.display_name);
  // 优先非「频道ID」的 handle(IG/TikTok 的 @用户名)→ 再非「频道ID」的真频道名 → 兜底
  if (handle && !YT_CHANNEL_ID_RE.test(handle)) return handle;
  if (displayName && !YT_CHANNEL_ID_RE.test(displayName)) return displayName;
  // 身份修(2026-07-21):UC 开头的裸频道 ID 永远不当显示名。富化前的旧入库行再探一层
  // 会话 payload 里的真频道名;全无 → 诚实显示「YouTube 频道」(真名由回填脚本补)。
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const channelName = cleanText(src.channel_name);
  if (channelName && !YT_CHANNEL_ID_RE.test(channelName) && channelName.toLowerCase() !== "unknown creator") {
    return channelName;
  }
  return handle || displayName ? "YouTube 频道" : "";
}

// 契合命中 tags 中文化:海外创作者发现是英文搜索词命中,这里把常见摄影/创作术语映射成中文(生僻保留原文)。
const RELEVANCE_ZH: Record<string, string> = {
  wedding: "婚礼", portrait: "人像", landscape: "风光", wildlife: "野生动物", travel: "旅行",
  street: "街拍", fashion: "时尚", product: "产品", food: "美食", sports: "运动", event: "活动",
  macro: "微距", astro: "星空", documentary: "纪实", lifestyle: "生活方式", newborn: "新生儿",
  family: "家庭", commercial: "商业", editorial: "大片", "fine art": "艺术", boudoir: "闺房写真",
  flash: "闪光灯", strobe: "影室灯", lighting: "灯光", "studio lighting": "影室布光", studio: "影棚",
  softbox: "柔光箱", lens: "镜头", camera: "相机", tripod: "三脚架", gimbal: "稳定器", drone: "无人机",
  educator: "教学", reviewer: "测评", vlogger: "Vlog", filmmaker: "影片制作", cinematographer: "摄影指导",
  photographer: "摄影师", creator: "创作者", influencer: "达人", tutorial: "教程", "how-to": "教程",
  bts: "幕后", videography: "视频", cinematic: "电影感", color: "调色", "color grading": "调色",
};
export function zhTag(s: string): string {
  const k = String(s || "").trim().toLowerCase();
  return RELEVANCE_ZH[k] || s;
}

// A·下框:时间戳分镜 + 内容概述。读 final_v1 cache 的 layer1_visual_content.scene_timeline / content_summary。
// 复用 KOLVideoAnalysisPanel 的 sceneTimeline 行结构;缺则静默不渲染(降级,绝不报错占位)。
export function sceneTimelineRowsLocal(value: unknown, max = 8): { key: string; timestamp: string; what: string }[] {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    return {
      key: `${cleanText(record.timestamp) || "scene"}-${index}`,
      timestamp: cleanText(record.timestamp),
      what: cleanText(record.what ?? record.scene ?? record.content),
    };
  }).filter((row) => row.timestamp || row.what).slice(0, max);
}
