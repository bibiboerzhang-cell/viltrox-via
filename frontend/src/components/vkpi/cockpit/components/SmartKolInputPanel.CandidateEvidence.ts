import type { VkpiKolRecallItem } from "../../../../domains/kol";

type EvidenceGrade = "strong" | "basic" | "missing";

export type CandidateRankSummary = {
  score: number | null;
  scoreLabel: "增长候选分" | "本地词项相关度" | "混合相关度" | "向量相关度" | "检索相关度";
  methodLabel: "增长候选排序" | "本地词项排序" | "混合排序" | "向量排序" | "检索排序";
  kind: "growth" | "lexical" | "hybrid" | "vector" | "retrieval";
  sourceField: string | null;
  rawMethod: string;
  detail: string;
};

export type CandidateGrowthDimension = {
  key: "product_use_fit" | "market_activation" | "audience_fit" | "content_execution";
  label: "产品适配" | "市场推进" | "受众适配" | "内容执行";
  weight: 40 | 30 | 15;
  score: number | null;
  displayValue: string;
};

export type CandidateGrowthSummary = {
  active: boolean;
  objective: string;
  score: number | null;
  evidenceConfidence: number | null;
  claimStatus: string;
  dimensions: CandidateGrowthDimension[];
  missingLabels: string[];
  decisionReadiness: string;
  decisionLabel: string;
  strictGatePassed: boolean;
  whyToFind: string[];
  nextAction: string;
  disclaimer: string;
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

function textList(value: unknown, limit = 4): string[] {
  return Array.isArray(value)
    ? Array.from(new Set(value.map(text).filter(Boolean))).slice(0, limit)
    : [];
}

function finiteScore(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function boundedGrowthScore(value: unknown): number | null {
  const parsed = finiteScore(value);
  return parsed != null && parsed >= 0 && parsed <= 100 ? parsed : null;
}

function growthDisplayValue(value: number | null): string {
  if (value == null) return "待补证";
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

/**
 * 统一读取本地实时召回与联网会话投影中的增长候选评分。联网结果把安全投影放在
 * source_fields 中，本地结果通常直接放在 item 顶层；两条车道必须使用同一套门面口径。
 * 缺失维度永远显示“待补证”，不按 0 分处理。
 */
export function candidateGrowthSummary(item: VkpiKolRecallItem): CandidateGrowthSummary {
  const root = item as unknown as Record<string, unknown>;
  const source = sourceRecord(item);
  const scoring = record(root.growth_candidate_scoring ?? source.growth_candidate_scoring);
  const rationale = record(
    root.selection_rationale
      ?? source.selection_rationale
      ?? scoring.selection_rationale,
  );
  const objective = text(root.objective ?? source.objective ?? scoring.objective).toLowerCase();
  const score = boundedGrowthScore(root.growth_candidate_score ?? source.growth_candidate_score);
  const projected = score != null || objective === "prospective_growth" || Object.keys(scoring).length > 0;
  const dimensionSpecs = [
    ["product_use_fit", "产品适配", 40],
    ["market_activation", "市场推进", 30],
    ["audience_fit", "受众适配", 15],
    ["content_execution", "内容执行", 15],
  ] as const;
  const dimensions = dimensionSpecs.map(([key, label, weight]): CandidateGrowthDimension => {
    const value = boundedGrowthScore(root[key] ?? source[key]);
    return { key, label, weight, score: value, displayValue: growthDisplayValue(value) };
  });
  const evidenceConfidence = boundedGrowthScore(root.evidence_confidence ?? source.evidence_confidence);
  const claimStatus = text(root.claim_status ?? source.claim_status ?? scoring.claim_status).toLowerCase();
  const decisionReadiness = text(rationale.decision_readiness).toLowerCase();
  const strictGatePassed = (
    root.growth_qualification_pass === true
    || source.growth_qualification_pass === true
    || text(rationale.strict_gate_status).toLowerCase() === "passed"
  );
  const reasonCards = Array.isArray(rationale.reason_cards) ? rationale.reason_cards : [];
  const whyToFind = textList(rationale.why_find_this_creator);
  if (!whyToFind.length) {
    reasonCards.forEach((value) => {
      const card = record(value);
      const summary = text(card.summary);
      if (text(card.status) === "observed" && summary && !whyToFind.includes(summary) && whyToFind.length < 4) {
        whyToFind.push(summary);
      }
    });
  }
  const nextAction = text(record(rationale.next_action).label);
  const decisionLabel = strictGatePassed && decisionReadiness === "decision_support_ready"
    ? "严格证据已就绪 · 值得人工复核"
    : strictGatePassed && decisionReadiness === "strict_gate_passed_needs_review"
      ? "已过严格证据 · 仍需补全决策信息"
      : "仅候选 · 待补证";
  return {
    active: projected,
    objective,
    score,
    evidenceConfidence,
    claimStatus,
    dimensions,
    missingLabels: dimensions.filter((dimension) => dimension.score == null).map((dimension) => dimension.label),
    decisionReadiness,
    decisionLabel,
    strictGatePassed,
    whyToFind,
    nextAction,
    disclaimer: claimStatus === "descriptive_only" ? "描述性决策支持，不代表转化" : "",
  };
}

/**
 * 新旧后端兼容的检索分展示。字段名只用于选择来源，UI 永远不把 precision/confidence
 * 命名为准确率；方法未知时使用中性的“检索相关度”。
 */
export function candidateRankSummary(item: VkpiKolRecallItem): CandidateRankSummary {
  const itemRecord = item as unknown as Record<string, unknown>;
  const source = sourceRecord(item);
  const growth = candidateGrowthSummary(item);
  if (growth.active) {
    const missing = growth.missingLabels.length ? `；待补证：${growth.missingLabels.join("、")}` : "";
    const detail = growth.score == null
      ? `增长候选分待补证${missing}`
      : `增长候选分 ${growthDisplayValue(growth.score)} · 增长候选排序${missing}${growth.disclaimer ? `；${growth.disclaimer}` : ""}`;
    return {
      score: growth.score,
      scoreLabel: "增长候选分",
      methodLabel: "增长候选排序",
      kind: "growth",
      sourceField: growth.score == null ? null : "growth_candidate_score",
      rawMethod: "",
      detail,
    };
  }
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
    : `${scoreLabel} ${score.toFixed(3)} · ${methodLabel}；仅用于本次候选排序，不代表业务结果`;

  return { score, scoreLabel, methodLabel, kind, sourceField, rawMethod, detail };
}

/** 与后端 VERTICAL_LABELS_ZH / 筛选面板 VERTICAL_OPTIONS 同源；门面只出中文。 */
const VERTICAL_LABELS: Record<string, string> = {
  lens_review: "镜头评测",
  photography_tutorial: "摄影教程",
  gear_comparison: "器材对比",
  portrait: "人像创作",
  video_creation: "视频创作",
  camera_system: "相机系统",
  vlog: "Vlog",
  lifestyle: "生活方式",
  technology: "科技",
};

export type CandidateVerticalTag = { label: string; reasons: string[] };

/**
 * 卡面垂类标签：后端判到几类就显示几类，每类都带「为什么算他是这一类」。
 * 判不出就是空数组 —— 由卡面显示“垂类未知”，绝不默认归进某一类。
 */
export function candidateVerticalTags(item: VkpiKolRecallItem): CandidateVerticalTag[] {
  const explained = Array.isArray(item.vertical_evidence) ? item.vertical_evidence : [];
  const byVertical = new Map<string, string[]>();
  explained.forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    const label = text(entry.label) || text(entry.vertical);
    if (!label) return;
    const reasons = Array.isArray(entry.reasons) ? entry.reasons.map(text).filter(Boolean) : [];
    byVertical.set(label, reasons);
  });
  const tags = Array.isArray(item.vertical_tags) ? item.vertical_tags.map(text).filter(Boolean) : [];
  tags.forEach((tag) => {
    // 后端只给了 id 没给解释(旧会话回放)时按口径表翻中文;认不出的一律不显示,
    // 绝不把内部 id 摆到卡面上。
    const label = VERTICAL_LABELS[tag.toLowerCase()];
    if (label && !byVertical.has(label)) byVertical.set(label, []);
  });
  return Array.from(byVertical, ([label, reasons]) => ({ label, reasons }));
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
