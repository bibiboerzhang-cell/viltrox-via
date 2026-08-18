import type { VkpiKolRecallItem, VkpiKolSearchHistoryItem } from "../../../../domains/kol";

import { asRecord, cleanText, display, type Row } from "./SmartKolInputPanel.helpers";
import {
  LOCAL_QUALIFIED_TARGET,
  localQualifiedRowsFromItems,
  type LocalQualifiedRow,
  type LocalQualifiedSummary,
} from "./SmartKolInputPanel.LocalQualified";
import { sessionItems } from "./SmartKolInputPanel.sessionProjection";

export const ONLINE_QUALIFICATION_SPEC = Object.freeze({
  version: "online_net_new_30_v1",
  target_count: 30,
});

export const ONLINE_QUALIFICATION_SCHEMA = "smart_online_net_new_qualified_v1";
export const STRICT_ONLINE_PLATFORMS = Object.freeze(["youtube", "instagram", "tiktok"]);
const ONLINE_ITEM_TYPE = "online_qualified_candidate";
const ONLINE_SOURCE = "platform_discovery_strict";
const FINGERPRINT_RE = /^[a-f0-9]{64}$/;

export function strictOnlineDiscoveryPlatforms(values: readonly string[]): string[] {
  const supported = Array.from(new Set(values.map(cleanText).map((value) => value.toLowerCase())))
    .filter((value) => STRICT_ONLINE_PLATFORMS.includes(value));
  return supported.length ? supported : [...STRICT_ONLINE_PLATFORMS];
}

export type OnlineQualifiedSummary = LocalQualifiedSummary & {
  contractValid: boolean;
  terminal: boolean;
  snapshotComplete: boolean;
  snapshotRevision: number;
  evaluated: number;
  providerRounds: number;
  providerCalls: number;
  duplicateLocal: number;
  duplicateOnline: number;
  duplicateLocalInventory: number;
  candidateBudget: number;
  candidateBudgetUsed: number;
  exhausted: boolean;
  selectionReady: boolean;
};

function count(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function rank(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function onlineItemFromSessionItem(item: Row): VkpiKolRecallItem {
  const payload = asRecord(item.payload);
  const proof = asRecord(payload.qualification_evidence);
  const followers = Number(payload.followers ?? asRecord(proof.followers).value ?? 0);
  const profileType = cleanText(payload.profile_type);
  const bucket = ["creator", "reviewer", "mixed"].includes(profileType) ? profileType : "";
  return {
    bucket,
    kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
    handle: display(payload.handle || payload.display_name, "unknown"),
    display_name: cleanText(payload.display_name || payload.handle),
    platform: cleanText(payload.platform),
    profile_type: profileType,
    followers: Number.isFinite(followers) && followers >= 0 ? followers : null,
    avatar_url: cleanText(payload.avatar_url),
    profile_url: cleanText(item.source_url || payload.profile_url),
    vector_score: Number(item.score || 0),
    recall_rank_score: Number(item.score || 0),
    type_label: "联网净新增",
    creator_type_score: bucket === "creator" ? 1 : 0,
    reviewer_type_score: bucket === "reviewer" ? 1 : 0,
    why_fit: cleanText(payload.why_fit),
    source_fields: payload,
  };
}

function outerOnlineProofPassed(
  item: Row,
  contract: Row,
  contractValid: boolean,
): boolean {
  const payload = asRecord(item.payload);
  const proof = asRecord(payload.qualification_evidence);
  const contractRevision = count(contract.snapshot_revision);
  const contractSnapshotId = cleanText(contract.snapshot_id);
  const poolId = Number(item.kol_pool_id);
  const canonicalFingerprint = cleanText(payload.canonical_fingerprint);
  const serverRank = rank(payload.server_rank);
  const globalRank = rank(payload.global_unique_rank);
  return Boolean(
    contractValid
    && cleanText(item.item_type) === ONLINE_ITEM_TYPE
    && cleanText(item.status) === "ready"
    && cleanText(payload.schema) === ONLINE_QUALIFICATION_SCHEMA
    && cleanText(payload.origin_lane) === "online"
    && cleanText(payload.source) === ONLINE_SOURCE
    && cleanText(payload.qualification_status) === "accepted"
    && FINGERPRINT_RE.test(canonicalFingerprint)
    && Number(proof.kol_pool_id) === poolId
    && cleanText(proof.canonical_fingerprint) === canonicalFingerprint
    && Array.isArray(asRecord(proof.relevance).evidence)
    && (asRecord(proof.relevance).evidence as unknown[]).length > 0
    && poolId > 0
    && serverRank && serverRank <= LOCAL_QUALIFIED_TARGET
    && globalRank && globalRank >= serverRank && globalRank <= 60
    && contractRevision != null
    && count(payload.snapshot_revision) === contractRevision
    && count(proof.snapshot_revision) === contractRevision
    && contractSnapshotId
    && cleanText(payload.snapshot_id) === contractSnapshotId
    && cleanText(proof.snapshot_id) === contractSnapshotId
    && rank(proof.server_rank) === serverRank
    && rank(proof.global_unique_rank) === globalRank
  );
}

function onlineRows(session: VkpiKolSearchHistoryItem | null, contract: Row, contractValid: boolean): LocalQualifiedRow[] {
  if (!session) return [];
  const rawItems = sessionItems(session).filter((item) => cleanText(item.item_type) === ONLINE_ITEM_TYPE);
  const rows = localQualifiedRowsFromItems(rawItems.map(onlineItemFromSessionItem));
  return rows.map((row, index): LocalQualifiedRow => {
    const rawItem = rawItems.find((item) => Number(item.kol_pool_id) === Number(row.item.kol_pool_id)) || {};
    const accepted = row.strictQualified && outerOnlineProofPassed(rawItem, contract, contractValid);
    if (accepted) return { ...row, rank: rank(asRecord(rawItem.payload).server_rank) || index + 1 };
    if (row.qualification === "rejected") return { ...row, strictQualified: false };
    return {
      ...row,
      strictQualified: false,
      qualification: "pending",
      qualificationLabel: "待服务端联网验收",
    };
  }).sort((a, b) => a.rank - b.rank);
}

const REASON_LABELS: Record<string, string> = {
  followers_unknown: "粉丝数待核验",
  followers_below_floor: "粉丝不足 3,000",
  latest_video_unknown: "最新视频日期待核验",
  latest_video_stale: "最近 45 天未更新",
  latest_video_identity_missing: "最新视频缺少可审计身份",
  market_unknown: "市场证据待核验",
  market_mismatch: "不符合目标市场",
  language_unknown: "内容语言待核验",
  language_mismatch: "不符合所选内容语言",
  profile_type_unknown: "KOL 类型待核验",
  profile_type_mismatch: "不符合所选 KOL 类型",
  platform_unknown: "平台待核验",
  platform_mismatch: "不在所选平台",
  low_relevance: "市场/产品相关性不足",
  pending: "仍在补证",
  rejected: "未通过严格硬闸",
  duplicate_local: "与本地名单重复",
  duplicate_online: "联网结果内重复",
  duplicate_batch: "供应商批次重复",
  duplicate_local_inventory: "已存在于本地库",
  provider_failed: "数据源暂不可用",
  candidate_budget_exhausted: "候选预算已用尽",
  provider_round_budget_exhausted: "供应商轮次已用尽",
  candidate_exhausted: "可核验候选已耗尽",
  enrollment_failed: "合格候选入库失败",
};

function reasonLabels(contract: Row, shortfall: number): string[] {
  const raw = asRecord(contract.shortfall_reasons);
  const labels = Object.entries(raw).flatMap(([key, value]) => {
    const amount = count(value);
    if (amount == null || amount <= 0) return [];
    return [`${REASON_LABELS[key] || key} ${amount}`];
  });
  if (!labels.length && shortfall > 0) labels.push(`还缺 ${shortfall} 个合格且联网净新增的 KOL`);
  return labels.slice(0, 8);
}

export function onlineQualifiedSummaryFromSession(session: VkpiKolSearchHistoryItem | null): OnlineQualifiedSummary {
  const contract = asRecord(asRecord(session?.result_summary).online_qualification);
  const acceptedClaim = count(contract.net_new_accepted_count);
  const returnedClaim = count(contract.returned_count);
  const revision = count(contract.snapshot_revision);
  const snapshotId = cleanText(contract.snapshot_id);
  const contractValid = Boolean(
    cleanText(contract.schema) === ONLINE_QUALIFICATION_SCHEMA
    && Number(contract.policy_version) === 1
    && contract.server_owned === true
    && cleanText(contract.origin_lane) === "online"
    && cleanText(contract.source) === ONLINE_SOURCE
    && count(contract.target_count) === LOCAL_QUALIFIED_TARGET
    && acceptedClaim != null
    && returnedClaim != null
    && revision != null
    && revision > 0
    && snapshotId
  );
  const rows = onlineRows(session, contract, contractValid);
  const strictRows = rows.filter((row) => row.strictQualified);
  const accepted = contractValid ? Math.min(acceptedClaim || 0, returnedClaim || 0, strictRows.length) : 0;
  const rowPending = rows.filter((row) => row.qualification === "pending").length;
  const rowRejected = rows.filter((row) => row.qualification === "rejected").length;
  const target = LOCAL_QUALIFIED_TARGET;
  const shortfall = Math.max(target - accepted, contractValid ? count(contract.shortfall) || 0 : target);
  const pending = (contractValid ? count(contract.pending_count) || 0 : 0) + rowPending;
  const rejected = (contractValid ? count(contract.rejected_count) || 0 : 0) + rowRejected;
  return {
    contractValid,
    terminal: contractValid && contract.terminal === true,
    snapshotComplete: contractValid && contract.snapshot_complete === true,
    snapshotRevision: contractValid ? revision || 0 : 0,
    target,
    serverReturned: contractValid ? Math.min(returnedClaim || 0, rows.length) : 0,
    serverQualified: contractValid ? count(contract.strict_qualified_count) || 0 : 0,
    qualified: accepted,
    uniqueQualified: accepted,
    pending,
    rejected,
    shortfall,
    shortfallReasons: contractValid
      ? reasonLabels(contract, shortfall)
      : ["联网严格合同尚未完成，当前结果不计入 30 人目标"],
    rows,
    evaluated: contractValid ? count(contract.evaluated_count) || 0 : 0,
    providerRounds: contractValid ? count(contract.provider_rounds) || 0 : 0,
    providerCalls: contractValid ? count(contract.provider_calls) || 0 : 0,
    duplicateLocal: contractValid ? count(contract.duplicate_local_count) || 0 : 0,
    duplicateOnline: contractValid ? count(contract.duplicate_online_count) || 0 : 0,
    duplicateLocalInventory: contractValid ? count(contract.duplicate_local_inventory_count) || 0 : 0,
    candidateBudget: contractValid ? count(contract.candidate_budget) || 0 : 0,
    candidateBudgetUsed: contractValid ? count(contract.candidate_budget_used) || 0 : 0,
    exhausted: contractValid && contract.exhausted === true,
    selectionReady: contractValid && contract.terminal === true && contract.snapshot_complete === true,
  };
}
