from __future__ import annotations

from datetime import datetime, timezone

from app.domains.kol.analysis_readiness import build_analysis_readiness


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
FRESH = "2026-07-20T12:00:00Z"


def _analysis(
    evidence_id: int,
    *,
    status: str = "ready",
    result: dict | None = None,
    qa: bool = False,
) -> dict:
    return {
        "video": {"id": evidence_id},
        "final_entry": {
            "status": status,
            "result": result or {},
            "updated_at": FRESH,
        },
        "qa_entry": {"status": "ready", "updated_at": FRESH} if qa else None,
    }


def test_readiness_characterization_preserves_contract_and_reason_order() -> None:
    videos = [
        {"id": 3, "media_kind": "video", "view_count": "", "metrics_scraped_at": FRESH},
        {"id": 1, "media_kind": "video", "view_count": 0, "metrics_scraped_at": FRESH},
        {"id": 2, "media_kind": "reel", "view_count": 50, "metrics_scraped_at": FRESH},
        {"id": 99, "media_kind": "image", "view_count": 999, "metrics_scraped_at": FRESH},
    ]
    analyses = [
        _analysis(3, result={"method": "gemini_local_fileapi", "viltrox_detected": True}),
        _analysis(
            1,
            qa=True,
            result={
                "analysis_coverage": {
                    "source_duration_seconds": 100,
                    "analyzed_duration_seconds": 96,
                },
                "scenes": [{"timestamp": "00:01", "note": "Viltrox lens"}],
            },
        ),
        _analysis(2, status="pending"),
    ]

    result = build_analysis_readiness(
        item={
            "brand_collaborations_json": '[{"brand":"Other"}]',
            "updated_at": FRESH,
        },
        videos=videos,
        analysis_items=analyses,
        llm_deep={"items": []},
        now=NOW,
        sample_scope="characterization",
        sample_limit=3,
        sample_truncated=True,
    )

    assert list(result) == [
        "version",
        "level",
        "status",
        "claim_status",
        "decision_mode",
        "recommendation_status",
        "abstain",
        "key_sample_count",
        "view_count_completeness",
        "evidence_coverage",
        "brand_evidence",
        "freshness",
        "blocking_gaps",
        "warnings",
        "scopes",
        "thresholds",
        "diagnostics",
    ]
    assert result["view_count_completeness"] == {
        "known": 2,
        "total": 3,
        "ratio": 0.6667,
        "unknown": 1,
    }
    assert result["evidence_coverage"] == {
        "video_total": 3,
        "sample_scope": "characterization",
        "sample_limit": 3,
        "sample_truncated": True,
        "denominator_status": "partial_at_limit",
        "deep_ready": 2,
        "deep_ratio": 0.6667,
        "qa_ready": 1,
        "qa_ratio": 0.3333,
        "full_video_proven": 1,
        "full_video_ratio": 0.3333,
        "full_video_receipts": [
            {"evidence_id": 1, "basis": "duration_coverage_at_least_95pct"}
        ],
        "full_video_unproven": [
            {
                "evidence_id": 3,
                "basis": "full_file_input_without_source_completeness_receipt",
            }
        ],
    }
    assert result["brand_evidence"] == {
        "types": ["model_detected_without_timestamp", "structured_collaboration_record"],
        "counts": {
            "model_detected_with_timestamp_context": 0,
            "model_detected_without_timestamp": 1,
            "structured_collaboration_record": 1,
        },
        "strongest_type": "structured_collaboration_record",
        "claimable_for_brand_history": False,
        "note": "缺少品牌证据不阻断 overall；只影响 brand_history 作用域。",
    }
    assert result["freshness"] == {
        "status": "fresh",
        "profile_latest_at": FRESH,
        "evidence_latest_at": FRESH,
        "analysis_latest_at": FRESH,
        "age_days": {"profile": 14, "evidence": 14, "analysis": 14},
        "decision_age_days": 14,
        "basis": "max_age_of_latest_evidence_and_latest_analysis",
    }
    assert [gap["code"] for gap in result["warnings"]] == [
        "video_sample_below_decision_target",
        "view_count_coverage_below_decision_target",
        "deep_analysis_coverage_below_decision_target",
        "evidence_sample_truncated_at_limit",
    ]
    assert [gap["code"] for gap in result["scopes"]["brand_history"]["warnings"]] == [
        "brand_history_not_timestamp_grounded"
    ]


def test_readiness_characterization_duplicate_analysis_is_last_write_wins() -> None:
    videos = [
        {"id": 2, "media_kind": "video", "view_count": 1, "updated_at": FRESH},
        {"id": 1, "media_kind": "video", "view_count": 1, "updated_at": FRESH},
    ]
    full_receipt = {
        "analysis_coverage": {
            "analysis_scope": "full_video",
            "full_video": True,
        }
    }

    ready_then_pending = build_analysis_readiness(
        item={"updated_at": FRESH},
        videos=videos,
        analysis_items=[
            _analysis(1, result=full_receipt, qa=True),
            _analysis(1, status="pending"),
            _analysis(2, result=full_receipt),
        ],
        llm_deep={"items": []},
        now=NOW,
    )
    pending_then_ready = build_analysis_readiness(
        item={"updated_at": FRESH},
        videos=videos,
        analysis_items=[
            _analysis(1, status="pending"),
            _analysis(1, result=full_receipt, qa=True),
            _analysis(2, result=full_receipt),
        ],
        llm_deep={"items": []},
        now=NOW,
    )

    assert ready_then_pending["evidence_coverage"]["deep_ready"] == 1
    assert ready_then_pending["evidence_coverage"]["qa_ready"] == 0
    assert ready_then_pending["evidence_coverage"]["full_video_receipts"] == [
        {"evidence_id": 2, "basis": "explicit_full_video_scope"}
    ]
    assert pending_then_ready["evidence_coverage"]["deep_ready"] == 2
    assert pending_then_ready["evidence_coverage"]["qa_ready"] == 1
    assert pending_then_ready["evidence_coverage"]["full_video_receipts"] == [
        {"evidence_id": 1, "basis": "explicit_full_video_scope"},
        {"evidence_id": 2, "basis": "explicit_full_video_scope"},
    ]
