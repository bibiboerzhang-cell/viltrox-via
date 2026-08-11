from __future__ import annotations

import pytest

from app.domains.actions import validators


def _action(*, cost: int) -> dict:
    return {
        "status": "approved",
        "touches_v6_fit": False,
        "uses_llm": True,
        "estimated_cost_cents": cost,
        "entity_type": "",
        "entity_id": "",
    }


def test_llm_action_requires_positive_server_cost_without_budget_call(monkeypatch) -> None:
    monkeypatch.setattr(
        validators.budget_guard,
        "check_budget",
        lambda *_args, **_kwargs: pytest.fail("zero estimate must stop before budget lookup"),
    )
    result = validators.validate_action(_action(cost=0))
    assert result["reason"] == "budget_estimate_required"
    assert result["checks"]["budget_ok"] is False


def test_llm_action_budget_lookup_is_configured_and_fail_closed(monkeypatch) -> None:
    observed: dict = {}

    def allow(scope: str, amount: float, **kwargs):
        observed.update(scope=scope, amount=amount, kwargs=kwargs)
        return True

    monkeypatch.setattr(validators.budget_guard, "check_budget", allow)
    result = validators.validate_action(_action(cost=9))
    assert result["ok"] is True
    assert observed == {
        "scope": "single_call",
        "amount": 0.09,
        "kwargs": {"require_configured": True},
    }

    monkeypatch.setattr(
        validators.budget_guard,
        "check_budget",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("budget offline")),
    )
    blocked = validators.validate_action(_action(cost=9))
    assert blocked["reason"] == "budget_guard_unavailable"
    assert blocked["checks"]["budget_ok"] is False
