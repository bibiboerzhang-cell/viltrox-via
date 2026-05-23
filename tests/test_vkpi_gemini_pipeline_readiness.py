from __future__ import annotations

from scripts import vkpi_gemini_pipeline_readiness


def _go_no_go(decision: str = "hold", *, provider_calls: bool = False) -> dict:
    return {
        "decision": decision,
        "decision_reason": "provider_or_budget_gate_not_ready" if decision == "hold" else "ready",
        "blockers": ["provider_gate:force_offline"] if decision == "hold" else [],
        "provider_calls": provider_calls,
        "llm_calls": provider_calls,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "summary": {
            "candidate_count": 1,
            "valid_video_url": decision != "no_go_for_this_kol",
            "provider_path": "youtube_direct_url_preflight",
            "top_video_url": "https://www.youtube.com/watch?v=top123456",
            "provider_gate_reason": "force_offline",
            "ready_for_manual_live_test": decision == "go_manual_single_call",
        },
    }


def test_gemini_pipeline_readiness_blocks_batch_and_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_gemini_pipeline_readiness.natural_search, "search", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(
        vkpi_gemini_pipeline_readiness.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("hold"),
    )

    report = vkpi_gemini_pipeline_readiness.build_report(
        kol_pool_ids=[123],
        requested_batch_size=500,
        requested_concurrency=9,
    )

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["policy"]["batch_execution_allowed"] is False
    assert report["policy"]["effective_batch_size"] == 30
    assert report["policy"]["effective_concurrency"] == 2
    assert report["checks"]["single_live_required_before_batch"] is True
    assert report["readiness"] == "design_ready_provider_or_budget_hold"
    assert "Gemini Pipeline Readiness" in vkpi_gemini_pipeline_readiness.render_markdown(report)


def test_gemini_pipeline_readiness_reports_single_ready_but_batch_still_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_pipeline_readiness.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("go_manual_single_call"),
    )

    report = vkpi_gemini_pipeline_readiness.build_report(kol_pool_ids=[123], requested_batch_size=30, requested_concurrency=1)

    assert report["passed"] is True
    assert report["readiness"] == "single_call_candidate_ready_batch_blocked"
    assert report["sample"]["decision_counts"] == {"go_manual_single_call": 1}
    assert report["policy"]["executor_exists"] is False


def test_gemini_pipeline_readiness_fails_if_sample_makes_provider_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_pipeline_readiness.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("hold", provider_calls=True),
    )

    report = vkpi_gemini_pipeline_readiness.build_report(kol_pool_ids=[123])

    assert report["passed"] is False
    assert report["checks"]["provider_calls_blocked"] is False
