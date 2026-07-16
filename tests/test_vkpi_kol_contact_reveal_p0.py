"""P0 regression tests for audited KOL contact disclosure."""
from __future__ import annotations

import json
from typing import Any

from app.core.permissions import check_contact_reveal_permission
from app.domains.audit import service as audit_service
from app.domains.kol import contacts as contacts_domain
from app.domains.kol import contact_reveal
from app.domains.kol import profile as profile_domain
from app.domains.kol import contact_access
from app.domains.kol.contact_access import authorize_plaintext_contacts
from app.domains.kol.outreach_pack import _email_status

EMAIL = "creator@example.com"
PHONE = "+12025550199"


def _contact_payload() -> dict[str, Any]:
    return {
        "kol_id": 7,
        "contacts": [
            {"contact_type": "email", "contact_value": EMAIL, "source": "profile"},
            {"contact_type": "phone", "contact_value": PHONE, "source": "manual"},
        ],
    }


def test_reveal_permission_fails_closed_for_legacy_vkpi_read() -> None:
    reader = {"id": 4, "role": "viewer", "permissions": {"vkpi": "read"}}
    explicit = {
        "id": 5,
        "role": "viewer",
        "permissions": {"vkpi": "read", "contacts.reveal": "read"},
    }

    assert check_contact_reveal_permission(reader) is False
    assert check_contact_reveal_permission(explicit) is True
    assert check_contact_reveal_permission({"id": 6, "role": "staff"}) is True
    assert check_contact_reveal_permission({"id": 7, "role": "admin"}) is True


def test_plaintext_authorization_requires_successful_audit(monkeypatch) -> None:
    staff = {"id": 5, "role": "viewer", "permissions": {"contacts.reveal": "read"}}
    calls: list[dict[str, Any]] = []

    def logged(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"id": 91, "status": "logged"}

    monkeypatch.setattr(audit_service, "log_sensitive_access", logged)
    assert authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=7,
        page_path="/kols/7/contacts",
    ) is True
    assert calls[0]["action_type"] == "view_kol_contact"

    monkeypatch.setattr(audit_service, "log_sensitive_access", lambda **_: {"skipped": True})
    assert authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=7,
        page_path="/kols/7/contacts",
    ) is False


def test_contacts_endpoint_masks_reader_and_audits_explicit_reveal(monkeypatch) -> None:
    monkeypatch.setattr(contacts_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(contacts_domain, "contact_rows", lambda *args, **kwargs: _contact_payload())
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: {"id": 92, "status": "logged"},
    )

    reader = {"id": 4, "role": "viewer", "permissions": {"vkpi": "read"}}
    revealed_staff = {
        "id": 5,
        "role": "viewer",
        "permissions": {"vkpi": "read", "contacts.reveal": "read"},
    }

    masked = contacts_domain.contact_rows_for_request(7, staff=reader)
    revealed = contacts_domain.contact_rows_for_request(7, staff=revealed_staff)

    assert EMAIL not in str(masked)
    assert PHONE not in str(masked)
    assert masked["contact_masked"] is True
    assert revealed["contacts"][0]["contact_value"] == EMAIL
    assert revealed["contact_masked"] is False


def test_profile_masks_all_embedded_contact_copies(monkeypatch) -> None:
    monkeypatch.setattr(
        profile_domain.claims_domain,
        "profile",
        lambda *args, **kwargs: {
            "kol": {
                "id": 7,
                "contact_email": EMAIL,
                "contact_phone": PHONE,
                "contact_links_json": json.dumps([{"value": EMAIL}]),
            },
            "contacts": {
                "email": EMAIL,
                "phone": PHONE,
                "profile_url": "https://example.com/creator",
                "links": [{"contact_type": "email", "contact_value": EMAIL}],
            },
        },
    )
    monkeypatch.setattr(
        profile_domain.account_domain,
        "get_dossier",
        lambda *_: {"contact_email": EMAIL, "contact_emails": [EMAIL]},
    )

    payload = profile_domain.profile_with_dossier(
        7,
        staff={"id": 4, "role": "viewer", "permissions": {"vkpi": "read"}},
    )

    assert EMAIL not in str(payload)
    assert PHONE not in str(payload)
    assert payload["contacts"]["profile_url"] == "https://example.com/creator"
    assert payload["contacts"]["contact_masked"] is True


class _EmailConn:
    def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
        del sql, params

        class _Result:
            @staticmethod
            def fetchone() -> dict[str, Any]:
                return {"email": EMAIL}

        return _Result()


def test_outreach_email_status_is_masked_by_default() -> None:
    masked = _email_status(_EmailConn(), 7)
    revealed = _email_status(_EmailConn(), 7, reveal=True)

    assert EMAIL not in str(masked)
    assert masked["contact_masked"] is True
    assert revealed["email"] == EMAIL
    assert revealed["contact_masked"] is False


class _RevealConn:
    def __init__(self) -> None:
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
        del params
        if "SELECT id, email" in sql:
            class _Result:
                @staticmethod
                def fetchone() -> dict[str, Any]:
                    return {
                        "id": 7,
                        "email": EMAIL,
                        "other_contacts_json": json.dumps(
                            [{"contact_type": "phone", "contact_value": PHONE}]
                        ),
                    }

            return _Result()
        if "UPDATE vkpi_kol_pool" in sql:
            return object()
        raise AssertionError(sql)

    def commit(self) -> None:
        self.commits += 1


def test_legacy_contact_reveal_confirm_cannot_bypass_permission(monkeypatch) -> None:
    conn = _RevealConn()
    monkeypatch.setattr(contact_reveal, "get_conn", lambda: conn)
    monkeypatch.setattr(contact_access, "authorize_plaintext_contacts", lambda *args, **kwargs: False)

    result = contact_reveal.view_kol_contact(
        7,
        confirm=True,
        staff={"id": 4, "role": "viewer", "permissions": {"vkpi": "read"}},
    )

    assert EMAIL not in str(result)
    assert PHONE not in str(result)
    assert result["status"] == "masked"
    assert conn.commits == 0
