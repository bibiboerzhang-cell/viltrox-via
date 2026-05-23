from __future__ import annotations

from scripts import vkpi_gemini_batch30_dry_run


def _go_no_go(decision: str = "hold", *, provider_calls: bool = False) -> dict:
    return {
        "decision": decision,
        "decision_reason": "provider_or_budget_gate_not_ready" if decision == "hold" else "ready",
        "blockers": ["provider_gate:monthly_env_budget_disabled"] if decision == "hold" else [],
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
            "provider_gate_reason": "monthly_env_budget_disabled",
            "ready_for_manual_live_test": decision == "go_manual_single_call",
        },
    }


def test_batch30_dry_run_blocks_execution_and_clamps_controls(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_batch30_dry_run.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("hold"),
    )

    report = vkpi_gemini_batch30_dry_run.build_report(
        kol_pool_ids=list(range(1, 41)),
        target_size=999,
        window_size=99,
        requested_concurrency=9,
    )

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["batch_execution_allowed"] is False
    assert report["readiness"] == "blocked_provider_or_budget_hold"
    assert report["policy"]["effective_target_size"] == 30
    assert report["targets"]["effective_count"] == 30
    assert report["policy"]["effective_window_size"] == 5
    assert report["policy"]["effective_concurrency"] == 2
    assert len(report["dry_run_windows"]) == 6
    assert all(window["execution_enabled"] is False for window in report["dry_run_windows"])
    assert all(window["execution_command"] == "" for window in report["dry_run_windows"])
    assert "Gemini Batch-30 Dry Run" in vkpi_gemini_batch30_dry_run.render_markdown(report)


def test_batch30_dry_run_uses_natural_search_when_ids_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_batch30_dry_run.natural_search,
        "search",
        lambda *_args, **_kwargs: {
            "items": [
                {"source_table": "vkpi_kol_pool", "source_id": "10"},
                {"source_table": "vkpi_kol_pool", "source_id": "10"},
                {"source_table": "other", "source_id": "99"},
                {"source_table": "vkpi_kol_pool", "source_id": "11"},
            ]
        },
    )
    monkeypatch.setattr(
        vkpi_gemini_batch30_dry_run.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("go_manual_single_call"),
    )

    report = vkpi_gemini_batch30_dry_run.build_report(kol_pool_ids=[], target_size=30)

    assert report["passed"] is True
    assert report["candidate_source"] == "natural_search"
    assert report["targets"]["effective_ids"] == [10, 11]
    assert report["targets"]["decision_counts"] == {"go_manual_single_call": 2}
    assert report["readiness"] == "blocked_single_live_review_required"
    assert report["checks"]["single_live_review_required"] is True
    assert report["checks"]["no_execution_commands"] is True


def test_batch30_dry_run_fails_if_underlying_report_makes_provider_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_batch30_dry_run.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: _go_no_go("hold", provider_calls=True),
    )

    report = vkpi_gemini_batch30_dry_run.build_report(kol_pool_ids=[123])

    assert report["passed"] is False
    assert report["readiness"] == "failed_side_effect_guard"
    assert report["checks"]["provider_calls_blocked"] is False
    assert report["checks"]["llm_calls_blocked"] is False
