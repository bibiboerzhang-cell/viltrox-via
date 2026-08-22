from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path

import pytest

from app.db import connection
from app.domains.kol.contact_ingest import (
    ContactValidationError,
    ingest_contact,
    normalize_contact,
    set_contact_verification_status,
)
from app.domains.kol.contact_suppression import (
    SUPPRESSION_HMAC_ENV,
    SuppressionConfigurationError,
    contact_eligibility,
    contact_fingerprint,
    is_contact_suppressed,
    record_suppression,
    release_suppression,
)


ROOT = Path(__file__).resolve().parents[1]
FORWARD_MIGRATION = ROOT / "migrations/282_vkpi_kol_contact_acquisition_safety.sql"
DOWN_MIGRATION = ROOT / "migrations/282_vkpi_kol_contact_acquisition_safety_down.sql"
TEST_SECRET = b"contact-suppression-test-key-32b!"


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE staff (id INTEGER PRIMARY KEY);
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY);
        INSERT INTO staff(id) VALUES (7), (8);
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


def _verified_email(db: sqlite3.Connection, *, value: str = "Business@Example.com") -> dict:
    return ingest_contact(
        kol_pool_id=1,
        contact_type="business_email",
        contact_value=value,
        source_type="youtube_about_declared",
        source_url="https://www.youtube.com/@creator/about?tracking=secret#business",
        source_field="about.business_email",
        evidence_text="public business email label",
        confidence=0.95,
        is_public_declared=True,
        verification_status="verified_public_business",
        consent_basis="legitimate_interest_public_business",
        observed_at="2026-08-15T01:00:00Z",
        conn=db,
    )


def test_migration_is_additive_runner_owned_and_contains_safety_contract() -> None:
    sequence = connection._discover_postgres_migrations()
    # The contact safety migration remains ordered after the LLM precision
    # migration. Later additive migrations are allowed and must not make this
    # contract test stale merely because the repository advanced.
    assert FORWARD_MIGRATION.name in sequence
    assert sequence.index("275_vkpi_llm_cost_precision.sql") < sequence.index(FORWARD_MIGRATION.name)
    sql = FORWARD_MIGRATION.read_text(encoding="utf-8")
    upper = sql.upper()
    assert "BEGIN;" not in upper
    assert "COMMIT;" not in upper
    assert "CREATE TABLE IF NOT EXISTS VKPI_KOL_CONTACT_EVIDENCE" in upper
    assert "CREATE TABLE IF NOT EXISTS VKPI_KOL_CONTACT_SUPPRESSIONS" in upper
    assert "CREATE TABLE IF NOT EXISTS VKPI_KOL_CONTACT_ACQUISITION_QUEUE" in upper
    for column in (
        "normalized_value",
        "channel",
        "verification_status",
        "verified_at",
        "invalidated_at",
        "revoked_at",
        "consent_basis",
        "consent_at",
    ):
        assert column in sql
    for status in (
        "observed",
        "verified_public_business",
        "stale",
        "invalid",
        "revoked",
    ):
        assert status in sql
    assert "raw_full_scan" in sql
    assert "chk_vkpi_kol_contact_evidence_manual_actor" in sql
    assert "contact_fingerprint CHAR(64)" in sql
    assert "fingerprint_key_id CHAR(16)" in sql
    assert "payload" not in sql.lower()
    assert "provider execution is owned elsewhere" in sql
    assert DOWN_MIGRATION.exists()


@pytest.mark.parametrize(
    ("kind", "value", "channel", "normalized"),
    [
        ("business_email", "Team@Example.COM", "email", "team@example.com"),
        ("phone", "+1 (415) 555-2671", "phone", "+14155552671"),
        ("whatsapp", "https://wa.me/14155552671?text=hello", "whatsapp", "+14155552671"),
        ("instagram_dm", "https://www.instagram.com/Creator.Name/?hl=en", "instagram_dm", "@creator.name"),
        ("tiktok_dm", "https://www.tiktok.com/@CameraLab", "tiktok_dm", "@cameralab"),
        ("website", "HTTPS://WWW.Example.com:443/contact?email=x#top", "website", "https://example.com/contact"),
    ],
)
def test_normalize_contact_supported_channels(
    kind: str, value: str, channel: str, normalized: str
) -> None:
    result = normalize_contact(kind, value)
    assert result.channel == channel
    assert result.normalized_value == normalized


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("email", "Name <team@example.com>"),
        ("email", ".team@example.com"),
        ("email", "team..ops@example.com"),
        ("phone", "415-555-2671"),
        ("whatsapp", "http://wa.me/14155552671"),
        ("instagram_dm", "https://evil.example/creator"),
        ("website", "https://user:pass@example.com/private"),
        ("website", "http://127.0.0.1/internal"),
        ("website", "http://0177.0.0.1/internal"),
        ("website", "https://bad_host.example/path"),
        ("website", "https://example.com/%0aheader"),
        ("unknown", "anything"),
    ],
)
def test_normalize_contact_rejects_ambiguous_or_unsafe_values(kind: str, value: str) -> None:
    with pytest.raises(ContactValidationError):
        normalize_contact(kind, value)


def test_verified_ingest_canonicalizes_source_url_and_does_not_store_evidence_text() -> None:
    db = _db()
    result = _verified_email(db)

    assert result == {
        "contact_id": 1,
        "kol_pool_id": 1,
        "channel": "email",
        "verification_status": "verified_public_business",
        "inserted": True,
        "evidence_id": 1,
        "promoted": False,
    }
    contact = dict(db.execute("SELECT * FROM vkpi_kol_pool_contacts").fetchone())
    evidence = dict(db.execute("SELECT * FROM vkpi_kol_contact_evidence").fetchone())
    assert contact["contact_value"] == "business@example.com"
    assert contact["normalized_value"] == "business@example.com"
    assert contact["verified_at"] == "2026-08-15T01:00:00Z"
    assert contact["source_url"] == "https://youtube.com/@creator/about"
    assert evidence["source_url"] == "https://youtube.com/@creator/about"
    assert evidence["consent_basis"] == "legitimate_interest_public_business"
    assert evidence["consent_at"] is None
    assert len(evidence["evidence_fingerprint"]) == 64
    assert "public business email label" not in repr(evidence)
    assert "tracking" not in repr(evidence)
    assert "secret" not in repr(evidence)


@pytest.mark.parametrize(
    "source_url",
    [
        "https://user:password@youtube.com/@creator/about",
        "http://127.0.0.1/profile",
        "https://bad_host.example/profile",
    ],
)
def test_ingest_rejects_unsafe_source_url_before_any_write(source_url: str) -> None:
    db = _db()
    with pytest.raises(ContactValidationError):
        ingest_contact(
            kol_pool_id=1,
            contact_type="email",
            contact_value="business@example.com",
            source_type="youtube_about_declared",
            source_url=source_url,
            source_field="about.business_email",
            confidence=0.95,
            is_public_declared=True,
            verification_status="verified_public_business",
            conn=db,
        )
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_pool_contacts").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_contact_evidence").fetchone()[0] == 0


def test_unpersisted_evidence_text_alone_cannot_grant_verified_status() -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type="youtube_about_declared",
        evidence_text="business contact shown publicly",
        confidence=0.95,
        is_public_declared=True,
        verification_status="verified_public_business",
        conn=db,
    )
    assert result["verification_status"] == "observed"


@pytest.mark.parametrize(
    ("source_type", "source_url", "source_field", "provider_run_ref", "staff_id"),
    [
        ("manual", "https://creator.example/contact", "contact.email", "", 7),
        ("manual_verified_public_business", "", "contact.email", "", 7),
        ("youtube_about_declared", "https://youtube.com/@creator/about", "", "run-1", None),
        ("youtube_about_declared", "", "about.business_email", "run-1", None),
    ],
)
def test_public_verification_rejects_manual_or_incomplete_field_proof(
    source_type: str,
    source_url: str,
    source_field: str,
    provider_run_ref: str,
    staff_id: int | None,
) -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type=source_type,
        source_url=source_url,
        source_field=source_field,
        evidence_text="free text is not proof",
        provider_run_ref=provider_run_ref,
        confidence=0.99,
        is_public_declared=True,
        verification_status="verified_public_business",
        staff_id=staff_id,
        conn=db,
    )
    assert result["verification_status"] == "observed"


def test_reviewed_manual_public_business_source_requires_actor_url_and_field() -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type="manual_verified_public_business",
        source_url="https://creator.example/contact?tracking=removed",
        source_field="contact.email",
        confidence=0.95,
        is_public_declared=True,
        verification_status="verified_public_business",
        staff_id=7,
        conn=db,
    )
    assert result["verification_status"] == "verified_public_business"


def test_consent_evidence_is_recorded_but_not_actionable_without_public_proof() -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="instagram_dm",
        contact_value="@creator",
        source_type="manual",
        source_field="dm.consent",
        confidence=1.0,
        is_public_declared=False,
        verification_status="verified_public_business",
        consent_basis="platform_messaging_consent",
        consent_at="2026-08-15T09:00:00Z",
        staff_id=7,
        conn=db,
    )
    evidence = db.execute(
        "SELECT consent_basis, consent_at FROM vkpi_kol_contact_evidence WHERE id=?",
        (result["evidence_id"],),
    ).fetchone()
    assert result["verification_status"] == "observed"
    assert evidence["consent_basis"] == "platform_messaging_consent"
    assert evidence["consent_at"] == "2026-08-15T09:00:00Z"
    # Consent evidence never promotes the row to verified; a manual observation
    # is disclosable only at the observed tier (B 方案).
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert verdict["eligible"] is True
    assert verdict["tier"] == "observed"
    assert verdict["verification_status"] == "observed"
    assert verdict["reason"] == "eligible_observed_source"


def test_duplicate_discovery_updates_last_seen_and_preserves_multiple_sources() -> None:
    db = _db()
    first = _verified_email(db)
    duplicate = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type="website_declared",
        source_url="https://creator.example/contact?utm_source=private",
        source_field="contact.email",
        evidence_text="same public address on creator website",
        confidence=0.9,
        is_public_declared=True,
        verification_status="verified_public_business",
        consent_basis="source_observation",
        observed_at="2026-08-15T02:00:00Z",
        conn=db,
    )
    older_repeat = ingest_contact(
        kol_pool_id=1,
        contact_type="business_email",
        contact_value="BUSINESS@EXAMPLE.COM",
        source_type="website_declared",
        source_url="https://creator.example/contact?different=tracking",
        source_field="contact.email",
        evidence_text="same public address on creator website",
        confidence=0.88,
        is_public_declared=True,
        observed_at="2026-08-15T00:30:00Z",
        conn=db,
    )

    assert duplicate["contact_id"] == first["contact_id"] == older_repeat["contact_id"]
    assert duplicate["inserted"] is False
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_pool_contacts").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_contact_evidence").fetchone()[0] == 2
    contact = db.execute(
        "SELECT last_seen_at, verification_status FROM vkpi_kol_pool_contacts"
    ).fetchone()
    assert contact["last_seen_at"] == "2026-08-15T02:00:00Z"
    assert contact["verification_status"] == "verified_public_business"
    website_evidence = db.execute(
        "SELECT source_url, last_seen_at, confidence FROM vkpi_kol_contact_evidence WHERE source_type='website_declared'"
    ).fetchone()
    assert website_evidence["source_url"] == "https://creator.example/contact"
    assert website_evidence["last_seen_at"] == "2026-08-15T02:00:00Z"
    assert website_evidence["confidence"] == pytest.approx(0.9)


def test_raw_full_scan_never_becomes_verified_but_is_observed_tier_eligible() -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="raw@example.com",
        source_type="raw_full_scan",
        source_field="raw_platform_data",
        evidence_text="unstructured raw payload match",
        confidence=1.0,
        is_public_declared=True,
        verification_status="verified_public_business",
        observed_at="2026-08-15T03:00:00Z",
        conn=db,
    )
    assert result["verification_status"] == "observed"
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    # Disclosable at the observed tier only; the verified tier stays closed.
    assert verdict["eligible"] is True
    assert verdict["tier"] == "observed"
    assert verdict["reason"] == "eligible_observed_source"
    assert verdict["verification_status"] == "observed"
    assert "raw@example.com" not in repr(verdict)


def test_explicit_consent_is_per_evidence_and_requires_timestamp() -> None:
    db = _db()
    with pytest.raises(ContactValidationError, match="consent timestamp"):
        ingest_contact(
            kol_pool_id=1,
            contact_type="instagram_dm",
            contact_value="@creator",
            source_type="bio_explicit_contact",
            source_field="bio.instagram",
            confidence=0.9,
            is_public_declared=True,
            consent_basis="platform_messaging_consent",
            conn=db,
        )
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="instagram_dm",
        contact_value="@creator",
        source_type="bio_explicit_contact",
        source_field="bio.instagram",
        confidence=0.9,
        is_public_declared=True,
        consent_basis="platform_messaging_consent",
        consent_at="2026-08-14T09:30:00+08:00",
        actor_staff_id=7,
        conn=db,
    )
    evidence = db.execute(
        "SELECT consent_basis, consent_at, observed_by_staff_id FROM vkpi_kol_contact_evidence WHERE id=?",
        (result["evidence_id"],),
    ).fetchone()
    assert evidence["consent_basis"] == "platform_messaging_consent"
    assert evidence["consent_at"] == "2026-08-14T01:30:00Z"
    assert evidence["observed_by_staff_id"] == 7


@pytest.mark.parametrize("source_type", ["manual", "manual_verified_public_business"])
def test_manual_observation_requires_a_staff_actor_even_when_not_verified(
    source_type: str,
) -> None:
    db = _db()
    with pytest.raises(ContactValidationError, match="staff actor"):
        ingest_contact(
            kol_pool_id=1,
            contact_type="email",
            contact_value="manual@example.com",
            source_type=source_type,
            source_field="operator.entry",
            confidence=0.5,
            is_public_declared=False,
            verification_status="observed",
            consent_basis="manual_entry",
            conn=db,
        )
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_pool_contacts").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM vkpi_kol_contact_evidence").fetchone()[0] == 0


def test_revoked_contact_cannot_be_repromoted_by_old_or_new_public_evidence() -> None:
    db = _db()
    result = _verified_email(db)
    set_contact_verification_status(
        result["contact_id"],
        "revoked",
        staff_id=7,
        changed_at="2026-08-15T04:00:00Z",
        conn=db,
    )
    repeated = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type="ig_business_profile",
        source_url="https://instagram.com/creator?contact=business",
        source_field="profile.business_email",
        evidence_text="new public observation after revocation",
        confidence=0.99,
        is_public_declared=True,
        verification_status="verified_public_business",
        observed_at="2026-08-15T05:00:00Z",
        conn=db,
    )
    assert repeated["verification_status"] == "revoked"
    assert repeated["promoted"] is False
    row = db.execute(
        "SELECT verification_status, revoked_at FROM vkpi_kol_pool_contacts WHERE id=?",
        (result["contact_id"],),
    ).fetchone()
    assert row["verification_status"] == "revoked"
    assert row["revoked_at"] == "2026-08-15T04:00:00Z"
    with pytest.raises(ContactValidationError, match="cannot be reopened"):
        set_contact_verification_status(
            result["contact_id"], "verified_public_business", conn=db
        )


def test_verified_state_requires_qualifying_public_evidence() -> None:
    db = _db()
    observed = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="weak@example.com",
        source_type="raw_bio_scan",
        source_field="bio",
        confidence=0.6,
        is_public_declared=False,
        conn=db,
    )
    with pytest.raises(ContactValidationError, match="requires public business evidence"):
        set_contact_verification_status(
            observed["contact_id"], "verified_public_business", conn=db
        )


@pytest.mark.parametrize(
    ("source_type", "source_url", "source_field", "evidence_text"),
    [
        (
            "ig_business_profile",
            "https://youtube.com/@forged",
            "profile.business_email",
            "business email forged@example.com",
        ),
        (
            "ig_business_profile",
            "https://instagram.com/creator",
            "profile.email",
            "business email forged@example.com",
        ),
        (
            "youtube_about_declared",
            "https://youtube.com/@creator/about",
            "profile.email",
            "business email forged@example.com",
        ),
        (
            "website_declared",
            "https://creator.example/contact",
            "contact.email",
            "business email forged@example.com",
        ),
        (
            "bio_explicit_contact",
            "https://youtube.com/@creator",
            "profile.bio",
            "photography by forged@example.com",
        ),
        (
            "bio_explicit_contact",
            "https://creator.example/about",
            "profile.bio",
            "business inquiries: forged@example.com",
        ),
    ],
)
def test_direct_ingest_forged_public_source_matrix_stays_observed(
    source_type: str,
    source_url: str,
    source_field: str,
    evidence_text: str,
) -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="forged@example.com",
        source_type=source_type,
        source_url=source_url,
        source_field=source_field,
        evidence_text=evidence_text,
        confidence=0.99,
        is_public_declared=True,
        verification_status="verified_public_business",
        consent_basis="legitimate_interest_public_business",
        conn=db,
    )

    assert result["verification_status"] == "observed"
    assert result["promoted"] is False


def test_direct_ingest_bounded_bio_identity_on_supported_profile_can_verify() -> None:
    db = _db()
    result = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        source_type="bio_explicit_contact",
        source_url="https://youtube.com/@creator",
        source_field="profile.bio",
        evidence_text="business inquiries: business@example.com",
        confidence=0.9,
        is_public_declared=True,
        verification_status="verified_public_business",
        consent_basis="legitimate_interest_public_business",
        conn=db,
    )

    assert result["verification_status"] == "verified_public_business"
    assert result["inserted"] is True


def test_explicit_verifier_rejects_website_without_creator_identity_proof() -> None:
    db = _db()
    raw = ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="later@example.com",
        source_type="raw_full_scan",
        source_field="raw_platform_data",
        confidence=0.45,
        is_public_declared=False,
        conn=db,
    )
    ingest_contact(
        kol_pool_id=1,
        contact_type="email",
        contact_value="later@example.com",
        source_type="website_declared",
        source_url="https://creator.example/contact?tracking=removed",
        source_field="contact.email",
        confidence=0.91,
        is_public_declared=True,
        verification_status="observed",
        conn=db,
    )
    with pytest.raises(ContactValidationError, match="requires public business evidence"):
        set_contact_verification_status(
            raw["contact_id"],
            "verified_public_business",
            changed_at="2026-08-15T08:00:00Z",
            conn=db,
        )
    row = db.execute(
        "SELECT contact_source, verification_status, verified_at FROM vkpi_kol_pool_contacts WHERE id=?",
        (raw["contact_id"],),
    ).fetchone()
    assert row["verification_status"] == "observed"
    assert row["verified_at"] is None


def test_manual_evidence_without_actor_cannot_back_eligibility() -> None:
    db = _db()
    result = _verified_email(db)
    db.execute(
        "UPDATE vkpi_kol_contact_evidence SET source_type='manual', observed_by_staff_id=NULL"
    )
    db.commit()
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert verdict["eligible"] is False
    assert verdict["reason"] == "verification_evidence_missing"


def test_fingerprint_is_keyed_scope_bound_and_not_plain_sha256() -> None:
    normalized = "business@example.com"
    one = contact_fingerprint(
        brand_scope="organization:1",
        kol_pool_id=1,
        channel="email",
        normalized_value=normalized,
        secret=TEST_SECRET,
    )
    two = contact_fingerprint(
        brand_scope="organization:2",
        kol_pool_id=1,
        channel="email",
        normalized_value=normalized,
        secret=TEST_SECRET,
    )
    expected_payload = b"v1\x1forganization:1\x1f1\x1femail\x1fbusiness@example.com"
    assert one == hmac.new(TEST_SECRET, expected_payload, hashlib.sha256).hexdigest()
    assert one != hashlib.sha256(normalized.encode()).hexdigest()
    assert one != two
    assert normalized not in one


def test_missing_or_short_hmac_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    result = _verified_email(db)
    monkeypatch.delenv(SUPPRESSION_HMAC_ENV, raising=False)

    with pytest.raises(SuppressionConfigurationError):
        record_suppression(
            kol_pool_id=1,
            contact_type="email",
            contact_value="business@example.com",
            brand_scope="organization:1",
            reason="unsubscribe",
            source_type="reply",
            conn=db,
        )
    direct = is_contact_suppressed(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        brand_scope="organization:1",
        conn=db,
    )
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
    )
    assert direct == {
        "suppressed": True,
        "fail_closed": True,
        "reason": "suppression_check_unavailable",
    }
    assert verdict["eligible"] is False
    assert verdict["reason"] == "fingerprint_key_unavailable"


def test_durable_suppression_blocks_then_staff_release_allows_eligibility() -> None:
    db = _db()
    result = _verified_email(db)
    before = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert before["eligible"] is True

    recorded = record_suppression(
        kol_pool_id=1,
        contact_type="business_email",
        contact_value="BUSINESS@EXAMPLE.COM",
        brand_scope="organization:1",
        reason="manual_block",
        source_type="manual",
        staff_id=7,
        event_at="2026-08-15T06:00:00Z",
        conn=db,
        secret=TEST_SECRET,
    )
    stored = dict(db.execute("SELECT * FROM vkpi_kol_contact_suppressions").fetchone())
    assert recorded["status"] == "suppressed"
    assert stored["contact_fingerprint"] != "business@example.com"
    assert len(stored["fingerprint_key_id"]) == 16
    assert "business@example.com" not in repr(stored)
    blocked = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert blocked["eligible"] is False
    assert blocked["reason"] == "suppressed"
    assert "business@example.com" not in repr(blocked)

    released = release_suppression(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        brand_scope="organization:1",
        staff_id=8,
        event_at="2026-08-15T07:00:00Z",
        conn=db,
        secret=TEST_SECRET,
    )
    after = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert released["released"] is True
    assert after["eligible"] is True
    audit = db.execute(
        "SELECT is_active, suppressed_by_staff_id, released_by_staff_id, released_at FROM vkpi_kol_contact_suppressions"
    ).fetchone()
    assert audit["is_active"] == 0
    assert audit["suppressed_by_staff_id"] == 7
    assert audit["released_by_staff_id"] == 8
    assert audit["released_at"] == "2026-08-15T07:00:00Z"


def test_hmac_rotation_mismatch_cannot_silently_unsuppress() -> None:
    db = _db()
    result = _verified_email(db)
    record_suppression(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        brand_scope="organization:1",
        reason="unsubscribe",
        source_type="reply",
        conn=db,
        secret=TEST_SECRET,
    )
    rotated = b"rotated-contact-suppression-key!"
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=rotated,
    )
    direct = is_contact_suppressed(
        kol_pool_id=1,
        contact_type="email",
        contact_value="business@example.com",
        brand_scope="organization:1",
        conn=db,
        secret=rotated,
    )
    assert verdict["eligible"] is False
    assert verdict["reason"] == "suppression_check_unavailable"
    assert direct == {
        "suppressed": True,
        "fail_closed": True,
        "reason": "suppression_check_unavailable",
    }
    with pytest.raises(SuppressionConfigurationError, match="key mismatch"):
        release_suppression(
            kol_pool_id=1,
            contact_type="email",
            contact_value="business@example.com",
            brand_scope="organization:1",
            staff_id=8,
            conn=db,
            secret=rotated,
        )


def test_missing_suppression_table_is_restrictive() -> None:
    db = _db()
    result = _verified_email(db)
    db.execute("DROP TABLE vkpi_kol_contact_suppressions")
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert verdict["eligible"] is False
    assert verdict["reason"] == "suppression_check_unavailable"


def test_manual_suppression_requires_actor_and_scope_is_explicit() -> None:
    db = _db()
    with pytest.raises(ContactValidationError, match="staff id"):
        record_suppression(
            kol_pool_id=1,
            contact_type="email",
            contact_value="business@example.com",
            brand_scope="organization:1",
            reason="manual_block",
            source_type="manual",
            conn=db,
            secret=TEST_SECRET,
        )
    assert contact_eligibility(
        contact_id=1,
        kol_pool_id=1,
        brand_scope="",
        conn=db,
        secret=TEST_SECRET,
    )["reason"] == "invalid_brand_scope"


def test_public_api_results_never_return_contact_plaintext() -> None:
    db = _db()
    result = _verified_email(db)
    verdict = contact_eligibility(
        contact_id=result["contact_id"],
        kol_pool_id=1,
        brand_scope="organization:1",
        conn=db,
        secret=TEST_SECRET,
    )
    assert "business@example.com" not in repr(result)
    assert "business@example.com" not in repr(verdict)
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


def test_env_key_is_supported_without_exposing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SUPPRESSION_HMAC_ENV, TEST_SECRET.decode())
    fingerprint = contact_fingerprint(
        brand_scope="organization:1",
        kol_pool_id=1,
        channel="email",
        normalized_value="business@example.com",
    )
    assert len(fingerprint) == 64
    assert os.environ[SUPPRESSION_HMAC_ENV] not in fingerprint
