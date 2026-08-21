"""GoAffPro link routes never export KOL contact values to the provider."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_goaffpro


_CONTACT_STATES = ("observed", "verified", "revoked", "suppressed")


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _NameOnlyIdentityConn:
    def __init__(self):
        self.queries: list[str] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        self.queries.append(normalized)
        forbidden = (
            "email",
            "phone",
            "whatsapp",
            "contact_value",
            "vkpi_kol_pool_contacts",
        )
        assert not any(token in normalized for token in forbidden)
        assert "select id, display_name, handle from vkpi_kol_pool" in normalized
        assert params == (73,)
        return _Cursor({"id": 73, "display_name": "Boundary Creator", "handle": "boundary"})


class _UpdateConn:
    def __init__(self):
        self.updates: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.updates.append((" ".join(str(sql).split()), tuple(params)))
        return _Cursor(None)

    def commit(self):
        self.commits += 1


def _identity_with_contact_state(state: str) -> tuple[dict, tuple[str, str]]:
    email = f"{state}.private@example.test"
    phone = f"+1-555-010-{len(state):02d}"
    return (
        {
            "kol_pool_id": 73,
            "name": "Boundary Creator",
            "email": email,
            "phone": phone,
            "contact_state": state,
            "contacts": [{"contact_value": email, "verification_status": state}],
        },
        (email, phone),
    )


def _assert_name_only_lookup(calls: list[dict], secrets: tuple[str, ...]) -> None:
    assert len(calls) == 1
    assert calls[0]["name"] == "Boundary Creator"
    assert calls[0]["create"] is False
    assert set(calls[0]) <= {"name", "create", "email"}
    assert calls[0].get("email") is None
    for secret in secrets:
        assert secret not in repr(calls)


def _patch_route_db(monkeypatch, conn) -> None:
    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "ensure_goaffpro_links_schema", lambda: None)
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: conn)
    monkeypatch.setattr(vkpi_goaffpro, "_assert_goaffpro_target_writable", lambda *_args: 73)
    monkeypatch.setattr(vkpi_goaffpro, "_assert_goaffpro_target_readable", lambda *_args: 73)
    monkeypatch.setattr(vkpi_goaffpro, "_assert_goaffpro_provider_write_allowed", lambda: None)


def test_identity_loader_reads_name_only_and_never_queries_contact_sources():
    conn = _NameOnlyIdentityConn()

    identity = vkpi_goaffpro._load_kol_identity(conn, 73)

    assert identity == {"kol_pool_id": 73, "name": "Boundary Creator"}
    assert len(conn.queries) == 1


@pytest.mark.parametrize("contact_state", _CONTACT_STATES)
def test_post_links_existing_affiliate_name_only_for_every_contact_state(monkeypatch, contact_state):
    conn = object()
    identity, secrets = _identity_with_contact_state(contact_state)
    provider_calls: list[dict] = []

    _patch_route_db(monkeypatch, conn)
    monkeypatch.setattr(vkpi_goaffpro, "_load_link", lambda *_args: None)
    monkeypatch.setattr(vkpi_goaffpro, "_load_kol_identity", lambda *_args: identity)

    def resolve_affiliate(**kwargs):
        provider_calls.append(dict(kwargs))
        return {
            "ok": True,
            "affiliate_id": "aff-73",
            "ref_code": "BOUNDARY73",
            "coupon": "",
            "status": "approved",
            "affiliate": {"id": "aff-73", "ref_code": "BOUNDARY73"},
        }

    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "resolve_affiliate", resolve_affiliate)
    monkeypatch.setattr(
        vkpi_goaffpro,
        "_store_kol_link",
        lambda *_args: {"ok": True, "linked": True, "ref_code": "BOUNDARY73"},
    )

    result = vkpi_goaffpro.link_kol_affiliate(73, product=None, staff={})

    assert result["ok"] is True
    assert result["created"] is False
    assert "email_synthetic" not in result
    _assert_name_only_lookup(provider_calls, secrets)


@pytest.mark.parametrize("contact_state", _CONTACT_STATES)
def test_post_missing_affiliate_fails_closed_without_provider_create(monkeypatch, contact_state):
    conn = object()
    identity, secrets = _identity_with_contact_state(contact_state)
    provider_calls: list[dict] = []

    _patch_route_db(monkeypatch, conn)
    monkeypatch.setattr(vkpi_goaffpro, "_load_link", lambda *_args: None)
    monkeypatch.setattr(vkpi_goaffpro, "_load_kol_identity", lambda *_args: identity)

    def resolve_affiliate(**kwargs):
        provider_calls.append(dict(kwargs))
        return {"ok": False, "reason": "not_found"}

    monkeypatch.setattr(vkpi_goaffpro.goaffpro_connect, "resolve_affiliate", resolve_affiliate)
    monkeypatch.setattr(
        vkpi_goaffpro,
        "_store_kol_link",
        lambda *_args: pytest.fail("a missing affiliate must not be stored"),
    )

    with pytest.raises(HTTPException) as raised:
        vkpi_goaffpro.link_kol_affiliate(73, product=None, staff={})

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "goaffpro_affiliate_creation_requires_contact",
        "message": "GOAFFPRO affiliate creation requires an authorized contact workflow",
        "retryable": False,
    }
    for secret in secrets:
        assert secret not in str(raised.value.detail)
    _assert_name_only_lookup(provider_calls, secrets)


def test_get_bad_ref_is_pure_read_and_requests_explicit_regeneration(monkeypatch):
    conn = _UpdateConn()

    _patch_route_db(monkeypatch, conn)
    monkeypatch.setattr(vkpi_goaffpro, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        vkpi_goaffpro,
        "_load_link",
        lambda *_args: {
            "kol_pool_id": 73,
            "affiliate_id": "aff-73",
            "ref_code": "aff-73",
            "tracking_url": "",
            "coupon": "",
            "created_at": "2026-08-15T00:00:00Z",
        },
    )
    monkeypatch.setattr(vkpi_goaffpro, "_load_cached_affiliate_state", lambda *_args: {})
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "resolve_affiliate",
        lambda **_kwargs: pytest.fail("GET must not resolve an affiliate"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "get_affiliate",
        lambda *_args: pytest.fail("GET must not fetch commission from the provider"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "find_product_handle",
        lambda *_args: pytest.fail("GET must not resolve products through the provider"),
    )
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("GET must not create or migrate schema"),
    )

    result = vkpi_goaffpro.get_kol_affiliate_link(73, product="AF 35mm", staff={})

    assert result["ref_code"] == "aff-73"
    assert result["tracking_url"] == ""
    assert result["needs_regenerate"] is True
    assert result["tracks_now"] is False
    assert result["product_url"] is None
    assert conn.commits == 0
    assert conn.updates == []


def test_get_absent_link_table_does_not_bootstrap_schema(monkeypatch):
    conn = object()
    monkeypatch.setattr(vkpi_goaffpro, "get_conn", lambda: conn)
    monkeypatch.setattr(vkpi_goaffpro, "_assert_goaffpro_target_readable", lambda *_args: 73)
    monkeypatch.setattr(vkpi_goaffpro, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        vkpi_goaffpro.goaffpro_connect,
        "ensure_goaffpro_links_schema",
        lambda: pytest.fail("GET must not bootstrap schema"),
    )
    assert vkpi_goaffpro.get_kol_affiliate_link(73, product=None, staff={}) == {
        "linked": False,
        "kol_pool_id": 73,
        "needs_regenerate": False,
    }
