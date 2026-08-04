import { describe, expect, it } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  mergeKolRecallSnapshots,
  recallResultFromSession,
  sanitizeKolRecallSnapshot,
} from "./SmartKolInputPanel.derivers";

const canonicalSession = (): VkpiKolSearchHistoryItem => ({
  id: 99,
  query_type: "text_recall",
  query_text: "26mm reviewer",
  status: "ready",
  result_summary: {
    method: "vector_recall",
    query: { query_text: "26mm reviewer" },
    ratio: { creator_quota: 1, reviewer_quota: 1, policy: "soft", mixed_policy: "dominant", dedupe: true },
    evaluation_status: { state: "not_evaluated", target_count: 360 },
    diagnostics: { final_count: 3, returned_count: 3 },
    replay_contract: {
      schema: "kol_recall_candidate_v2",
      source: "canonical_items",
      complete: true,
      source_count: 3,
      persisted_count: 3,
      missing_count: 0,
    },
  },
  items: [
    {
      id: 201,
      item_type: "recall_candidate",
      rank: 1,
      score: 0,
      kol_pool_id: 2,
      source_url: "https://example.test/reviewer-two",
      payload: {
        session_payload_schema: "kol_recall_candidate_v2",
        session_replay_complete: true,
        session_replay_source: "canonical_items",
        bucket: "reviewer",
        handle: "reviewer_two",
        profile_type: "reviewer",
        type_label: "测评号",
        followers: 420000,
        avg_views: 18000,
        recall_rank_score: 0,
        vector_score: 0.91,
        match_tier: "strict",
        candidate_bucket: "core_vertical",
        why_fit: "作品证据与产品场景匹配",
        evidence_quality: { video_evidence_count: 4, deep_analysis_count: 2 },
        representative_evidence: [{ title: "26mm field review", content_url: "https://example.test/v/1" }],
        unknown_fields: [],
        data_truth: {
          fields: {
            followers: { status: "observed", displayable: true },
            avg_views: { status: "declared", displayable: true },
          },
        },
      },
    },
    {
      id: 202,
      item_type: "recall_candidate",
      rank: 2,
      score: 0.74,
      kol_pool_id: 1,
      source_url: "https://example.test/creator-one",
      payload: {
        session_payload_schema: "kol_recall_candidate_v2",
        session_replay_complete: true,
        session_replay_source: "canonical_items",
        bucket: "creator",
        handle: "creator_one",
        profile_type: "creator",
        type_label: "创作者",
        robust_rank_score: 0.74,
        recall_rank_score: 0.7,
        creator_type_score: 82,
        reviewer_type_score: 18,
        match_tier: "relaxed",
        candidate_bucket: "expansion",
        why_fit: "人像创作画像匹配",
        evidence_quality: { video_evidence_count: 3, deep_analysis_count: 1 },
        representative_evidence: [{ title: "portrait setup" }],
        unknown_fields: ["language"],
        data_truth: { language: { status: "missing" } },
      },
    },
    {
      id: 203,
      item_type: "recall_candidate",
      rank: 3,
      score: null,
      kol_pool_id: 3,
      source_url: "https://example.test/unclassified-three",
      payload: {
        session_payload_schema: "kol_recall_candidate_v2",
        session_replay_complete: true,
        session_replay_source: "canonical_items",
        bucket: "unknown",
        handle: "unclassified_three",
        profile_type: "unknown",
        type_label: "未分类",
        match_tier: "backfill",
        candidate_bucket: "exploration",
        why_fit: "仅相关性补位",
        evidence_quality: { video_evidence_count: 0, deep_analysis_count: 0 },
        representative_evidence: [],
        unknown_fields: ["profile_type"],
        data_truth: { profile_type: { status: "missing" } },
      },
    },
  ],
});

describe("KOL recall attach -> session replay fidelity", () => {
  it("restores canonical order, every bucket, audit fields, and null score semantics", () => {
    const result = recallResultFromSession(canonicalSession());

    expect(result.items.map((item) => item.kol_pool_id)).toEqual([2, 1, 3]);
    expect(result.buckets.reviewer.map((item) => item.kol_pool_id)).toEqual([2]);
    expect(result.buckets.creator.map((item) => item.kol_pool_id)).toEqual([1]);
    expect(result.buckets.unknown?.map((item) => item.kol_pool_id)).toEqual([3]);

    expect(result.items[0]).toMatchObject({
      recall_rank_score: 0,
      vector_score: 0.91,
      match_tier: "strict",
      candidate_bucket: "core_vertical",
      why_fit: "作品证据与产品场景匹配",
      evidence_quality: { video_evidence_count: 4, deep_analysis_count: 2 },
      representative_evidence: [{ title: "26mm field review" }],
      unknown_fields: [],
      followers: 420000,
      avg_views: 18000,
      data_truth: {
        fields: {
          followers: { status: "observed", displayable: true },
          avg_views: { status: "declared", displayable: true },
        },
      },
      session_replay_complete: true,
    });
    expect(result.items[1]).toMatchObject({
      robust_rank_score: 0.74,
      creator_type_score: 82,
      reviewer_type_score: 18,
      unknown_fields: ["language"],
    });
    expect(result.items[2]).toMatchObject({
      bucket: "unknown",
      robust_rank_score: null,
      retrieval_score: null,
      recall_rank_score: null,
      vector_score: null,
      display_rank_score: null,
      creator_type_score: null,
      reviewer_type_score: null,
    });
    expect(result.diagnostics).toMatchObject({
      returned_count: 3,
      unknown_returned: 1,
      session_replay_contract: "kol_recall_candidate_v2",
      session_replay_complete: true,
      session_replay_incomplete_count: 0,
    });
    expect(result.evaluation_status).toMatchObject({ state: "not_evaluated", target_count: 360 });
  });

  it("keeps legacy rows readable without inventing scores and marks them incomplete", () => {
    const result = recallResultFromSession({
      id: 100,
      query_type: "text_recall",
      result_summary: {},
      items: [{
        id: 301,
        item_type: "recall_candidate",
        rank: 1,
        score: null,
        kol_pool_id: 7,
        payload: { bucket: "unknown", handle: "legacy_unknown", followers: 999999, avg_views: 888888 },
      }],
    });

    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toMatchObject({
      bucket: "unknown",
      recall_rank_score: null,
      vector_score: null,
      robust_rank_score: null,
      creator_type_score: null,
      reviewer_type_score: null,
      followers: null,
      avg_views: null,
      session_replay_contract: "legacy_incomplete",
      session_replay_complete: false,
    });
    expect(result.items[0].session_replay_missing_fields).toEqual(expect.arrayContaining([
      "match_tier",
      "candidate_bucket",
      "evidence_quality",
      "data_truth",
    ]));
    expect(result.diagnostics).toMatchObject({
      returned_count: 1,
      unknown_returned: 1,
      session_replay_contract: "legacy_incomplete",
      session_replay_complete: false,
      session_replay_incomplete_count: 1,
    });
  });

  it("marks an empty session without a replay contract as legacy incomplete", () => {
    const result = recallResultFromSession({
      id: 102,
      query_type: "text_recall",
      result_summary: {},
      items: [],
    });

    expect(result.items).toEqual([]);
    expect(result.diagnostics).toMatchObject({
      returned_count: 0,
      session_replay_contract: "legacy_incomplete",
      session_replay_complete: false,
      session_replay_incomplete_count: 0,
    });
  });

  it("does not regroup or discard unknown candidates when polling snapshots merge", () => {
    const restored = recallResultFromSession(canonicalSession());
    const sparse = recallResultFromSession(canonicalSession());
    const merged = mergeKolRecallSnapshots(restored, sparse);

    expect(merged.items.map((item) => item.kol_pool_id)).toEqual([2, 1, 3]);
    expect(merged.buckets.unknown?.map((item) => item.kol_pool_id)).toEqual([3]);
  });

  it("lets a newer incomplete truth contract clear stale browser metrics", () => {
    const previous = recallResultFromSession(canonicalSession());
    const incoming = recallResultFromSession({
      id: 103,
      query_type: "text_recall",
      result_summary: {},
      items: [{
        id: 401,
        item_type: "recall_candidate",
        rank: 1,
        kol_pool_id: 2,
        source_url: "https://example.test/reviewer-two",
        payload: {
          bucket: "reviewer",
          handle: "reviewer_two",
          followers: 742000,
          avg_views: 98000,
          representative_evidence: [{
            title: "unreceipted legacy video",
            content_url: "https://example.test/legacy-video",
            view_count: 123456,
          }],
          audience_preview: { status: "ready", method: "legacy_guess" },
        },
      }],
    });

    const merged = mergeKolRecallSnapshots(previous, incoming);
    const reviewer = merged.items.find((item) => item.kol_pool_id === 2)!;
    expect(reviewer).toMatchObject({
      followers: null,
      avg_views: null,
      representative_evidence: [],
      data_truth: null,
      session_replay_complete: false,
    });
    expect((reviewer.source_fields as Record<string, unknown>).followers).toBeNull();
    expect((reviewer.source_fields as Record<string, unknown>).avg_views).toBeNull();
    expect((reviewer.source_fields as Record<string, unknown>).audience_preview).toBeNull();
  });

  it("sanitizes terminal browser snapshots even when no later poll will arrive", () => {
    const sanitized = sanitizeKolRecallSnapshot({
      method: "persisted_browser_snapshot",
      query: { query_text: "camera lens creator" },
      ratio: { creator_quota: 1, reviewer_quota: 1, policy: "soft", mixed_policy: "dominant", dedupe: true },
      diagnostics: {},
      buckets: {
        creator: [],
        reviewer: [{
          bucket: "reviewer",
          kol_pool_id: 2,
          handle: "reviewer_two",
          followers: 742000,
          avg_views: 98000,
          avg_likes: 2000,
          data_truth: {
            fields: {
              followers: { displayable: false },
              avg_views: { displayable: true },
              avg_likes: { displayable: false },
              audience_estimated: { displayable: false },
            },
          },
          source_fields: {
            followers: 742000,
            avg_views: 98000,
            avg_likes: 2000,
            audience_preview: { status: "ready", method: "legacy_guess" },
          },
          representative_evidence: [{
            content_url: "https://example.test/unreceipted",
            view_count: 123456,
          }],
        }],
      },
    });

    const reviewer = sanitized.items[0];
    expect(reviewer).toMatchObject({ followers: null, avg_views: 98000, avg_likes: null });
    expect(reviewer.representative_evidence).toEqual([]);
    expect((reviewer.source_fields as Record<string, unknown>)).toMatchObject({
      followers: null,
      avg_views: 98000,
      avg_likes: null,
      audience_preview: null,
    });
  });

  it("ignores non-recall orchestration rows in a mixed session", () => {
    const result = recallResultFromSession({
      id: 101,
      query_type: "text_recall",
      result_summary: {},
      items: [
        { id: 1, item_type: "url_video", payload: { handle: "not_a_candidate" } },
        { id: 2, item_type: "new_creator", payload: { handle: "belongs_in_discovery" } },
        { id: 3, item_type: "unknown", payload: { handle: "untyped_orchestration" } },
        {
          id: 4,
          item_type: "recall_candidate",
          kol_pool_id: 44,
          payload: { bucket: "creator", handle: "actual_recall" },
        },
        {
          id: 5,
          item_type: "unknown",
          kol_pool_id: 45,
          payload: {
            session_payload_schema: "kol_recall_candidate_v2",
            session_replay_complete: true,
            bucket: "unknown",
            handle: "rolling_upgrade_recall",
          },
        },
      ],
    });

    expect(result.items.map((item) => item.kol_pool_id)).toEqual([44, 45]);
    expect(result.items.map((item) => item.handle)).toEqual(["actual_recall", "rolling_upgrade_recall"]);
  });
});
