from __future__ import annotations

from scripts import vkpi_llm_gemini_acceptance


def _budget() -> dict:
    return {
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "preflight": {
            "provider_gate_reason": "monthly_env_budget_disabled",
            "monthly_env_budget_usd": 0,
            "monthly_env_remaining_usd": 0,
            "provider_calls_allowed": False,
            "providers": [
                {
                    "provider": "google",
                    "configured": True,
                    "estimated_cost_usd": 0.02,
                    "budget_allowed": False,
                    "provider_calls_allowed": False,
                    "scopes": ["monthly_total", "single_call", "provider:gemini"],
                }
            ],
        },
    }


def _go_no_go(decision: str = "hold") -> dict:
    return {
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "summary": {
            "decision": decision,
            "decision_reason": "provider_or_budget_gate_not_ready" if decision == "hold" else "ready",
            "blockers": ["provider_gate:monthly_env_budget_disabled"] if decision == "hold" else [],
            "candidate_count": 1,
            "valid_video_url": True,
            "provider_path": "youtube_direct_url_preflight",
            "top_video_url": "https://youtube.com/watch?v=abc123456",
            "provider_gate_reason": "monthly_env_budget_disabled" if decision == "hold" else "",
            "ready_for_manual_live_test": decision == "go_manual_single_call",
        },
    }


def _batch(readiness: str = "blocked_provider_or_budget_hold") -> dict:
    return {
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "batch_execution_allowed": False,
        "readiness": readiness,
        "checks": {"no_execution_commands": True},
        "targets": {
            "effective_count": 1,
            "decision_counts": {"hold": 1},
            "blocker_counts": {"provider_gate:monthly_env_budget_disabled": 1},
        },
    }


def _ai_brief() -> dict:
    return {
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "checks": {
            "all_brief_items_traceable": True,
            "all_next_actions_traceable": True,
            "new_fact_generation_disabled": True,
            "recommendations_require_evidence": True,
        },
        "summary": {
            "headline": "AI Brief v0 is anchored on product_fit evidence; readiness=ready.",
            "brief_item_count": 8,
            "next_action_count": 2,
            "evidence_backlink_count": 19,
            "sections": ["product_fit", "competitors"],
        },
    }


def test_llm_gemini_acceptance_holds_live_and_batch_when_budget_blocked(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_llm_budget_acceptance, "build_report", lambda **_kwargs: _budget())
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_gemini_go_no_go_report, "build_report", lambda **_kwargs: _go_no_go("hold"))
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_gemini_batch30_dry_run, "build_report", lambda **_kwargs: _batch())
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_ai_brief_acceptance, "build_report", lambda **_kwargs: _ai_brief())

    report = vkpi_llm_gemini_acceptance.build_report(kol_pool_id=4217, kol_pool_ids=[4217])

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["decisions"]["evidence_only"]["status"] == "go"
    assert report["decisions"]["single_live_gemini"]["status"] == "hold"
    assert report["decisions"]["batch_gemini"]["status"] == "hold"
    assert report["decisions"]["final"]["status"] == "go_evidence_only_hold_live_and_batch"
    assert report["checks"]["batch_execution_blocked"] is True
    assert "LLM/Gemini Phase Acceptance" in vkpi_llm_gemini_acceptance.render_markdown(report)


def test_llm_gemini_acceptance_allows_one_manual_call_but_holds_batch(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_llm_budget_acceptance, "build_report", lambda **_kwargs: _budget())
    monkeypatch.setattr(
        vkpi_llm_gemini_acceptance.vkpi_gemini_go_no_go_report,
        "build_report",
        lambda **_kwargs: _go_no_go("go_manual_single_call"),
    )
    monkeypatch.setattr(
        vkpi_llm_gemini_acceptance.vkpi_gemini_batch30_dry_run,
        "build_report",
        lambda **_kwargs: _batch("blocked_single_live_review_required"),
    )
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_ai_brief_acceptance, "build_report", lambda **_kwargs: _ai_brief())

    report = vkpi_llm_gemini_acceptance.build_report(kol_pool_id=4217, kol_pool_ids=[4217])

    assert report["passed"] is True
    assert report["decisions"]["single_live_gemini"]["status"] == "go_one_manual_paid_call_only"
    assert report["decisions"]["batch_gemini"]["status"] == "hold"
    assert report["decisions"]["final"]["status"] == "go_evidence_only_and_one_manual_live_call_hold_batch"
    assert "batch_gemini" in report["decisions"]["final"]["hold"]


def test_llm_gemini_acceptance_fails_if_ai_brief_not_traceable(monkeypatch) -> None:
    brief = _ai_brief()
    brief["passed"] = False
    brief["checks"]["all_brief_items_traceable"] = False
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_llm_budget_acceptance, "build_report", lambda **_kwargs: _budget())
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_gemini_go_no_go_report, "build_report", lambda **_kwargs: _go_no_go("hold"))
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_gemini_batch30_dry_run, "build_report", lambda **_kwargs: _batch())
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_ai_brief_acceptance, "build_report", lambda **_kwargs: brief)

    report = vkpi_llm_gemini_acceptance.build_report(kol_pool_id=4217, kol_pool_ids=[4217])

    assert report["passed"] is False
    assert report["decisions"]["final"]["status"] == "hold_all_ai_surface"
    assert report["checks"]["evidence_only_can_continue"] is False


def test_llm_gemini_acceptance_does_not_fail_when_budget_gate_is_open_but_readonly(monkeypatch) -> None:
    budget = _budget()
    budget["passed"] = False
    budget["preflight"]["provider_gate_reason"] = "provider_calls_allowed"
    budget["preflight"]["provider_calls_allowed"] = True
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_llm_budget_acceptance, "build_report", lambda **_kwargs: budget)
    monkeypatch.setattr(
        vkpi_llm_gemini_acceptance.vkpi_gemini_go_no_go_report,
        "build_report",
        lambda **_kwargs: _go_no_go("go_manual_single_call"),
    )
    monkeypatch.setattr(
        vkpi_llm_gemini_acceptance.vkpi_gemini_batch30_dry_run,
        "build_report",
        lambda **_kwargs: _batch("blocked_single_live_review_required"),
    )
    monkeypatch.setattr(vkpi_llm_gemini_acceptance.vkpi_ai_brief_acceptance, "build_report", lambda **_kwargs: _ai_brief())

    report = vkpi_llm_gemini_acceptance.build_report(kol_pool_id=4217, kol_pool_ids=[4217])

    assert report["passed"] is True
    assert report["checks"]["budget_report_readonly"] is True
    assert report["provider_calls"] is False
    assert report["decisions"]["final"]["status"] == "go_evidence_only_and_one_manual_live_call_hold_batch"
