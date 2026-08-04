import type { VkpiKolRecallItem } from "../../../../domains/kol";

type EvidenceGrade = "strong" | "basic" | "missing";

export type CandidateRankSummary = {
  score: number | null;
  scoreLabel: "本地词项相关度" | "混合相关度" | "向量相关度" | "检索相关度";
  methodLabel: "本地词项排序" | "混合排序" | "向量排序" | "检索排序";
  kind: "lexical" | "hybrid" | "vector" | "retrieval";
  sourceField: string | null;
  rawMethod: string;
  detail: string;
};

export type CandidateEvidenceSummary = {
  grade: EvidenceGrade;
  gradeLabel: string;
  gradeDetail: string;
  missingLabels: string[];
  reasonLabel: "为何推荐" | "为何仅候选";
  reason: string;
  onlyCandidate: boolean;
};

const UNKNOWN_FIELD_LABELS: Record<string, string> = {
  platform: "主要平台",
  country: "国家/地区",
  region: "国家/地区",
  language: "内容语言",
  languages: "内容语言",
  content_language: "内容语言",
  followers: "粉丝数",
  followers_min: "最低粉丝数",
  followers_max: "最高粉丝数",
  creator_type: "创作者类型",
  profile_type: "创作者类型",
  vertical: "垂直标签",
  verticals: "垂直标签",
  primary_topic: "内容垂类",
  gear_content: "摄影器材内容",
  lens_content: "镜头内容",
  recent_content: "近期内容",
  representative_evidence: "代表内容证据",
  video_evidence: "视频证据",
};

function text(value: unknown): string {
  return String(value ?? "").trim();
}

function positiveCount(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function sourceRecord(item: VkpiKolRecallItem): Record<string, unknown> {
  return item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {};
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function finiteScore(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * 新旧后端兼容的检索分展示。字段名只用于选择来源，UI 永远不把 precision/confidence
 * 命名为准确率；方法未知时使用中性的“检索相关度”。
 */
export function candidateRankSummary(item: VkpiKolRecallItem): CandidateRankSummary {
  const itemRecord = item as unknown as Record<string, unknown>;
  const source = sourceRecord(item);
  const scoreFields = [
    "robust_rank_score",
    "precision_rank_score",
    "retrieval_score",
    "recall_rank_score",
    "vector_score",
  ] as const;
  let score: number | null = null;
  let sourceField: string | null = null;
  for (const field of scoreFields) {
    const parsed = finiteScore(itemRecord[field] ?? source[field]);
    if (parsed != null) {
      score = parsed;
      sourceField = field;
      break;
    }
  }

  const methodFields = [
    "robust_rank_method",
    "precision_rank_method",
    "retrieval_method",
    "recall_rank_score_method",
    "ranking_method",
  ] as const;
  let rawMethod = "";
  for (const field of methodFields) {
    rawMethod = text(itemRecord[field] ?? source[field]);
    if (rawMethod) break;
  }
  const method = rawMethod.toLowerCase();
  const isLexical = /provider[_-]?free|pool[_-]?text|lexical|keyword|bm25|full[_-]?text|fts/.test(method);
  const isHybrid = /hybrid|mixed|blend|rrf|vector[_-]?type[_-]?weighted|bm25.*vector|vector.*bm25/.test(method);
  const isVector = !isLexical && !isHybrid && /vector|embedding|semantic|qdrant/.test(method);
  const kind: CandidateRankSummary["kind"] = isLexical
    ? "lexical"
    : isHybrid
      ? "hybrid"
      : isVector || (!rawMethod && sourceField === "vector_score")
        ? "vector"
        : "retrieval";
  const scoreLabel = kind === "lexical"
    ? "本地词项相关度"
    : kind === "hybrid"
      ? "混合相关度"
      : kind === "vector"
        ? "向量相关度"
        : "检索相关度";
  const methodLabel = kind === "lexical"
    ? "本地词项排序"
    : kind === "hybrid"
      ? "混合排序"
      : kind === "vector"
        ? "向量排序"
        : "检索排序";
  const detail = score == null
    ? `${scoreLabel}待返回；不以 0 代替`
    : `${scoreLabel} ${score.toFixed(3)} · ${methodLabel}${rawMethod ? `（${rawMethod}）` : "（方法标识未返回）"}；仅用于本次候选排序，不代表业务结果`;

  return { score, scoreLabel, methodLabel, kind, sourceField, rawMethod, detail };
}

export function candidateMissingLabels(item: VkpiKolRecallItem): string[] {
  const unknown = Array.isArray(item.unknown_fields) ? item.unknown_fields : [];
  return Array.from(new Set(unknown
    .map((field) => text(field))
    .filter(Boolean)
    .map((field) => UNKNOWN_FIELD_LABELS[field.toLowerCase()] || field)));
}

/**
 * 只描述当前响应中可见的证据，不把相关度、confidence 或模型分数解释为准确率。
 * 缺失字段保持“未知”，不会折算为 0。
 */
export function candidateEvidenceSummary(item: VkpiKolRecallItem): CandidateEvidenceSummary {
  const source = sourceRecord(item);
  const representativeCount = Array.isArray(item.representative_evidence)
    ? item.representative_evidence.filter((entry) => entry && typeof entry === "object" && (text(entry.title) || text(entry.content_url))).length
    : null;
  const lensCount = Array.isArray(item.used_lenses)
    ? item.used_lenses.map(text).filter(Boolean).length
    : null;
  const videoCount = positiveCount(source.video_evidence_count);
  const deepAnalysisCount = positiveCount(source.deep_analysis_count ?? source.llm_analysis_count);
  const missingLabels = candidateMissingLabels(item);

  const itemRecord = item as unknown as Record<string, unknown>;
  const explicitQuality = record(itemRecord.evidence_quality ?? source.evidence_quality);
  const explicitConfidence = itemRecord.evidence_confidence ?? source.evidence_confidence;
  const explicitConfidenceValue = finiteScore(explicitConfidence);
  const explicitLevel = text(explicitQuality.level)
    || (explicitConfidenceValue == null ? text(explicitConfidence) : "");
  const explicitLevelKey = explicitLevel.toLowerCase().replace(/[\s_-]+/g, "");
  const explicitCoverage = explicitQuality.coverage
    ?? explicitQuality.coverage_ratio
    ?? explicitQuality.coverage_pct
    ?? source.evidence_coverage;

  const evidenceParts = [
    representativeCount && representativeCount > 0 ? `代表内容 ${representativeCount} 条` : "",
    lensCount && lensCount > 0 ? `器材线索 ${lensCount} 项` : "",
    videoCount ? `视频证据 ${videoCount} 条` : "",
    deepAnalysisCount ? `深析结果 ${deepAnalysisCount} 项` : "",
  ].filter(Boolean);
  const evidenceKinds = evidenceParts.length;

  let grade: EvidenceGrade = "missing";
  let gradeText = "待补";
  if (["high", "strong", "complete", "ready", "verified", "l3", "a"].includes(explicitLevelKey)) {
    grade = "strong";
    gradeText = "较完整";
  } else if (["medium", "moderate", "basic", "partial", "l2", "b"].includes(explicitLevelKey)) {
    grade = "basic";
    gradeText = "基础";
  } else if (["low", "weak", "missing", "insufficient", "unknown", "blocked", "l1", "c"].includes(explicitLevelKey)) {
    grade = "missing";
    gradeText = "待补";
  } else if (representativeCount != null && representativeCount > 0 && evidenceKinds >= 2 && missingLabels.length === 0) {
    grade = "strong";
    gradeText = "较完整";
  } else if (evidenceKinds >= 1) {
    grade = "basic";
    gradeText = "基础";
  }
  const gradeLabel = `证据置信等级 · ${gradeText}`;

  const explicitParts = [
    explicitLevel ? `上游等级 ${explicitLevel}` : "",
    explicitConfidenceValue != null ? `上游证据置信值 ${explicitConfidenceValue.toFixed(3)}` : "",
    explicitCoverage != null && explicitCoverage !== ""
      ? `证据覆盖 ${typeof explicitCoverage === "object" ? "已返回明细" : text(explicitCoverage)}`
      : "",
  ].filter(Boolean);

  const gradeDetailParts = [...explicitParts, ...evidenceParts];
  const gradeDetail = gradeDetailParts.length
    ? `${gradeDetailParts.join(" · ")}${missingLabels.length ? `；仍缺：${missingLabels.join("、")}` : ""}`
    : missingLabels.length
      ? `未返回可核验证据；仍缺：${missingLabels.join("、")}`
      : "本次响应未返回可核验证据字段";

  const bucket = text(item.candidate_bucket ?? item.business_lane ?? item.candidate_lane);
  const matchTier = text(item.match_tier);
  const onlyCandidate = matchTier === "backfill"
    || bucket === "expansion"
    || bucket === "exploration"
    || grade === "missing";
  const whyFit = text(item.why_fit);
  const recallReason = text(item.recall_reason);
  const bucketReason = text(item.candidate_bucket_reason);

  let reason = whyFit || recallReason || bucketReason || "推荐依据待补";
  if (onlyCandidate) {
    reason = bucketReason
      || recallReason
      || (matchTier === "backfill"
        ? "严格相关候选不足，仅按查询相关性补位；显式硬筛选未放宽"
        : bucket === "expansion" || bucket === "exploration"
          ? "具备查询相关性，但垂直内容证据仍待补齐"
          : "可核验证据不足，暂不作为确定性推荐");
  }

  return {
    grade,
    gradeLabel,
    gradeDetail,
    missingLabels,
    reasonLabel: onlyCandidate ? "为何仅候选" : "为何推荐",
    reason,
    onlyCandidate,
  };
}
