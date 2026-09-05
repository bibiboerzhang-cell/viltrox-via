/** Read-only, fail-closed projection of server-owned candidate material. */
type Row = Record<string, unknown>;
const record = (value: unknown): Row => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const positiveId = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
const count = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
const text = (value: unknown, limit = 2000): string | null => typeof value === "string" && value.trim() && value.length <= limit ? value.trim() : null;
const boundedRows = (value: unknown, maximum: number): value is unknown[] => Array.isArray(value) && value.length <= maximum;

export const EVIDENCE_FIELDS: Record<string, string> = {
  bio: "资料简介", country: "资料国家或地区", language: "资料语言", primary_topic: "资料主题", content_style: "资料内容风格",
};
export const EVIDENCE_GAPS: Record<string, string> = {
  match_reason_unverified: "个人匹配理由未核实，需结合产品、市场与内容人工核对。",
  collection_time_unknown: "未提供可确认的采集时间。",
  profile_facts_missing: "缺少可展示的候选资料字段。",
  source_url_missing: "缺少可核对的公开资料网址。",
  record_updated_at_unknown: "资料更新时间未知。",
};
type Source = { ref: string; url: string | null; updatedAt: string | null };
type Fact = { field: string; value: string; sourceRef: string };
type Evidence = { state: "legacy" | "invalid" | "unknown" | "partial"; facts: Fact[]; sources: Source[]; gaps: string[]; omitted: boolean };
const requiredGaps = ["match_reason_unverified", "collection_time_unknown"];
const fallback = (state: Evidence["state"]): Evidence => ({ state, facts: [], sources: [], gaps: requiredGaps, omitted: false });

function sourceUrl(value: unknown): string | null {
  const raw = text(value, 2048);
  if (!raw || /[\s\u0000-\u001f]/.test(raw)) return null;
  try {
    const url = new URL(raw);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash ? raw : null;
  } catch { return null; }
}

function updatedAt(value: unknown): string | null {
  const raw = text(value, 40);
  const match = raw?.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?(?:Z|([+-])(\d{2}):(\d{2}))?)?$/);
  if (!match) return null;
  const [year, month, day] = match.slice(1, 4).map(Number);
  const [hour, minute, second] = match.slice(4, 7).map(value => Number(value ?? 0));
  const calendar = new Date(Date.UTC(year, month - 1, day));
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day
    || hour > 23 || minute > 59 || second > 59 || Number(match[8] ?? 0) > 23 || Number(match[9] ?? 0) > 59) return null;
  return raw; // Preserve record precision and timezone, including their absence.
}

export function candidateEvidence(creatorValue: unknown): Evidence {
  const creator = record(creatorValue);
  if (creator.evidence === undefined || creator.evidence === null) return fallback("legacy");
  const data = record(creator.evidence);
  const id = positiveId(creator.id);
  if (!id || data.schema_version !== "campaign_candidate_evidence.v1" || data.claim_status !== "descriptive_only"
    || data.match_status !== "unverified" || data.observed_at !== null || !["partial", "unknown"].includes(String(data.status))
    || !boundedRows(data.facts, 16) || !boundedRows(data.source_refs, 8) || !boundedRows(data.gaps, 16)) return fallback("invalid");
  const gaps = data.gaps.map(gap => text(record(gap).code, 80));
  if (gaps.some(gap => !gap) || requiredGaps.some(gap => !gaps.includes(gap))) return fallback("invalid");
  const sources: Source[] = [];
  let omitted = false;
  for (const raw of data.source_refs) {
    const source = record(raw);
    if (source.ref !== `kol_pool:${id}` || source.kind !== "kol_pool" || source.record_id !== id || sources.length) {
      omitted = true;
      continue;
    }
    const url = sourceUrl(source.url);
    const date = updatedAt(source.record_updated_at);
    omitted ||= (source.url !== null && !url) || (source.record_updated_at !== null && !date);
    sources.push({ ref: source.ref as string, url, updatedAt: date });
  }
  const facts: Fact[] = [];
  for (const raw of data.facts) {
    const fact = record(raw);
    const field = text(fact.field, 40);
    const value = text(fact.value);
    if (!field || !Object.prototype.hasOwnProperty.call(EVIDENCE_FIELDS, field) || !value || facts.some(row => row.field === field)
      || !sources.some(source => source.ref === fact.source_ref)) { omitted = true; continue; }
    facts.push({ field, value, sourceRef: fact.source_ref as string });
  }
  const hasMaterial = facts.length > 0 || sources.some(source => source.url);
  if (data.status === "unknown" && hasMaterial) return fallback("invalid");
  return { state: hasMaterial ? "partial" : "unknown", facts, sources,
    gaps: [...new Set(gaps as string[])], omitted: omitted || (data.status === "partial" && !hasMaterial) };
}

export function candidateCoverage(groupValue: unknown): { samples: number; missing: number } | null {
  const group = record(groupValue);
  const raw = record(group.candidate_coverage);
  const planned = count(group.count);
  if (planned === null || raw.planned_count !== planned || !boundedRows(group.sample_creators, 32)) return null;
  const ids = group.sample_creators.map(row => positiveId(record(row).id));
  if (ids.some(id => id === null)) return null;
  const unique = new Set(ids).size;
  const expected = unique >= planned ? "count_met" : unique === 0 ? "empty" : "insufficient";
  if (unique > planned || unique !== ids.length || raw.unique_sample_count !== unique || raw.status !== expected || !boundedRows(raw.gaps, 16)) return null;
  if (unique < planned && !raw.gaps.some(gap => record(gap).code === "candidate_samples_insufficient")) return null;
  return { samples: unique, missing: Math.max(0, planned - unique) };
}
