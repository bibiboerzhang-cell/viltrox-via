from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException, Request, Response

from app.domains.kol import contact_acquisition_queue, contact_reveal, contact_system


def _request(method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/admin/vkpi/kol-pool/1",
            "headers": [(b"user-agent", b"contract-test")],
            "client": ("127.0.0.1", 4567),
        }
    )


def _contact_summary_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            contact_type TEXT NOT NULL,
            contact_value TEXT NOT NULL,
            channel TEXT,
            verification_status TEXT,
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT
        );
        INSERT INTO vkpi_kol_pool_contacts VALUES
          (1, 1, 'email', 'verified-secret@example.com', 'email',
           'verified_public_business', '2026-08-15T01:00:00Z', NULL, NULL),
          (2, 1, 'instagram_dm', '@observed-secret', 'instagram_dm',
           'observed', NULL, NULL, NULL),
          (3, 1, 'phone', '+14155550000', 'phone',
           'invalid', NULL, '2026-08-15T02:00:00Z', NULL),
          (4, 1, 'email', 'revoked-secret@example.com', 'email',
           'revoked', '2026-08-15T03:00:00Z', NULL, '2026-08-15T04:00:00Z');
        """
    )
    return db


def _reveal_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            contact_reveal_count INTEGER NOT NULL DEFAULT 0,
            contact_last_revealed_at TEXT,
            contact_last_revealed_by_staff_id INTEGER
        );
        INSERT INTO vkpi_kol_pool(id) VALUES (1);
        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            contact_type TEXT NOT NULL,
            contact_value TEXT NOT NULL,
            contact_source TEXT,
            verified_at TEXT
        );
        INSERT INTO vkpi_kol_pool_contacts VALUES
          (11, 1, 'email', 'private-contract-value@example.com',
           'youtube_about_declared', '2026-08-15T01:00:00Z');
        """
    )
    return db


def _queue_db(*, profile_url: str = "https://youtube.com/@creator") -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            profile_url TEXT,
            bio TEXT,
            viltrox_fit_score REAL,
            raw_platform_data TEXT
        );
        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            contact_type TEXT,
            contact_value TEXT,
            channel TEXT,
            normalized_value TEXT,
            verification_status TEXT DEFAULT 'observed',
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE vkpi_kol_contact_evidence (
            id INTEGER PRIMARY KEY,
            contact_id INTEGER NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            confidence REAL,
            is_public_declared INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE vkpi_kol_contact_suppressions (
            id INTEGER PRIMARY KEY,
            brand_scope TEXT NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            contact_fingerprint TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE vkpi_kol_contact_acquisition_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL,
            trigger_source TEXT NOT NULL DEFAULT 'reconcile',
            reason_code TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            contactability_score REAL,
            last_reconciled_at TEXT,
            next_attempt_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.execute(
        "INSERT INTO vkpi_kol_pool VALUES (1, 'youtube', ?, '', NULL, '{}')",
        (profile_url,),
    )
    db.commit()
    return db


def test_contact_summary_is_value_free_and_does_not_claim_observed_as_actionable() -> None:
    result = contact_system.contact_summary(1, conn=_contact_summary_db())

    assert result["status"] == "known"
    assert result["known_contact_count"] == 2
    assert result["verified_contact_count"] == 1
    assert result["channel_types"] == ["email", "instagram_dm"]
    assert result["verified_channel_types"] == ["email"]
    assert result["last_verified_at"] == "2026-08-15T01:00:00Z"
    assert result["actionability"] == "requires_reveal"
    serialized = json.dumps(result, sort_keys=True)
    for secret in (
        "verified-secret@example.com",
        "@observed-secret",
        "+14155550000",
        "revoked-secret@example.com",
    ):
        assert secret not in serialized


def test_contactability_is_a_clue_score_and_uses_lifecycle_verified_at_only() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, email TEXT);
        INSERT INTO vkpi_kol_pool VALUES (1, 'legacy-main-email@example.com');
        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            contact_type TEXT,
            contact_value TEXT,
            contact_source TEXT,
            confidence REAL,
            first_seen_at TEXT,
            last_seen_at TEXT,
            created_at TEXT,
            verification_status TEXT,
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT
        );
        INSERT INTO vkpi_kol_pool_contacts VALUES
          (1, 1, 'email', 'verified-clue@example.com', 'youtube_about_declared', .95,
           '2026-08-01T00:00:00Z', '2026-08-15T00:00:00Z', NULL,
           'verified_public_business', '2026-08-14T00:00:00Z', NULL, NULL),
          (2, 1, 'instagram_dm', '@observed-clue', 'raw_bio_scan', .6,
           '2026-08-02T00:00:00Z', '2026-08-15T02:00:00Z', NULL,
           'observed', NULL, NULL, NULL),
          (3, 1, 'phone', '+14155550100', 'manual', 1,
           '2026-08-03T00:00:00Z', '2026-08-15T03:00:00Z', NULL,
           'invalid', NULL, '2026-08-15T04:00:00Z', NULL),
          (4, 1, 'email', 'revoked-clue@example.com', 'manual', 1,
           '2026-08-04T00:00:00Z', '2026-08-15T05:00:00Z', NULL,
           'revoked', '2026-08-10T00:00:00Z', NULL, '2026-08-15T06:00:00Z');
        """
    )

    result = contact_system.contactability(1, conn=db)

    assert result["score_kind"] == "contact_clue_score"
    assert result["known_contact_count"] == 2
    assert result["verified_contact_count"] == 1
    assert result["actionability"] == "requires_reveal"
    assert result["last_verified_at"] == "2026-08-14T00:00:00Z"
    assert set(result["channels"]) == {"email", "instagram_dm"}
    serialized = json.dumps(result, sort_keys=True)
    assert "masked_value" not in serialized
    assert "legacy-main-email@example.com" not in serialized
    assert "+14155550100" not in serialized
    assert "revoked-clue@example.com" not in serialized


@pytest.mark.parametrize("purpose", ["kol_detail_view", "compose_outreach"])
def test_reveal_is_typed_and_uses_one_audit_then_keyword_only_eligibility(
    monkeypatch: pytest.MonkeyPatch, purpose: str
) -> None:
    db = _reveal_db()
    audits: list[dict[str, Any]] = []
    eligibility_calls: list[dict[str, Any]] = []

    import app.domains.kol.contact_access as contact_access
    import app.domains.kol.contact_suppression as contact_suppression
    import app.core.permissions as permissions

    def fake_authorize(_staff: Any, **kwargs: Any) -> bool:
        audits.append(kwargs)
        return True

    def fake_eligibility(**kwargs: Any) -> dict[str, Any]:
        eligibility_calls.append(kwargs)
        return {
            "status": "eligible",
            "eligible": True,
            "reason": "eligible_verified_public_business",
            "channel": "email",
            "verification_status": "verified_public_business",
        }

    monkeypatch.setattr(contact_reveal, "get_conn", lambda: db)
    monkeypatch.setattr(contact_reveal, "_ensure_contact_audit_schema", lambda: None)
    monkeypatch.setattr(permissions, "check_kol_pool_employee_contact_permission", lambda _staff: True)
    monkeypatch.setattr(contact_access, "authorize_plaintext_contacts", fake_authorize)
    monkeypatch.setattr(contact_suppression, "contact_eligibility", fake_eligibility)

    result = contact_reveal.view_kol_contact(
        1,
        confirm=True,
        purpose=purpose,
        staff={"staff_id": 7, "organization_id": 9},
    )

    assert result["status"] == "full"
    assert result["contact_masked"] is False
    assert result["contacts"] == [
        {
            "id": 11,
            "channel": "email",
            "contact_type": "email",
            "value": "private-contract-value@example.com",
            "verification_status": "verified_public_business",
            "source_type": "youtube_about_declared",
            "verified_at": "2026-08-15T01:00:00Z",
        }
    ]
    assert len(audits) == 1
    assert audits[0]["metadata"]["purpose"] == purpose
    assert len(eligibility_calls) == 1
    assert eligibility_calls[0]["contact_id"] == 11
    assert eligibility_calls[0]["kol_pool_id"] == 1
    assert eligibility_calls[0]["brand_scope"] == "organization:9"


def test_reveal_suppression_is_restricted_and_contains_no_contact_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _reveal_db()
    import app.domains.kol.contact_access as contact_access
    import app.domains.kol.contact_suppression as contact_suppression
    import app.core.permissions as permissions

    monkeypatch.setattr(contact_reveal, "get_conn", lambda: db)
    monkeypatch.setattr(permissions, "check_kol_pool_employee_contact_permission", lambda _staff: True)
    monkeypatch.setattr(
        contact_access,
        "authorize_plaintext_contacts",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("restricted reveal must not write plaintext audit")
        ),
    )
    monkeypatch.setattr(
        contact_suppression,
        "contact_eligibility",
        lambda **_kw: {"status": "restricted", "eligible": False, "reason": "suppressed"},
    )

    result = contact_reveal.view_kol_contact(
        1,
        confirm=True,
        purpose="compose_outreach",
        staff={"staff_id": 7, "organization_id": 9},
    )

    assert result == {
        "status": "restricted",
        "kol_pool_id": 1,
        "contacts": [],
        "contact_masked": True,
        "reason": "suppressed",
    }
    assert "private-contract-value@example.com" not in json.dumps(result)


@pytest.mark.parametrize(
    ("eligibility_reason", "public_reason"),
    [
        ("verification_not_eligible", "verification_required"),
        ("fingerprint_key_unavailable", "contact_guard_unavailable"),
        ("verification_evidence_missing", "contact_guard_unavailable"),
    ],
)
def test_reveal_distinguishes_unverified_from_guard_failure_without_pii(
    monkeypatch: pytest.MonkeyPatch,
    eligibility_reason: str,
    public_reason: str,
) -> None:
    db = _reveal_db()
    import app.domains.kol.contact_access as contact_access
    import app.domains.kol.contact_suppression as contact_suppression
    import app.core.permissions as permissions

    monkeypatch.setattr(contact_reveal, "get_conn", lambda: db)
    monkeypatch.setattr(permissions, "check_kol_pool_employee_contact_permission", lambda _staff: True)
    monkeypatch.setattr(
        contact_access,
        "authorize_plaintext_contacts",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("restricted reveal must not write plaintext audit")
        ),
    )
    monkeypatch.setattr(
        contact_suppression,
        "contact_eligibility",
        lambda **_kw: {
            "status": "restricted",
            "eligible": False,
            "reason": eligibility_reason,
        },
    )
    result = contact_reveal.view_kol_contact(
        1,
        confirm=True,
        purpose="kol_detail_view",
        staff={"staff_id": 7, "organization_id": 9},
    )

    assert result["status"] == "restricted"
    assert result["reason"] == public_reason
    assert result["contacts"] == []
    assert "private-contract-value@example.com" not in json.dumps(result)


def test_get_item_and_bundle_poll_do_not_consume_contact_limiter_or_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routers.vkpi_kol_contact_projection as projection
    import app.api.routers.vkpi_kol_pool as routes
    import app.domains.kol.contact_access as contact_access

    calls = {"limit": 0, "audit": 0}

    def forbidden_limit(*_args: Any, **_kwargs: Any) -> None:
        calls["limit"] += 1
        raise AssertionError("GET poll must not consume contact rate limit")

    def forbidden_audit(*_args: Any, **_kwargs: Any) -> bool:
        calls["audit"] += 1
        raise AssertionError("GET poll must not write sensitive access audit")

    monkeypatch.setattr(projection, "enforce_contact_read_rate_limit", forbidden_limit)
    monkeypatch.setattr(contact_access, "authorize_plaintext_contacts", forbidden_audit)
    monkeypatch.setattr(
        routes.kol_pool,
        "get_item",
        lambda *_a, **kwargs: {
            "item": {"contact_summary": {"status": "known"}},
            "visibility": kwargs.get("contact_visibility"),
        },
    )
    monkeypatch.setattr(
        routes.kol_pool,
        "detail_bundle",
        lambda *_a, **kwargs: {
            "item": {"contact_summary": {"status": "known"}},
            "visibility": kwargs.get("contact_visibility"),
        },
    )

    async def fake_refresh(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"freshness": "fresh", "status": "not_enqueued"}

    monkeypatch.setattr(routes, "_maybe_enqueue_refresh", fake_refresh)
    item = asyncio.run(
        routes.get_item(
            request=_request(),
            response=Response(),
            kol_pool_id=1,
            refresh_if_stale=False,
            staff={"staff_id": 7},
        )
    )
    bundle = routes.get_item_detail_bundle(
        request=_request(),
        response=Response(),
        kol_pool_id=1,
        video_limit=24,
        llm_limit=20,
        staff={"staff_id": 7},
    )

    assert calls == {"limit": 0, "audit": 0}
    assert item["contact_projection_reason"] == "summary_only"
    assert bundle["contact_projection_reason"] == "summary_only"
    assert item["visibility"] == "masked"
    assert bundle["visibility"] == "masked"


def test_value_free_projection_removes_aliases_nested_values_and_inline_masks() -> None:
    payload = {
        "id": 7,
        "email": "raw-secret@example.com",
        "contactPhone": "+14155550123",
        "businessEmail": "business-secret@example.com",
        "bio": "Email n***@g*** or raw-bio@example.com; call +***0 / +1 415 555 0123",
        "rawPlatformData": {"profile": {"email": "nested-secret@example.com"}},
        "analysis": {
            "contacts": [
                {
                    "contactType": "email",
                    "displayValue": "d***@x***",
                    "value": "deep-secret@example.com",
                    "source_url": "https://example.com/contact",
                }
            ],
            "summary": "Creator listed s***@m*** in profile",
        },
        "contact_summary": {
            "status": "known",
            "known_contact_count": 2,
            "verified_contact_count": 1,
            "channel_types": ["email"],
            "email": "summary-secret@example.com",
            "value": "+14155559999",
            "nested": {"contact_value": "nested-summary@example.com"},
            "allowed_reveal_purposes": ["kol_detail_view", "forged-purpose"],
        },
    }

    result = contact_system.value_free_contact_projection(payload)

    assert result["contact_summary"] == {
        "status": "known",
        "known_contact_count": 2,
        "verified_contact_count": 1,
        "channel_types": ["email"],
        "allowed_reveal_purposes": ["kol_detail_view"],
        "last_verified_at": None,
    }
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "raw-secret@example.com",
        "+14155550123",
        "business-secret@example.com",
        "nested-secret@example.com",
        "deep-secret@example.com",
        "n***@g***",
        "d***@x***",
        "s***@m***",
        "+***0",
        "+1 415 555 0123",
        "summary-secret@example.com",
        "+14155559999",
        "nested-summary@example.com",
        "forged-purpose",
    ):
        assert forbidden not in serialized
    assert "***" not in serialized
    assert "[contact hidden]" in serialized


def test_external_processing_sanitizer_keeps_context_but_removes_all_contact_tokens() -> None:
    payload = {
        "raw_platform_data": {
            "profile": {
                "bio": (
                    "Camera creator. business@example.com / +1 415 555 0199 / "
                    "mailto:team@example.com / https://wa.me/14155550199 / "
                    "https://t.me/secret_creator / Discord contact: @private-discord / "
                    "Instagram DM: @private-ig / Discord: secret_user#1234"
                ),
                "website": "https://creator.example/portfolio",
                "contactEmail": "nested@example.com",
            }
        },
        "profile_text": "Reach n***@g*** or +***9 for partnerships",
        "handle": "@creator_identity",
        "profile_url": "https://youtube.com/@creator",
    }

    result = contact_system.sanitize_contact_values_for_external_processing(payload)

    assert result["raw_platform_data"]["profile"]["website"] == "https://creator.example/portfolio"
    assert result["profile_url"] == "https://youtube.com/@creator"
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "business@example.com",
        "+1 415 555 0199",
        "mailto:",
        "team@example.com",
        "wa.me",
        "t.me",
        "private-discord",
        "private-ig",
        "secret_user#1234",
        "14155550199",
        "nested@example.com",
        "n***@g***",
        "+***9",
    ):
        assert forbidden not in serialized
    assert "***" not in serialized
    assert "[contact removed]" in serialized
    assert result["handle"] == "@creator_identity"


def test_post_reveal_route_consumes_one_limit_and_forwards_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routers.vkpi_kol_pool_intel as routes

    limiter_calls: list[int] = []
    domain_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(routes, "release_validation_active", lambda: False)
    monkeypatch.setattr(routes, "_assert_private_kol_target", lambda *_a, **_kw: None)
    monkeypatch.setattr(routes, "legacy_system_admin_scope_guard", lambda *_a, **_kw: None)
    monkeypatch.setattr(routes, "check_kol_pool_employee_contact_permission", lambda _staff: True)
    monkeypatch.setattr(
        routes,
        "enforce_contact_read_rate_limit",
        lambda *_a, **_kw: limiter_calls.append(1),
    )

    def fake_view(kol_pool_id: int, **kwargs: Any) -> dict[str, Any]:
        domain_calls.append({"kol_pool_id": kol_pool_id, **kwargs})
        return {
            "status": "empty",
            "kol_pool_id": kol_pool_id,
            "contacts": [],
            "contact_masked": False,
            "reason": "no_verified_contacts",
        }

    monkeypatch.setattr(contact_reveal, "view_kol_contact", fake_view)
    result = routes.reveal_kol_contact(
        request=_request("POST"),
        response=Response(),
        kol_pool_id=1,
        body={"confirm": True, "purpose": "kol_detail_view"},
        staff={"staff_id": 7, "organization_id": 9},
    )

    assert result["status"] == "empty"
    assert len(limiter_calls) == 1
    assert len(domain_calls) == 1
    assert domain_calls[0]["purpose"] == "kol_detail_view"


def test_post_reveal_denies_contact_permission_before_limiter_or_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.routers.vkpi_kol_pool_intel as routes

    monkeypatch.setattr(routes, "release_validation_active", lambda: False)
    monkeypatch.setattr(routes, "_assert_private_kol_target", lambda *_a, **_kw: None)
    monkeypatch.setattr(routes, "legacy_system_admin_scope_guard", lambda *_a, **_kw: None)
    monkeypatch.setattr(routes, "check_kol_pool_employee_contact_permission", lambda _staff: False)
    monkeypatch.setattr(
        routes,
        "enforce_contact_read_rate_limit",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("unauthorized request must not consume limiter")
        ),
    )
    monkeypatch.setattr(
        contact_reveal,
        "view_kol_contact",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("unauthorized request must not reach reveal domain")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        routes.reveal_kol_contact(
            request=_request("POST"),
            response=Response(),
            kol_pool_id=1,
            body={"confirm": True, "purpose": "kol_detail_view"},
            staff={"staff_id": 7},
        )

    assert getattr(exc_info.value, "status_code", None) == 403


@pytest.mark.parametrize(
    ("profile_url", "candidates", "expected_status"),
    [
        ("", [], "needs_public_profile"),
        (
            "https://youtube.com/@creator",
            [
                {
                    "contact_type": "website",
                    "contact_value": "https://creator.example/contact",
                    "source_type": "raw_bio_scan",
                    "confidence": 0.45,
                    "evidence_text": "public link",
                }
            ],
            "needs_website",
        ),
        ("https://youtube.com/@creator", [], "needs_marketplace_or_dm"),
    ],
)
def test_l0_reconcile_classifies_manual_next_step_without_external_calls(
    monkeypatch: pytest.MonkeyPatch,
    profile_url: str,
    candidates: list[dict[str, Any]],
    expected_status: str,
) -> None:
    db = _queue_db(profile_url=profile_url)
    import app.domains.kol.business_contact_extract as extractor
    import app.domains.kol.contact_ingest as contact_ingest
    import app.domains.kol.contact_suppression as contact_suppression

    ingested: list[dict[str, Any]] = []
    monkeypatch.setattr(extractor, "extract_contacts_multi_source", lambda *_a, **_kw: candidates)
    monkeypatch.setattr(
        contact_ingest,
        "ingest_contact",
        lambda **kwargs: ingested.append(kwargs) or {"contact_id": 99},
    )
    monkeypatch.setattr(
        contact_system,
        "refresh_contactability",
        lambda *_a, **_kw: {"written": True, "score": 12.5},
    )
    monkeypatch.setattr(
        contact_suppression,
        "contact_eligibility",
        lambda **_kw: {"status": "restricted", "eligible": False, "reason": "verification_not_eligible"},
    )

    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    result = contact_acquisition_queue.reconcile_contact_acquisition(
        1,
        brand_scope="organization:9",
        conn=db,
    )

    assert result["status"] == expected_status
    assert result["provider_calls"] is False
    assert result["website_crawls"] is False
    assert result["messages_sent"] is False
    serialized = json.dumps(result, sort_keys=True)
    assert "creator.example" not in serialized
    assert "contact_value" not in serialized
    if candidates:
        assert len(ingested) == 1
        assert ingested[0]["verification_status"] == "observed"


def test_l0_reconcile_promotes_explicit_bio_proof_and_returns_counts_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _queue_db()
    db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
          (id, kol_pool_id, contact_type, contact_value, channel,
           normalized_value, verification_status, verified_at)
        VALUES (44, 1, 'email', 'stored-secret@example.com', 'email',
                'stored-secret@example.com',
                'verified_public_business', '2026-08-15T01:00:00Z')
        """
    )
    db.commit()
    import app.domains.kol.business_contact_extract as extractor
    import app.domains.kol.contact_ingest as contact_ingest
    import app.domains.kol.contact_suppression as contact_suppression

    candidate = {
        "contact_type": "email",
        "contact_value": "new-secret@example.com",
        "source_type": "raw_bio_scan",
        "source_field": "profile.bio",
        "confidence": 0.9,
        "evidence_text": "business inquiries: new-secret@example.com",
    }
    ingested: list[dict[str, Any]] = []
    eligibility_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(extractor, "extract_contacts_multi_source", lambda *_a, **_kw: [candidate])
    monkeypatch.setattr(
        contact_ingest,
        "ingest_contact",
        lambda **kwargs: ingested.append(kwargs) or {"contact_id": 55},
    )
    monkeypatch.setattr(
        contact_system,
        "refresh_contactability",
        lambda *_a, **_kw: {"written": True, "score": 77.0},
    )

    def eligible(**kwargs: Any) -> dict[str, Any]:
        eligibility_calls.append(kwargs)
        return {"status": "eligible", "eligible": True, "reason": "eligible_verified_public_business"}

    monkeypatch.setattr(contact_suppression, "contact_eligibility", eligible)
    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    result = contact_acquisition_queue.reconcile_contact_acquisition(
        1,
        brand_scope="organization:9",
        conn=db,
    )

    assert result["status"] == "ready"
    assert result["l0_candidate_count"] == 1
    assert result["ingested_count"] == 1
    assert result["eligible_contact_count"] == 1
    assert ingested[0]["source_type"] == "bio_explicit_contact"
    assert ingested[0]["is_public_declared"] is True
    assert ingested[0]["verification_status"] == "verified_public_business"
    assert eligibility_calls == [
        {
            "contact_id": 44,
            "kol_pool_id": 1,
            "brand_scope": "organization:9",
            "conn": db,
        }
    ]
    serialized = json.dumps(result, sort_keys=True)
    assert "stored-secret@example.com" not in serialized
    assert "new-secret@example.com" not in serialized


def test_raw_bio_confidence_without_bounded_identity_anchor_stays_observed() -> None:
    source, public, source_field = contact_acquisition_queue._candidate_source(  # noqa: SLF001
        {
            "contact_type": "email",
            "contact_value": "personal@example.com",
            "source_type": "raw_bio_scan",
            "source_field": "profile.bio",
            "confidence": 0.9,
            # Old extractor could award .9 because a business word occurred
            # elsewhere in the bio; this bounded snippet has no nearby anchor.
            "evidence_text": "personal@example.com photography and travel",
        },
        platform="youtube",
        source_url="https://youtube.com/@creator",
    )

    assert source == "raw_bio_scan"
    assert public is False
    assert source_field == "profile.bio"


def test_platform_email_source_is_field_level_and_youtube_never_becomes_ig_verified() -> None:
    from app.domains.kol.business_contact_extract import extract_contacts_multi_source

    youtube_candidates = extract_contacts_multi_source(
        {"profile": {"email": "youtube-secret@example.com"}},
        platform="youtube",
    )
    assert len(youtube_candidates) == 1
    youtube = youtube_candidates[0]
    assert youtube["source_type"] == "raw_bio_scan"
    assert youtube["source_field"] == "profile.email"
    source, public, source_field = contact_acquisition_queue._candidate_source(  # noqa: SLF001
        youtube,
        platform="youtube",
        source_url="https://youtube.com/@creator",
    )
    assert (source, public, source_field) == (
        "raw_bio_scan",
        False,
        "profile.email",
    )

    instagram_candidates = extract_contacts_multi_source(
        {"profile": {"businessEmail": "ig-secret@example.com"}},
        platform="instagram",
    )
    assert len(instagram_candidates) == 1
    instagram = instagram_candidates[0]
    assert instagram["source_type"] == "ig_business_profile"
    assert instagram["source_field"] == "profile.businessEmail"
    source, public, source_field = contact_acquisition_queue._candidate_source(  # noqa: SLF001
        instagram,
        platform="instagram",
        source_url="https://instagram.com/creator",
    )
    assert (source, public, source_field) == (
        "ig_business_profile",
        True,
        "profile.businessEmail",
    )
    forged_source, forged_public, _ = contact_acquisition_queue._candidate_source(  # noqa: SLF001
        instagram,
        platform="youtube",
        source_url="https://youtube.com/@creator",
    )
    assert forged_source == "raw_bio_scan"
    assert forged_public is False

    wrong_host_source, wrong_host_public, _ = contact_acquisition_queue._candidate_source(  # noqa: SLF001
        instagram,
        platform="instagram",
        source_url="https://youtube.com/@not-instagram",
    )
    assert wrong_host_source == "raw_bio_scan"
    assert wrong_host_public is False


def test_seed_and_pending_worker_use_server_owned_priority_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _queue_db(profile_url="")
    db.executemany(
        """
        INSERT INTO vkpi_kol_pool
          (id, platform, profile_url, bio, viltrox_fit_score, raw_platform_data)
        VALUES (?, 'youtube', ?, ?, ?, '{}')
        """,
        [
            (50, "https://youtube.com/@verified", "", 10),
            (40, "https://youtube.com/@high", "", 85),
            (30, "https://youtube.com/@video", "", None),
            (20, "", "public creator bio", 35),
        ],
    )
    db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
          (id, kol_pool_id, contact_type, contact_value, channel,
           normalized_value, verification_status, verified_at)
        VALUES (500, 50, 'email', 'priority-secret@example.com', 'email',
                'priority-secret@example.com',
                'verified_public_business', '2026-08-15T01:00:00Z')
        """
    )
    db.execute("INSERT INTO vkpi_kol_video_evidence VALUES (300, 30, 1)")
    db.commit()

    seeded = contact_acquisition_queue.seed_existing_contact_acquisition_queue(limit=3, conn=db)

    queued_ids = {
        int(row["kol_pool_id"])
        for row in db.execute("SELECT kol_pool_id FROM vkpi_kol_contact_acquisition_queue").fetchall()
    }
    assert queued_ids == {40, 30, 20}
    assert seeded["priority_tier_counts"] == {
        "tier_verified_existing": 0,
        "tier_a_high_fit_public_clue": 1,
        "tier_b_unscored_video_public_clue": 1,
        "tier_c_medium_fit_public_clue": 1,
        "tier_d_other": 0,
    }

    contact_acquisition_queue.enqueue_contact_acquisition(50, conn=db)
    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    processed: list[int] = []

    def fake_reconcile(kol_pool_id: int, **_kwargs: Any) -> dict[str, Any]:
        processed.append(kol_pool_id)
        return {"status": "ready"}

    monkeypatch.setattr(contact_acquisition_queue, "reconcile_contact_acquisition", fake_reconcile)
    result = contact_acquisition_queue.reconcile_pending_contact_acquisition(
        brand_scope="organization:9",
        limit=5,
        conn=db,
    )

    assert processed == [40, 30, 20, 1, 50]
    assert result["priority_tier_counts"] == {
        "tier_verified_existing": 1,
        "tier_a_high_fit_public_clue": 1,
        "tier_b_unscored_video_public_clue": 1,
        "tier_c_medium_fit_public_clue": 1,
        "tier_d_other": 1,
    }


def test_error_backoff_prevents_immediate_or_unbounded_retry() -> None:
    db = _queue_db()
    contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    contact_acquisition_queue._queue_update(  # noqa: SLF001
        db,
        kol_pool_id=1,
        status="error",
        reason_code="reconcile_failed",
        contactability_score=None,
    )
    first = dict(
        db.execute(
            "SELECT status, attempt_count, next_attempt_at FROM vkpi_kol_contact_acquisition_queue"
        ).fetchone()
    )
    assert first["status"] == "error"
    assert first["attempt_count"] == 1
    assert first["next_attempt_at"]

    immediate = contact_acquisition_queue.reconcile_pending_contact_acquisition(
        brand_scope="organization:9", limit=10, conn=db
    )
    assert immediate["processed"] == 0

    db.execute(
        """
        UPDATE vkpi_kol_contact_acquisition_queue
        SET attempt_count=?, next_attempt_at='2000-01-01T00:00:00Z'
        WHERE kol_pool_id=1
        """,
        (contact_acquisition_queue.MAX_ERROR_ATTEMPTS,),
    )
    db.commit()
    exhausted = contact_acquisition_queue.reconcile_pending_contact_acquisition(
        brand_scope="organization:9", limit=10, conn=db
    )
    assert exhausted["processed"] == 0

    rearmed = contact_acquisition_queue.enqueue_contact_acquisition(1, conn=db)
    assert rearmed["status"] == "pending_l0"
    assert rearmed["attempt_count"] == 0
