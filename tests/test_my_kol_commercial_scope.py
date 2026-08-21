from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.routers import vkpi_kol_pool_intel, vkpi_rates
from app.domains.kol import (
    business_contact_extract,
    contact_reveal,
    cooperation,
    outreach_draft,
    outreach_pack,
    rate_card,
)


WRITER = {
    "id": 41,
    "staff_id": 41,
    "active": 1,
    "role": "employee",
    "permissions": {"vkpi": "write"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/admin/vkpi/kol-pool/7/contacts/reveal",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })


def _deny(*_args: Any, **_kwargs: Any) -> None:
    raise HTTPException(status_code=403, detail="my_kol_target_forbidden")


def test_arbitrary_id_private_reads_stop_before_commercial_domains(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(vkpi_rates, "_assert_rate_target", _deny)
    monkeypatch.setattr(vkpi_kol_pool_intel, "_assert_private_kol_target", _deny)
    monkeypatch.setattr(rate_card, "list_rates", lambda *_a, **_k: calls.append("rates"))
    monkeypatch.setattr(cooperation, "get_cooperation", lambda *_a, **_k: calls.append("coop"))
    monkeypatch.setattr(outreach_draft, "get_outreach_draft", lambda *_a, **_k: calls.append("draft"))
    monkeypatch.setattr(outreach_pack, "get_outreach_pack", lambda *_a, **_k: calls.append("pack"))

    readers = (
        lambda: vkpi_rates.list_kol_rates(7, limit=10, staff=WRITER),
        lambda: vkpi_kol_pool_intel.get_kol_cooperation(7, staff=WRITER),
        lambda: vkpi_kol_pool_intel.get_kol_outreach_draft(7, staff=WRITER),
        lambda: vkpi_kol_pool_intel.get_kol_outreach_pack(7, staff=WRITER),
    )
    for read in readers:
        with pytest.raises(HTTPException) as exc_info:
            read()
        assert exc_info.value.status_code == 403
    assert calls == []


def test_arbitrary_id_private_writes_stop_before_contact_and_cooperation(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(vkpi_rates, "_assert_rate_target", _deny)
    monkeypatch.setattr(vkpi_kol_pool_intel, "_assert_private_kol_target", _deny)
    monkeypatch.setattr(rate_card, "add_rate", lambda *_a, **_k: calls.append("rate"))
    monkeypatch.setattr(cooperation, "record_action", lambda *_a, **_k: calls.append("coop"))
    monkeypatch.setattr(
        business_contact_extract,
        "add_manual_contact",
        lambda *_a, **_k: calls.append("contact"),
    )

    writers = (
        lambda: vkpi_rates.add_kol_rate(7, {"amount_usd": 100}, staff=WRITER),
        lambda: vkpi_kol_pool_intel.record_kol_cooperation(
            7, {"action": "note"}, staff=WRITER
        ),
        lambda: vkpi_kol_pool_intel.add_kol_manual_contact(
            7, {"email": "creator@example.com"}, staff=WRITER
        ),
    )
    for write in writers:
        with pytest.raises(HTTPException) as exc_info:
            write()
        assert exc_info.value.status_code == 403
    assert calls == []


def test_arbitrary_id_contact_reveal_has_zero_limiter_audit_or_contact_read(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "legacy_system_admin_scope_guard",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "check_kol_pool_employee_contact_permission",
        lambda _staff: True,
    )
    monkeypatch.setattr(vkpi_kol_pool_intel, "_assert_private_kol_target", _deny)
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "enforce_contact_read_rate_limit",
        lambda *_a, **_k: calls.append("limiter"),
    )
    monkeypatch.setattr(
        contact_reveal,
        "view_kol_contact",
        lambda *_a, **_k: calls.append("contact"),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.reveal_kol_contact(
            _request(),
            Response(),
            7,
            body={"confirm": True, "purpose": "kol_detail_view"},
            staff=WRITER,
        )

    assert exc_info.value.status_code == 403
    assert calls == []


def test_private_routes_select_read_vs_write_target_policy(monkeypatch) -> None:
    rate_modes: list[bool] = []
    intel_modes: list[bool] = []
    monkeypatch.setattr(
        vkpi_rates,
        "_assert_rate_target",
        lambda _kol, _staff, *, write: rate_modes.append(write),
    )
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "_assert_private_kol_target",
        lambda _kol, _staff, *, write: intel_modes.append(write),
    )
    monkeypatch.setattr(rate_card, "list_rates", lambda *_a, **_k: {"status": "ready"})
    monkeypatch.setattr(rate_card, "add_rate", lambda *_a, **_k: {"status": "saved"})
    monkeypatch.setattr(cooperation, "get_cooperation", lambda *_a: {"status": "ready"})
    monkeypatch.setattr(vkpi_kol_pool_intel, "_assert_not_others_claim", lambda *_a: None)
    monkeypatch.setattr(cooperation, "record_action", lambda *_a, **_k: {"status": "saved"})

    vkpi_rates.list_kol_rates(7, limit=10, staff=WRITER)
    vkpi_rates.add_kol_rate(7, {"amount_usd": 100}, staff=WRITER)
    vkpi_kol_pool_intel.get_kol_cooperation(7, staff=WRITER)
    vkpi_kol_pool_intel.record_kol_cooperation(7, {"action": "note"}, staff=WRITER)

    assert rate_modes == [False, True]
    assert intel_modes == [False, True]


def test_outreach_generation_release_fence_prevents_provider(monkeypatch) -> None:
    calls: list[str] = []
    modes: list[bool] = []
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "_assert_private_kol_target",
        lambda _kol, _staff, *, write: modes.append(write),
    )
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        outreach_pack,
        "generate_outreach_pack",
        lambda *_a, **_k: calls.append("provider"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            vkpi_kol_pool_intel.generate_kol_outreach_pack(7, {}, staff=WRITER)
        )

    assert exc_info.value.status_code == 503
    assert modes == [True]
    assert calls == []
