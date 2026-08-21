from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_goaffpro, vkpi_shopify
from app.domains.audit import decorator as audit_decorator


EMPLOYEE = {"id": 73, "role": "employee", "permissions": {"vkpi": "write"}}
MANAGER = {"id": 7, "role": "manager", "permissions": {"vkpi": "write"}}


def _bomb(message: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(message)

    return fail


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: vkpi_goaffpro.sync_goaffpro_metrics(limit=None, staff=EMPLOYEE),
        lambda: vkpi_goaffpro.sync_goaffpro_sales(limit=None, staff=EMPLOYEE),
    ],
)
def test_employee_cannot_enter_global_goaffpro_provider_or_db(monkeypatch, invoke) -> None:
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", _bomb("employee must not open DB"))
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", _bomb("employee denial must precede audit DB"))
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "sync_kol_metrics",
        _bomb("employee must not sync GOAFFPRO metrics"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        _bomb("employee must not create GOAFFPRO schema"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "list_orders",
        _bomb("employee must not read GOAFFPRO orders"),
    )

    with pytest.raises(HTTPException) as caught:
        invoke()

    assert caught.value.status_code == 403
    assert caught.value.detail == "management permission required"


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: vkpi_goaffpro.sync_goaffpro_metrics(limit=None, staff=MANAGER),
        lambda: vkpi_goaffpro.sync_goaffpro_sales(limit=None, staff=MANAGER),
    ],
)
def test_release_validation_blocks_global_goaffpro_before_audit_provider_or_db(monkeypatch, invoke) -> None:
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: True)
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", _bomb("release fence must precede DB"))
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", _bomb("release fence must precede audit DB"))
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "sync_kol_metrics", _bomb("provider reached"))
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "ensure_goaffpro_links_schema", _bomb("schema reached"))
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "list_orders", _bomb("provider reached"))

    with pytest.raises(HTTPException) as caught:
        invoke()

    assert caught.value.status_code == 503
    assert caught.value.detail == "release_validation_fenced"


def test_global_goaffpro_syncs_emit_named_audit_actions(monkeypatch) -> None:
    audits: list[dict] = []
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: False)
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "sync_kol_metrics",
        lambda *, limit=None: {"ok": True, "synced": 2, "limit": limit},
    )
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "ensure_goaffpro_links_schema", lambda: None)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "list_orders",
        lambda **_kwargs: {"ok": False, "reason": "not_configured"},
    )

    metrics = vkpi_goaffpro.sync_goaffpro_metrics(limit=10, staff=MANAGER)
    sales = vkpi_goaffpro.sync_goaffpro_sales(limit=10, staff=MANAGER)

    assert metrics["synced"] == 2
    assert sales["reason"] == "not_configured"
    assert [item["action_type"] for item in audits] == [
        "goaffpro_metrics_sync",
        "goaffpro_sales_sync",
    ]
    assert all(item["status"] == "success" for item in audits)


def test_employee_cannot_enter_shopify_discount_provider_or_db(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_shopify, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_shopify, "get_conn", _bomb("employee must not open DB"))
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", _bomb("employee denial must precede audit DB"))
    monkeypatch.setattr(
        vkpi_shopify.shopify_discounts,
        "create_kol_discount",
        _bomb("employee must not create Shopify discount"),
    )

    with pytest.raises(HTTPException) as caught:
        vkpi_shopify.create_shopify_discount(
            body={"code": "EMP10", "value": 10, "kol_id": 42},
            staff=EMPLOYEE,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "management permission required"


def test_release_validation_blocks_shopify_discount_before_audit_provider_or_db(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_shopify, "release_validation_active", lambda: True)
    monkeypatch.setattr(vkpi_shopify, "get_conn", _bomb("release fence must precede DB"))
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", _bomb("release fence must precede audit DB"))
    monkeypatch.setattr(vkpi_shopify.shopify_discounts, "create_kol_discount", _bomb("provider reached"))

    with pytest.raises(HTTPException) as caught:
        vkpi_shopify.create_shopify_discount(
            body={"code": "MGR10", "value": 10, "kol_id": 42},
            staff=MANAGER,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "release_validation_fenced"


class _DiscountConn:
    def __init__(self, *, assigned: bool = True):
        self.assigned = assigned
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        self.queries.append((normalized, tuple(params)))
        if "from vkpi_kol_pool where id" in normalized:
            return _Row({"id": 42, "duplicate_of_id": None})
        if "from vkpi_project_kol_assignments" in normalized:
            return _Row({"ok": 1} if self.assigned else None)
        raise AssertionError(f"unexpected discount scope query: {normalized}")


class _Row:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


def test_shopify_single_project_discount_requires_writable_assigned_kol_and_audits(monkeypatch) -> None:
    conn = _DiscountConn(assigned=True)
    project_checks: list[tuple[int, dict, bool]] = []
    provider_payloads: list[dict] = []
    audits: list[dict] = []
    monkeypatch.setattr(vkpi_shopify, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_shopify, "get_conn", lambda: conn)
    monkeypatch.setattr(
        vkpi_shopify.scope,
        "assert_project_access",
        lambda project_id, staff, *, write=False: project_checks.append((project_id, staff, write)),
    )
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **kwargs: audits.append(kwargs))
    monkeypatch.setattr(
        vkpi_shopify.shopify_discounts,
        "create_kol_discount",
        lambda **kwargs: provider_payloads.append(kwargs) or {"ok": True, "code": kwargs["code"]},
    )

    result = vkpi_shopify.create_shopify_discount(
        body={
            "code": "PROJECT10",
            "value": 10,
            "kol_id": 42,
            "project_id": 91,
            "product_handle": "af-35mm",
        },
        staff=MANAGER,
    )

    assert result == {"ok": True, "code": "PROJECT10"}
    assert project_checks == [(91, MANAGER, True)]
    assert provider_payloads[0]["kol_id"] == 42
    assert provider_payloads[0]["project_id"] == 91
    assert any("from vkpi_project_kol_assignments" in sql for sql, _ in conn.queries)
    assert audits[0]["action_type"] == "shopify_discount_create"
    assert audits[0]["status"] == "success"


def test_shopify_discount_rejects_project_kol_mismatch_before_provider(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_shopify, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_shopify, "get_conn", lambda: _DiscountConn(assigned=False))
    monkeypatch.setattr(vkpi_shopify.scope, "assert_project_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kwargs: None)
    monkeypatch.setattr(vkpi_shopify.shopify_discounts, "create_kol_discount", _bomb("provider reached"))

    with pytest.raises(HTTPException) as caught:
        vkpi_shopify.create_shopify_discount(
            body={"code": "BAD10", "value": 10, "kol_id": 42, "project_id": 91},
            staff=MANAGER,
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == "shopify_project_kol_assignment_required"


def test_shopify_project_denial_precedes_target_db_and_provider(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_shopify, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_shopify.scope,
        "assert_project_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(vkpi_shopify.scope.ScopeDenied("denied")),
    )
    monkeypatch.setattr(vkpi_shopify, "get_conn", _bomb("project denial must precede target DB"))
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kwargs: None)
    monkeypatch.setattr(vkpi_shopify.shopify_discounts, "create_kol_discount", _bomb("provider reached"))

    with pytest.raises(HTTPException) as caught:
        vkpi_shopify.create_shopify_discount(
            body={"code": "DENIED10", "value": 10, "kol_id": 42, "project_id": 91},
            staff=MANAGER,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "shopify_project_write_forbidden"
