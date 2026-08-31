"""GOAFFPRO KOL writes must honor target-level MY KOL ownership."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_goaffpro
from app.domains.audit import decorator as audit_decorator


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RowsCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _SharedOnlyConn:
    """The target exists but has no favorite row for this staff actor."""

    def __init__(self):
        self.queries: list[str] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        self.queries.append(normalized)
        if "from vkpi_kol_pool where id" in normalized:
            assert params == (991,)
            return _Cursor({"id": 991, "duplicate_of_id": None})
        if "from vkpi_kol_pool_favorites" in normalized:
            assert params == (991, 73)
            return _Cursor(None)
        raise AssertionError(f"write/provider path reached after scope denial: {normalized}")

    def commit(self):
        raise AssertionError("scope-denied requests must never commit")


@pytest.mark.parametrize("operation", ("link", "commission", "coupon"))
def test_shared_or_arbitrary_pool_id_cannot_reach_goaffpro_writes(monkeypatch, operation):
    conn = _SharedOnlyConn()
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "write"}}
    provider_calls: list[str] = []

    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: conn)
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("scope denial must happen before schema writes"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "resolve_affiliate",
        lambda **_kwargs: provider_calls.append("resolve"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "update_affiliate_commission",
        lambda *_args, **_kwargs: provider_calls.append("commission"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "update_affiliate_coupon",
        lambda *_args, **_kwargs: provider_calls.append("coupon"),
    )

    with pytest.raises(HTTPException) as raised:
        if operation == "link":
            vkpi_goaffpro.link_kol_affiliate(991, product=None, staff=staff)
        elif operation == "commission":
            vkpi_goaffpro.update_kol_commission(991, body={"rate": 10}, staff=staff)
        else:
            vkpi_goaffpro.update_kol_coupon(
                991,
                body={"code": "SHARED10", "discount_value": 10},
                staff=staff,
            )

    assert raised.value.status_code == 403
    assert raised.value.detail == "my_kol_goaffpro_write_forbidden"
    assert provider_calls == []
    assert all("vkpi_kol_pool_members" not in query for query in conn.queries)


def test_provider_write_is_fenced_after_target_validation(monkeypatch):
    class _OwnedConn:
        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).lower().split())
            if "from vkpi_kol_pool where id" in normalized:
                return _Cursor({"id": 991, "duplicate_of_id": None})
            if "from vkpi_kol_pool_favorites" in normalized:
                return _Cursor({"id": 1})
            raise AssertionError(f"unexpected query: {normalized}")

    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "write"}}
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: _OwnedConn())
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: True)
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kwargs: None)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("fenced POST must not write schema"),
    )

    with pytest.raises(HTTPException) as raised:
        vkpi_goaffpro.link_kol_affiliate(991, product=None, staff=staff)

    assert raised.value.status_code == 503
    assert raised.value.detail == "release_validation_fenced"


class _ReadScopeConn:
    def __init__(self, *, shared: bool):
        self.shared = shared

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        if "from vkpi_kol_pool where id" in normalized:
            return _Cursor({"id": 991})
        if "from vkpi_kol_pool_favorites" in normalized:
            return _Cursor(None)
        if "from vkpi_kol_pool_members" in normalized:
            return _Cursor({"id": 9} if self.shared else None)
        raise AssertionError(f"unexpected direct-ID read query: {normalized}")


def test_arbitrary_pool_id_cannot_read_goaffpro_commerce_fields(monkeypatch):
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: _ReadScopeConn(shared=False))
    monkeypatch.setattr(
        vkpi_goaffpro,
        "table_exists",
        lambda _name: pytest.fail("scope denial must precede link-table access"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "resolve_affiliate",
        lambda **_kwargs: pytest.fail("scope-denied GET must not contact provider"),
    )

    with pytest.raises(HTTPException) as raised:
        vkpi_goaffpro.get_kol_affiliate_link(991, product=None, staff=staff)

    assert raised.value.status_code == 403
    assert raised.value.detail == "my_kol_goaffpro_read_forbidden"


def test_shared_member_can_read_local_goaffpro_mapping_state(monkeypatch):
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: _ReadScopeConn(shared=True))
    monkeypatch.setattr(vkpi_goaffpro, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("shared GET must stay read-only"),
    )

    result = vkpi_goaffpro.get_kol_affiliate_link(991, product=None, staff=staff)

    assert result == {
        "linked": False,
        "kol_pool_id": 991,
        "needs_regenerate": False,
    }


def test_arbitrary_pool_id_cannot_read_goaffpro_attribution(monkeypatch):
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: _ReadScopeConn(shared=False))
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("scope denial must happen before schema access"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro,
        "_aggregate_confirmed_sales",
        lambda *_args, **_kwargs: pytest.fail("scope denial must happen before commerce reads"),
    )

    with pytest.raises(HTTPException) as raised:
        vkpi_goaffpro.goaffpro_attribution(kol_pool_id=991, project_id=None, staff=staff)

    assert raised.value.status_code == 403
    assert raised.value.detail == "my_kol_goaffpro_read_forbidden"


@pytest.mark.parametrize("endpoint", ("attribution", "summary"))
def test_out_of_scope_project_cannot_read_goaffpro_commerce(monkeypatch, endpoint):
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}

    def deny_project(project_id, seen_staff, *, write=False):
        assert project_id == 41
        assert seen_staff is staff
        assert write is False
        raise vkpi_goaffpro.scope.ScopeDenied("project scope denied")

    monkeypatch.setattr(vkpi_goaffpro.scope, "assert_project_access", deny_project)
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("project denial must happen before schema access"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro,
        "get_conn",
        lambda: pytest.fail("project denial must happen before commerce reads"),
    )

    with pytest.raises(HTTPException) as raised:
        if endpoint == "attribution":
            vkpi_goaffpro.goaffpro_attribution(kol_pool_id=None, project_id=41, staff=staff)
        else:
            vkpi_goaffpro.goaffpro_summary(limit=200, project_id=41, search=None, staff=staff)

    assert raised.value.status_code == 403
    assert raised.value.detail == "goaffpro_project_read_forbidden"


class _SummaryConn:
    def __init__(self, project_rows=None, summary_rows=None):
        self.project_rows = project_rows or []
        self.summary_rows = summary_rows or []
        self.summary_sql = ""
        self.summary_params = ()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        if "from vkpi_project_kol_assignments" in normalized:
            return _RowsCursor(self.project_rows)
        if "from vkpi_goaffpro_kol_links l" in normalized:
            self.summary_sql = normalized
            self.summary_params = tuple(params)
            return _RowsCursor(self.summary_rows)
        raise AssertionError(f"unexpected summary query: {normalized}")


def _run_summary(monkeypatch, conn, staff, *, project_id=None):
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "ensure_goaffpro_links_schema", lambda: None)
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: conn)
    return vkpi_goaffpro.goaffpro_summary(
        limit=200,
        project_id=project_id,
        search=None,
        staff=staff,
    )


def test_employee_summary_is_limited_to_owned_or_shared_kols(monkeypatch):
    conn = _SummaryConn()
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}

    result = _run_summary(monkeypatch, conn, staff)

    assert result["items"] == []
    assert "vkpi_kol_pool_favorites own_favorite" in conn.summary_sql
    assert "vkpi_kol_pool_members shared_member" in conn.summary_sql
    assert conn.summary_params == (73, 73, 200)


def test_employee_project_summary_intersects_project_and_my_kol_scope(monkeypatch):
    conn = _SummaryConn(project_rows=[{"kol_pool_id": 991}, {"kol_pool_id": 992}])
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}
    project_checks = []
    monkeypatch.setattr(
        vkpi_goaffpro.scope,
        "assert_project_access",
        lambda project_id, seen_staff, *, write=False: project_checks.append(
            (project_id, seen_staff, write)
        ),
    )

    result = _run_summary(monkeypatch, conn, staff, project_id=41)

    assert result["items"] == []
    assert project_checks == [(41, staff, False)]
    assert "vkpi_kol_pool_favorites own_favorite" in conn.summary_sql
    assert "vkpi_kol_pool_members shared_member" in conn.summary_sql
    assert "l.kol_pool_id in (?,?)" in conn.summary_sql
    assert conn.summary_params == (73, 73, 991, 992, 200)


def test_manager_summary_retains_full_cached_commerce_view(monkeypatch):
    conn = _SummaryConn()
    staff = {"id": 7, "role": "manager", "permissions": {"vkpi": "read"}}

    result = _run_summary(monkeypatch, conn, staff)

    assert result["items"] == []
    assert "vkpi_kol_pool_favorites own_favorite" not in conn.summary_sql
    assert "vkpi_kol_pool_members shared_member" not in conn.summary_sql
    assert conn.summary_params == (200,)


def test_summary_preserves_cached_item_totals_and_stale_note(monkeypatch):
    conn = _SummaryConn(summary_rows=[
        {
            "kol_pool_id": 91,
            "affiliate_id": "aff-91",
            "ref_code": "ref91",
            "coupon": "SAVE91",
            "tracking_url": "https://example.test/ref91",
            "display_name": "Creator",
            "handle": "@creator",
            "avatar_url": "avatar.jpg",
            "platform": "youtube",
            "m_clicks": 12,
            "m_orders": 2,
            "m_gmv_cents": 12345,
            "m_commission_cents": 678,
            "m_commission_rate": "10%",
            "m_status": "approved",
            "m_currency": "USD",
            "m_partial": 1,
            "m_synced_at": None,
        },
        {"kol_pool_id": 92, "affiliate_id": "", "ref_code": "ignored"},
    ])
    staff = {"id": 7, "role": "manager", "permissions": {"vkpi": "read"}}

    result = _run_summary(monkeypatch, conn, staff)

    assert result["count"] == 1
    assert result["items"][0] == {
        "kol_pool_id": 91,
        "kol_name": "Creator",
        "kol_handle": "@creator",
        "kol_avatar": "avatar.jpg",
        "kol_platform": "youtube",
        "affiliate_id": "aff-91",
        "ref_code": "ref91",
        "coupon": "SAVE91",
        "commission_rate": "10%",
        "status": "approved",
        "tracking_url": "https://example.test/ref91",
        "source_label": "GOAFFPRO",
        "source_type": "goaffpro",
        "product_sku": "—",
        "clicks": 12,
        "orders": 2,
        "gmv_usd": 123.45,
        "commission_usd": 6.78,
        "currency": "USD",
        "partial": True,
        "stale": True,
    }
    assert result["totals"] == {
        "kol_count": 1,
        "clicks": 12,
        "orders": 2,
        "gmv_usd": 123.45,
        "commission_usd": 6.78,
    }
    assert result["partial_count"] == 1
    assert result["stale_count"] == 1
    assert result["last_synced_at"] is None
    assert result["note"] == "1 个 KOL 刚建链还没同步,点「刷新」拉取最新数据。"


@pytest.mark.parametrize(
    "invoke,provider_name",
    (
        (lambda staff: vkpi_goaffpro.list_goaffpro_affiliates(limit=5, offset=None, staff=staff), "list_affiliates"),
        (lambda staff: vkpi_goaffpro.list_goaffpro_orders(limit=5, offset=None, staff=staff), "list_orders"),
        (lambda staff: vkpi_goaffpro.goaffpro_products(keyword=None, limit=5, staff=staff), "list_products"),
        (lambda staff: vkpi_goaffpro.goaffpro_resolve_product(query="AF 35mm", staff=staff), "find_product_handle"),
    ),
)
def test_employee_cannot_trigger_goaffpro_provider_reads(monkeypatch, invoke, provider_name):
    staff = {"id": 73, "role": "employee", "permissions": {"vkpi": "read"}}
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        provider_name,
        lambda *_args, **_kwargs: pytest.fail("employee must not reach GOAFFPRO provider"),
    )

    with pytest.raises(HTTPException) as raised:
        invoke(staff)

    assert raised.value.status_code == 403
    assert raised.value.detail == "management permission required"


@pytest.mark.parametrize(
    "invoke,provider_name",
    (
        (lambda staff: vkpi_goaffpro.list_goaffpro_affiliates(limit=5, offset=None, staff=staff), "list_affiliates"),
        (lambda staff: vkpi_goaffpro.list_goaffpro_orders(limit=5, offset=None, staff=staff), "list_orders"),
        (lambda staff: vkpi_goaffpro.goaffpro_products(keyword=None, limit=5, staff=staff), "list_products"),
        (lambda staff: vkpi_goaffpro.goaffpro_resolve_product(query="AF 35mm", staff=staff), "find_product_handle"),
    ),
)
def test_release_validation_blocks_goaffpro_provider_reads(monkeypatch, invoke, provider_name):
    staff = {"id": 7, "role": "manager", "permissions": {"vkpi": "write"}}
    monkeypatch.setattr(vkpi_goaffpro, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        provider_name,
        lambda *_args, **_kwargs: pytest.fail("release fence must precede provider access"),
    )

    with pytest.raises(HTTPException) as raised:
        invoke(staff)

    assert raised.value.status_code == 503
    assert raised.value.detail == "release_validation_fenced"


def test_product_provider_routes_are_explicit_post_only():
    methods_by_path = {
        route.path: set(route.methods or set())
        for route in vkpi_goaffpro.router.routes
        if route.path.endswith(("/products", "/resolve-product"))
    }

    assert methods_by_path == {
        "/api/admin/vkpi/goaffpro/products": {"POST"},
        "/api/admin/vkpi/goaffpro/resolve-product": {"POST"},
    }
