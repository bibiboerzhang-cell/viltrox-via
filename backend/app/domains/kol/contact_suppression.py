"""Durable, plaintext-minimizing suppression and contact eligibility gates.

Suppression keys are scoped HMAC fingerprints.  The key is required at runtime
and is never persisted.  Every read-side failure (missing key, schema drift, or
query error) is restrictive so reveal/outreach callers cannot fail open.

Eligibility verdicts carry a ``tier``:

* ``verified`` - ``verified_public_business`` rows backed by qualifying public
  evidence (the original, strictest path).
* ``observed`` - ``observed`` rows whose contact source is a scan/declaration
  the pipeline itself produced (bio/full scans, platform declarations, manual
  entry).  Such rows are still subject to invalidation/revocation and the
  organization-scoped suppression ledger; they are never promoted to
  ``verified`` and the requester must still clear the audited reveal boundary.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.contact_ingest import (
    ContactValidationError,
    VERIFIED_PUBLIC_MIN_CONFIDENCE,
    normalize_contact,
)


SUPPRESSION_HMAC_ENV = "VKPI_CONTACT_SUPPRESSION_HMAC_KEY"
SUPPRESSION_REASONS = frozenset(
    {
        "unsubscribe",
        "manual_block",
        "complaint",
        "hard_bounce",
        "legal_request",
        "invalid_contact",
        "provider_request",
    }
)
SUPPRESSION_SOURCES = frozenset({"reply", "manual", "compliance", "bounce", "provider"})
_ACTOR_REQUIRED_SOURCES = frozenset({"manual", "compliance"})
_BRAND_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{1,127}$")
_VERIFIABLE_PUBLIC_SOURCES = (
    "youtube_about_declared",
    "ig_business_profile",
    "bio_explicit_contact",
    "website_declared",
    "manual_verified_public_business",
)
# Sources the pipeline observed or a staff member declared.  An ``observed``
# row from one of these may be disclosed at the ``observed`` tier after the
# suppression ledger is consulted.  ``manual*`` variants are matched by prefix.
OBSERVED_ELIGIBLE_SOURCES = frozenset(
    {
        "raw_bio_scan",
        "raw_full_scan",
        "youtube_about_declared",
        "ig_business_profile",
        "bio_explicit_contact",
        "website_declared",
        "manual",
        "manual_verified_public_business",
    }
)
_MANUAL_SOURCE_PREFIX = "manual"
TIER_VERIFIED = "verified"
TIER_OBSERVED = "observed"

logger = get_logger(__name__)


class SuppressionConfigurationError(RuntimeError):
    """Raised for write operations that cannot safely fingerprint a contact."""


def _positive_id(value: Any, *, field: str, optional: bool = False) -> int | None:
    if value in (None, "") and optional:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ContactValidationError(f"invalid {field}") from exc
    if result <= 0:
        raise ContactValidationError(f"invalid {field}")
    return result


def _scope(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not _BRAND_SCOPE_RE.fullmatch(normalized):
        raise ContactValidationError("invalid brand scope")
    return normalized


def _timestamp(value: Any | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContactValidationError("invalid event timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _secret_bytes(secret: str | bytes | None = None) -> bytes:
    supplied: str | bytes = secret if secret is not None else os.environ.get(SUPPRESSION_HMAC_ENV, "")
    if isinstance(supplied, bytes):
        key = supplied
    else:
        value = supplied.strip()
        if value.startswith("base64:"):
            try:
                key = base64.b64decode(value[7:], validate=True)
            except Exception as exc:
                raise SuppressionConfigurationError("invalid suppression fingerprint key") from exc
        else:
            key = value.encode("utf-8")
    if len(key) < 32:
        raise SuppressionConfigurationError("suppression fingerprint key is unavailable")
    return key


def contact_fingerprint(
    *,
    brand_scope: str,
    kol_pool_id: int,
    channel: str,
    normalized_value: str,
    secret: str | bytes | None = None,
) -> str:
    """Return a versioned, scope-bound HMAC fingerprint for internal writes."""

    fingerprint, _ = _fingerprint_and_key_id(
        brand_scope=brand_scope,
        kol_pool_id=kol_pool_id,
        channel=channel,
        normalized_value=normalized_value,
        secret=secret,
    )
    return fingerprint


def _fingerprint_and_key_id(
    *,
    brand_scope: str,
    kol_pool_id: int,
    channel: str,
    normalized_value: str,
    secret: str | bytes | None = None,
) -> tuple[str, str]:
    """Resolve one key once, preventing silent un-suppression after rotation."""

    scope = _scope(brand_scope)
    pool_id = _positive_id(kol_pool_id, field="KOL pool id")
    canonical = normalize_contact(channel, normalized_value)
    if canonical.channel != str(channel or "").strip().lower():
        raise ContactValidationError("channel does not match normalized contact")
    payload = "\x1f".join(
        ("v1", scope, str(pool_id), canonical.channel, canonical.normalized_value)
    ).encode("utf-8")
    key = _secret_bytes(secret)
    fingerprint = hmac.new(key, payload, hashlib.sha256).hexdigest()
    key_id = hashlib.sha256(b"vkpi-contact-suppression-key-id\x00" + key).hexdigest()[:16]
    return fingerprint, key_id


def observed_source_eligible(source: Any) -> bool:
    """True when an ``observed`` row's source is a pipeline scan/declaration."""

    normalized = str(source or "").strip().lower()
    if not normalized:
        return False
    return normalized in OBSERVED_ELIGIBLE_SOURCES or normalized.startswith(_MANUAL_SOURCE_PREFIX)


def _eligible(
    *,
    tier: str,
    reason: str,
    contact_id: int,
    kol_pool_id: int,
    channel: str,
    verification_status: str,
) -> dict[str, Any]:
    return {
        "status": "eligible",
        "eligible": True,
        "tier": tier,
        "reason": reason,
        "contact_id": int(contact_id),
        "kol_pool_id": int(kol_pool_id),
        "channel": channel,
        "verification_status": verification_status,
    }


def _suppression_ledger_reason(
    db: Any,
    *,
    scope: str,
    pool_id: int,
    channel: str,
    normalized_value: str,
    secret: str | bytes | None,
) -> str | None:
    """Return a restrictive reason code, or ``None`` when the ledger is clear."""

    try:
        fingerprint, key_id = _fingerprint_and_key_id(
            brand_scope=scope,
            kol_pool_id=int(pool_id),
            channel=channel,
            normalized_value=normalized_value,
            secret=secret,
        )
    except (ContactValidationError, SuppressionConfigurationError):
        return "fingerprint_key_unavailable"
    try:
        suppression_rows = db.execute(
            """
            SELECT contact_fingerprint, fingerprint_key_id
            FROM vkpi_kol_contact_suppressions
            WHERE brand_scope=? AND kol_pool_id=? AND channel=? AND is_active=TRUE
            """,
            (scope, int(pool_id), channel),
        ).fetchall()
    except Exception:
        logger.warning("contact suppression ledger unavailable; eligibility fails closed", exc_info=True)
        return "suppression_check_unavailable"
    if any(
        str(dict(item).get("contact_fingerprint") or "") == fingerprint
        for item in suppression_rows
    ):
        return "suppressed"
    if any(
        str(dict(item).get("fingerprint_key_id") or "") != key_id
        for item in suppression_rows
    ):
        return "suppression_check_unavailable"
    return None


def _restricted(
    *,
    reason: str,
    contact_id: int,
    kol_pool_id: int,
    channel: str | None = None,
    verification_status: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "restricted",
        "eligible": False,
        "reason": reason,
        "contact_id": int(contact_id),
        "kol_pool_id": int(kol_pool_id),
    }
    if channel:
        result["channel"] = channel
    if verification_status:
        result["verification_status"] = verification_status
    return result


def record_suppression(
    *,
    kol_pool_id: int,
    contact_type: str,
    contact_value: str,
    brand_scope: str,
    reason: str,
    source_type: str,
    staff_id: int | None = None,
    event_at: Any | None = None,
    conn: Any | None = None,
    secret: str | bytes | None = None,
) -> dict[str, Any]:
    """Create or reactivate a durable suppression without storing plaintext."""

    db = conn or get_conn()
    pool_id = _positive_id(kol_pool_id, field="KOL pool id")
    actor_id = _positive_id(staff_id, field="staff id", optional=True)
    scope = _scope(brand_scope)
    reason_code = str(reason or "").strip().lower()
    source = str(source_type or "").strip().lower()
    if reason_code not in SUPPRESSION_REASONS:
        raise ContactValidationError("invalid suppression reason")
    if source not in SUPPRESSION_SOURCES:
        raise ContactValidationError("invalid suppression source")
    if source in _ACTOR_REQUIRED_SOURCES and actor_id is None:
        raise ContactValidationError("staff id is required for this suppression source")
    normalized = normalize_contact(contact_type, contact_value)
    fingerprint, key_id = _fingerprint_and_key_id(
        brand_scope=scope,
        kol_pool_id=int(pool_id),
        channel=normalized.channel,
        normalized_value=normalized.normalized_value,
        secret=secret,
    )
    at = _timestamp(event_at)
    try:
        cursor = db.execute(
            """
            INSERT INTO vkpi_kol_contact_suppressions
                (brand_scope, kol_pool_id, channel, contact_fingerprint, fingerprint_key_id,
                 reason, source_type, is_active, suppressed_by_staff_id,
                 suppressed_at, released_by_staff_id, released_at, last_event_at)
            VALUES (?,?,?,?,?,?,?,TRUE,?,?,NULL,NULL,?)
            ON CONFLICT(brand_scope, kol_pool_id, channel, contact_fingerprint)
            DO UPDATE SET
                reason=excluded.reason,
                source_type=excluded.source_type,
                is_active=TRUE,
                suppressed_by_staff_id=excluded.suppressed_by_staff_id,
                suppressed_at=excluded.suppressed_at,
                released_by_staff_id=NULL,
                released_at=NULL,
                last_event_at=excluded.last_event_at
            RETURNING id
            """,
            (
                scope,
                int(pool_id),
                normalized.channel,
                fingerprint,
                key_id,
                reason_code,
                source,
                actor_id,
                at,
                at,
            ),
        )
        row = cursor.fetchone()
        suppression_id = int(dict(row).get("id") or row[0])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "suppression_id": suppression_id,
        "status": "suppressed",
        "kol_pool_id": int(pool_id),
        "channel": normalized.channel,
        "reason": reason_code,
    }


def release_suppression(
    *,
    kol_pool_id: int,
    contact_type: str,
    contact_value: str,
    brand_scope: str,
    staff_id: int,
    event_at: Any | None = None,
    conn: Any | None = None,
    secret: str | bytes | None = None,
) -> dict[str, Any]:
    """Deactivate, but never delete, a suppression event.  Staff is required."""

    db = conn or get_conn()
    pool_id = _positive_id(kol_pool_id, field="KOL pool id")
    actor_id = _positive_id(staff_id, field="staff id")
    scope = _scope(brand_scope)
    normalized = normalize_contact(contact_type, contact_value)
    fingerprint, key_id = _fingerprint_and_key_id(
        brand_scope=scope,
        kol_pool_id=int(pool_id),
        channel=normalized.channel,
        normalized_value=normalized.normalized_value,
        secret=secret,
    )
    at = _timestamp(event_at)
    try:
        cursor = db.execute(
            """
            UPDATE vkpi_kol_contact_suppressions
            SET is_active=FALSE, released_by_staff_id=?, released_at=?, last_event_at=?
            WHERE brand_scope=? AND kol_pool_id=? AND channel=?
              AND contact_fingerprint=? AND is_active=TRUE
            """,
            (actor_id, at, at, scope, int(pool_id), normalized.channel, fingerprint),
        )
        released = int(cursor.rowcount or 0) > 0
        if not released:
            mismatched_key = db.execute(
                """
                SELECT 1 FROM vkpi_kol_contact_suppressions
                WHERE brand_scope=? AND kol_pool_id=? AND channel=?
                  AND fingerprint_key_id<>? AND is_active=TRUE
                LIMIT 1
                """,
                (scope, int(pool_id), normalized.channel, key_id),
            ).fetchone()
            if mismatched_key is not None:
                raise SuppressionConfigurationError("suppression fingerprint key mismatch")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "status": "released" if released else "not_found",
        "released": released,
        "kol_pool_id": int(pool_id),
        "channel": normalized.channel,
    }


def is_contact_suppressed(
    *,
    kol_pool_id: int,
    contact_type: str,
    contact_value: str,
    brand_scope: str,
    conn: Any | None = None,
    secret: str | bytes | None = None,
) -> dict[str, Any]:
    """Check suppression; any configuration or storage failure is restrictive."""

    try:
        db = conn or get_conn()
        pool_id = _positive_id(kol_pool_id, field="KOL pool id")
        scope = _scope(brand_scope)
        normalized = normalize_contact(contact_type, contact_value)
        fingerprint, key_id = _fingerprint_and_key_id(
            brand_scope=scope,
            kol_pool_id=int(pool_id),
            channel=normalized.channel,
            normalized_value=normalized.normalized_value,
            secret=secret,
        )
        rows = db.execute(
            """
            SELECT contact_fingerprint, fingerprint_key_id
            FROM vkpi_kol_contact_suppressions
            WHERE brand_scope=? AND kol_pool_id=? AND channel=? AND is_active=TRUE
            """,
            (scope, int(pool_id), normalized.channel),
        ).fetchall()
    except (ContactValidationError, SuppressionConfigurationError):
        return {"suppressed": True, "fail_closed": True, "reason": "suppression_check_unavailable"}
    except Exception:
        return {"suppressed": True, "fail_closed": True, "reason": "suppression_check_unavailable"}
    if any(str(dict(row).get("contact_fingerprint") or "") == fingerprint for row in rows):
        return {"suppressed": True, "fail_closed": False, "reason": "suppressed"}
    if any(str(dict(row).get("fingerprint_key_id") or "") != key_id for row in rows):
        return {"suppressed": True, "fail_closed": True, "reason": "suppression_check_unavailable"}
    if not rows:
        return {"suppressed": False, "fail_closed": False, "reason": "not_suppressed"}
    return {"suppressed": False, "fail_closed": False, "reason": "not_suppressed"}


def contact_eligibility(
    *,
    contact_id: int,
    kol_pool_id: int,
    brand_scope: str,
    conn: Any | None = None,
    secret: str | bytes | None = None,
) -> dict[str, Any]:
    """Return a PII-free, fail-closed reveal/outreach eligibility verdict."""

    try:
        cid = _positive_id(contact_id, field="contact id")
        pool_id = _positive_id(kol_pool_id, field="KOL pool id")
    except ContactValidationError:
        return _restricted(reason="contact_not_found", contact_id=0, kol_pool_id=0)
    try:
        scope = _scope(brand_scope)
    except ContactValidationError:
        return _restricted(
            reason="invalid_brand_scope", contact_id=int(cid), kol_pool_id=int(pool_id)
        )
    try:
        db = conn or get_conn()
        raw_row = db.execute(
            """
            SELECT id, kol_pool_id, normalized_value, channel, verification_status,
                   verified_at, invalidated_at, revoked_at,
                   contact_type, contact_value, contact_source
            FROM vkpi_kol_pool_contacts
            WHERE id=?
            """,
            (int(cid),),
        ).fetchone()
    except Exception:
        logger.warning("contact row lookup unavailable; eligibility fails closed", exc_info=True)
        return _restricted(
            reason="suppression_check_unavailable",
            contact_id=int(cid),
            kol_pool_id=int(pool_id),
        )
    if raw_row is None:
        return _restricted(
            reason="contact_not_found", contact_id=int(cid), kol_pool_id=int(pool_id)
        )
    row = dict(raw_row)
    if int(row.get("kol_pool_id") or 0) != int(pool_id):
        return _restricted(
            reason="contact_identity_mismatch",
            contact_id=int(cid),
            kol_pool_id=int(pool_id),
        )
    channel = str(row.get("channel") or "").strip().lower()
    status = str(row.get("verification_status") or "observed").strip().lower()
    verdict_context = {
        "contact_id": int(cid),
        "kol_pool_id": int(pool_id),
        "channel": channel or None,
        "verification_status": status,
    }
    if status == "verified_public_business":
        return _verified_tier_verdict(
            db,
            row=row,
            cid=int(cid),
            pool_id=int(pool_id),
            scope=scope,
            channel=channel,
            status=status,
            secret=secret,
            verdict_context=verdict_context,
        )
    if status == "observed" and observed_source_eligible(row.get("contact_source")):
        return _observed_tier_verdict(
            db,
            row=row,
            cid=int(cid),
            pool_id=int(pool_id),
            scope=scope,
            status=status,
            secret=secret,
            verdict_context=verdict_context,
        )
    return _restricted(reason="verification_not_eligible", **verdict_context)


def _verified_tier_verdict(
    db: Any,
    *,
    row: dict[str, Any],
    cid: int,
    pool_id: int,
    scope: str,
    channel: str,
    status: str,
    secret: str | bytes | None,
    verdict_context: dict[str, Any],
) -> dict[str, Any]:
    """Strict path: verified row + qualifying public evidence + clear ledger."""

    if not row.get("verified_at") or row.get("invalidated_at") or row.get("revoked_at"):
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    normalized_value = str(row.get("normalized_value") or "")
    if not channel or not normalized_value:
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    try:
        canonical = normalize_contact(channel, normalized_value)
    except ContactValidationError:
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    if canonical.channel != channel or canonical.normalized_value != normalized_value:
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    try:
        evidence = db.execute(
            """
            SELECT 1
            FROM vkpi_kol_contact_evidence
            WHERE contact_id=? AND kol_pool_id=?
              AND is_public_declared=TRUE AND confidence>=?
              AND source_type IN (?,?,?,?,?)
              AND COALESCE(source_url, '')<>''
              AND COALESCE(source_field, '')<>''
              AND (
                  source_type<>'manual_verified_public_business'
                  OR observed_by_staff_id IS NOT NULL
              )
            LIMIT 1
            """,
            (
                int(cid),
                int(pool_id),
                VERIFIED_PUBLIC_MIN_CONFIDENCE,
                *_VERIFIABLE_PUBLIC_SOURCES,
            ),
        ).fetchone()
    except Exception:
        logger.warning("contact evidence lookup unavailable; eligibility fails closed", exc_info=True)
        return _restricted(reason="verification_evidence_missing", **verdict_context)
    if evidence is None:
        return _restricted(reason="verification_evidence_missing", **verdict_context)
    ledger_reason = _suppression_ledger_reason(
        db,
        scope=scope,
        pool_id=int(pool_id),
        channel=channel,
        normalized_value=normalized_value,
        secret=secret,
    )
    if ledger_reason:
        return _restricted(reason=ledger_reason, **verdict_context)
    return _eligible(
        tier=TIER_VERIFIED,
        reason="eligible_verified_public_business",
        contact_id=int(cid),
        kol_pool_id=int(pool_id),
        channel=channel,
        verification_status=status,
    )


def _observed_tier_verdict(
    db: Any,
    *,
    row: dict[str, Any],
    cid: int,
    pool_id: int,
    scope: str,
    status: str,
    secret: str | bytes | None,
    verdict_context: dict[str, Any],
) -> dict[str, Any]:
    """Observed path: pipeline-sourced row, not invalidated/revoked, ledger clear.

    Rows written before canonical columns existed carry empty ``channel`` /
    ``normalized_value``; they are canonicalized in memory from the raw type
    and value so the suppression fingerprint is still scope-bound and exact.
    The canonical value is used for fingerprinting only and never returned.
    """

    if row.get("invalidated_at") or row.get("revoked_at"):
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    channel = str(row.get("channel") or "").strip().lower()
    normalized_value = str(row.get("normalized_value") or "")
    try:
        if channel and normalized_value:
            canonical = normalize_contact(channel, normalized_value)
        else:
            canonical = normalize_contact(row.get("contact_type"), row.get("contact_value"))
    except ContactValidationError:
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    if channel and canonical.channel != channel:
        return _restricted(reason="verification_state_incomplete", **verdict_context)
    channel = canonical.channel
    verdict_context = {**verdict_context, "channel": channel}
    ledger_reason = _suppression_ledger_reason(
        db,
        scope=scope,
        pool_id=int(pool_id),
        channel=channel,
        normalized_value=canonical.normalized_value,
        secret=secret,
    )
    if ledger_reason:
        return _restricted(reason=ledger_reason, **verdict_context)
    return _eligible(
        tier=TIER_OBSERVED,
        reason="eligible_observed_source",
        contact_id=int(cid),
        kol_pool_id=int(pool_id),
        channel=channel,
        verification_status=status,
    )


__all__ = [
    "OBSERVED_ELIGIBLE_SOURCES",
    "SUPPRESSION_HMAC_ENV",
    "SUPPRESSION_REASONS",
    "SUPPRESSION_SOURCES",
    "TIER_OBSERVED",
    "TIER_VERIFIED",
    "SuppressionConfigurationError",
    "contact_eligibility",
    "contact_fingerprint",
    "is_contact_suppressed",
    "observed_source_eligible",
    "record_suppression",
    "release_suppression",
]
