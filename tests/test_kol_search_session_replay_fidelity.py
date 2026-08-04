from __future__ import annotations

from app.domains.kol import search_sessions, search_sessions_attach


def _capture_record_items(monkeypatch):
    captured: dict = {}

    def fake_record_items(session_id, items, *, status, summary):
        captured.update(
            {
                "session_id": session_id,
                "items": items,
                "status": status,
                "summary": summary,
            }
        )
        return {"id": session_id, "items": items, "status": status, "result_summary": summary}

    monkeypatch.setattr(search_sessions, "record_items", fake_record_items)
    return captured


def test_attach_recall_preserves_canonical_order_buckets_and_truth_fields(monkeypatch):
    captured = _capture_record_items(monkeypatch)
    canonical = [
        {
            "kol_pool_id": 2,
            "bucket": "reviewer",
            "handle": "reviewer_two",
            "profile_url": "https://example.test/reviewer-two",
            "recall_rank_score": 0.0,
            "vector_score": 0.91,
            "match_tier": "strict",
            "candidate_bucket": "core_vertical",
            "why_fit": "作品证据与产品场景匹配",
            "evidence_quality": {"video_evidence_count": 4, "deep_analysis_count": 2},
            "representative_evidence": [{"title": "26mm field review", "content_url": "https://example.test/v/1"}],
            "unknown_fields": [],
            "data_truth": {"followers": {"status": "observed"}},
            "source_fields": {
                "retrieval_method": "provider_free_pool_text",
                "retrieval_tier": "strict",
                "api_token": "must-not-persist",
                "raw_provider_payload": {"secret": "must-not-persist"},
            },
        },
        {
            "kol_pool_id": 1,
            "bucket": "creator",
            "handle": "creator_one",
            "profile_url": "https://example.test/creator-one",
            "robust_rank_score": 0.74,
            "recall_rank_score": 0.7,
            "match_tier": "relaxed",
            "candidate_bucket": "expansion",
            "why_fit": "人像创作画像匹配",
            "evidence_quality": {"video_evidence_count": 3, "deep_analysis_count": 1},
            "representative_evidence": [{"title": "portrait setup"}],
            "unknown_fields": ["language"],
            "data_truth": {"language": {"status": "missing"}},
        },
        {
            "kol_pool_id": 3,
            "bucket": "unknown",
            "handle": "unclassified_three",
            "profile_url": "https://example.test/unclassified-three",
            "match_tier": "backfill",
            "candidate_bucket": "exploration",
            "why_fit": "仅相关性补位",
            "evidence_quality": {"video_evidence_count": 0, "deep_analysis_count": 0},
            "representative_evidence": [],
            "unknown_fields": ["profile_type"],
            "data_truth": {"profile_type": {"status": "missing"}},
        },
    ]

    search_sessions_attach.attach_recall_result(
        99,
        {
            "method": "vector_recall",
            "items": canonical,
            "buckets": {
                "creator": [canonical[1]],
                "reviewer": [canonical[0]],
                "unknown": [canonical[2]],
            },
            "query": {"query_text": "26mm reviewer"},
            "diagnostics": {"final_count": 3},
            "evaluation_status": {"state": "not_evaluated"},
        },
    )

    persisted = captured["items"]
    assert [item["kol_pool_id"] for item in persisted] == [2, 1, 3]
    assert [item["rank"] for item in persisted] == [1, 2, 3]
    assert [item["payload"]["bucket"] for item in persisted] == ["reviewer", "creator", "unknown"]
    # A real zero is a real zero; an absent score remains NULL.  Neither may be
    # replaced by another field merely because Python truthiness says so.
    assert persisted[0]["score"] == 0.0
    assert persisted[1]["score"] == 0.74
    assert persisted[2]["score"] is None

    for source, item in zip(canonical, persisted, strict=True):
        payload = item["payload"]
        assert payload["session_payload_schema"] == "kol_recall_candidate_v2"
        assert payload["session_replay_complete"] is True
        for field in (
            "match_tier",
            "candidate_bucket",
            "why_fit",
            "evidence_quality",
            "representative_evidence",
            "unknown_fields",
            "data_truth",
        ):
            assert payload[field] == source[field]

    replay = captured["summary"]["replay_contract"]
    assert replay == {
        "schema": "kol_recall_candidate_v2",
        "source": "canonical_items",
        "complete": True,
        "source_count": 3,
        "persisted_count": 3,
        "missing_count": 0,
    }
    assert captured["summary"]["evaluation_status"] == {"state": "not_evaluated"}
    assert persisted[0]["payload"]["source_fields"] == {
        "retrieval_method": "provider_free_pool_text",
        "retrieval_tier": "strict",
    }


def test_attach_recall_legacy_buckets_keep_unknown_but_mark_incomplete(monkeypatch):
    captured = _capture_record_items(monkeypatch)

    search_sessions_attach.attach_recall_result(
        100,
        {
            "items": [],
            "buckets": {
                "creator": [{"kol_pool_id": 1, "bucket": "creator"}],
                "reviewer": [],
                "unknown": [{"kol_pool_id": 3, "bucket": "unknown"}],
            },
            "diagnostics": {"final_count": 2},
        },
    )

    assert [item["kol_pool_id"] for item in captured["items"]] == [1, 3]
    assert captured["items"][1]["payload"]["bucket"] == "unknown"
    assert captured["items"][1]["score"] is None
    assert all(item["payload"]["session_replay_complete"] is False for item in captured["items"])
    assert captured["summary"]["replay_contract"] == {
        "schema": "kol_recall_candidate_v2",
        "source": "legacy_buckets",
        "complete": False,
        "source_count": 2,
        "persisted_count": 2,
        "missing_count": 0,
    }
