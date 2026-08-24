"""Route-level tenant isolation for legacy-global GTM data.

Every covered route must return ``scope_unavailable`` for a resolved non-org1,
missing membership, or ambiguous membership before builders, caches, database
access, persistence, or management checks are reached.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard


_SCOPES = [
    pytest.param(
        {"organization_id": 4, "organization_scope_status": "resolved"},
        "resolved-org4",
        id="resolved-org4",
    ),
    pytest.param(
        {"organization_scope_status": "membership_missing"},
        "membership-missing",
        id="membership-missing",
    ),
    pytest.param(
        {"organization_scope_status": "ambiguous"},
        "ambiguous",
        id="ambiguous",
    ),
]


def _staff(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": 991001,
        "staff_id": 991001,
        "user_id": 991001,
        "email": "scope-manager@example.test",
        "role": "manager",
        "is_owner": 0,
        "permissions": {"vkpi": "write"},
        **scope,
    }


def _make_client(staff: dict[str, Any]):
    import app.api.dependencies.perms as perms_mod
    import app.main as main_mod
    from app.api.dependencies.auth import get_user_required
    from app.main import app
    from fastapi.testclient import TestClient

    user = {"id": staff["user_id"], "email": staff["email"], "role": staff["role"]}
    saved = {
        "main_gcu": main_mod.get_current_user,
        "main_scfu": main_mod.staff_context_for_user,
        "perms_scfu": perms_mod.staff_context_for_user,
        "overrides": dict(app.dependency_overrides),
    }
    main_mod.get_current_user = lambda _request: user
    main_mod.staff_context_for_user = lambda _user: staff
    perms_mod.staff_context_for_user = lambda _user: staff
    app.dependency_overrides[get_user_required] = lambda: user
    client = TestClient(app, raise_server_exceptions=False)

    def teardown() -> None:
        client.close()
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])

    return client, teardown


def _bomb(label: str) -> Callable[..., Any]:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"scope guard ran too late: {label}")

    return fail


def _block_all_gtm_side_effect_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    # Load the complete application graph before replacing connection-module
    # attributes with bombs.  Otherwise a module imported for the first time
    # while the bomb is installed can bind ``from app.db.connection import
    # get_conn`` permanently; MonkeyPatch restores the source module attribute,
    # not aliases copied into newly imported modules.
    import app.main  # noqa: F401

    from app.api.routers import (
        vkpi_gtm_materialize,
        vkpi_gtm_verdicts,
        vkpi_market_brain,
        vkpi_market_brain_summary,
        vkpi_northstar,
    )
    from app.db import connection
    from app.domains.market_brain import (
        gtm_plan_preview,
        gtm_windows,
        materialize,
        summary,
        verdict_flow,
        weekly_answers,
        weight_feedback,
    )

    monkeypatch.setattr(vkpi_market_brain, "cache_get_or_build", _bomb("preview cache"))
    monkeypatch.setattr(vkpi_market_brain_summary, "cache_get_or_build", _bomb("summary cache"))
    monkeypatch.setattr(gtm_plan_preview, "build_preview", _bomb("preview builder"))
    monkeypatch.setattr(summary, "build_summary", _bomb("summary builder"))
    monkeypatch.setattr(materialize, "materialize_plan", _bomb("materialize builder/DB"))
    monkeypatch.setattr(vkpi_gtm_materialize, "require_manager_staff", _bomb("materialize manager guard"))
    monkeypatch.setattr(vkpi_gtm_verdicts, "require_manager_staff", _bomb("verdict manager guard"))
    monkeypatch.setattr(verdict_flow, "list_pending_verdicts", _bomb("pending verdict DB"))
    monkeypatch.setattr(verdict_flow, "list_outcomes", _bomb("outcomes DB"))
    monkeypatch.setattr(verdict_flow, "record_verdict", _bomb("verdict write"))
    monkeypatch.setattr(connection, "table_exists", _bomb("GTM table probe"))
    monkeypatch.setattr(connection, "get_conn", _bomb("GTM DB connection"))
    monkeypatch.setattr(weight_feedback, "apply_weight_change", _bomb("weight builder"))
    monkeypatch.setattr(gtm_windows, "refresh_gtm_windows", _bomb("window write"))
    monkeypatch.setattr(weekly_answers, "weekly_report", _bomb("weekly builder"))
    monkeypatch.setattr(vkpi_northstar, "_build_northstar", _bomb("northstar DB builder"))


def test_guard_allows_only_explicit_resolved_organization_one() -> None:
    assert legacy_gtm_scope_guard(
        {"organization_id": 1, "organization_scope_status": "resolved"}
    ) is None
    for staff in (
        {"organization_id": 1, "organization_scope_status": "ambiguous"},
        {"organization_id": 4, "organization_scope_status": "resolved"},
        {"organization_scope_status": "membership_missing"},
        {"organization_scope_status": "ambiguous"},
    ):
        result = legacy_gtm_scope_guard(staff)
        assert result is not None
        assert result["status"] == "scope_unavailable"
        assert result["writes"] is False


@pytest.mark.parametrize(("scope", "_label"), _SCOPES)
def test_all_legacy_global_gtm_routes_fail_closed_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, Any],
    _label: str,
) -> None:
    _block_all_gtm_side_effect_seams(monkeypatch)
    client, teardown = _make_client(_staff(scope))
    requests = [
        ("get", "/api/admin/vkpi/market-brain/summary", None),
        (
            "get",
            "/api/admin/vkpi/market-brain/gtm-plan/preview?sku=AF-85&country=US&goal=conversion",
            None,
        ),
        ("get", "/api/admin/vkpi/gtm/verdicts/pending", None),
        ("get", "/api/admin/vkpi/gtm/outcomes", None),
        ("get", "/api/admin/vkpi/gtm/weight-changes/preview", None),
        ("get", "/api/admin/vkpi/gtm/verdicts/1/context?id_type=inbox", None),
        ("get", "/api/admin/vkpi/gtm/weekly-answers?days=7", None),
        ("get", "/api/admin/vkpi/gtm/northstar", None),
        (
            "post",
            "/api/admin/vkpi/market-brain/gtm-plan/materialize",
            {"sku": "AF-85", "dry_run": True},
        ),
        (
            "post",
            "/api/admin/vkpi/market-brain/gtm-plan/materialize",
            {"sku": "AF-85", "dry_run": False},
        ),
        (
            "post",
            "/api/admin/vkpi/gtm/verdicts/1/decide",
            {"decision": "validated", "lesson": "must never persist"},
        ),
        ("post", "/api/admin/vkpi/gtm/windows/refresh?dry_run=true", {}),
        ("post", "/api/admin/vkpi/gtm/windows/refresh?dry_run=false", {}),
    ]
    headers = {"Authorization": "Bearer gtm-scope-test"}
    try:
        for method, path, payload in requests:
            response = client.request(method, path, json=payload, headers=headers)
            assert response.status_code == 200, (path, response.status_code, response.text[:500])
            body = response.json()
            assert body["status"] == "scope_unavailable", (path, body)
            assert body["writes"] is False, (path, body)
            assert body["organization_scope_status"] == scope["organization_scope_status"]
            expected_org = scope.get("organization_id")
            assert body["organization_id"] == expected_org
    finally:
        teardown()
