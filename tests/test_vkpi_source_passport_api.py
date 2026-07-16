from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import ADMIN_ROUTER_MODULES
from app.api.routers import vkpi_source_passports as api


MANAGER = {"id": 7, "role": "manager", "organization_id": 3}


def test_source_passport_router_is_registered_with_expected_contract_paths():
    assert "vkpi_source_passports" in ADMIN_ROUTER_MODULES
    paths = {route.path for route in api.router.routes}
    assert paths == {
        "/api/admin/vkpi/source-passports",
        "/api/admin/vkpi/source-passports/field-evidence",
        "/api/admin/vkpi/source-passports/{passport_id}/field-evidence",
    }


def test_list_route_uses_trusted_staff_workspace(monkeypatch):
    captured = {}

    def list_passports(**kwargs):
        captured.update(kwargs)
        return {"items": [], "claim_status": "descriptive_only"}

    monkeypatch.setattr(api.source_passport_store, "list_passports", list_passports)
    result = api.source_passport_list(
        entity_type="event_source",
        entity_key=None,
        offset=0,
        limit=20,
        staff=MANAGER,
    )
    assert result["claim_status"] == "descriptive_only"
    assert captured["organization_id"] == 3
    assert captured["entity_type"] == "event_source"


def test_write_routes_use_authenticated_actor_not_body_reviewer(monkeypatch):
    captured = {}

    def save(body, **kwargs):
        captured.update({"body": body, **kwargs})
        return {"ok": True, "claim_status": "descriptive_only"}

    monkeypatch.setattr(api.source_passport_store, "save_passport", save)
    result = api.source_passport_upsert(
        {"entity_type": "event_source", "event_source_id": "event_source_example"},
        staff=MANAGER,
    )
    assert result["ok"] is True
    assert captured["organization_id"] == 3
    assert captured["reviewer_staff_id"] == 7


def test_schema_pending_maps_to_503_without_empty_fallback(monkeypatch):
    def unavailable(**_kwargs):
        raise api.source_passport_store.SourcePassportSchemaUnavailable(
            "migration_248_pending"
        )

    monkeypatch.setattr(api.source_passport_store, "list_passports", unavailable)
    with pytest.raises(HTTPException) as error:
        api.source_passport_list(
            entity_type=None,
            entity_key=None,
            offset=0,
            limit=10,
            staff=MANAGER,
        )
    assert error.value.status_code == 503
    assert error.value.detail == "migration_248_pending"


def test_invalid_input_and_missing_passport_have_distinct_http_statuses():
    with pytest.raises(HTTPException) as bad:
        api._guard(lambda: (_ for _ in ()).throw(ValueError("bad passport")))
    assert bad.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        api._guard(lambda: (_ for _ in ()).throw(LookupError("passport missing")))
    assert missing.value.status_code == 404
