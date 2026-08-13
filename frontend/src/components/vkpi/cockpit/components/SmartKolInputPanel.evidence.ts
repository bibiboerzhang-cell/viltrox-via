const MATCH_FIELDS = new Set([
  "handle", "display_name", "bio", "primary_topic", "content_style",
  "secondary_topics_json", "profile_text", "type_reason", "representative_evidence.title",
]);
const FACET_NAMES = [
  "platform", "country", "language", "profile_type", "contact_available", "video_evidence",
] as const;

export type RecallMatchEvidence = { field: string; term: string; source: string };

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function looksLikeContactValue(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  return value.includes("@")
    || /(?:https?:\/\/|www\.)/i.test(value)
    || (digits.length >= 7 && /^[+()\d.\s-]+$/.test(value));
}

export function recallMatchEvidence(value: unknown): RecallMatchEvidence[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 12).flatMap((candidate) => {
    const item = record(candidate);
    const field = String(item.field || "").slice(0, 48);
    const term = String(item.term || "").slice(0, 80);
    const source = String(item.source || "");
    return MATCH_FIELDS.has(field) && term && !looksLikeContactValue(term) && source === "server_profile_evidence"
      ? [{ field, term, source }]
      : [];
  });
}

export function recallCandidateFacets(value: unknown): Record<string, string> {
  const source = record(value);
  return Object.fromEntries(FACET_NAMES.flatMap((name) => {
    const text = String(source[name] || "").toLowerCase().slice(0, 40);
    if ((name === "contact_available" || name === "video_evidence") && !["yes", "no", "unknown"].includes(text)) {
      return [];
    }
    return /^[a-z][a-z0-9 _-]{0,39}$|^unknown$/.test(text) ? [[name, text]] : [];
  }));
}

export function recallCandidateDistribution(value: unknown): Record<string, unknown> | undefined {
  const source = record(value);
  const denominator = Number(source.denominator);
  if (source.claim_status !== "descriptive_only" || !Number.isInteger(denominator) || denominator < 0 || denominator > 30) {
    return undefined;
  }
  const sourceFacets = record(source.facets);
  const facets: Record<string, Record<string, number>> = {};
  for (const name of FACET_NAMES) {
    const counts: Record<string, number> = {};
    for (const [label, rawCount] of Object.entries(record(sourceFacets[name])).slice(0, 32)) {
      const safeLabel = recallCandidateFacets({ [name]: label })[name];
      const count = Number(rawCount);
      if (safeLabel && Number.isInteger(count) && count >= 0 && count <= denominator) counts[safeLabel] = count;
    }
    if (Object.values(counts).reduce((sum, count) => sum + count, 0) !== denominator) return undefined;
    facets[name] = counts;
  }
  return {
    claim_status: "descriptive_only",
    denominator,
    denominator_definition: "returned_canonical_candidates",
    facets,
  };
}

const DISTRIBUTION_DIMENSION_LABELS: Record<string, string> = {
  platform: "平台",
  country: "市场",
  profile_type: "类型",
  contact_available: "联系方式",
  video_evidence: "作品证据",
};

const DISTRIBUTION_VALUE_LABELS: Record<string, string> = {
  yes: "有",
  no: "无",
  unknown: "未知",
  gb: "UK",
  us: "US",
};

export type RecallDistributionView = {
  denominator: number;
  chips: { dimension: string; label: string; count: number }[];
};

export function recallDistributionView(value: unknown): RecallDistributionView | null {
  const distribution = recallCandidateDistribution(value);
  if (!distribution) return null;
  const denominator = Number(distribution.denominator);
  if (denominator <= 0) return null;
  const facets = record(distribution.facets);
  const chips: RecallDistributionView["chips"] = [];
  for (const dimension of ["platform", "country", "profile_type", "contact_available", "video_evidence"]) {
    const counts = Object.entries(record(facets[dimension]))
      .map(([label, count]) => [label, Number(count)] as const)
      .filter(([, count]) => count > 0)
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, 3);
    for (const [label, count] of counts) {
      const readable = DISTRIBUTION_VALUE_LABELS[label] || (dimension === "platform" ? label : label.toUpperCase());
      chips.push({
        dimension,
        label: `${DISTRIBUTION_DIMENSION_LABELS[dimension]} ${readable}`,
        count,
      });
    }
  }
  return { denominator, chips };
}
