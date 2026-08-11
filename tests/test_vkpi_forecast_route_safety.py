"""Fail-closed route boundaries for evaluation and forecast learning writes."""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_agents, vkpi_forecast, vkpi_forecast_feedback


_ORG1_MANAGER = {
    "id": 81,
    "role": "manager",
    "is_owner": 0,
    "organization_id": 1,
    "organization_scope_status": "resolved",
}
_ORG1_EMPLOYEE = {
    "id": 82,
    "role": "employee",
    "is_owner": 0,
    "organization_id": 1,
    "organization_scope_status": "resolved",
}
_ORG2_MANAGER = {
    "id": 83,
    "role": "manager",
    "is_owner": 0,
    "organization_id": 2,
    "organization_scope_status": "resolved",
}


def _assert_manager_dependency(endpoint: Callable[..., Any]) -> None:
    dependency = inspect.signature(endpoint).parameters["staff"].default.dependency
    with pytest.raises(HTTPException) as caught:
        asyncio.run(dependency(staff=_ORG1_EMPLOYEE))
    assert caught.value.status_code == 403
    assert caught.value.detail == "management permission required"
    assert asyncio.run(dependency(staff=_ORG1_MANAGER)) == _ORG1_MANAGER


def test_eval_run_is_manager_only_and_stops_org2_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.platform import evals

    calls: list[str] = []

    def fake_suite() -> dict[str, Any]:
        calls.append("eval-domain")
        return {"status": "ok"}

    monkeypatch.setattr(evals, "run_builtin_suite", fake_suite)
    _assert_manager_dependency(vkpi_agents.evals_run)

    with pytest.raises(HTTPException) as caught:
        vkpi_agents.evals_run(staff=_ORG2_MANAGER)
    assert caught.value.status_code == 403
    assert caught.value.detail["status"] == "scope_unavailable"
    assert caught.value.detail["writes"] is False
    assert calls == []
    assert vkpi_agents.evals_run(staff=_ORG1_MANAGER) == {"status": "ok"}
    assert calls == ["eval-domain"]


def test_forecast_get_forces_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import performance_forecast

    calls: list[tuple[int, str | None, bool]] = []

    def fake_forecast(
        kol_pool_id: int,
        sku: str | None = None,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        calls.append((kol_pool_id, sku, dry_run))
        return {"status": "ready", "kol_pool_id": kol_pool_id}

    monkeypatch.setattr(performance_forecast, "forecast_for_kol", fake_forecast)

    result = vkpi_forecast.get_kol_forecast(
        17,
        sku="  AF 85  ",
        staff=_ORG1_EMPLOYEE,
    )

    assert result == {"status": "ready", "kol_pool_id": 17}
    assert calls == [(17, "AF 85", True)]


def test_forecast_get_stops_org2_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import performance_forecast

    calls: list[str] = []
    monkeypatch.setattr(
        performance_forecast,
        "forecast_for_kol",
        lambda *args, **kwargs: calls.append("forecast-domain"),
    )

    with pytest.raises(HTTPException) as caught:
        vkpi_forecast.get_kol_forecast(17, sku=None, staff=_ORG2_MANAGER)
    assert caught.value.status_code == 403
    assert caught.value.detail["status"] == "scope_unavailable"
    assert calls == []


def test_forecast_outcome_refresh_is_manager_only_and_stops_org2_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.learning import forecast_feedback

    calls: list[str] = []

    def fake_refresh(**kwargs: Any) -> dict[str, Any]:
        calls.append("forecast-feedback-domain")
        return {"status": "ok", **kwargs}

    monkeypatch.setattr(forecast_feedback, "refresh_forecast_outcomes", fake_refresh)
    _assert_manager_dependency(vkpi_forecast_feedback.refresh_forecast_outcomes)

    with pytest.raises(HTTPException) as caught:
        vkpi_forecast_feedback.refresh_forecast_outcomes(
            min_age_days=30,
            limit=200,
            staff=_ORG2_MANAGER,
        )
    assert caught.value.status_code == 403
    assert caught.value.detail["status"] == "scope_unavailable"
    assert caught.value.detail["writes"] is False
    assert calls == []
    assert vkpi_forecast_feedback.refresh_forecast_outcomes(
        min_age_days=30,
        limit=200,
        staff=_ORG1_MANAGER,
    ) == {"status": "ok", "min_age_days": 30, "limit": 200}
    assert calls == ["forecast-feedback-domain"]


def test_forecast_summary_stops_org2_before_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.learning import forecast_feedback

    calls: list[str] = []

    def fake_summary(**kwargs: Any) -> dict[str, Any]:
        calls.append("forecast-summary-domain")
        return {"status": "ok", **kwargs}

    monkeypatch.setattr(forecast_feedback, "forecast_log_summary", fake_summary)
    with pytest.raises(HTTPException) as caught:
        vkpi_forecast_feedback.get_forecast_log_summary(
            recent_limit=10,
            staff=_ORG2_MANAGER,
        )
    assert caught.value.status_code == 403
    assert caught.value.detail["status"] == "scope_unavailable"
    assert calls == []
    assert vkpi_forecast_feedback.get_forecast_log_summary(
        recent_limit=10,
        staff=_ORG1_EMPLOYEE,
    ) == {"status": "ok", "recent_limit": 10}
    assert calls == ["forecast-summary-domain"]
