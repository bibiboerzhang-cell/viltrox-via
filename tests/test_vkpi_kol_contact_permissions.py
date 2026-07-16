"""Hermetic P0 tests for KOL contact permissions, masking, and cache scope."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.api.routers import vkpi_kol_pool as kol_pool_router
from app.core.permissions import check_tab_permission, normalize_permissions
from app.domains.kol import pool as kol_pool
from app.domains.kol.pool_common import (
    CONTACT_VISIBILITY_FULL,
    CONTACT_VISIBILITY_MASKED,
    KOL_POOL_LIST_COLUMNS,
    contact_visibility_for_staff,
    mask_pool_item,
)


SECRET_EMAIL = "creator@example.com"
SECRET_PHONE = "+12025550199"


def _row() -> dict[str, Any]:
    return {
        "id": 7,
        "handle": "contact-p0",
        "email": SECRET_EMAIL,
        "contact_phone": SECRET_PHONE,
        "other_contacts_json": json.dumps(
            [
                {
                    "contact_type": "email",
                    "contact_value": "manager@example.com",
                    "source": "manual",
                },
                {"contact_type": "phone", "contact_value": SECRET_PHONE, "source": "manual"},
                "whatsapp:+12025550188",
            ]
        ),
        "contact_channels": {
            "email": {"value": "channel@example.com", "source": "profile"},
            "phone": SECRET_PHONE,
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


def test_explicit_vkpi_none_is_not_promoted_to_read() -> None:
    staff = {"role": "employee", "permissions_json": '{"vkpi":"none"}', "is_owner": 0}

    assert normalize_permissions(staff["permissions_json"], staff["role"])["vkpi"] == "none"
    assert check_tab_permission(staff, "vkpi", "read") is False
    assert normalize_permissions({}, "viewer")["vkpi"] == "read"


def test_contact_visibility_requires_owner_or_vkpi_admin() -> None:
    read_staff = {"role": "viewer", "permissions": {"vkpi": "read"}, "is_owner": 0}
    admin_staff = {"role": "employee", "permissions": {"vkpi": "admin"}, "is_owner": 0}
    owner_staff = {"role": "viewer", "permissions": {"vkpi": "none"}, "is_owner": 1}

    assert contact_visibility_for_staff(read_staff) == CONTACT_VISIBILITY_MASKED
    assert contact_visibility_for_staff(admin_staff) == CONTACT_VISIBILITY_FULL
    assert contact_visibility_for_staff(owner_staff) == CONTACT_VISIBILITY_FULL


def test_mask_pool_item_covers_email_phone_other_contacts_and_channels() -> None:
    raw = _row()
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
    assert raw["email"] == SECRET_EMAIL
    assert "malformed-email-value" not in json.dumps(masked)
    assert "https://contact.example.com/private" not in json.dumps(masked)
    assert "nonstandard@example.com" not in json.dumps(masked)
    assert "private-contact-not-json" not in json.dumps(malformed_json)


def test_list_and_workspace_mask_readers_and_isolate_cache_by_visibility(
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
    assert full_list["items"][0]["email"] == SECRET_EMAIL
    assert full_workspace["list"]["items"][0]["email"] == SECRET_EMAIL
    assert any("contact_visibility:masked" in key for key in cache)
    assert any("contact_visibility:full" in key for key in cache)
    assert len([key for key in cache if ":list:" in key]) == 2
    assert len([key for key in cache if ":workspace:" in key]) == 2


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
