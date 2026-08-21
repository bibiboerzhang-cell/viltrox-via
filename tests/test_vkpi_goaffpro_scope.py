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
