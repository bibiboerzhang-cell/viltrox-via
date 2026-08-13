"""Hermetic P0 tests for KOL contact permissions, masking, and cache scope."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.routers import vkpi_kol_contact_projection as contact_projection
from app.api.routers import vkpi_kol_pool as kol_pool_router
from app.core.permissions import check_tab_permission, normalize_permissions
from app.domains.audit import service as audit_service
from app.domains.kol import pool as kol_pool
from app.domains.kol import contacts as contacts_domain
from app.domains.kol.pool_common import (
    CONTACT_VISIBILITY_FULL,
    CONTACT_VISIBILITY_MASKED,
    KOL_POOL_LIST_COLUMNS,
    contact_visibility_for_staff,
    mask_pool_item,
)


SECRET_EMAIL = "creator@example.com"
SECRET_PHONE = "+12025550199"
ACTIVE_STAFF = {
    "id": 17,
    "staff_id": 17,
    "user_id": 170,
    "active": 1,
    "role": "employee",
    "permissions": {"vkpi": "read"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


@pytest.fixture(autouse=True)
def _allow_contact_read_rate_limit(monkeypatch):
    monkeypatch.setattr(
        contact_projection,
        "check_rate_limit",
        lambda *args, **kwargs: (True, contact_projection.CONTACT_READ_MAX_REQUESTS - 1),
    )


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"user-agent", b"kol-contact-test")],
            "client": ("127.0.0.1", 12345),
        }
    )


def _row() -> dict[str, Any]:
    return {
        "id": 7,
        "handle": "contact-p0",
        "bio": f"Business: {SECRET_EMAIL} / WhatsApp {SECRET_PHONE}",
        "email": SECRET_EMAIL,
        "contact_phone": SECRET_PHONE,
        "other_contacts_json": json.dumps(
            [
                {
                    "contact_type": "email",
                    "contact_value": "manager@example.com",
                    "label": "backup manager@example.com",
                    "source": "profile https://example.com/manager@example.com",
                    "reason": f"call {SECRET_PHONE}",
                    "channel": f"email:{SECRET_EMAIL}",
                },
                {"contact_type": "phone", "contact_value": SECRET_PHONE, "source": "manual"},
                "whatsapp:+12025550188",
                {"telegram": "creator_handle"},
            ]
        ),
        "contact_channels": {
            "email": {"masked_value": "c***@e***", "source": "profile"},
            "phone": {"masked_value": "1***9", "source": "manual"},
        },
        "raw_platform_data": {
            "profile": {"email": SECRET_EMAIL, "phone": SECRET_PHONE},
        },
    }


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        row: dict[str, Any] | None = None,
    ):
        self._rows = rows or []
        self._row = row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _PoolConn:
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        del params
        compact = " ".join(sql.split())
        if "GROUP BY COALESCE(NULLIF(candidate_kind" in compact:
            return _Result(rows=[])
        if "ORDER BY" in compact and "LIMIT" in compact:
            return _Result(rows=[_row()])
        if "COUNT(*) AS n" in compact:
            return _Result(row={"n": 1})
        raise AssertionError(f"unexpected SQL: {compact}")


class _SingleItemConn:
    def __init__(self) -> None:
        self.canonical_reads = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        del params
        compact = " ".join(sql.split())
        if "SELECT * FROM vkpi_kol_pool WHERE id=" in compact:
            return _Result(row=_row())
        if "FROM vkpi_kol_pool_contacts" in compact:
            self.canonical_reads += 1
            return _Result(
                rows=[
                    {
                        "contact_type": "email",
                        "contact_value": "canonical@example.com",
                        "contact_source": "website_declared",
                        "consent_basis": "public_scan",
                        "is_public_declared": True,
                        "confidence": 0.9,
                        "first_seen_at": "2026-08-01T00:00:00Z",
                        "last_seen_at": "2026-08-02T00:00:00Z",
                        "created_at": "2026-08-01T00:00:00Z",
                    },
                    {
                        "contact_type": "phone",
                        "contact_value": SECRET_PHONE,
                        "contact_source": "manual",
                    },
                    {
                        "contact_type": "whatsapp",
                        "contact_value": "+12025550188",
                        "contact_source": "manual",
                    },
                    {
                        "contact_type": "website",
                        "contact_value": "https://creator.example/contact",
                        "contact_source": "website_declared",
                    },
                ]
            )
        raise AssertionError(f"unexpected SQL: {compact}")


def _install_hermetic_pool(monkeypatch) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}

    monkeypatch.setattr(kol_pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(kol_pool, "get_conn", lambda: _PoolConn())
    monkeypatch.setattr(
        kol_pool,
        "_table_columns",
        lambda conn, table: set(KOL_POOL_LIST_COLUMNS) | {"candidate_kind"},
    )
    monkeypatch.setattr(
        kol_pool,
        "summary",
        lambda: {"total": 1, "by_platform": [], "country_distribution": []},
    )
    monkeypatch.setattr(kol_pool, "cache_get", lambda key: cache.get(key))

    def store(key: str, payload: dict[str, Any]) -> dict[str, Any]:
        cache[key] = payload
        return payload

    monkeypatch.setattr(kol_pool, "_kol_pool_cache_store", store)
    return cache


def _assert_no_contact_truth(payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    for secret in (SECRET_EMAIL, SECRET_PHONE, "manager@example.com", "channel@example.com"):
        assert secret not in serialized


def test_inline_bio_contact_redactor_preserves_non_contact_metrics() -> None:
    raw = {
        **_row(),
        "bio": (
            f"Business: {SECRET_EMAIL}; WhatsApp {SECRET_PHONE}. "
            "Shoots 35mm and 85mm lenses at 24 fps with 120000 followers."
        ),
    }

    masked = mask_pool_item(raw)

    _assert_no_contact_truth(masked)
    assert "35mm" in masked["bio"]
    assert "85mm" in masked["bio"]
    assert "24 fps" in masked["bio"]
    assert "120000 followers" in masked["bio"]


def test_explicit_vkpi_none_is_not_promoted_to_read() -> None:
    staff = {"role": "employee", "permissions_json": '{"vkpi":"none"}', "is_owner": 0}

    assert normalize_permissions(staff["permissions_json"], staff["role"])["vkpi"] == "none"
    assert check_tab_permission(staff, "vkpi", "read") is False
    assert normalize_permissions({}, "viewer")["vkpi"] == "read"


def test_bulk_contact_visibility_is_masked_for_every_role() -> None:
    read_staff = {"role": "viewer", "permissions": {"vkpi": "read"}, "is_owner": 0}
    admin_staff = {"role": "employee", "permissions": {"vkpi": "admin"}, "is_owner": 0}
    owner_staff = {"role": "viewer", "permissions": {"vkpi": "none"}, "is_owner": 1}

    assert contact_visibility_for_staff(read_staff) == CONTACT_VISIBILITY_MASKED
    assert contact_visibility_for_staff(admin_staff) == CONTACT_VISIBILITY_MASKED
    assert contact_visibility_for_staff(owner_staff) == CONTACT_VISIBILITY_MASKED


def test_legacy_contact_surface_stays_masked_for_ordinary_employee(monkeypatch) -> None:
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(contacts_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        contacts_domain,
        "contact_rows",
        lambda *args, **kwargs: {
            "kol_id": 7,
            "contacts": [{"contact_type": "email", "contact_value": SECRET_EMAIL}],
        },
    )
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 1, "status": "logged"},
    )

    result = contacts_domain.contact_rows_for_request(7, staff=ACTIVE_STAFF)

    _assert_no_contact_truth(result)
    assert result["contact_masked"] is True
    assert audit_calls == []


def test_mask_pool_item_covers_email_phone_other_contacts_and_channels() -> None:
    raw = _row()
    raw["raw_platform_data"] = {
        "profile": {"email": "nested@example.com", "phone": "+12025550177"},
    }
    raw["business_email"] = "malformed-email-value"
    raw["other_contacts_json"] = json.dumps(
        [
            *json.loads(raw["other_contacts_json"]),
            {"contact_type": "website", "url": "https://contact.example.com/private"},
            {
                "contact_type": "other",
                "address": "nonstandard@example.com",
                "links": [{"destination": SECRET_PHONE}],
            },
        ]
    )

    masked = mask_pool_item(raw)
    full = mask_pool_item(raw, contact_visibility=CONTACT_VISIBILITY_FULL)
    malformed_json = mask_pool_item({"other_contacts_json": "private-contact-not-json"})

    _assert_no_contact_truth(masked)
    assert masked["contact_masked"] is True
    assert full["email"] == SECRET_EMAIL
    assert full["contact_phone"] == SECRET_PHONE
    assert full["contact_masked"] is False
    assert "raw_platform_data" not in masked
    assert "raw_platform_data" not in full
    assert SECRET_EMAIL not in masked["bio"]
    assert SECRET_PHONE not in masked["bio"]
    assert full["bio"] == raw["bio"]
    assert "nested@example.com" not in json.dumps(masked)
    assert "nested@example.com" not in json.dumps(full)
    assert raw["email"] == SECRET_EMAIL
    assert "malformed-email-value" not in json.dumps(masked)
    assert "https://contact.example.com/private" not in json.dumps(masked)
    assert "nonstandard@example.com" not in json.dumps(masked)
    assert "private-contact-not-json" not in json.dumps(malformed_json)


def test_list_and_workspace_force_masked_and_never_cache_bulk_plaintext(
    monkeypatch,
) -> None:
    cache = _install_hermetic_pool(monkeypatch)

    masked_list = kol_pool.list_pool(
        query="contact-p0",
        contact_visibility=CONTACT_VISIBILITY_MASKED,
    )
    full_list = kol_pool.list_pool(
        query="contact-p0",
        contact_visibility=CONTACT_VISIBILITY_FULL,
    )
    masked_workspace = kol_pool.workspace(
        query="contact-p0",
        contact_visibility=CONTACT_VISIBILITY_MASKED,
    )
    full_workspace = kol_pool.workspace(
        query="contact-p0",
        contact_visibility=CONTACT_VISIBILITY_FULL,
    )

    _assert_no_contact_truth(masked_list)
    _assert_no_contact_truth(masked_workspace)
    _assert_no_contact_truth(full_list)
    _assert_no_contact_truth(full_workspace)
    for payload in cache.values():
        _assert_no_contact_truth(payload)
    assert any("contact_visibility:masked" in key for key in cache)
    assert not any("contact_visibility:full" in key for key in cache)
    assert len([key for key in cache if ":list:" in key]) == 1
    assert len([key for key in cache if ":workspace:" in key]) == 1


def test_domain_full_item_merges_canonical_only_channels_but_masked_never_loads_them(monkeypatch) -> None:
    conn = _SingleItemConn()
    monkeypatch.setattr(kol_pool, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(kol_pool, "get_conn", lambda: conn)
    monkeypatch.setattr(kol_pool, "_v6_breakdown_for_item", lambda item: {})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", lambda *_args, **_kwargs: [])

    masked = kol_pool.get_item(7, contact_visibility=CONTACT_VISIBILITY_MASKED)
    assert conn.canonical_reads == 0
    full = kol_pool.get_item(7, contact_visibility=CONTACT_VISIBILITY_FULL)
    assert conn.canonical_reads == 1
    contacts = json.loads(full["item"]["other_contacts_json"])

    _assert_no_contact_truth(masked)
    assert {row["contact_type"] for row in contacts} >= {
        "email", "phone", "telegram", "whatsapp", "website",
    }
    assert any(row["contact_value"] == "canonical@example.com" for row in contacts)
    assert any(row["contact_value"] == SECRET_PHONE for row in contacts)
    assert any(
        row["contact_type"] == "whatsapp" and row["contact_value"] == "+12025550188"
        for row in contacts
    )
    assert any(
        row["contact_type"] == "telegram" and row["contact_value"] == "creator_handle"
        for row in contacts
    )
    assert "contact_channels" not in full["item"]
    assert "masked_value" not in str(full)
    assert all("evidence_text" not in row and "source_url" not in row for row in contacts)


def test_bulk_router_surfaces_always_use_masked_contact_projection(monkeypatch) -> None:
    seen: list[str] = []

    def list_stub(**kwargs: Any) -> dict[str, Any]:
        seen.append(str(kwargs["contact_visibility"]))
        return {"items": []}

    def workspace_stub(**kwargs: Any) -> dict[str, Any]:
        seen.append(str(kwargs["contact_visibility"]))
        return {"list": {"items": []}}

    monkeypatch.setattr(kol_pool_router.kol_pool, "list_pool", list_stub)
    monkeypatch.setattr(kol_pool_router.kol_pool, "workspace", workspace_stub)
    read_staff = {"role": "viewer", "permissions": {"vkpi": "read"}, "is_owner": 0}
    admin_staff = {"role": "admin", "permissions": {"vkpi": "admin"}, "is_owner": 0}

    asyncio.run(
        kol_pool_router.list_pool(
            request=object(),
            limit=10,
            offset=0,
            platform="",
            query="",
            country="",
            data_status="",
            sort_by="fit",
            enrichable=None,
            refresh_if_stale=False,
            staff=read_staff,
        )
    )
    kol_pool_router.get_pool_workspace(
        limit=10,
        offset=0,
        platform="",
        query="",
        country="",
        data_status="",
        sort_by="fit",
        enrichable=None,
        staff=admin_staff,
    )

    assert seen == [CONTACT_VISIBILITY_MASKED, CONTACT_VISIBILITY_MASKED]


def test_single_item_route_auto_reveals_for_audited_active_employee(monkeypatch) -> None:
    seen: list[str] = []
    audit_calls: list[dict[str, Any]] = []

    def item_stub(kol_pool_id: int, *, contact_visibility: str) -> dict[str, Any]:
        assert kol_pool_id == 7
        seen.append(contact_visibility)
        return {"item": mask_pool_item(_row(), contact_visibility=contact_visibility)}

    async def refresh_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"freshness": {"status": "fresh"}}

    monkeypatch.setattr(contact_projection, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        contact_projection,
        "authorize_plaintext_contacts",
        lambda *args, **kwargs: audit_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(kol_pool_router.kol_pool, "get_item", item_stub)
    monkeypatch.setattr(kol_pool_router, "_maybe_enqueue_refresh", refresh_stub)
    response = Response()

    result = asyncio.run(
        kol_pool_router.get_item(
            request=_request("/api/admin/vkpi/kol-pool/7"),
            response=response,
            kol_pool_id=7,
            refresh_if_stale=False,
            staff=ACTIVE_STAFF,
        )
    )

    assert seen == [CONTACT_VISIBILITY_FULL]
    assert result["item"]["email"] == SECRET_EMAIL
    assert "manager@example.com" in result["item"]["other_contacts_json"]
    assert "contact_channels" not in result["item"]
    assert "masked_value" not in str(result["item"])
    assert result["item"]["contact_masked"] is False
    assert "raw_platform_data" not in result["item"]
    assert result["contact_projection_reason"] == "audited_internal_staff"
    assert audit_calls[0]["metadata"] == {"surface": "kol_pool_item", "bulk": False}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization, Cookie"


def test_detail_bundle_route_auto_reveals_one_item_without_bulk_cache(monkeypatch) -> None:
    seen: list[str] = []

    def detail_stub(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        assert kol_pool_id == 7
        seen.append(str(kwargs["contact_visibility"]))
        return {
            "status": "ready",
            "item": mask_pool_item(_row(), contact_visibility=kwargs["contact_visibility"]),
        }

    monkeypatch.setattr(contact_projection, "release_validation_active", lambda: False)
    monkeypatch.setattr(contact_projection, "authorize_plaintext_contacts", lambda *args, **kwargs: True)
    monkeypatch.setattr(kol_pool_router.kol_pool, "detail_bundle", detail_stub)
    response = Response()

    result = kol_pool_router.get_item_detail_bundle(
        request=_request("/api/admin/vkpi/kol-pool/7/detail-bundle"),
        response=response,
        kol_pool_id=7,
        video_limit=24,
        llm_limit=20,
        staff=ACTIVE_STAFF,
    )

    assert seen == [CONTACT_VISIBILITY_FULL]
    assert result["item"]["email"] == SECRET_EMAIL
    assert SECRET_PHONE in result["item"]["other_contacts_json"]
    assert result["item"]["contact_masked"] is False
    assert "raw_platform_data" not in result["item"]
    assert result["contact_projection_reason"] == "audited_internal_staff"
    assert response.headers["cache-control"] == "private, no-store"


def test_release_fence_downgrades_single_item_to_masked_without_audit(monkeypatch) -> None:
    seen: list[str] = []

    def item_stub(kol_pool_id: int, *, contact_visibility: str) -> dict[str, Any]:
        seen.append(contact_visibility)
        return {"item": mask_pool_item(_row(), contact_visibility=contact_visibility)}

    async def refresh_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"freshness": {}}

    monkeypatch.setattr(contact_projection, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        contact_projection,
        "check_rate_limit",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rate limit must remain fenced")),
    )
    monkeypatch.setattr(
        contact_projection,
        "authorize_plaintext_contacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audit must remain fenced")),
    )
    monkeypatch.setattr(kol_pool_router.kol_pool, "get_item", item_stub)
    monkeypatch.setattr(kol_pool_router, "_maybe_enqueue_refresh", refresh_stub)

    result = asyncio.run(
        kol_pool_router.get_item(
            request=_request("/api/admin/vkpi/kol-pool/7"),
            response=Response(),
            kol_pool_id=7,
            refresh_if_stale=False,
            staff=ACTIVE_STAFF,
        )
    )

    assert seen == [CONTACT_VISIBILITY_MASKED]
    _assert_no_contact_truth(result)
    assert result["item"]["contact_masked"] is True
    assert result["contact_projection_reason"] == "release_validation_fenced"


def test_audit_failure_downgrades_single_item_to_masked(monkeypatch) -> None:
    async def refresh_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"freshness": {}}

    monkeypatch.setattr(contact_projection, "release_validation_active", lambda: False)
    monkeypatch.setattr(contact_projection, "authorize_plaintext_contacts", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        kol_pool_router.kol_pool,
        "get_item",
        lambda kol_pool_id, *, contact_visibility: {
            "item": mask_pool_item(_row(), contact_visibility=contact_visibility)
        },
    )
    monkeypatch.setattr(kol_pool_router, "_maybe_enqueue_refresh", refresh_stub)

    result = asyncio.run(
        kol_pool_router.get_item(
            request=_request("/api/admin/vkpi/kol-pool/7"),
            response=Response(),
            kol_pool_id=7,
            refresh_if_stale=False,
            staff=ACTIVE_STAFF,
        )
    )

    _assert_no_contact_truth(result)
    assert result["item"]["contact_masked"] is True
    assert result["contact_projection_reason"] == "sensitive_access_audit_unavailable"


def test_get_and_detail_share_contact_read_limit_and_block_enumeration(monkeypatch) -> None:
    checks: list[tuple[str, str, int, int]] = []
    pool_reads: list[str] = []

    def limited(bucket: str, actor: str, maximum: int, window: int) -> tuple[bool, int]:
        checks.append((bucket, actor, maximum, window))
        return (len(checks) <= 2, max(0, 2 - len(checks)))

    async def refresh_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"freshness": {}}

    def item_stub(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        pool_reads.append(f"item:{kol_pool_id}")
        return {"item": mask_pool_item(_row(), contact_visibility=kwargs["contact_visibility"])}

    def detail_stub(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        pool_reads.append(f"detail:{kol_pool_id}")
        return {"item": mask_pool_item(_row(), contact_visibility=kwargs["contact_visibility"])}

    monkeypatch.setattr(contact_projection, "release_validation_active", lambda: False)
    monkeypatch.setattr(contact_projection, "check_rate_limit", limited)
    monkeypatch.setattr(contact_projection, "authorize_plaintext_contacts", lambda *args, **kwargs: True)
    monkeypatch.setattr(kol_pool_router.kol_pool, "get_item", item_stub)
    monkeypatch.setattr(kol_pool_router.kol_pool, "detail_bundle", detail_stub)
    monkeypatch.setattr(kol_pool_router, "_maybe_enqueue_refresh", refresh_stub)

    asyncio.run(
        kol_pool_router.get_item(
            request=_request("/api/admin/vkpi/kol-pool/7"),
            response=Response(),
            kol_pool_id=7,
            refresh_if_stale=False,
            staff=ACTIVE_STAFF,
        )
    )
    kol_pool_router.get_item_detail_bundle(
        request=_request("/api/admin/vkpi/kol-pool/8/detail-bundle"),
        response=Response(),
        kol_pool_id=8,
        video_limit=24,
        llm_limit=20,
        staff=ACTIVE_STAFF,
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            kol_pool_router.get_item(
                request=_request("/api/admin/vkpi/kol-pool/9"),
                response=Response(),
                kol_pool_id=9,
                refresh_if_stale=False,
                staff=ACTIVE_STAFF,
            )
        )

    assert pool_reads == ["item:7", "detail:8"]
    assert checks == [
        ("contact_reveal", "user:170", 30, 300),
        ("contact_reveal", "user:170", 30, 300),
        ("contact_reveal", "user:170", 30, 300),
    ]
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Cache-Control"] == "private, no-store"
    assert exc_info.value.headers["Vary"] == "Authorization, Cookie"
    assert exc_info.value.headers["X-RateLimit-Bucket"] == "contact_reveal"


@pytest.mark.parametrize(
    "staff",
    [
        {**ACTIVE_STAFF, "active": 0},
        {**ACTIVE_STAFF, "organization_id": 2},
        {**ACTIVE_STAFF, "organization_scope_status": "ambiguous"},
        {**ACTIVE_STAFF, "is_owner": 1, "organization_id": 2},
        {**ACTIVE_STAFF, "permissions": {"vkpi": "none"}},
        {**ACTIVE_STAFF, "permissions": {"vkpi": "read", "board.kol-pool": "none"}},
    ],
)
def test_single_item_rejects_ineligible_scope_before_contact_lookup(monkeypatch, staff) -> None:
    monkeypatch.setattr(
        kol_pool_router.kol_pool,
        "get_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pool read must not run")),
    )
    monkeypatch.setattr(
        contact_projection,
        "authorize_plaintext_contacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("audit must not run")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            kol_pool_router.get_item(
                request=_request("/api/admin/vkpi/kol-pool/7"),
                response=Response(),
                kol_pool_id=7,
                refresh_if_stale=False,
                staff=staff,
            )
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "kol_contact_access_not_authorized"}
