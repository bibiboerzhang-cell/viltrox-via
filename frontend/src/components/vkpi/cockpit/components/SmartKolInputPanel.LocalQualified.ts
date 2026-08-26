import type { VkpiKolRecallItem, VkpiKolRecallResponse } from "../../../../domains/kol";

import { asRecord, cleanText, type Row, providerGateReasonOf, providerUnavailableLabel } from "./SmartKolInputPanel.helpers";

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

// 服务端「活跃度未知」桶:这个人的最近视频我们一次都没抓到过。他不是被判不
// 合格,也不算进 30 人目标数,但确实被返回给了操作员,所以必须一眼看得出来
// 和真·活跃的人不一样,并且能单独勾选。
export const ACTIVITY_UNKNOWN_STATUS = "activity_unknown_pending_fetch";
export const ACTIVITY_UNKNOWN_TIER = "deferred_activity_unknown";
export const ACTIVITY_UNKNOWN_LABEL = "活跃度未知 · 从没抓到过视频";
export const ACTIVITY_UNKNOWN_VIDEO_LABEL = "从没抓到过";

export type LocalQualifiedRow = {
  identity: string;
  rank: number;
  item: VkpiKolRecallItem;
  name: string;
  platform: string;
  followers: number | null;
  latestVideoAt: string;
  marketEvidence: string;
  languageEvidence: string;
  profileType: string;
  accountQuality: string;
  whyFit: string;
  contactStatus: string;
  analysisStatus: string;
  qualification: LocalQualificationState;
  qualificationLabel: string;
  strictQualified: boolean;
  /** 服务端说「这个人的视频我们从没抓到过」:不计入 30 人,但可单独勾选。 */
  activityUnknown: boolean;
};

export type LocalQualifiedSummary = {
  target: number;
  serverReturned: number;
  serverQualified: number;
  qualified: number;
  uniqueQualified: number;
  pending: number;
  rejected: number;
  activityUnknown: number;
  shortfall: number;
  shortfallReasons: string[];
  rows: LocalQualifiedRow[];
};

export const STRICT_V2_GATES = [
  "account_quality", "followers", "activity", "market", "language", "profile_type", "platform", "relevance",
] as const;

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

export function strictV2QualificationState(qualification: Row): LocalQualificationState | null {
  if (cleanText(qualification.schema) !== "smart_local_gate_evidence_v2") return null;
  if (qualification.passed === false) return "rejected";
  const gatePassed = STRICT_V2_GATES.map((key) => asRecord(qualification[key]).passed);
  if (gatePassed.some((passed) => passed === false)) return "rejected";
  if (qualification.passed === true && gatePassed.every((passed) => passed === true)) return "qualified";
  return "pending";
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

/** 服务端标记 → 界面判据。证据面(proof)与条目面(item)两处任一命中即成立,
 *  因为会话回放走的是条目字段,实时搜索走的是证据字段。 */
function activityUnknownFor(qualification: Row, root: Row, source: Row): boolean {
  const activity = asRecord(qualification.activity);
  if (qualification.deferred === true && cleanText(activity.status) === ACTIVITY_UNKNOWN_STATUS) return true;
  if (activity.deferred === true && activity.known === false) return true;
  return cleanText(firstValue([root, source], ["activity_status"])) === ACTIVITY_UNKNOWN_STATUS
    || cleanText(firstValue([root, source], ["selection_tier"])) === ACTIVITY_UNKNOWN_TIER;
}

function qualificationFor(
  qualification: Row,
  activityUnknown: boolean,
): Pick<LocalQualifiedRow, "qualification" | "qualificationLabel"> {
  // 「从没抓到过视频」不是「未通过」。先于合格判据回答,否则这批人会被涂成
  // 红色的「未通过」,把一个数据缺口谎报成一次质量裁决。
  if (activityUnknown) return { qualification: "pending", qualificationLabel: ACTIVITY_UNKNOWN_LABEL };
  const proofSchema = cleanText(qualification.schema);
  const strictState = strictV2QualificationState(qualification);
  if (proofSchema === "smart_local_gate_evidence_v2") {
    if (strictState === "qualified") return { qualification: "qualified", qualificationLabel: "合格" };
    if (strictState === "rejected") return { qualification: "rejected", qualificationLabel: "未通过" };
    return { qualification: "pending", qualificationLabel: "待服务端验收" };
  }
  // v1 did not carry the full server-owned proof set. It remains readable as legacy evidence,
  // but must never light up the strict-v2 qualified/approval path.
  if (proofSchema === "smart_local_gate_evidence_v1") {
    return { qualification: "pending", qualificationLabel: "待服务端 v2 验收" };
  }
  return { qualification: "pending", qualificationLabel: "待服务端 v2 验收" };
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

function codeList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(cleanText).filter(Boolean);
  const text = cleanText(value);
  return text ? [text] : [];
}

const PROFILE_TYPE_LABELS: Record<string, string> = {
  creator: "创作者",
  reviewer: "评测号",
  mixed: "创作+评测",
};

function languageEvidenceFor(qualification: Row, candidateFacets: Row, root: Row, source: Row): string {
  const gate = asRecord(qualification.language);
  const values = codeList(gate.values);
  const fallback = cleanText(firstValue([candidateFacets, root, source], ["language", "content_language"]));
  return (values.length ? values : fallback ? [fallback] : []).join("/");
}

function profileTypeFor(qualification: Row, candidateFacets: Row, root: Row, source: Row): string {
  const gate = asRecord(qualification.profile_type);
  const values = codeList(gate.values);
  const fallback = cleanText(firstValue([candidateFacets, root, source], ["profile_type", "kol_type"]));
  return (values.length ? values : fallback ? [fallback] : [])
    .map((value) => PROFILE_TYPE_LABELS[value] || value)
    .join("/");
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
  const accountQuality = asRecord(qualification.account_quality);
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
  const activityUnknown = activityUnknownFor(qualification, root, source);
  return {
    identity: identityFor(item),
    activityUnknown,
    rank: rankFor(records, fallbackRank),
    item,
    name: cleanText(item.display_name || item.handle) || `KOL #${item.kol_pool_id}`,
    platform: cleanText(item.platform) || "未知平台",
    followers: Number.isFinite(followersValue) && followersValue >= 0 ? followersValue : null,
    latestVideoAt: latestVideoFor(records),
    marketEvidence: marketEvidenceFor(marketEvidence, root, source),
    languageEvidence: languageEvidenceFor(qualification, candidateFacets, root, source),
    profileType: profileTypeFor(qualification, candidateFacets, root, source),
    accountQuality: cleanText(accountQuality.verdict),
    whyFit,
    contactStatus: statusLabel(contactStatus, "contact"),
    analysisStatus: statusLabel(analysisStatus, "analysis"),
    strictQualified: !activityUnknown && strictV2QualificationState(qualification) === "qualified",
    ...qualificationFor(qualification, activityUnknown),
  };
}

function resultItems(result: VkpiKolRecallResponse): VkpiKolRecallItem[] {
  if (Array.isArray(result.items) && result.items.length) return result.items;
  return [
    ...(Array.isArray(result.buckets?.creator) ? result.buckets.creator : []),
    ...(Array.isArray(result.buckets?.reviewer) ? result.buckets.reviewer : []),
  ];
}

export function localQualifiedRowsFromItems(items: VkpiKolRecallItem[]): LocalQualifiedRow[] {
  const deduped = new Map<string, LocalQualifiedRow>();
  items.forEach((item, index) => {
    const row = rowFromItem(item, index + 1);
    const existing = deduped.get(row.identity);
    if (!existing || row.rank < existing.rank) deduped.set(row.identity, row);
  });
  return Array.from(deduped.values()).sort((a, b) => a.rank - b.rank);
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
  latest_video_unknown: "从没抓到过视频，活跃度未知",
  stale: "最近 45 天未更新",
  latest_video_stale: "最近 45 天未更新",
  latest_video_identity_missing: "最新视频缺少可审计链接或视频 ID",
  latest_video_not_active_video: "最近内容不是有效视频证据",
  latest_video_in_future: "最新视频时间异常",
  market_unknown: "市场证据待核验",
  market_unverified: "市场证据待核验",
  market_untrusted_source: "市场仅有模型推断，未计入",
  market_mismatch: "不符合目标市场",
  language_unknown: "内容语言待核验",
  language_mismatch: "不符合所选内容语言",
  language_filter_invalid: "语言筛选值无效",
  profile_type_unknown: "KOL 类型待核验",
  profile_type_mismatch: "不符合所选 KOL 类型",
  profile_type_filter_invalid: "KOL 类型筛选值无效",
  account_own_brand: "Viltrox 自有账号已排除",
  account_brand_official: "品牌官方账号已排除",
  account_retailer: "零售/经销账号已排除",
  account_garbage: "无效账号已排除",
  low_relevance: "市场/产品相关性不足",
  duplicate: "跨来源重复",
  duplicate_canonical_identity: "跨来源重复",
  platform_unknown: "平台待核验",
  platform_mismatch: "不在所选平台",
  provider_failed: "数据源未就绪(配置/预算)",
  budget_exhausted: "本轮预算已到上限",
};

function shortfallReasons(records: Row[], pending: number, shortfall: number): string[] {
  const raw = firstValue(records, ["local_shortfall_reasons", "shortfall_reasons", "rejected_by_reason", "rejection_reasons", "shortfall_reason"]);
  // F5:provider_failed 必须带原因(provider_gate_reason);没有就「未就绪(配置/预算)」,不假装排队。
  const gateReason = providerGateReasonOf(...records);
  const labelOf = (key: string) => (key === "provider_failed" ? providerUnavailableLabel(gateReason) : (SHORTFALL_LABELS[key] || key));
  const labels: string[] = [];
  if (Array.isArray(raw)) {
    raw.forEach((value) => {
      const key = cleanText(value);
      if (key) labels.push(labelOf(key));
    });
  } else if (raw && typeof raw === "object") {
    Object.entries(raw as Row).forEach(([key, value]) => {
      const count = Number(value);
      const label = labelOf(key);
      labels.push(Number.isFinite(count) && count > 0 ? `${label} ${count}` : label);
    });
  } else {
    const text = cleanText(raw);
    if (text) labels.push(labelOf(text));
  }
  if (!labels.length && shortfall > 0) {
    labels.push(pending > 0 ? `仍有 ${pending} 人待服务端硬闸验收` : `还缺 ${shortfall} 个合格且唯一的本地 KOL`);
  }
  return Array.from(new Set(labels)).slice(0, 5);
}

export function localQualifiedSummary(result: VkpiKolRecallResponse): LocalQualifiedSummary {
  const rows = localQualifiedRowsFromItems(resultItems(result));
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
  ].find((lane) => cleanText(lane.schema) === "smart_local_qualified_v2") ?? {};
  const hasSmartLocalContract = cleanText(localLane.schema) === "smart_local_qualified_v2";
  const contractRecords = hasSmartLocalContract ? [localLane] : [];
  const observedRecords = [diagnostics, resultRoot];
  const parsedQualified = rows.filter((row) => row.qualification === "qualified").length;
  const strictParsedQualified = rows.filter((row) => row.strictQualified).length;
  const claimedReturned = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["returned_count"])
    : explicitCount(observedRecords, ["local_returned_count", "returned_count", "visible_count"])) ?? rows.length;
  const claimedQualified = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["qualified_count"])
    : null) ?? parsedQualified;
  // qualified_returned_count 是「真正过闸并返回」的人数,不含活跃度未知桶;
  // returned_count 含,所以它只能垫底,不能排在前面。
  const claimedAccepted = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["qualified_returned_count", "accepted_count", "returned_count"])
    : null) ?? parsedQualified;
  const claimedUnique = (hasSmartLocalContract
    ? explicitCount(contractRecords, ["unique_qualified_count", "accepted_unique_count"])
    : null) ?? parsedQualified;
  // A strict aggregate is useful context, but the actionable list cannot claim or approve rows
  // that are absent from the current sanitized snapshot.
  const serverReturned = hasSmartLocalContract ? Math.min(claimedReturned, rows.length) : claimedReturned;
  const serverQualified = hasSmartLocalContract ? Math.min(claimedQualified, strictParsedQualified) : claimedQualified;
  const qualified = hasSmartLocalContract ? Math.min(claimedAccepted, strictParsedQualified) : claimedAccepted;
  const uniqueQualified = hasSmartLocalContract ? Math.min(claimedUnique, strictParsedQualified) : claimedUnique;
  const target = (hasSmartLocalContract
    ? explicitCount([localLane, asRecord(localLane.policy)], ["target_count", "target"])
    : null) ?? LOCAL_QUALIFIED_TARGET;
  const activityUnknown = rows.filter((row) => row.activityUnknown).length;
  const pending = rows.filter((row) => row.qualification === "pending" && !row.activityUnknown).length;
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
    activityUnknown,
    shortfall,
    shortfallReasons: shortfallReasons(contractRecords, pending, shortfall),
    rows,
  };
}
