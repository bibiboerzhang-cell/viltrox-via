"""Fail closed before legacy-global shadow evaluation reads KOL evidence."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.auth import get_user_required


def _client(monkeypatch: pytest.MonkeyPatch, scope: dict[str, Any]):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.dependencies.legacy_scope as legacy_scope_mod
    import app.api.dependencies.perms as perms_mod
    from app.api.routers import vkpi_shadow_eval

    staff = {
        "id": 881001,
        "staff_id": 881001,
        "user_id": 881001,
        "email": "shadow-eval-scope@example.test",
        "role": "admin",
        "is_owner": 1,
        "permissions": {"vkpi": "admin"},
        **scope,
    }
    user = {"id": staff["user_id"], "email": staff["email"], "role": "admin"}
    monkeypatch.setattr(legacy_scope_mod, "staff_context_for_user", lambda _user: staff)
    monkeypatch.setattr(perms_mod, "staff_context_for_user", lambda _user: staff)

    app = FastAPI()
    app.include_router(vkpi_shadow_eval.router)
    app.dependency_overrides[get_user_required] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(
            {"organization_id": 4, "organization_scope_status": "resolved"},
            id="resolved-org4",
        ),
        pytest.param(
            {"organization_scope_status": "membership_missing"},
            id="missing-membership",
        ),
        pytest.param(
            {"organization_scope_status": "ambiguous"},
            id="ambiguous-membership",
        ),
    ],
)
def test_non_org1_scope_stops_before_shadow_eval_reads(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, Any],
) -> None:
    from app.domains.learning import shadow_eval

    calls: list[str] = []

    def forbidden(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("shadow-eval-read")
        raise AssertionError("legacy-global shadow eval ran before tenant guard")

    monkeypatch.setattr(shadow_eval, "run_shadow_eval", forbidden)
    client = _client(monkeypatch, scope)
    try:
        response = client.post(
            "/api/admin/vkpi/learning/shadow-evals/forecast_backtest/run"
        )
    finally:
        client.close()

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["status"] == "scope_unavailable"
    assert detail["writes"] is False
    assert calls == []


def test_org1_can_reach_read_only_shadow_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.learning import shadow_eval

    calls: list[str] = []

    def run(eval_name: str) -> dict[str, Any]:
        calls.append(eval_name)
        return {"status": "empty", "eval": eval_name, "writes": False}

    monkeypatch.setattr(shadow_eval, "run_shadow_eval", run)
    client = _client(
        monkeypatch,
        {"organization_id": 1, "organization_scope_status": "resolved"},
    )
    try:
        response = client.post(
            "/api/admin/vkpi/learning/shadow-evals/forecast_backtest/run"
        )
    finally:
        client.close()

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "empty"
    assert calls == ["forecast_backtest"]
