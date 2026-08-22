import { describe, expect, it } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  discoveryItemsFromSession,
  mergeKolRecallSnapshots,
  mergeKolSearchSessionSnapshots,
  reachFloorDisplayFromSession,
  recallResultFromSession,
} from "./SmartKolInputPanel.derivers";

const recallItem = (kolPoolId: number, handle: string) => ({
  bucket: "creator" as const,
  kol_pool_id: kolPoolId,
  handle,
  display_name: handle,
  platform: "youtube",
  profile_type: "creator",
  followers: 40_000,
  profile_url: `https://www.youtube.com/@${handle}`,
  recall_rank_score: 0.9,
  vector_score: 0.9,
  type_label: "创作者",
  creator_type_score: 1,
  reviewer_type_score: 0,
  recall_reason: "portrait",
  why_fit: "bio 命中 portrait",
  source_fields: {},
});


describe("SmartKolInputPanel three-frame session projection", () => {
  it("puts only recall candidates in frame 2 and discovery/existing matches in frame 3", () => {
    const session = {
      id: 901,
      status: "ready",
      items: [
        {
          id: 1,
          item_type: "recall_candidate",
          kol_pool_id: 101,
          score: 0.91,
          payload: { bucket: "creator", platform: "youtube", handle: "local-recall" },
        },
        {
          id: 2,
          item_type: "existing_kol",
          kol_pool_id: 102,
          score: 0.82,
          payload: { platform: "instagram", handle: "discovered-existing" },
        },
        {
          id: 3,
          item_type: "new_creator",
          score: 0.73,
          payload: { platform: "tiktok", handle: "discovered-new" },
        },
      ],
    } as unknown as VkpiKolSearchHistoryItem;

    expect(recallResultFromSession(session).items.map((item) => item.kol_pool_id)).toEqual([101]);
    expect(discoveryItemsFromSession(session).map((item) => item.handle)).toEqual([
      "discovered-existing",
      "discovered-new",
    ]);
  });

  it("attributes hidden existing matches to frame 3 rather than frame 2", () => {
    const display = reachFloorDisplayFromSession({
      reach_floor_display: {
        by_type: {
          recall_candidate: { hidden_low_reach: 2, hidden_analyzing: 1 },
          existing_kol: { hidden_low_reach: 3, hidden_analyzing: 4, visible_analyzing: 1 },
          new_creator: { hidden_low_reach: 5, hidden_analyzing: 6, visible_analyzing: 2 },
        },
      },
    });

    expect(display).toEqual({
      recall: { lowReach: 2, analyzing: 1, pendingFollowers: 0 },
      discovery: { lowReach: 8, analyzing: 10, pendingFollowers: 3 },
    });
  });
});


describe("SmartKolInputPanel authoritative polling snapshots", () => {
  it("removes rows omitted by a full reach-gated backend snapshot", () => {
    const previous = {
      id: 902,
      status: "running",
      item_count: 2,
      items: [
        {
          id: 11,
          item_type: "recall_candidate",
          kol_pool_id: 201,
          payload: { platform: "youtube", handle: "still-visible" },
        },
        {
          id: 12,
          item_type: "existing_kol",
          kol_pool_id: 202,
          payload: { platform: "instagram", handle: "now-hidden" },
        },
      ],
    } as unknown as VkpiKolSearchHistoryItem;
    const authoritative = {
      id: 902,
      status: "running",
      items_snapshot_complete: true,
      item_count: 1,
      items: [
        {
          id: 11,
          item_type: "recall_candidate",
          kol_pool_id: 201,
          payload: { platform: "youtube", handle: "still-visible", followers: 44_000 },
        },
      ],
      reach_floor_display: {
        hidden_low_reach: 1,
        hidden_analyzing: 0,
        by_type: {
          existing_kol: { hidden_low_reach: 1, hidden_analyzing: 0 },
        },
      },
    } as unknown as VkpiKolSearchHistoryItem;

    const merged = mergeKolSearchSessionSnapshots(previous, authoritative);

    expect(merged.items?.map((item) => item.kol_pool_id)).toEqual([201]);
    expect(merged.item_count).toBe(1);
    expect(merged.items?.[0].payload).toMatchObject({ followers: 44_000 });
  });

  it("keeps prior rows for a legacy empty snapshot without the completeness marker", () => {
    const previous = {
      id: 904,
      status: "running",
      items: [{
        id: 21,
        item_type: "recall_candidate",
        kol_pool_id: 301,
        payload: { platform: "youtube", handle: "keep-for-legacy" },
      }],
    } as unknown as VkpiKolSearchHistoryItem;
    const legacySparse = {
      id: 904,
      status: "running",
      items: [],
    } as unknown as VkpiKolSearchHistoryItem;

    const merged = mergeKolSearchSessionSnapshots(previous, legacySparse);

    expect(merged.items?.map((item) => item.kol_pool_id)).toEqual([301]);
  });

  it("removes old recall cards when the incoming recall snapshot is complete", () => {
    const keep = recallItem(401, "still-visible");
    const remove = recallItem(402, "now-hidden");
    const previous = {
      method: "search_session_history",
      query: { query_text: "portrait" },
      ratio: { creator_quota: 2, reviewer_quota: 0, policy: "history", mixed_policy: "history", dedupe: true },
      items: [keep, remove],
      buckets: { creator: [keep, remove], reviewer: [] },
      diagnostics: { returned_count: 2 },
    };
    const incoming = {
      ...previous,
      snapshot_complete: true,
      ratio: { ...previous.ratio, creator_quota: 1 },
      items: [keep],
      buckets: { creator: [keep], reviewer: [] },
      diagnostics: { returned_count: 1 },
    };

    const merged = mergeKolRecallSnapshots(previous, incoming);

    expect(merged.items.map((item) => item.kol_pool_id)).toEqual([401]);
    expect(merged.buckets.creator.map((item) => item.kol_pool_id)).toEqual([401]);
  });

  it("keeps initial preview rows while an unowned queued recall session is empty", () => {
    const previewItems = [1, 2, 3, 4].map((id) => recallItem(500 + id, `preview-${id}`));
    const previous = {
      method: "vector+structured+relation",
      query: { query_text: "portrait" },
      ratio: { creator_quota: 4, reviewer_quota: 0, policy: "smart", mixed_policy: "smart", dedupe: true },
      items: previewItems,
      buckets: { creator: previewItems, reviewer: [] },
      diagnostics: { returned_count: 4 },
      local_qualification: { schema: "smart_local_qualified_v2", qualified_count: 4, returned_count: 4 },
    };
    const queued = recallResultFromSession({
      id: 786,
      query_type: "text_recall",
      status: "running",
      items_snapshot_complete: true,
      items: [],
      result_summary: { smart_search_profile_advance_job: { status: "queued" } },
    } as unknown as VkpiKolSearchHistoryItem);

    expect(queued.snapshot_complete).toBe(false);
    const merged = mergeKolRecallSnapshots(previous, queued);
    expect(merged.items.map((item) => item.kol_pool_id)).toEqual([501, 502, 503, 504]);
    expect(merged.local_qualification).toMatchObject({ qualified_count: 4, returned_count: 4 });
  });

  it("deletes preview rows after the worker owns an explicitly complete empty recall", () => {
    const previewItems = [1, 2, 3, 4].map((id) => recallItem(600 + id, `preview-${id}`));
    const previous = {
      method: "vector+structured+relation",
      query: { query_text: "portrait" },
      ratio: { creator_quota: 4, reviewer_quota: 0, policy: "smart", mixed_policy: "smart", dedupe: true },
      items: previewItems,
      buckets: { creator: previewItems, reviewer: [] },
      diagnostics: { returned_count: 4 },
    };
    const completedEmpty = recallResultFromSession({
      id: 787,
      query_type: "text_recall",
      status: "partial",
      items_snapshot_complete: true,
      recall_snapshot_complete: true,
      items: [],
      result_summary: {
        kind: "kol_recall",
        recall_snapshot_attached: true,
        recall_snapshot_complete: true,
        match_status: "empty",
        local_qualification: {
          schema: "smart_local_qualified_v2",
          qualified_count: 0,
          returned_count: 0,
        },
      },
    } as unknown as VkpiKolSearchHistoryItem);

    expect(completedEmpty.snapshot_complete).toBe(true);
    const merged = mergeKolRecallSnapshots(previous, completedEmpty);
    expect(merged.items).toEqual([]);
    expect(merged.buckets).toEqual({ creator: [], reviewer: [] });
    expect(merged.local_qualification).toMatchObject({ qualified_count: 0, returned_count: 0 });
  });
});


describe("SmartKolInputPanel search-plan history restoration", () => {
  it("restores llm_query_plan from the durable result summary", () => {
    const plan = {
      status: "ready",
      search_query: "professional wedding videographer",
      target_persona: "Professional wedding filmmakers",
      resolved_product: { sku: "DC-550", model_name: "DC-550 Pro" },
      provider_calls_performed: false,
    };
    const result = recallResultFromSession({
      id: 903,
      query_text: "DC-550 wedding creators",
      result_summary: {
        query: { query_text: "professional wedding videographer" },
        llm_query_plan: plan,
      },
      items: [],
    });

    expect((result as unknown as Record<string, unknown>).llm_query_plan).toEqual(plan);
  });

  it("does not revive a legacy free-text fit claim without field evidence", () => {
    const result = recallResultFromSession({
      id: 905,
      query_text: "portrait creator",
      items: [{
        id: 31,
        item_type: "recall_candidate",
        kol_pool_id: 501,
        payload: {
          bucket: "creator",
          handle: "legacy-row",
          platform: "youtube",
          evidence: "画像与产品人群相近",
          why_fit: "适合该产品",
        },
      }],
    } as unknown as VkpiKolSearchHistoryItem);

    expect(result.items[0]?.match_evidence).toEqual([]);
    expect(result.items[0]?.why_fit).toBe("");
    expect(result.items[0]?.recall_reason).toBe("");
  });
});
