from __future__ import annotations

from typing import Any

from app.domains.kol import content_fit_analysis, content_fit_batch, content_fit_enqueue


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Conn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        return _Rows(self.rows)


def _cache_row() -> dict[str, Any]:
    return {
        "evidence_id": 7,
        "title": "Lens review",
        "content_url": "https://example.test/video/7",
        "platform": "youtube",
        "view_count": 1000,
        "like_count": 100,
        "comment_count": 10,
        "id": 71,
        "target_type": "video",
        "target_id": "7",
        "derive_method": content_fit_analysis.VIDEO_DERIVE_METHOD,
        "model": "gemini-test",
        "prompt_version": "prompt-test",
        "status": "ready",
        "result": {"layer1_visual_content": {"content_summary": "A real scene."}},
    }


def _gated_videos(status: str) -> content_fit_analysis._VideoAnalyses:
    items = [{"evidence_id": 7}] if status == "canonical" else []
    videos = content_fit_analysis._VideoAnalyses(items)
    videos.cache_gate = {
        "status": status,
        "revalidation_required": status == "legacy_unverified",
        "claim_status": "descriptive_only",
        "reasons": ["cache_prompt_contract_mismatch"] if status == "legacy_unverified" else [],
    }
    return videos


def test_loader_reuses_shared_classifier_and_rejects_any_noncanonical_ready_row(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def classify(row: dict[str, Any], **scope: Any) -> dict[str, Any]:
        calls.append({"row": row, **scope})
        return {
            "reusable": False,
            "cache_id": row["id"],
            "cache_reuse_status": "legacy_unverified",
            "revalidation_required": True,
            "claim_status": "descriptive_only",
            "reasons": ["cache_prompt_contract_mismatch"],
        }

    monkeypatch.setattr(content_fit_analysis, "canonical_final_v1_cache_reuse", classify)

    videos = content_fit_analysis._video_analyses(_Conn([_cache_row()]), 42)
    gate = content_fit_analysis._video_analysis_cache_gate(videos)

    assert videos == []
    assert gate == {
        "status": "legacy_unverified",
        "revalidation_required": True,
        "claim_status": "descriptive_only",
        "reasons": ["cache_prompt_contract_mismatch"],
        "cache_ids": [71],
    }
    assert calls[0]["target_type"] == "video"
    assert calls[0]["target_id"] == "7"
    assert calls[0]["derive_method"] == content_fit_analysis.VIDEO_DERIVE_METHOD


def test_loader_preserves_canonical_video_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        content_fit_analysis,
        "canonical_final_v1_cache_reuse",
        lambda row, **_scope: {"reusable": True, "cache_id": row["id"], "reasons": []},
    )

    videos = content_fit_analysis._video_analyses(_Conn([_cache_row()]), 42)

    assert [item["evidence_id"] for item in videos] == [7]
    assert content_fit_analysis._video_analysis_cache_gate(videos)["status"] == "canonical"


def test_analysis_worker_gate_blocks_cache_reuse_and_provider_even_when_forced(monkeypatch) -> None:
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(content_fit_analysis, "_kol_row", lambda *_a, **_k: {"id": 42})
    monkeypatch.setattr(
        content_fit_analysis,
        "_video_analyses",
        lambda *_a, **_k: _gated_videos("legacy_unverified"),
    )
    monkeypatch.setattr(
        content_fit_analysis,
        "_read_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy fit cache must not be reused")),
    )
    monkeypatch.setattr(
        content_fit_analysis.llm_production,
        "generate_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must stay closed")),
    )

    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["status"] == "legacy_unverified"
    assert result["revalidation_required"] is True
    assert result["claim_status"] == "descriptive_only"
    assert result["provider_calls"] is False
    assert result["write_db"] is False


def test_session_gate_skips_legacy_without_readiness_preflight_or_enqueue(monkeypatch) -> None:
    session = {
        "id": 1089,
        "created_by": 34,
        "items": [{"id": 2, "item_type": "recall_candidate", "kol_pool_id": 88, "payload": {}}],
    }
    evidence = content_fit_enqueue._CanonicalVideoEvidenceIds()
    evidence.legacy_unverified = {88: content_fit_analysis._video_analysis_cache_gate(
        _gated_videos("legacy_unverified")
    )}
    monkeypatch.setattr(content_fit_enqueue.search_sessions, "get_session", lambda _sid: session)
    monkeypatch.setattr(content_fit_enqueue, "get_conn", lambda: object())
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_video_evidence", lambda *_a: evidence)
    monkeypatch.setattr(content_fit_enqueue, "_ids_with_existing_fit", lambda *_a: {88})
    monkeypatch.setattr(content_fit_enqueue, "_already_queued_ids", lambda *_a: set())
    monkeypatch.setattr(content_fit_enqueue, "_exposure_potential", lambda *_a: {"exposure_potential": None})
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: (_ for _ in ()).throw(AssertionError("preflight must stay closed")),
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "enqueue_active_apify_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("job must not be enqueued")),
    )

    result = content_fit_enqueue.enqueue_content_fit_for_session(
        session_id=1089,
        provider_actor={"id": 12, "staff_id": 12, "user_id": 34},
    )

    assert result["status"] == "legacy_unverified"
    assert result["revalidation_required"] is True
    assert result["claim_status"] == "descriptive_only"
    assert result["enqueued_count"] == 0
    assert result["write_db"] is False
    assert result["ai_analysis"]["reason"] == "legacy_unverified"


def test_on_demand_gate_blocks_preflight_cache_reuse_and_enqueue(monkeypatch) -> None:
    from app.db import connection

    monkeypatch.setattr(connection, "get_conn", lambda: object())
    monkeypatch.setattr(
        content_fit_analysis,
        "_video_analyses",
        lambda *_a, **_k: _gated_videos("legacy_unverified"),
    )
    monkeypatch.setattr(
        content_fit_analysis,
        "get_content_fit",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy fit cache must not be reused")),
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: (_ for _ in ()).throw(AssertionError("preflight must stay closed")),
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "enqueue_active_apify_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("job must not be enqueued")),
    )

    result = content_fit_enqueue.enqueue_content_fit_on_demand(
        42, "AF-35-PRO", force=False, staff={"user_id": 7}
    )

    assert result["status"] == "legacy_unverified"
    assert result["revalidation_required"] is True
    assert result["claim_status"] == "descriptive_only"
    assert result["write_db"] is False


def test_overnight_build_and_consume_never_submit_or_write_legacy_source(monkeypatch, caplog) -> None:
    legacy = _gated_videos("legacy_unverified")
    monkeypatch.setattr(content_fit_analysis, "_kol_row", lambda *_a, **_k: {"id": 42})
    monkeypatch.setattr(content_fit_analysis, "_video_analyses", lambda *_a, **_k: legacy)
    monkeypatch.setattr(
        content_fit_analysis,
        "_fan_comments",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("prompt must not be built")),
    )

    assert content_fit_batch.build_item(object(), 42) is None

    monkeypatch.setattr(content_fit_batch, "get_conn", lambda: object())
    monkeypatch.setattr(
        content_fit_analysis,
        "_write_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("legacy result must not be cached")),
    )
    result = content_fit_batch.consume(
        {"42": '{"fit_verdict":"fit"}'},
        {"42": {"kol_pool_id": 42}},
    )

    assert result == {
        "written": 0,
        "failed": 0,
        "total": 1,
        "legacy_unverified": 1,
        "revalidation_required": True,
        "claim_status": "descriptive_only",
    }
    assert "legacy_video_cache_unverified" in caplog.text
