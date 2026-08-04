from __future__ import annotations

from datetime import datetime, timezone

from app.domains.kol import analysis_readiness as readiness_module
from app.domains.kol.analysis_readiness import (
    build_analysis_readiness,
    evidence_quality_projection,
    load_readiness_video_evidence,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
FRESH = "2026-07-20T12:00:00Z"


def _video(item_id: int, *, view_count=1000) -> dict:
    return {
        "id": item_id,
        "evidence_id": item_id,
        "media_kind": "video",
        "view_count": view_count,
        "metrics_scraped_at": FRESH,
        "updated_at": FRESH,
    }


def _analysis(video: dict, *, qa: bool = False, result: dict | None = None) -> dict:
    return {
        "video": dict(video),
        "final_entry": {
            "status": "ready",
            "result": result or {"method": "gemini_local_fileapi_test"},
            "updated_at": FRESH,
        },
        "qa_entry": {"status": "ready", "updated_at": FRESH} if qa else None,
        "state": "ready",
    }


def _build(*, videos: list[dict], analyses: list[dict], item: dict | None = None) -> dict:
    return build_analysis_readiness(
        item={"id": 42, "updated_at": FRESH, **(item or {})},
        videos=videos,
        analysis_items=analyses,
        llm_deep={"status": "ready", "items": []},
        now=NOW,
    )


def test_empty_evidence_abstains_and_never_upgrades_claim_status() -> None:
    result = _build(videos=[], analyses=[])

    assert result["level"] == "insufficient"
    assert result["claim_status"] == "descriptive_only"
    assert result["decision_mode"] == "abstain"
    assert result["recommendation_status"] == "abstain"
    assert result["abstain"] is True
    assert {gap["code"] for gap in result["blocking_gaps"]} >= {
        "video_sample_insufficient",
        "view_count_coverage_insufficient",
        "deep_analysis_missing",
    }
    assert result["diagnostics"]["viltrox_fit_score_write"] is False


def test_brand_history_gap_does_not_block_overall_new_creator_readiness() -> None:
    videos = [_video(item_id) for item_id in range(1, 6)]
    analyses = [
        _analysis(videos[0], qa=True),
        _analysis(videos[1]),
        _analysis(videos[2]),
    ]

    result = _build(videos=videos, analyses=analyses)

    assert result["level"] == "decision_ready"
    assert result["decision_mode"] == "human_decision_support"
    assert result["scopes"]["overall"]["level"] == "decision_ready"
    assert result["scopes"]["brand_history"]["level"] == "insufficient"
    assert result["scopes"]["brand_history"]["decision_mode"] == "abstain"
    assert "brand_history_evidence_missing" in {
        gap["code"] for gap in result["scopes"]["brand_history"]["blocking_gaps"]
    }
    assert "brand_history_evidence_missing" not in {
        gap["code"] for gap in result["blocking_gaps"]
    }


def test_fileapi_method_alone_does_not_prove_full_video_coverage() -> None:
    videos = [_video(item_id) for item_id in range(1, 6)]
    analyses = [
        _analysis(videos[0], qa=True),
        _analysis(videos[1]),
        _analysis(videos[2]),
    ]

    result = _build(videos=videos, analyses=analyses)

    assert result["evidence_coverage"]["full_video_proven"] == 0
    assert result["evidence_coverage"]["full_video_unproven"][0]["basis"] == (
        "full_file_input_without_source_completeness_receipt"
    )
    assert result["scopes"]["content_fit"]["level"] == "provisional"
    assert result["scopes"]["content_fit"]["decision_mode"] == "abstain"
    assert "full_video_coverage_unproven" in {
        gap["code"] for gap in result["scopes"]["content_fit"]["blocking_gaps"]
    }


def test_explicit_full_video_and_timestamped_brand_receipt_unlock_scoped_decisions() -> None:
    videos = [_video(item_id, view_count=0 if item_id == 1 else 1000) for item_id in range(1, 6)]
    receipt = {
        "method": "gemini_local_fileapi_test",
        "analysis_coverage": {
            "analysis_scope": "full_video",
            "source_duration_seconds": 600,
            "analyzed_duration_seconds": 600,
        },
        "raw_gemini_video": {"viltrox_detected": True},
        "video_analysis_final_v1": {
            "layer1_visual_content": {
                "evidence": {"timestamps": ["00:14 Viltrox lens shown"]},
            }
        },
    }
    analyses = [
        _analysis(videos[0], qa=True, result=receipt),
        _analysis(videos[1]),
        _analysis(videos[2]),
    ]

    result = _build(videos=videos, analyses=analyses)

    assert result["view_count_completeness"] == {"known": 5, "total": 5, "ratio": 1.0, "unknown": 0}
    assert result["evidence_coverage"]["full_video_proven"] == 1
    assert result["scopes"]["content_fit"]["level"] == "decision_ready"
    assert result["brand_evidence"]["strongest_type"] == "model_detected_with_timestamp_context"
    assert result["scopes"]["brand_history"]["level"] == "decision_ready"
    assert result["claim_status"] == "descriptive_only"


def test_unrelated_timestamp_does_not_upgrade_global_brand_boolean() -> None:
    videos = [_video(item_id) for item_id in range(1, 6)]
    receipt = {
        "raw_gemini_video": {"viltrox_detected": True},
        "video_analysis_final_v1": {
            "layer1_visual_content": {
                "evidence": {"timestamps": ["00:14 generic lens close-up"]},
            }
        },
    }
    analyses = [
        _analysis(videos[0], qa=True, result=receipt),
        _analysis(videos[1]),
        _analysis(videos[2]),
    ]

    result = _build(videos=videos, analyses=analyses)

    assert result["brand_evidence"]["counts"]["model_detected_with_timestamp_context"] == 0
    assert result["brand_evidence"]["counts"]["model_detected_without_timestamp"] == 1
    assert result["brand_evidence"]["strongest_type"] == "model_detected_without_timestamp"
    assert result["scopes"]["brand_history"]["level"] == "provisional"


def test_blank_legacy_media_kind_is_video_but_image_is_excluded() -> None:
    blank_video = {
        **_video(1, view_count=0),
        "media_kind": "  ",
        "evidence_type": "",
    }
    image = {
        **_video(2),
        "media_kind": "",
        "evidence_type": "image",
    }

    result = _build(videos=[blank_video, image], analyses=[])

    assert result["key_sample_count"] == 1
    assert result["view_count_completeness"] == {"known": 1, "total": 1, "ratio": 1.0, "unknown": 0}


def test_truncated_denominator_is_disclosed_and_cannot_be_decision_ready() -> None:
    videos = [_video(item_id) for item_id in range(1, 6)]
    analyses = [
        _analysis(videos[0], qa=True),
        _analysis(videos[1]),
        _analysis(videos[2]),
    ]
    result = build_analysis_readiness(
        item={"id": 42, "updated_at": FRESH},
        videos=videos,
        analysis_items=analyses,
        llm_deep={"status": "ready", "items": []},
        now=NOW,
        sample_scope="active_video_evidence_up_to_200",
        sample_limit=200,
        sample_truncated=True,
    )

    assert result["level"] == "provisional"
    assert result["evidence_coverage"]["denominator_status"] == "partial_at_limit"
    assert result["evidence_coverage"]["sample_truncated"] is True
    assert "evidence_sample_truncated_at_limit" in {gap["code"] for gap in result["warnings"]}


def test_readiness_loader_fetches_one_extra_row_and_discloses_truncation(monkeypatch) -> None:
    calls: list[tuple[str, tuple[int, int]]] = []

    class _Cursor:
        def fetchall(self):
            return [
                {
                    "id": index,
                    "evidence_id": index,
                    "evidence_type": "" if index == 1 else "video",
                    "view_count": index,
                }
                for index in range(1, 202)
            ]

    class _Conn:
        def execute(self, sql, params):
            calls.append((sql, params))
            return _Cursor()

    monkeypatch.setattr(readiness_module, "is_postgres_runtime", lambda: False)

    result = load_readiness_video_evidence(42, limit=200, conn=_Conn())

    assert len(result["items"]) == 200
    assert result["items"][0]["media_kind"] == "video"
    assert result["truncated"] is True
    assert result["sample_scope"] == "active_video_evidence_up_to_200"
    assert calls[0][1] == (42, 201)
    assert "COALESCE(is_active, 1) != 0" in calls[0][0]
    assert "NULLIF(TRIM(media_kind), '')" in calls[0][0]
    assert calls[0][0].index("NULLIF(TRIM(media_kind), '')") < calls[0][0].index("NULLIF(TRIM(evidence_type), '')")


def test_provisional_thresholds_and_compatibility_projection() -> None:
    videos = [_video(1), _video(2), _video(3, view_count=None)]
    result = _build(
        videos=videos,
        analyses=[_analysis(videos[0])],
        item={"brand_collaborations_json": [{"brand": "Viltrox"}]},
    )
    projection = evidence_quality_projection(result)

    assert result["level"] == "provisional"
    assert result["decision_mode"] == "human_review_required"
    assert result["view_count_completeness"]["ratio"] == 0.6667
    assert result["brand_evidence"]["strongest_type"] == "structured_collaboration_record"
    assert result["scopes"]["brand_history"]["level"] == "provisional"
    assert projection["level"] == "provisional"
    assert projection["claim_status"] == "descriptive_only"
    assert projection["key_sample_count"] == 3


def test_stale_evidence_forces_overall_abstention() -> None:
    stale = "2025-12-01T00:00:00Z"
    videos = [{**_video(item_id), "metrics_scraped_at": stale, "updated_at": stale} for item_id in range(1, 6)]
    analyses = [
        {
            **_analysis(videos[index], qa=index == 0),
            "final_entry": {
                **_analysis(videos[index])["final_entry"],
                "updated_at": stale,
            },
        }
        for index in range(3)
    ]
    result = build_analysis_readiness(
        item={"id": 42, "updated_at": stale},
        videos=videos,
        analysis_items=analyses,
        llm_deep={"status": "ready", "items": []},
        now=NOW,
    )

    assert result["freshness"]["status"] == "stale"
    assert result["level"] == "insufficient"
    assert result["decision_mode"] == "abstain"
    assert "evidence_stale" in {gap["code"] for gap in result["blocking_gaps"]}
