"""Fail-closed tenant guards for legacy-global AI/learning read and write routes."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import (
    vkpi_actions,
    vkpi_agents,
    vkpi_prediction_ledger,
    vkpi_product_analysis,
    vkpi_skills,
)


_DENIED_STAFF = {
    "id": 91,
    "role": "admin",
    "organization_id": 4,
    "organization_scope_status": "resolved",
}


def _expect_403_before(call: Callable[[], Any], calls: list[str]) -> None:
    with pytest.raises(HTTPException) as caught:
        call()
    exc = caught.value
    assert exc.status_code == 403
    assert exc.detail.get("status") == "scope_unavailable"
    assert calls == []


def test_skill_routes_stop_before_dispatch_or_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("skill-domain")
        raise AssertionError("skill domain must not run")

    monkeypatch.setitem(vkpi_skills._DISPATCH, "creator_match", bomb)
    monkeypatch.setattr(vkpi_skills, "_meta_for", bomb)
    monkeypatch.setattr(vkpi_skills.skill_registry, "list_skill_runs", bomb)

    _expect_403_before(
        lambda: vkpi_skills.run_skill(
            "creator_match", {"input": {}}, _staff=_DENIED_STAFF
        ),
        calls,
    )
    _expect_403_before(lambda: vkpi_skills.list_skills(_staff=_DENIED_STAFF), calls)
    _expect_403_before(
        lambda: vkpi_skills.list_skill_runs(_staff=_DENIED_STAFF),
        calls,
    )


def test_prediction_reads_stop_before_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.agents import prediction_ledger

    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("prediction-domain")
        raise AssertionError("prediction domain must not run")

    monkeypatch.setattr(prediction_ledger, "ledger_summary", bomb)
    monkeypatch.setattr(prediction_ledger, "hit_rate_for", bomb)

    _expect_403_before(
        lambda: vkpi_prediction_ledger.get_prediction_ledger_summary(staff=_DENIED_STAFF),
        calls,
    )
    _expect_403_before(
        lambda: vkpi_prediction_ledger.get_prediction_ledger_group(
            "kol_recommend", window=20, staff=_DENIED_STAFF
        ),
        calls,
    )


def test_action_routes_stop_before_any_legacy_global_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("action-domain")
        raise AssertionError("action domain must not run")

    for module, names in (
        (vkpi_actions.inbox, ("list_inbox", "approve_action", "reconcile_executing_action", "read_execution_ledger")),
        (vkpi_actions.executors, ("execute_action",)),
        (vkpi_actions.reviews, ("get_action_review_candidate", "verify_action_result")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, bomb)

    # Every Action route uses one of these dependencies before entering its
    # handler.  Exercise the dependency functions directly so this unit test
    # cannot accidentally bypass FastAPI's dependency resolution by passing a
    # raw ``staff=`` argument to the handler.
    calls_to_check = (
        lambda: vkpi_actions._legacy_action_read(_DENIED_STAFF),
        lambda: vkpi_actions._legacy_action_write(_DENIED_STAFF),
        lambda: vkpi_actions._legacy_action_manager_read(_DENIED_STAFF),
        lambda: vkpi_actions._legacy_action_manager_write(_DENIED_STAFF),
    )
    for call in calls_to_check:
        _expect_403_before(call, calls)


def test_agent_plan_workflow_event_and_orchestration_stop_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("agent-domain")
        raise AssertionError("agent domain must not run")

    monkeypatch.setattr(vkpi_agents.orchestrator, "plan_goal", bomb)
    monkeypatch.setattr(vkpi_agents.orchestrator, "get_plan", bomb)
    monkeypatch.setattr(vkpi_agents.orchestrator, "materialize_plan_to_inbox", bomb)

    for call in (
        lambda: vkpi_agents.plan({"goal": "inspect"}, staff=_DENIED_STAFF),
        lambda: vkpi_agents.read_plan(3, staff=_DENIED_STAFF),
        lambda: vkpi_agents.agent_cycle_run(staff=_DENIED_STAFF),
        lambda: vkpi_agents.agent_cycle_resume(3, staff=_DENIED_STAFF),
        lambda: vkpi_agents.workflow_run(3, staff=_DENIED_STAFF),
        lambda: vkpi_agents.event_ledger_recent(limit=10, staff=_DENIED_STAFF),
        lambda: vkpi_agents.materialize_plan(3, staff=_DENIED_STAFF),
        lambda: vkpi_agents.skills_orchestrate(
            {"goal": "review campaign", "dry_run": True}, staff=_DENIED_STAFF,
        ),
    ):
        _expect_403_before(call, calls)


def test_agent_scorecard_and_learning_stop_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.intelligence import marketing_brain_scorecard
    from app.domains.memory import learning_signals

    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("agent-domain")
        raise AssertionError("agent domain must not run")

    monkeypatch.setattr(marketing_brain_scorecard, "build_marketing_brain_scorecard", bomb)
    monkeypatch.setattr(learning_signals, "get_learning_status", bomb)

    _expect_403_before(
        lambda: vkpi_agents.marketing_brain_scorecard(staff=_DENIED_STAFF),
        calls,
    )
    _expect_403_before(lambda: vkpi_agents.learning_status(staff=_DENIED_STAFF), calls)


def test_recommendation_feedback_write_stops_org2_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def bomb(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("recommendation-domain")
        raise AssertionError("recommendation domain must not run")

    monkeypatch.setattr(vkpi_product_analysis.product_analysis, "action_recommendation", bomb)
    _expect_403_before(
        lambda: vkpi_product_analysis.product_analysis_recommendation_action(
            7,
            "feedback",
            {"note": "reviewed"},
            staff=_DENIED_STAFF,
        ),
        calls,
    )
