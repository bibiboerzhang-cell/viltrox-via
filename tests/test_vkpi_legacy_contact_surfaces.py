"""Legacy KOL surfaces must never bypass the canonical pool reveal boundary."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.audit import service as audit_service
from app.domains.kol import account as account_domain
from app.domains.kol import lookup as lookup_domain
from app.domains.kol import lookup_recovery


EMAIL = "legacy-secret@example.com"
PHONE = "+12025550199"
AUTHORIZED_STAFF = {
    "id": 5,
    "staff_id": 5,
    "active": 1,
    "role": "admin",
    "permissions": {"vkpi": "admin"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


@pytest.fixture
def sensitive_audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: calls.append(kwargs) or {"id": 99, "status": "logged"},
    )
    return calls


def _contact_payload() -> dict[str, Any]:
    return {
        "contact_email": EMAIL,
        "contact_phone": PHONE,
        "contacts": [
            {"contact_type": "email", "contact_value": EMAIL},
            {"contact_type": "phone", "contact_value": PHONE},
        ],
        "nested": {"business_email": EMAIL, "whatsapp": PHONE},
    }


def _assert_value_free(payload: Any) -> None:
    serialized = str(payload)
    assert EMAIL not in serialized
    assert PHONE not in serialized


def test_legacy_dossier_scan_and_analyze_are_masked_without_sensitive_audit(
    monkeypatch: pytest.MonkeyPatch,
    sensitive_audit_calls: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(account_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(account_domain, "get_dossier", lambda _kol_id: _contact_payload())

    async def scan_stub(_kol_id: int, *, max_posts: int = 24) -> dict[str, Any]:
        assert max_posts == 8
        return {"status": "ready", **_contact_payload()}

    async def analyze_stub(
        _kol_id: int,
        *,
        product_sku: str = "",
        snapshot_id: int | str | None = None,
    ) -> dict[str, Any]:
        assert product_sku == "EPIC-65"
        assert snapshot_id == 12
        return {"status": "ready", **_contact_payload()}

    monkeypatch.setattr(account_domain, "scan_account", scan_stub)
    monkeypatch.setattr(account_domain, "analyze_account", analyze_stub)

    results = [
        account_domain.dossier_for_request(7, staff=AUTHORIZED_STAFF),
        asyncio.run(account_domain.scan_account_for_request(7, max_posts=8, staff=AUTHORIZED_STAFF)),
        asyncio.run(
            account_domain.analyze_account_for_request(
                7,
                product_sku="EPIC-65",
                snapshot_id=12,
                staff=AUTHORIZED_STAFF,
            )
        ),
    ]

    for result in results:
        _assert_value_free(result)
    assert sensitive_audit_calls == []


def test_legacy_lookup_masks_response_and_durable_result_without_sensitive_audit(
    monkeypatch: pytest.MonkeyPatch,
    sensitive_audit_calls: list[dict[str, Any]],
) -> None:
    trackers: list[Any] = []

    class Tracker:
        def __init__(self, **_kwargs: Any) -> None:
            self.session_id = 71
            self.task_id = "lookup-task-71"
            self.finishes: list[dict[str, Any]] = []
            trackers.append(self)

        def open(self) -> None:
            return None

        def set_query_text(self, _result: dict[str, Any]) -> None:
            return None

        def stage(self, _stage: str) -> None:
            return None

        def finish(self, **kwargs: Any) -> None:
            self.finishes.append(kwargs)

    monkeypatch.setattr(lookup_domain, "LookupTracker", Tracker)
    monkeypatch.setattr(
        lookup_domain.claims_domain,
        "lookup",
        lambda _body, staff: {"matched": True, "kol": {"id": 7, **_contact_payload()}},
    )
    monkeypatch.setattr(lookup_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(lookup_domain.account_domain, "get_dossier", lambda _kol_id: _contact_payload())

    result = asyncio.run(lookup_domain.lookup_with_context({"query": "creator"}, staff=AUTHORIZED_STAFF))

    _assert_value_free(result)
    assert result["search_session_id"] == 71
    assert len(trackers) == 1
    assert len(trackers[0].finishes) == 1
    _assert_value_free(trackers[0].finishes[0]["result"])
    assert sensitive_audit_calls == []


def test_legacy_lookup_recovery_masks_session_and_ledger_without_sensitive_audit(
    monkeypatch: pytest.MonkeyPatch,
    sensitive_audit_calls: list[dict[str, Any]],
) -> None:
    session = {
        "id": 71,
        "status": "ready",
        "input_payload": {"task_id": "lookup-task-71", "contact_email": EMAIL},
        "result_summary": {
            "reason": "",
            "contacts": [{"contact_type": "phone", "contact_value": PHONE}],
        },
    }
    monkeypatch.setattr(lookup_recovery.search_sessions, "get_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(
        lookup_recovery,
        "_ledger_for_session",
        lambda _session: {"task_id": "lookup-task-71", "result": _contact_payload()},
    )

    result = lookup_recovery.recover_session(71, staff=AUTHORIZED_STAFF)

    _assert_value_free(result)
    assert result["session_id"] == 71
    assert sensitive_audit_calls == []
