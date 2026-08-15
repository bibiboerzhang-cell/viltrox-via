import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import { asRecord, cleanText, type Row } from "./SmartKolInputPanel.helpers";

export const LOCAL_QUALIFIED_TARGET = 30;
export const LOCAL_QUALIFICATION_SPEC = Object.freeze({
  version: "local_30_v1",
  target_count: LOCAL_QUALIFIED_TARGET,
  followers_min: 3_000,
  latest_video_max_age_days: 45,
  recent_video_preferred_days: 30,
  require_market_evidence: true,
  unknown_policy: "pending_not_counted",
  dedupe_scope: "search_session",
});

export type LocalQualificationState = "qualified" | "pending" | "rejected";

export type LocalQualifiedRow = {
  identity: string;
  rank: number;
  item: VkpiKolRecallItem;
  name: string;
  platform: string;
  followers: number | null;
  latestVideoAt: string;
  marketEvidence: string;
  whyFit: string;
  contactStatus: string;
  analysisStatus: string;
  qualification: LocalQualificationState;
  qualificationLabel: string;
};

export type LocalQualifiedSummary = {
  target: number;
  serverReturned: number;
  serverQualified: number;
  qualified: number;
  uniqueQualified: number;
  pending: number;
  rejected: number;
  shortfall: number;
  shortfallReasons: string[];
  rows: LocalQualifiedRow[];
};

const QUALIFIED_STATES = new Set(["accepted", "eligible", "pass", "passed", "qualified", "ready"]);
const REJECTED_STATES = new Set(["blocked", "disqualified", "failed", "ineligible", "reject", "rejected"]);

function firstValue(records: Row[], keys: string[]): unknown {
  for (const record of records) {
    for (const key of keys) {
      const value = record[key];
      if (value !== undefined && value !== null && value !== "") return value;
    }
  }
  return undefined;
}

function firstRecord(records: Row[], keys: string[]): Row {
  for (const record of records) {
    for (const key of keys) {
      const value = asRecord(record[key]);
      if (Object.keys(value).length) return value;
    }
  }
  return {};
}

function itemRecords(item: VkpiKolRecallItem): Row[] {
  const root = item as unknown as Row;
  const source = asRecord(item.source_fields);
  const qualification = firstRecord([root, source], [
    "local_qualification",
    "qualification_evidence",
    "qualification",
    "hard_gate",
    "quality_gate",
    "eligibility",
  ]);
  const evidence = firstRecord([qualification, root, source], ["evidence", "qualification_evidence", "hard_gate_evidence"]);
  const latestVideo = firstRecord([evidence, qualification, root, source], ["latest_video", "recent_video", "video_freshness", "activity"]);
  const market = firstRecord([evidence, qualification, root, source], ["market_evidence", "market_match", "market_verdict", "market"]);
  const contact = firstRecord([root, source], ["contact_preview", "contactability", "contact"]);
  const analysis = firstRecord([root, source], ["analysis", "analysis_preview", "profile_execute"]);
  return [qualification, evidence, latestVideo, market, contact, analysis, root, source];
}

function identityFor(item: VkpiKolRecallItem): string {
  const records = itemRecords(item);
  const canonical = cleanText(firstValue(records, ["canonical_creator_id", "canonical_identity", "creator_uid"]));
  if (canonical) return `canonical:${canonical.toLowerCase()}`;
  const platform = cleanText(item.platform).toLowerCase();
  const handle = cleanText(item.handle).toLowerCase().replace(/^@/, "");
  if (platform && handle && handle !== "unknown") return `profile:${platform}:${handle}`;
  const profileUrl = cleanText(item.profile_url).toLowerCase();
  if (profileUrl) return `url:${profileUrl}`;
  if (Number(item.kol_pool_id) > 0) return `pool:${Number(item.kol_pool_id)}`;
  return `unresolved:${platform}:${handle}`;
}

function qualificationFor(qualification: Row, root: Row, source: Row): Pick<LocalQualifiedRow, "qualification" | "qualificationLabel"> {
  const records = [qualification, root, source];
  const explicitBoolean = firstValue(records, [
    "hard_gate_pass",
    "is_qualified",
    "qualified",
    "accepted",
    "passed",
  ]);
  if (explicitBoolean === true) return { qualification: "qualified", qualificationLabel: "合格" };
  if (explicitBoolean === false) return { qualification: "rejected", qualificationLabel: "未通过" };
  const namedStatus = firstValue([root, source], [
    "qualification_status",
    "qualification_state",
    "hard_gate_status",
    "eligibility_status",
  ]);
  const status = cleanText(namedStatus ?? qualification.status ?? qualification.verdict).toLowerCase();
  if (QUALIFIED_STATES.has(status)) return { qualification: "qualified", qualificationLabel: "合格" };
  if (REJECTED_STATES.has(status)) return { qualification: "rejected", qualificationLabel: "未通过" };
  // Older backends do not own the 3,000-follower / 45-day / market hard gate. Even when a row
  // looks promising, the browser must not promote it to qualified by inference.
  return { qualification: "pending", qualificationLabel: "待服务端验收" };
}

function rankFor(records: Row[], fallback: number): number {
  const value = Number(firstValue(records, ["server_rank", "local_rank", "accepted_rank", "rank"]));
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function latestVideoFor(records: Row[]): string {
  return cleanText(firstValue(records, [
    "posted_at",
    "latest_video_published_at",
    "last_video_published_at",
    "latest_video_at",
    "last_video_at",
    "published_at",
    "publishedAt",
  ]));
}

function marketEvidenceFor(marketEvidence: Row, root: Row, sourceFields: Row): string {
  const records = [marketEvidence, root, sourceFields];
  const passed = marketEvidence.passed;
  const status = cleanText(firstValue(records, ["market_verdict", "market_status", "match_status"]) ?? marketEvidence.verdict ?? marketEvidence.status ?? (passed === true ? "pass" : ""));
  const market = cleanText(firstValue(records, ["target_market", "market", "country", "region", "language", "value", "target"]));
  const source = cleanText(firstValue(records, ["market_evidence_source", "evidence_source"]) ?? marketEvidence.source);
  const parts = [market, status && !["ready", "qualified", "accepted"].includes(status.toLowerCase()) ? status : "", source]
    .filter(Boolean);
  return parts.join(" · ");
}

function statusLabel(value: unknown, kind: "contact" | "analysis"): string {
  const status = cleanText(value).toLowerCase();
  if (!status) return kind === "contact" ? "待核验" : "待分析";
  if (["yes", "ready", "verified", "available", "found", "complete", "completed", "done"].includes(status)) {
    return kind === "contact" ? "可联系" : "已完成";
  }
  if (["queued", "running", "processing", "pending", "enriching", "analyzing"].includes(status)) {
    return kind === "contact" ? "核验中" : "分析中";
  }
  if (["no", "empty", "missing", "none", "no_contacts", "not_found", "unavailable"].includes(status)) {
    return kind === "contact" ? "暂缺" : "暂无分析";
  }
  if (["failed", "error", "blocked"].includes(status)) return kind === "contact" ? "核验失败" : "分析失败";
  return kind === "contact" ? "待核验" : "待分析";
}

function rowFromItem(item: VkpiKolRecallItem, fallbackRank: number): LocalQualifiedRow {
  const root = item as unknown as Row;
  const source = asRecord(item.source_fields);
  const qualification = firstRecord([root, source], ["local_qualification", "qualification_evidence", "qualification", "hard_gate", "quality_gate", "eligibility"]);
  const marketEvidence = firstRecord([qualification, root, source], ["market_evidence", "market_match", "market_verdict", "market"]);
  const contact = firstRecord([root, source], ["contact_preview", "contactability", "contact"]);
  const candidateFacets = firstRecord([root, source], ["candidate_facets"]);
  const analysis = firstRecord([root, source], ["analysis", "analysis_preview", "profile_execute"]);
  const records = itemRecords(item);
  const contactStatus = firstValue([contact], ["status", "state", "contact_available"])
    ?? firstValue([root, source], ["contact_status", "contactability_status"])
    ?? candidateFacets.contact_available;
  const analysisStatus = firstValue([root, source], [
    "analysis_status",
    "deep_analysis_status",
    "profile_status",
    "dossier_status",
  ]) ?? analysis.status;
  const gateFollowers = asRecord(qualification.followers);
  const followersValue = Number(
    firstValue([root, source], ["followers", "follower_count", "subscriber_count", "subscribers"])
      ?? gateFollowers.value,
  );
  const whyFit = cleanText(firstValue(records, ["why_fit", "recall_reason", "evidence", "sample_title"]));
  return {
    identity: identityFor(item),
    rank: rankFor(records, fallbackRank),
    item,
    name: cleanText(item.display_name || item.handle) || `KOL #${item.kol_pool_id}`,
    platform: cleanText(item.platform) || "未知平台",
    followers: Number.isFinite(followersValue) && followersValue >= 0 ? followersValue : null,
    latestVideoAt: latestVideoFor(records),
    marketEvidence: marketEvidenceFor(marketEvidence, root, source),
    whyFit,
    contactStatus: statusLabel(contactStatus, "contact"),
    analysisStatus: statusLabel(analysisStatus, "analysis"),
    ...qualificationFor(qualification, root, source),
  };
}

function resultItems(result: VkpiKolRecallResponse): VkpiKolRecallItem[] {
  if (Array.isArray(result.items) && result.items.length) return result.items;
  return [
    ...(Array.isArray(result.buckets?.creator) ? result.buckets.creator : []),
    ...(Array.isArray(result.buckets?.reviewer) ? result.buckets.reviewer : []),
  ];
}

function explicitCount(records: Row[], keys: string[]): number | null {
  const value = Number(firstValue(records, keys));
  return Number.isInteger(value) && value >= 0 ? value : null;
}

const SHORTFALL_LABELS: Record<string, string> = {
  below_3000: "粉丝不足 3,000",
  followers_below_floor: "粉丝不足 3,000",
  followers_below_3000: "粉丝不足 3,000",
  followers_unknown: "粉丝数待核验",
  freshness_unknown: "最新视频日期待核验",
  latest_video_unknown: "最新视频日期待核验",
  stale: "最近 45 天未更新",
  latest_video_stale: "最近 45 天未更新",
  market_unknown: "市场证据待核验",
  market_unverified: "市场证据待核验",
  market_mismatch: "不符合目标市场",
  low_relevance: "市场/产品相关性不足",
  duplicate: "跨来源重复",
  duplicate_canonical_identity: "跨来源重复",
  platform_unknown: "平台待核验",
  platform_mismatch: "不在所选平台",
  provider_failed: "数据源暂不可用",
  budget_exhausted: "本轮预算已到上限",
};

function shortfallReasons(records: Row[], pending: number, shortfall: number): string[] {
  const raw = firstValue(records, ["local_shortfall_reasons", "shortfall_reasons", "rejected_by_reason", "rejection_reasons", "shortfall_reason"]);
  const labels: string[] = [];
  if (Array.isArray(raw)) {
    raw.forEach((value) => {
      const key = cleanText(value);
      if (key) labels.push(SHORTFALL_LABELS[key] || key);
    });
  } else if (raw && typeof raw === "object") {
    Object.entries(raw as Row).forEach(([key, value]) => {
      const count = Number(value);
      const label = SHORTFALL_LABELS[key] || key;
      labels.push(Number.isFinite(count) && count > 0 ? `${label} ${count}` : label);
    });
  } else {
    const text = cleanText(raw);
    if (text) labels.push(SHORTFALL_LABELS[text] || text);
  }
  if (!labels.length && shortfall > 0) {
    labels.push(pending > 0 ? `仍有 ${pending} 人待服务端硬闸验收` : `还缺 ${shortfall} 个合格且唯一的本地 KOL`);
  }
  return Array.from(new Set(labels)).slice(0, 5);
}

export function localQualifiedSummary(result: VkpiKolRecallResponse): LocalQualifiedSummary {
  const deduped = new Map<string, LocalQualifiedRow>();
  resultItems(result).forEach((item, index) => {
    const row = rowFromItem(item, index + 1);
    const existing = deduped.get(row.identity);
    if (!existing || row.rank < existing.rank) deduped.set(row.identity, row);
  });
  const rows = Array.from(deduped.values()).sort((a, b) => a.rank - b.rank);
  const diagnostics = asRecord(result.diagnostics);
  const query = asRecord(result.query);
  const resultRoot = result as unknown as Row;
  const localLane = [
    asRecord(diagnostics.local_lane),
    asRecord(diagnostics.local_qualification),
    asRecord(query.local_lane),
    asRecord(query.local_qualification),
    asRecord(resultRoot.local_lane),
    asRecord(resultRoot.local_qualification),
  ].find((lane) => cleanText(lane.schema) === "smart_local_qualified_v1") ?? {};
  const hasSmartLocalContract = cleanText(localLane.schema) === "smart_local_qualified_v1";
  const contractRecords = hasSmartLocalContract ? [localLane] : [];
  const observedRecords = [diagnostics, resultRoot];
  const parsedQualified = rows.filter((row) => row.qualification === "qualified").length;
  const serverReturned = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["returned_count"])
    : explicitCount(observedRecords, ["local_returned_count", "returned_count", "visible_count"])) ?? rows.length;
  const serverQualified = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["qualified_count"])
    : null) ?? parsedQualified;
  const qualified = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["accepted_count", "returned_count"])
    : null) ?? parsedQualified;
  const uniqueQualified = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["unique_qualified_count", "accepted_unique_count"])
    : null) ?? parsedQualified;
  const target = (hasSmartLocalContract
    ? explicitCount([localLane, asRecord(localLane.policy)], ["target_count", "target"])
    : null) ?? LOCAL_QUALIFIED_TARGET;
  const pending = rows.filter((row) => row.qualification === "pending").length;
  const rejected = rows.filter((row) => row.qualification === "rejected").length;
  const shortfall = Math.max(0, target - qualified);
  return {
    target,
    serverReturned,
    serverQualified,
    qualified,
    uniqueQualified,
    pending,
    rejected,
    shortfall,
    shortfallReasons: shortfallReasons(contractRecords, pending, shortfall),
    rows,
  };
}
