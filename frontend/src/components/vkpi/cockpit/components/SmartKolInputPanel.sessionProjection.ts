import {
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
} from "../../../../domains/kol";

import { asRecord, cleanText, display, type Row } from "./SmartKolInputPanel.helpers";
import {
  recallCandidateDistribution,
  recallCandidateFacets,
  recallMatchEvidence,
} from "./SmartKolInputPanel.evidence";

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

export function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
  const creator: VkpiKolRecallItem[] = [];
  const reviewer: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
    if (cleanText(item.item_type) !== "recall_candidate") return;
    const payload = asRecord(item.payload);
    const bucket: "creator" | "reviewer" = cleanText(payload.bucket) === "reviewer" ? "reviewer" : "creator";
    const matchEvidence = recallMatchEvidence(payload.match_evidence);
    const row = {
      bucket,
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || item.item_type, "creator"),
      followers: Number(payload.followers || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.recall_rank_score ?? payload.vector_score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      display_rank_score: Number(payload.display_rank_score ?? item.score ?? payload.recall_rank_score ?? 0),
      display_relevance_adjust: Number(payload.display_relevance_adjust ?? 0),
      relevance_flags: Array.isArray(payload.relevance_flags)
        ? (payload.relevance_flags as unknown[]).map(cleanText).filter(Boolean)
        : [],
      relevance_tier_hint: cleanText(payload.relevance_tier_hint),
      match_evidence: matchEvidence,
      candidate_facets: recallCandidateFacets(payload.candidate_facets),
      type_label: bucket === "reviewer" ? "测评号" : "创作者",
      creator_type_score: bucket === "creator" ? 1 : 0,
      reviewer_type_score: bucket === "reviewer" ? 1 : 0,
      recall_reason: matchEvidence.length ? cleanText(payload.evidence || payload.sample_title) : "",
      why_fit: matchEvidence.length ? cleanText(payload.why_fit) : "",
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? 0) || null,
      source_fields: payload,
    } as VkpiKolRecallItem;
    if (bucket === "reviewer") reviewer.push(row);
    else creator.push(row);
  });
  const summary = asRecord(session.result_summary);
  const querySummary = asRecord(summary.query);
  const diagnostics = asRecord(summary.diagnostics);
  const llmQueryPlan = asRecord(summary.llm_query_plan);
  return {
    method: "search_session_history",
    query: { query_text: display(querySummary.query_text || summary.query || session.query_text, "") },
    ratio: {
      creator_quota: creator.length,
      reviewer_quota: reviewer.length,
      policy: "history",
      mixed_policy: "history",
      dedupe: true,
    },
    items: [...creator, ...reviewer],
    buckets: { creator, reviewer },
    diagnostics: {
      ...diagnostics,
      candidate_count: Number(diagnostics.candidate_count ?? session.item_count ?? creator.length + reviewer.length),
      creator_returned: Number(diagnostics.creator_returned ?? creator.length),
      reviewer_returned: Number(diagnostics.reviewer_returned ?? reviewer.length),
      returned_count: creator.length + reviewer.length,
    },
    match_status: cleanText(summary.match_status),
    candidate_set_distribution: recallCandidateDistribution(summary.candidate_set_distribution),
    ...(Object.keys(llmQueryPlan).length ? { llm_query_plan: llmQueryPlan } : {}),
    snapshot_complete: session.items_snapshot_complete === true || summary.items_snapshot_complete === true,
  } satisfies VkpiKolRecallResponse;
}

export function discoveryItemsFromSession(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  const out: VkpiKolRecallItem[] = [];
  const indexByIdentity = new Map<string, number>();
  discoveryItemsFromSessionRaw(session).forEach((item) => {
    const handle = cleanText(item.handle).toLowerCase().replace(/^@/, "");
    const platform = cleanText(item.platform).toLowerCase();
    const identity = handle && handle !== "unknown" ? `${platform}:${handle}` : cleanText(item.profile_url).toLowerCase();
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
      followers: Number(payload.followers || payload.follower_count || payload.subscriber_count || payload.subscribers || payload.avg_views || payload.views || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: itemType === "existing_kol" ? "库内已有" : "全网发现",
      creator_type_score: 1,
      reviewer_type_score: 0,
      recall_reason: cleanText(payload.sample_title || payload.evidence),
      why_fit: cleanText(payload.why_fit || payload.sample_title),
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? payload.views ?? 0) || null,
      source_fields: payload,
    } as VkpiKolRecallItem);
  });
  return out;
}
