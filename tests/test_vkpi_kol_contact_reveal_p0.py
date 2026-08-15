"""P0 regression tests for audited KOL contact disclosure."""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.routers import vkpi_kol_contact_projection as contact_projection
from app.api.routers import vkpi_kol_pool_intel
from app.core import permissions as permissions_domain
from app.core.permissions import (
    check_contact_reveal_permission,
    check_kol_pool_employee_contact_permission,
)
from app.domains.audit import service as audit_service
from app.domains.kol import contacts as contacts_domain
from app.domains.kol import contact_reveal
from app.domains.kol import contact_suppression
from app.domains.kol import profile as profile_domain
from app.domains.kol import business_contact_extract
from app.domains.kol import contact_access
from app.domains.kol import outreach_pack as outreach_pack_domain
from app.domains.kol.contact_access import authorize_plaintext_contacts
from app.domains.kol.outreach_pack import _email_status

EMAIL = "creator@example.com"
PHONE = "+12025550199"
REVEAL_BODY = {"confirm": True, "purpose": "compose_outreach"}
ORG1_EMPLOYEE = {
    "id": 4,
    "active": 1,
    "role": "employee",
    "permissions": {"vkpi": "read"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}
ORG1_ADMIN = {
    "id": 5,
    "active": 1,
    "role": "admin",
    "permissions": {"vkpi": "admin"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


@pytest.fixture(autouse=True)
def _contact_boundary_defaults(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: False)
    monkeypatch.setattr(
        contact_projection,
        "check_rate_limit",
        lambda *args, **kwargs: (True, contact_projection.CONTACT_READ_MAX_REQUESTS - 1),
    )


def _contact_payload() -> dict[str, Any]:
    return {
        "kol_id": 7,
        "contacts": [
            {"contact_type": "email", "contact_value": EMAIL, "source": "profile"},
            {"contact_type": "phone", "contact_value": PHONE, "source": "manual"},
        ],
    }


def test_pool_employee_contact_permission_matches_active_org1_vkpi_boundary() -> None:
    assert check_kol_pool_employee_contact_permission(ORG1_EMPLOYEE) is True
    assert check_kol_pool_employee_contact_permission(
        {**ORG1_EMPLOYEE, "permissions": {"vkpi": "read", "contacts.reveal": "deny"}}
    ) is True
    assert check_kol_pool_employee_contact_permission(ORG1_ADMIN) is True
    assert check_kol_pool_employee_contact_permission({**ORG1_EMPLOYEE, "active": 0}) is False
    assert check_kol_pool_employee_contact_permission({**ORG1_EMPLOYEE, "organization_id": 2}) is False
    assert check_kol_pool_employee_contact_permission(
        {**ORG1_EMPLOYEE, "organization_scope_status": "ambiguous"}
    ) is False
    assert check_kol_pool_employee_contact_permission(
        {**ORG1_EMPLOYEE, "is_owner": 1, "organization_id": 2}
    ) is False
    assert check_kol_pool_employee_contact_permission(
        {**ORG1_EMPLOYEE, "permissions": {"vkpi": "none"}}
    ) is False
    assert check_kol_pool_employee_contact_permission(
        {**ORG1_EMPLOYEE, "permissions": {"vkpi": "read", "board.kol-pool": "none"}}
    ) is False
    assert check_kol_pool_employee_contact_permission(
        {
            **ORG1_EMPLOYEE,
            "is_owner": 1,
            "permissions": {"vkpi": "read", "board.kol-pool": "none"},
        }
    ) is False
    assert check_kol_pool_employee_contact_permission({"id": 6, "role": "staff"}) is False


def test_legacy_contact_permission_does_not_expand_to_every_employee() -> None:
    assert check_contact_reveal_permission(ORG1_EMPLOYEE) is False
    assert check_contact_reveal_permission(
        {**ORG1_EMPLOYEE, "permissions": {"vkpi": "read", "contacts.reveal": "read"}}
    ) is True
    assert check_contact_reveal_permission(ORG1_ADMIN) is True
    assert check_contact_reveal_permission({**ORG1_ADMIN, "organization_id": 2}) is False
    assert check_contact_reveal_permission(
        {**ORG1_ADMIN, "organization_scope_status": "ambiguous"}
    ) is False
    assert check_contact_reveal_permission(
        {**ORG1_ADMIN, "is_owner": 1, "organization_id": 2}
    ) is False
    assert check_contact_reveal_permission({**ORG1_ADMIN, "active": 0}) is False
    assert check_contact_reveal_permission({"id": 5, "role": "staff"}) is False


class _OutreachConn:
    def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
        del params

        class _Result:
            @staticmethod
            def fetchone() -> dict[str, Any] | None:
                if "SELECT 1 FROM vkpi_kol_pool" in sql:
                    return {"exists": 1}
                if "SELECT email FROM vkpi_kol_pool" in sql:
                    return {"email": EMAIL}
                if "FROM vkpi_analysis_cache" in sql:
                    return None
                raise AssertionError(sql)

        return _Result()


@pytest.mark.parametrize(
    "staff",
    [
        {**ORG1_ADMIN, "organization_id": 2},
        {**ORG1_ADMIN, "organization_scope_status": "ambiguous"},
        {**ORG1_ADMIN, "active": 0},
    ],
)
def test_outreach_pack_cross_tenant_or_inactive_is_masked_without_audit(monkeypatch, staff) -> None:
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(outreach_pack_domain, "get_conn", lambda: _OutreachConn())
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 1, "status": "logged"},
    )

    result = outreach_pack_domain.get_outreach_pack(7, staff=staff)

    assert EMAIL not in str(result)
    assert result["email"]["contact_masked"] is True
    assert audit_calls == []


def test_outreach_pack_same_tenant_admin_stays_masked_without_audit(monkeypatch) -> None:
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(outreach_pack_domain, "get_conn", lambda: _OutreachConn())
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 1, "status": "logged"},
    )

    result = outreach_pack_domain.get_outreach_pack(7, staff=ORG1_ADMIN)

    assert EMAIL not in str(result)
    assert result["email"]["contact_masked"] is True
    assert audit_calls == []


def test_outreach_pack_generate_cached_and_fresh_responses_stay_masked_without_audit(monkeypatch) -> None:
    email_status_reveals: list[bool] = []
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(outreach_pack_domain, "get_conn", lambda: object())
    monkeypatch.setattr(outreach_pack_domain, "_kol_row", lambda *_: {"id": 7, "email": EMAIL})
    monkeypatch.setattr(
        outreach_pack_domain,
        "_email_status",
        lambda *_args, reveal=False, **_kwargs: email_status_reveals.append(reveal) or {
            "state": "present",
            "email": "c***@e***",
            "contact_masked": True,
        },
    )
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 1, "status": "logged"},
    )
    monkeypatch.setattr(
        outreach_pack_domain,
        "_read_pack_cache",
        lambda *_: {
            "pack": {"provenance": {"generated_date": outreach_pack_domain._today()}},
            "model": "rule_template",
            "updated_at": "2026-08-13T00:00:00Z",
        },
    )

    cached = outreach_pack_domain.generate_outreach_pack(7, staff=ORG1_ADMIN)

    monkeypatch.setattr(outreach_pack_domain, "_read_pack_cache", lambda *_: None)
    monkeypatch.setattr(outreach_pack_domain, "_content_fit_snapshot", lambda *_: {})
    monkeypatch.setattr(outreach_pack_domain, "_build_brief", lambda *_: {})
    monkeypatch.setattr(outreach_pack_domain, "_personalization_context", lambda *_: {})
    monkeypatch.setattr(outreach_pack_domain, "_critic_context", lambda *_: {})
    monkeypatch.setattr(
        outreach_pack_domain,
        "_generate_email_draft",
        lambda *_args, **_kwargs: ({"subject": "draft"}, {"model": "rule_template"}),
    )
    monkeypatch.setattr(outreach_pack_domain, "_write_pack_cache", lambda *_args, **_kwargs: None)

    fresh = outreach_pack_domain.generate_outreach_pack(7, force=True, staff=ORG1_ADMIN)

    assert cached["email"]["contact_masked"] is True
    assert fresh["email"]["contact_masked"] is True
    assert email_status_reveals == [False, False]
    assert EMAIL not in str(cached)
    assert EMAIL not in str(fresh)
    assert audit_calls == []


@pytest.mark.parametrize(
    "staff",
    [
        {**ORG1_ADMIN, "organization_id": 2},
        {**ORG1_ADMIN, "organization_scope_status": "ambiguous"},
        {**ORG1_ADMIN, "active": 0},
        {**ORG1_EMPLOYEE, "permissions": {"vkpi": "read"}},
    ],
)
def test_manual_contact_write_rejects_scope_or_write_denial_before_domain(monkeypatch, staff) -> None:
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(business_contact_extract, "add_manual_contact", unexpected)

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.add_kol_manual_contact(
            7,
            {"email": EMAIL},
            staff=staff,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "kol_contact_write_not_authorized"}
    assert called is False


def test_manual_contact_write_response_is_masked_for_authorized_employee(monkeypatch) -> None:
    writer = {**ORG1_EMPLOYEE, "permissions": {"vkpi": "write"}}
    monkeypatch.setattr(
        business_contact_extract,
        "add_manual_contact",
        lambda *args, **kwargs: {
            "status": "saved",
            "contacts": [{"contact_type": "email", "contact_value": EMAIL}],
        },
    )

    result = vkpi_kol_pool_intel.add_kol_manual_contact(
        7,
        {"email": EMAIL},
        staff=writer,
    )

    assert result["contact_masked"] is True
    assert EMAIL not in str(result)


def test_plaintext_authorization_requires_successful_audit(monkeypatch) -> None:
    staff = ORG1_ADMIN
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

    calls.clear()
    assert authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=7,
        page_path="/kols/7/contacts",
        ip="203.0.113.10",
        user_agent="contact-test-agent",
    ) is True
    assert calls[0]["ip"] == "203.0.113.10"
    assert calls[0]["user_agent"] == "contact-test-agent"

    monkeypatch.setattr(audit_service, "log_sensitive_access", lambda **_: {"skipped": True})
    assert authorize_plaintext_contacts(
        staff,
        resource_type="kol",
        resource_id=7,
        page_path="/kols/7/contacts",
    ) is False


def test_legacy_contacts_endpoint_masks_every_staff_without_sensitive_audit(monkeypatch) -> None:
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(contacts_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(contacts_domain, "contact_rows", lambda *args, **kwargs: _contact_payload())
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 92, "status": "logged"},
    )

    reader = {**ORG1_EMPLOYEE, "permissions": {"vkpi": "read", "board.kol-pool": "none"}}
    for staff in (reader, ORG1_ADMIN):
        result = contacts_domain.contact_rows_for_request(7, staff=staff)
        assert EMAIL not in str(result)
        assert PHONE not in str(result)
        assert result["contact_masked"] is True
    assert audit_calls == []


def test_profile_masks_all_embedded_contact_copies(monkeypatch) -> None:
    audit_calls: list[dict[str, Any]] = []
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
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 93, "status": "logged"},
    )

    payload = profile_domain.profile_with_dossier(
        7,
        staff=ORG1_ADMIN,
    )

    assert EMAIL not in str(payload)
    assert PHONE not in str(payload)
    assert payload["contacts"]["profile_url"] == "https://example.com/creator"
    assert payload["contacts"]["contact_masked"] is True
    assert audit_calls == []


class _EmailConn:
    def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
        del sql, params

        class _Result:
            @staticmethod
            def fetchone() -> dict[str, Any]:
                return {"email": EMAIL}

        return _Result()


def test_outreach_email_status_cannot_bypass_pool_reveal_boundary() -> None:
    masked = _email_status(_EmailConn(), 7)
    legacy_reveal_flag = _email_status(_EmailConn(), 7, reveal=True)

    assert EMAIL not in str(masked)
    assert masked["contact_masked"] is True
    assert EMAIL not in str(legacy_reveal_flag)
    assert legacy_reveal_flag["contact_masked"] is True


class _RevealConn:
    def __init__(self, canonical_rows: list[dict[str, Any]] | None = None) -> None:
        self.commits = 0
        self.canonical_rows = canonical_rows if canonical_rows is not None else [
            {
                "id": 41,
                "contact_type": "whatsapp",
                "contact_value": "+12025550188",
                "contact_source": "website_declared",
                "verified_at": "2026-08-02T00:00:00Z",
            }
        ]

    def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
        del params
        if "SELECT id FROM vkpi_kol_pool" in sql:
            class _Result:
                @staticmethod
                def fetchone() -> dict[str, Any]:
                    return {"id": 7}

            return _Result()
        if "FROM vkpi_kol_pool_contacts" in sql:
            rows = self.canonical_rows

            class _CanonicalResult:
                @staticmethod
                def fetchall() -> list[dict[str, Any]]:
                    return rows

            return _CanonicalResult()
        if "UPDATE vkpi_kol_pool" in sql:
            return object()
        raise AssertionError(sql)

    def commit(self) -> None:
        self.commits += 1


def test_contact_reveal_forwards_request_metadata_to_audit_boundary(monkeypatch) -> None:
    conn = _RevealConn()
    calls: list[dict[str, Any]] = []

    def authorized(*args: Any, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(contact_reveal, "get_conn", lambda: conn)
    monkeypatch.setattr(contact_reveal, "_ensure_contact_audit_schema", lambda: None)
    monkeypatch.setattr(contact_access, "authorize_plaintext_contacts", authorized)
    monkeypatch.setattr(
        contact_suppression,
        "contact_eligibility",
        lambda **_kwargs: {
            "status": "eligible",
            "eligible": True,
            "channel": "whatsapp",
            "verification_status": "verified_public_business",
        },
    )

    result = contact_reveal.view_kol_contact(
        7,
        confirm=True,
        staff=ORG1_EMPLOYEE,
        ip="203.0.113.10",
        user_agent="contact-test-agent",
        purpose="compose_outreach",
    )

    assert result["status"] == "full"
    assert result["contacts"] == [
        {
            "id": 41,
            "channel": "whatsapp",
            "contact_type": "whatsapp",
            "value": "+12025550188",
            "verification_status": "verified_public_business",
            "source_type": "website_declared",
            "verified_at": "2026-08-02T00:00:00Z",
        }
    ]
    assert conn.commits == 1
    assert len(calls) == 1
    assert calls[0]["ip"] == "203.0.113.10"
    assert calls[0]["user_agent"] == "contact-test-agent"
    assert calls[0]["metadata"]["purpose"] == "compose_outreach"


def test_legacy_contact_reveal_confirm_cannot_bypass_permission(monkeypatch) -> None:
    conn = _RevealConn()
    monkeypatch.setattr(contact_reveal, "get_conn", lambda: conn)
    monkeypatch.setattr(permissions_domain, "check_kol_pool_employee_contact_permission", lambda *_a, **_kw: False)

    result = contact_reveal.view_kol_contact(
        7,
        confirm=True,
        staff=ORG1_EMPLOYEE,
        purpose="kol_detail_view",
    )

    assert EMAIL not in str(result)
    assert PHONE not in str(result)
    assert result["status"] == "restricted"
    assert result["contacts"] == []
    assert result["reason"] == "contact_reveal_not_authorized"
    assert conn.commits == 0


class _RevealRequest:
    client = type("Client", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "kol-contact-test"}

    def __init__(self) -> None:
        self.state = type("State", (), {})()


def _response() -> Response:
    return Response()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"confirm": True},
        {"confirm": True, "purpose": "other"},
        {"confirm": True, "purpose": "compose_outreach", "extra": True},
    ],
)
def test_reveal_route_requires_exact_confirmation_and_purpose(monkeypatch, body) -> None:
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(contact_reveal, "view_kol_contact", unexpected)

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.reveal_kol_contact(
            _RevealRequest(),
            _response(),
            7,
            body=body,
            staff=ORG1_ADMIN,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"code": "contact_reveal_confirmation_and_purpose_required"}
    assert called is False


@pytest.mark.parametrize(
    ("expected_status", "canonical_rows", "eligibility", "expected_reason"),
    [
        (
            "full",
            None,
            {
                "status": "eligible",
                "eligible": True,
                "channel": "whatsapp",
                "verification_status": "verified_public_business",
            },
            "eligible_contacts_available",
        ),
        (
            "restricted",
            None,
            {"status": "restricted", "eligible": False, "reason": "verification_not_eligible"},
            "verification_required",
        ),
        ("empty", [], None, "no_verified_contacts"),
    ],
)
def test_reveal_route_limits_every_post_and_audits_only_full_plaintext(
    monkeypatch,
    expected_status,
    canonical_rows,
    eligibility,
    expected_reason,
) -> None:
    conn = _RevealConn(canonical_rows=canonical_rows)
    audit_calls: list[dict[str, Any]] = []
    rate_calls: list[tuple[Any, ...]] = []
    eligibility_calls: list[dict[str, Any]] = []

    def eligible(**kwargs: Any) -> dict[str, Any]:
        eligibility_calls.append(kwargs)
        if eligibility is None:
            raise AssertionError("empty canonical set must not run eligibility")
        return dict(eligibility)

    monkeypatch.setattr(contact_reveal, "get_conn", lambda: conn)
    monkeypatch.setattr(contact_reveal, "_ensure_contact_audit_schema", lambda: None)
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 91, "status": "logged"},
    )
    monkeypatch.setattr(contact_suppression, "contact_eligibility", eligible)
    monkeypatch.setattr(
        contact_projection,
        "check_rate_limit",
        lambda *args, **kwargs: rate_calls.append(args) or (True, 29),
    )

    result = vkpi_kol_pool_intel.reveal_kol_contact(
        _RevealRequest(),
        response := _response(),
        7,
        body=REVEAL_BODY,
        staff=ORG1_ADMIN,
    )

    assert result["status"] == expected_status
    assert result["reason"] == expected_reason
    assert len(audit_calls) == (1 if expected_status == "full" else 0)
    if expected_status == "full":
        assert audit_calls[0]["action_type"] == "view_kol_contact"
        assert audit_calls[0]["metadata"]["purpose"] == "compose_outreach"
    assert rate_calls == [("contact_reveal", "user:5", 30, 300)]
    assert len(eligibility_calls) == (0 if expected_status == "empty" else 1)
    assert conn.commits == (1 if expected_status == "full" else 0)
    if expected_status == "full":
        assert result["contacts"][0]["value"] == "+12025550188"
        assert result["contacts"][0]["source_type"] == "website_declared"
        assert result["contact_masked"] is False
    else:
        assert result["contacts"] == []
        assert "+12025550188" not in str(result)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Authorization, Cookie"


def test_reveal_route_rejects_malformed_domain_payload_without_pii(monkeypatch) -> None:
    monkeypatch.setattr(
        contact_reveal,
        "view_kol_contact",
        lambda *args, **kwargs: {
            "status": "masked",
            # Even if a malformed domain result contains PII, the route rejects
            # it with a generic error and never serializes this payload.
            "email": EMAIL,
            "other_contacts": [{"contact_type": "phone", "contact_value": PHONE}],
            "contact_masked": True,
        },
    )

    result = vkpi_kol_pool_intel.reveal_kol_contact(
        _RevealRequest(),
        _response(),
        7,
        body=REVEAL_BODY,
        staff=ORG1_ADMIN,
    )

    assert result == {
        "status": "restricted",
        "kol_pool_id": 7,
        "contacts": [],
        "contact_masked": True,
        "reason": "contact_reveal_unavailable",
    }
    assert EMAIL not in str(result)
    assert PHONE not in str(result)


def test_reveal_route_maps_missing_kol_without_leaking_lookup_details(monkeypatch) -> None:
    def missing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise LookupError("internal lookup detail")

    monkeypatch.setattr(contact_reveal, "view_kol_contact", missing)

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.reveal_kol_contact(
            _RevealRequest(),
            _response(),
            404,
            body=REVEAL_BODY,
            staff=ORG1_ADMIN,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "kol pool item not found"


def test_reveal_route_rate_limits_sensitive_reads_before_domain_call(monkeypatch) -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/admin/vkpi/kol-pool/7/contacts/reveal",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(contact_reveal, "view_kol_contact", unexpected)
    monkeypatch.setattr(contact_projection, "check_rate_limit", lambda *args, **kwargs: (False, 0))

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.reveal_kol_contact(
            request,
            _response(),
            7,
            body=REVEAL_BODY,
            staff=ORG1_ADMIN,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["X-RateLimit-Bucket"] == "contact_reveal"
    assert exc_info.value.headers["Cache-Control"] == "private, no-store"
    assert exc_info.value.headers["Pragma"] == "no-cache"
    assert exc_info.value.headers["Vary"] == "Authorization, Cookie"
    assert called is False


def test_reveal_route_release_fence_returns_safe_mask_without_rate_audit_or_db(monkeypatch) -> None:
    rate_calls: list[tuple[Any, ...]] = []
    domain_called = False

    def unexpected_domain(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal domain_called
        domain_called = True
        return {
            "status": "full",
            "contacts": [{"value": EMAIL}],
            "contact_masked": False,
        }

    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        contact_projection,
        "check_rate_limit",
        lambda *args, **kwargs: rate_calls.append(args) or (True, 29),
    )
    monkeypatch.setattr(contact_reveal, "view_kol_contact", unexpected_domain)

    result = vkpi_kol_pool_intel.reveal_kol_contact(
        _RevealRequest(),
        response := _response(),
        7,
        body=REVEAL_BODY,
        staff=ORG1_ADMIN,
    )

    assert result == {
        "status": "restricted",
        "kol_pool_id": 7,
        "contacts": [],
        "contact_masked": True,
        "reason": "release_validation_fenced",
    }
    assert rate_calls == []
    assert domain_called is False
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "staff",
    [
        {"id": 5, "role": "staff", "organization_id": 2, "organization_scope_status": "resolved"},
        {"id": 5, "role": "staff", "organization_id": 1, "organization_scope_status": "unresolved"},
        {"id": 5, "role": "staff"},
    ],
)
def test_reveal_route_stops_unresolved_or_cross_tenant_before_domain(monkeypatch, staff) -> None:
    called = False

    def unexpected(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(contact_reveal, "view_kol_contact", unexpected)

    with pytest.raises(HTTPException) as exc_info:
        vkpi_kol_pool_intel.reveal_kol_contact(
            _RevealRequest(),
            _response(),
            7,
            body=REVEAL_BODY,
            staff=staff,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"code": "contact_reveal_scope_unavailable"}
    assert called is False
