"""Two-tier KOL contact eligibility: verified vs observed (scan/declared) rows.

Scope (B 方案):
* ``observed`` rows from pipeline scans / platform declarations / manual entry
  become revealable at the ``observed`` tier after the suppression ledger is
  consulted; they are never promoted to ``verified``.
* ``verified_public_business`` keeps the strict evidence path and is tagged
  ``verified``.
* Suppressed, revoked/invalidated, unknown-source and unauthorized paths still
  fail closed, and no verdict or restricted payload carries contact plaintext.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi import Response

from app.api.routers import vkpi_kol_contact_projection as contact_projection
from app.api.routers import vkpi_kol_pool_intel
from app.core import permissions as permissions_domain
from app.domains.audit import service as audit_service
from app.domains.kol import contact_access, contact_reveal, contact_suppression
from app.domains.kol.contact_ingest import ingest_contact
from app.domains.kol.contact_suppression import (
    OBSERVED_ELIGIBLE_SOURCES,
    TIER_OBSERVED,
    TIER_VERIFIED,
    contact_eligibility,
    observed_source_eligible,
    record_suppression,
)

TEST_SECRET = b"contact-suppression-test-key-32b!"
SCOPE = "organization:1"
OBSERVED_EMAIL = "scan-only@example.com"
VERIFIED_EMAIL = "business@example.com"
LEGACY_HANDLE = "@legacy_creator"
ORG1_EMPLOYEE = {
    "id": 4,
    "active": 1,
    "role": "employee",
    "permissions": {"vkpi": "read"},
    "organization_id": 1,
    "organization_scope_status": "resolved",
}


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE staff (id INTEGER PRIMARY KEY);
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            contact_reveal_count INTEGER NOT NULL DEFAULT 0,
            contact_last_revealed_at TEXT,
            contact_last_revealed_by_staff_id INTEGER
        );
        INSERT INTO staff(id) VALUES (4), (7);
        INSERT INTO vkpi_kol_pool(id) VALUES (1), (2);

        CREATE TABLE vkpi_kol_pool_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
            contact_type TEXT NOT NULL,
            contact_value TEXT NOT NULL,
            contact_source TEXT NOT NULL,
            source_url TEXT DEFAULT '',
            consent_basis TEXT NOT NULL DEFAULT 'source_observation',
            is_public_declared INTEGER NOT NULL DEFAULT 0,
            extracted_by_staff_id INTEGER REFERENCES staff(id),
            apify_run_ref TEXT DEFAULT '',
            confidence REAL,
            evidence_text TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            normalized_value TEXT,
            channel TEXT,
            verification_status TEXT NOT NULL DEFAULT 'observed',
            verified_at TEXT,
            invalidated_at TEXT,
            revoked_at TEXT,
            UNIQUE(kol_pool_id, contact_type, contact_value),
            UNIQUE(kol_pool_id, channel, normalized_value)
        );

        CREATE TABLE vkpi_kol_contact_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES vkpi_kol_pool_contacts(id) ON DELETE CASCADE,
            kol_pool_id INTEGER NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            source_field TEXT NOT NULL DEFAULT '',
            evidence_fingerprint TEXT NOT NULL,
            confidence REAL,
            is_public_declared INTEGER NOT NULL DEFAULT 0,
            consent_basis TEXT NOT NULL DEFAULT 'source_observation',
            consent_at TEXT,
            provider_run_ref TEXT NOT NULL DEFAULT '',
            observed_by_staff_id INTEGER REFERENCES staff(id),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(contact_id, evidence_fingerprint)
        );

        CREATE TABLE vkpi_kol_contact_suppressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_scope TEXT NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            contact_fingerprint TEXT NOT NULL,
            fingerprint_key_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_type TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            suppressed_by_staff_id INTEGER REFERENCES staff(id),
            suppressed_at TEXT NOT NULL,
            released_by_staff_id INTEGER REFERENCES staff(id),
            released_at TEXT,
            last_event_at TEXT NOT NULL,
            UNIQUE(brand_scope, kol_pool_id, channel, contact_fingerprint)
        );
        """
    )
    return db


def _legacy_row(
    db: sqlite3.Connection,
    *,
    kol_pool_id: int = 1,
    contact_type: str = "email",
    contact_value: str = OBSERVED_EMAIL,
    contact_source: str = "raw_bio_scan",
    verification_status: str = "observed",
    revoked_at: str | None = None,
    invalidated_at: str | None = None,
) -> int:
    """Insert a pre-canonical row (NULL channel/normalized_value) like prod backfills."""

    cursor = db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
            (kol_pool_id, contact_type, contact_value, contact_source,
             verification_status, revoked_at, invalidated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            kol_pool_id,
            contact_type,
            contact_value,
            contact_source,
            verification_status,
            revoked_at,
            invalidated_at,
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def _verified_row(db: sqlite3.Connection) -> int:
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="business_email",
        contact_value=VERIFIED_EMAIL,
        source_type="youtube_about_declared",
        source_url="https://www.youtube.com/@creator/about",
        source_field="about.business_email",
        evidence_text="public business email label",
        confidence=0.95,
        is_public_declared=True,
        verification_status="verified_public_business",
        consent_basis="legitimate_interest_public_business",
        observed_at="2026-08-15T01:00:00Z",
        conn=db,
    )
    assert result["verification_status"] == "verified_public_business"
    return int(result["contact_id"])


def _verdict(db: sqlite3.Connection, contact_id: int, *, kol_pool_id: int = 1) -> dict[str, Any]:
    return contact_eligibility(
        contact_id=contact_id,
        kol_pool_id=kol_pool_id,
        brand_scope=SCOPE,
        conn=db,
        secret=TEST_SECRET,
    )


@pytest.mark.parametrize(
    "source",
    sorted(OBSERVED_ELIGIBLE_SOURCES) + ["manual_entry", "manual_verified", "MANUAL"],
)
def test_observed_scan_or_declared_source_is_eligible_at_observed_tier(source: str) -> None:
    db = _db()
    contact_id = _legacy_row(db, contact_source=source)

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is True
    assert verdict["status"] == "eligible"
    assert verdict["tier"] == TIER_OBSERVED
    assert verdict["reason"] == "eligible_observed_source"
    assert verdict["verification_status"] == "observed"
    assert verdict["channel"] == "email"
    assert OBSERVED_EMAIL not in repr(verdict)
    assert "scan-only" not in repr(verdict).lower()


@pytest.mark.parametrize("source", ["", "apify_guess", "llm_inferred", "crawler_unknown", "reply"])
def test_observed_row_from_unknown_source_stays_restricted(source: str) -> None:
    db = _db()
    contact_id = _legacy_row(db, contact_source=source)

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["status"] == "restricted"
    assert verdict["reason"] == "verification_not_eligible"
    assert "tier" not in verdict
    assert observed_source_eligible(source) is False


@pytest.mark.parametrize("status", ["stale", "invalid", "revoked"])
def test_non_observed_non_verified_status_stays_restricted(status: str) -> None:
    db = _db()
    contact_id = _legacy_row(db, verification_status=status)

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_not_eligible"


@pytest.mark.parametrize(
    ("revoked_at", "invalidated_at"),
    [("2026-08-20T00:00:00Z", None), (None, "2026-08-20T00:00:00Z")],
)
def test_revoked_or_invalidated_observed_row_is_restricted(
    revoked_at: str | None, invalidated_at: str | None
) -> None:
    db = _db()
    contact_id = _legacy_row(db, revoked_at=revoked_at, invalidated_at=invalidated_at)

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_state_incomplete"


def test_observed_row_canonicalizes_legacy_dm_handle_for_fingerprint_only() -> None:
    db = _db()
    contact_id = _legacy_row(
        db, contact_type="instagram_link", contact_value=LEGACY_HANDLE, contact_source="raw_full_scan"
    )

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is True
    assert verdict["tier"] == TIER_OBSERVED
    assert verdict["channel"] == "instagram_dm"
    assert "legacy_creator" not in repr(verdict)


def test_observed_row_with_unnormalizable_value_is_restricted() -> None:
    db = _db()
    contact_id = _legacy_row(db, contact_type="email", contact_value="not an email")

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_state_incomplete"


def test_observed_row_with_channel_drift_is_restricted() -> None:
    db = _db()
    contact_id = _legacy_row(db)
    db.execute(
        "UPDATE vkpi_kol_pool_contacts SET channel='phone', normalized_value=? WHERE id=?",
        (OBSERVED_EMAIL, contact_id),
    )
    db.commit()

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_state_incomplete"


def test_observed_row_identity_mismatch_is_restricted() -> None:
    db = _db()
    contact_id = _legacy_row(db, kol_pool_id=1)

    verdict = _verdict(db, contact_id, kol_pool_id=2)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "contact_identity_mismatch"


def test_suppressed_observed_row_is_still_restricted_and_release_restores() -> None:
    db = _db()
    contact_id = _legacy_row(db)
    assert _verdict(db, contact_id)["tier"] == TIER_OBSERVED

    record_suppression(
        kol_pool_id=1,
        contact_type="email",
        contact_value=OBSERVED_EMAIL,
        brand_scope=SCOPE,
        reason="unsubscribe",
        source_type="reply",
        conn=db,
        secret=TEST_SECRET,
    )
    blocked = _verdict(db, contact_id)
    assert blocked["eligible"] is False
    assert blocked["status"] == "restricted"
    assert blocked["reason"] == "suppressed"
    assert "tier" not in blocked

    other_scope = contact_eligibility(
        contact_id=contact_id,
        kol_pool_id=1,
        brand_scope="organization:2",
        conn=db,
        secret=TEST_SECRET,
    )
    assert other_scope["tier"] == TIER_OBSERVED, "suppression stays organization-scoped"

    contact_suppression.release_suppression(
        kol_pool_id=1,
        contact_type="email",
        contact_value=OBSERVED_EMAIL,
        brand_scope=SCOPE,
        staff_id=7,
        conn=db,
        secret=TEST_SECRET,
    )
    assert _verdict(db, contact_id)["tier"] == TIER_OBSERVED


def test_observed_tier_without_fingerprint_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(contact_suppression.SUPPRESSION_HMAC_ENV, raising=False)
    db = _db()
    contact_id = _legacy_row(db)

    verdict = contact_eligibility(contact_id=contact_id, kol_pool_id=1, brand_scope=SCOPE, conn=db)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "fingerprint_key_unavailable"


def test_observed_tier_with_missing_ledger_table_fails_closed() -> None:
    db = _db()
    contact_id = _legacy_row(db)
    db.execute("DROP TABLE vkpi_kol_contact_suppressions")
    db.commit()

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "suppression_check_unavailable"


def test_verified_row_is_tagged_verified_and_never_downgraded() -> None:
    db = _db()
    contact_id = _verified_row(db)

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is True
    assert verdict["tier"] == TIER_VERIFIED
    assert verdict["reason"] == "eligible_verified_public_business"
    assert verdict["verification_status"] == "verified_public_business"
    assert VERIFIED_EMAIL.lower() not in repr(verdict).lower()


def test_verified_row_without_evidence_does_not_fall_back_to_observed_tier() -> None:
    db = _db()
    contact_id = _verified_row(db)
    db.execute("DELETE FROM vkpi_kol_contact_evidence WHERE contact_id=?", (contact_id,))
    db.commit()

    verdict = _verdict(db, contact_id)

    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_evidence_missing"
    assert "tier" not in verdict


def test_verdict_shape_is_pii_free_and_carries_tier() -> None:
    db = _db()
    observed_id = _legacy_row(db)
    verified_id = _verified_row(db)

    for contact_id in (observed_id, verified_id):
        verdict = _verdict(db, contact_id)
        assert set(verdict) == {
            "status",
            "eligible",
            "tier",
            "reason",
            "contact_id",
            "kol_pool_id",
            "channel",
            "verification_status",
        }
        assert "example.com" not in repr(verdict)


# ---------------------------------------------------------------------------
# Reveal boundary
# ---------------------------------------------------------------------------


def _reveal(
    monkeypatch: pytest.MonkeyPatch,
    db: sqlite3.Connection,
    *,
    staff: dict[str, Any] | None = None,
    authorized: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    audits: list[dict[str, Any]] = []

    def fake_authorize(_staff: Any, **kwargs: Any) -> bool:
        audits.append(kwargs)
        return True

    monkeypatch.setenv(contact_suppression.SUPPRESSION_HMAC_ENV, TEST_SECRET.decode())
    monkeypatch.setattr(contact_reveal, "get_conn", lambda: db)
    monkeypatch.setattr(contact_reveal, "_ensure_contact_audit_schema", lambda: None)
    monkeypatch.setattr(
        permissions_domain, "check_kol_pool_employee_contact_permission", lambda *_a, **_kw: authorized
    )
    monkeypatch.setattr(contact_access, "authorize_plaintext_contacts", fake_authorize)
    result = contact_reveal.view_kol_contact(
        1,
        confirm=True,
        staff=staff if staff is not None else ORG1_EMPLOYEE,
        purpose="compose_outreach",
    )
    return result, audits


def test_reveal_returns_full_with_observed_tier_and_no_internal_labels(monkeypatch) -> None:
    db = _db()
    contact_id = _legacy_row(db)

    result, audits = _reveal(monkeypatch, db)

    assert result["status"] == "full"
    assert result["contact_masked"] is False
    assert result["reason"] == "eligible_contacts_available"
    assert result["verified_count"] == 0
    assert result["observed_count"] == 1
    assert result["contacts"] == [
        {
            "id": contact_id,
            "channel": "email",
            "contact_type": "email",
            "value": OBSERVED_EMAIL,
            "tier": "observed",
            "verification_status": "observed",
            "source_type": "raw_bio_scan",
        }
    ]
    assert "verified_at" not in result["contacts"][0]
    assert len(audits) == 1
    assert audits[0]["metadata"]["purpose"] == "compose_outreach"
    revealed = db.execute(
        "SELECT contact_reveal_count, contact_last_revealed_by_staff_id FROM vkpi_kol_pool WHERE id=1"
    ).fetchone()
    assert int(revealed["contact_reveal_count"]) == 1
    assert int(revealed["contact_last_revealed_by_staff_id"]) == 4


def test_reveal_lists_verified_before_observed_and_counts_each_tier(monkeypatch) -> None:
    db = _db()
    observed_id = _legacy_row(db)
    verified_id = _verified_row(db)
    assert observed_id < verified_id, "insertion order must not decide listing order"

    result, _ = _reveal(monkeypatch, db)

    assert result["status"] == "full"
    assert [entry["tier"] for entry in result["contacts"]] == ["verified", "observed"]
    assert [entry["id"] for entry in result["contacts"]] == [verified_id, observed_id]
    assert result["contacts"][0]["verification_status"] == "verified_public_business"
    assert result["contacts"][0]["verified_at"]
    assert result["verified_count"] == 1
    assert result["observed_count"] == 1


def test_reveal_suppressed_observed_only_is_restricted_without_plaintext(monkeypatch) -> None:
    db = _db()
    _legacy_row(db)
    record_suppression(
        kol_pool_id=1,
        contact_type="email",
        contact_value=OBSERVED_EMAIL,
        brand_scope=SCOPE,
        reason="unsubscribe",
        source_type="reply",
        conn=db,
        secret=TEST_SECRET,
    )

    result, audits = _reveal(monkeypatch, db)

    assert result["status"] == "restricted"
    assert result["reason"] == "suppressed"
    assert result["contacts"] == []
    assert result["contact_masked"] is True
    assert OBSERVED_EMAIL not in repr(result)
    assert audits == []


def test_reveal_unknown_source_only_is_restricted_as_verification_required(monkeypatch) -> None:
    db = _db()
    _legacy_row(db, contact_source="llm_inferred")

    result, audits = _reveal(monkeypatch, db)

    assert result["status"] == "restricted"
    assert result["reason"] == "verification_required"
    assert OBSERVED_EMAIL not in repr(result)
    assert audits == []


def test_reveal_unauthorized_staff_gets_no_observed_tier_plaintext(monkeypatch) -> None:
    db = _db()
    _legacy_row(db)

    result, audits = _reveal(monkeypatch, db, authorized=False)

    assert result["status"] == "restricted"
    assert result["reason"] == "contact_reveal_not_authorized"
    assert result["contacts"] == []
    assert OBSERVED_EMAIL not in repr(result)
    assert "tier" not in repr(result)
    assert audits == []


def test_reveal_without_confirm_or_purpose_never_evaluates_observed_tier(monkeypatch) -> None:
    db = _db()
    _legacy_row(db)
    monkeypatch.setattr(contact_reveal, "get_conn", lambda: pytest.fail("no store access"))

    unconfirmed = contact_reveal.view_kol_contact(1, confirm=False, staff=ORG1_EMPLOYEE, purpose="compose_outreach")
    bad_purpose = contact_reveal.view_kol_contact(1, confirm=True, staff=ORG1_EMPLOYEE, purpose="export")

    assert unconfirmed["reason"] == "confirmation_required"
    assert bad_purpose["reason"] == "purpose_not_allowed"
    assert OBSERVED_EMAIL not in repr((unconfirmed, bad_purpose))


def test_reveal_tier_fallback_when_verdict_has_no_tier(monkeypatch) -> None:
    """Rolling upgrade: an older eligibility payload without ``tier`` maps by status."""

    db = _db()
    _legacy_row(db)
    monkeypatch.setattr(
        contact_suppression,
        "contact_eligibility",
        lambda **_kw: {"status": "eligible", "eligible": True, "channel": "email", "verification_status": "observed"},
    )

    result, _ = _reveal(monkeypatch, db)

    assert result["status"] == "full"
    assert result["contacts"][0]["tier"] == "observed"
    assert result["contacts"][0]["verification_status"] == "observed"


class _RevealRequest:
    client = type("Client", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "kol-contact-tier-test"}

    def __init__(self) -> None:
        self.state = type("State", (), {})()


def test_reveal_route_passes_tier_through_the_typed_boundary(monkeypatch) -> None:
    db = _db()
    contact_id = _legacy_row(db)
    audit_calls: list[dict[str, Any]] = []

    monkeypatch.setenv(contact_suppression.SUPPRESSION_HMAC_ENV, TEST_SECRET.decode())
    monkeypatch.setattr(vkpi_kol_pool_intel, "release_validation_active", lambda: False)
    monkeypatch.setattr(vkpi_kol_pool_intel, "_assert_private_kol_target", lambda *_a, **_kw: None)
    monkeypatch.setattr(contact_projection, "check_rate_limit", lambda *_a, **_kw: (True, 29))
    monkeypatch.setattr(contact_reveal, "get_conn", lambda: db)
    monkeypatch.setattr(contact_reveal, "_ensure_contact_audit_schema", lambda: None)
    monkeypatch.setattr(
        audit_service,
        "log_sensitive_access",
        lambda **kwargs: audit_calls.append(kwargs) or {"id": 91, "status": "logged"},
    )

    response = Response()
    result = vkpi_kol_pool_intel.reveal_kol_contact(
        _RevealRequest(),
        response,
        1,
        body={"confirm": True, "purpose": "kol_detail_view"},
        staff={**ORG1_EMPLOYEE, "organization_id": 1},
    )

    assert result["status"] == "full"
    assert result["contacts"][0]["id"] == contact_id
    assert result["contacts"][0]["tier"] == "observed"
    assert result["contacts"][0]["value"] == OBSERVED_EMAIL
    assert len(audit_calls) == 1
    assert response.headers["cache-control"] == "private, no-store"
