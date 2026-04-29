"""
services/party/party_service.py

Party creation + identity resolution.

Phase 1 scope:
    - create_party(...)
    - get_or_create_by_email(email, ...) — the primary Phase 1 stitch entry point
    - get_or_create_by_creator_code(creator_code, ...)
    - add_identity_link(party_id, link_type, link_value_hash, ...)
    - resolve_party_by_link(link_type, link_value_hash)

Phase 2 will add:
    - merge_parties(winner_id, loser_id)  (stitch conflict resolution)
    - resolve_party_by_any(identifiers: list)  (multi-signal stitch)

All functions require is_postgres_runtime() == True. They silently no-op and
return None if PG isn't available, so they're safe to call from code paths that
run on both SQLite (dev) and PG (prod).
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Optional

from app.db.connection import _get_pg_pool, is_postgres_runtime
from app.services.party.email_normalize import email_hashes

logger = logging.getLogger(__name__)


# =====================================================================
# Low-level helpers
# =====================================================================

def _new_party_id() -> str:
    """UUIDv4 as string. Used by all callers to seed a new party."""
    return str(uuid.uuid4())


def _hash_value(link_type: str, value: str) -> str:
    """
    Generic SHA-256 for non-email link types. Emails should go through
    email_normalize.email_hashes() instead.
    """
    if not value:
        return ""
    key = f"{link_type}:{value.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# =====================================================================
# Core: create party
# =====================================================================

def create_party(
    *,
    origin_source: str = "unknown",
    origin_channel: str = "",
    origin_utm_source: str = "",
    origin_utm_medium: str = "",
    origin_utm_campaign: str = "",
    origin_ref_code: str = "",
    display_name: str = "",
    locale: str = "",
    country_code: str = "",
    timezone: str = "",
    lifecycle_stage: str = "anonymous",
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """
    Insert a new row into parties. Returns party_id (UUID string).
    Returns None if PG is not available.
    """
    if not is_postgres_runtime():
        return None

    pool = _get_pg_pool()
    if pool is None:
        return None

    import json as _json

    party_id = _new_party_id()
    metadata_json = _json.dumps(metadata or {})

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO parties (
                    party_id,
                    display_name, locale, country_code, timezone,
                    origin_source, origin_channel,
                    origin_utm_source, origin_utm_medium, origin_utm_campaign,
                    origin_ref_code,
                    lifecycle_stage,
                    metadata_json
                ) VALUES (
                    %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s,
                    %s,
                    %s::jsonb
                )
                """,
                (
                    party_id,
                    display_name, locale, country_code, timezone,
                    origin_source, origin_channel,
                    origin_utm_source, origin_utm_medium, origin_utm_campaign,
                    origin_ref_code,
                    lifecycle_stage,
                    metadata_json,
                ),
            )
        conn.commit()
    return party_id


# =====================================================================
# Core: identity_links
# =====================================================================

def add_identity_link(
    *,
    party_id: str,
    link_type: str,
    link_value_hash: str,
    link_value_preview: str = "",
    source: str = "unknown",
    source_event_id: Optional[str] = None,
    confidence_level: str = "medium",
    confidence_score: float = 0.5,
    is_primary: bool = False,
    verified: bool = False,
) -> bool:
    """
    Idempotent: upserts an identity_link row. If the (link_type, hash) pair
    already exists active, no-op returns False. Otherwise insert + True.
    """
    if not is_postgres_runtime():
        return False
    if not party_id or not link_type or not link_value_hash:
        return False

    pool = _get_pg_pool()
    if pool is None:
        return False

    with pool.connection() as conn:
        with conn.cursor() as cur:
            # Does a live link already exist for this (type, hash) pair?
            cur.execute(
                """
                SELECT party_id FROM identity_links
                WHERE link_type = %s AND link_value_hash = %s
                  AND is_active = TRUE AND retired_at IS NULL
                LIMIT 1
                """,
                (link_type, link_value_hash),
            )
            existing = cur.fetchone()
            if existing:
                existing_party = existing[0] if not isinstance(existing, dict) else existing["party_id"]
                if str(existing_party) == str(party_id):
                    # Same party already has this link; nothing to do.
                    return False
                # Conflict: same credential points to a different party.
                # Phase 1 policy: log warning, do NOT merge. Phase 2 adds merge.
                logger.warning(
                    "identity_link conflict (Phase 1 no-merge): "
                    "link_type=%s hash=%s existing_party=%s attempted_party=%s",
                    link_type, link_value_hash, existing_party, party_id,
                )
                return False

            cur.execute(
                """
                INSERT INTO identity_links (
                    party_id, link_type, link_value_hash, link_value_preview,
                    confidence_level, confidence_score,
                    source, source_event_id,
                    is_primary, verified_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    CASE WHEN %s THEN NOW() ELSE NULL END
                )
                """,
                (
                    party_id, link_type, link_value_hash, link_value_preview,
                    confidence_level, confidence_score,
                    source, source_event_id,
                    is_primary,
                    verified,
                ),
            )
        conn.commit()
    return True


def resolve_party_by_link(link_type: str, link_value_hash: str) -> Optional[str]:
    """
    Returns party_id if an active identity_link exists for this (type, hash).
    Returns None otherwise (caller decides whether to create a new party).
    """
    if not is_postgres_runtime() or not link_type or not link_value_hash:
        return None

    pool = _get_pg_pool()
    if pool is None:
        return None

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT party_id FROM identity_links
                WHERE link_type = %s AND link_value_hash = %s
                  AND is_active = TRUE AND retired_at IS NULL
                LIMIT 1
                """,
                (link_type, link_value_hash),
            )
            row = cur.fetchone()
    if not row:
        return None
    value = row[0] if not isinstance(row, dict) else row["party_id"]
    return str(value)


# =====================================================================
# High-level stitch entry points (Phase 1 minimal)
# =====================================================================

def get_or_create_by_email(
    email: str,
    *,
    origin_source: str = "unknown",
    origin_channel: str = "",
    confirm_party_exists: bool = True,
    display_name: str = "",
) -> Optional[str]:
    """
    Phase 1 primary stitch entry.

    Lookup order:
        1. identity_links(link_type='email_normalized', hash=normalized_hash)
        2. identity_links(link_type='email_raw', hash=raw_hash)

    If neither is found, creates a new party and inserts BOTH link rows
    (raw + normalized) so future lookups succeed either way.

    Returns party_id, or None if PG unavailable / empty email.
    """
    if not email or not is_postgres_runtime():
        return None

    h = email_hashes(email)
    if not h["raw_hash"]:
        return None

    # Try normalized first (strongest), then raw
    party_id = resolve_party_by_link("email_normalized", h["normalized_hash"])
    if not party_id and h["was_normalized"]:
        party_id = resolve_party_by_link("email_raw", h["raw_hash"])
    elif not party_id:
        # If no normalization applied, both hashes are the same; already tried.
        pass

    if party_id:
        return party_id

    # Create party + add both link rows
    party_id = create_party(
        origin_source=origin_source,
        origin_channel=origin_channel,
        display_name=display_name,
        lifecycle_stage="identified",
    )
    if not party_id:
        return None

    # Raw hash link
    add_identity_link(
        party_id=party_id,
        link_type="email_raw",
        link_value_hash=h["raw_hash"],
        link_value_preview=h["normalized_form"],
        source=origin_source,
        confidence_level="high",
        confidence_score=0.9,
        is_primary=True,
    )

    # Normalized hash link (only if different from raw)
    if h["was_normalized"]:
        add_identity_link(
            party_id=party_id,
            link_type="email_normalized",
            link_value_hash=h["normalized_hash"],
            link_value_preview=h["normalized_form"],
            source=origin_source,
            confidence_level="high",
            confidence_score=0.85,  # slightly lower because normalization is lossy
            is_primary=False,
        )

    return party_id


def get_or_create_by_creator_code(
    creator_code: str,
    *,
    origin_source: str = "creator_signup",
) -> Optional[str]:
    """
    Link a party to a Viltrox creator_code (e.g. 'V_001234').
    Not a hash — creator_codes aren't PII.
    """
    if not creator_code or not is_postgres_runtime():
        return None

    # creator_code is not PII, but we still hash it for uniform index layout
    link_hash = _hash_value("creator_code", creator_code)
    party_id = resolve_party_by_link("creator_code", link_hash)
    if party_id:
        return party_id

    party_id = create_party(
        origin_source=origin_source,
        lifecycle_stage="creator",
    )
    if not party_id:
        return None

    add_identity_link(
        party_id=party_id,
        link_type="creator_code",
        link_value_hash=link_hash,
        link_value_preview=creator_code,  # safe to display
        source=origin_source,
        confidence_level="high",
        confidence_score=1.0,
        is_primary=True,
        verified=True,
    )

    # Mark is_creator flag
    _touch_party(party_id, is_creator=True, lifecycle_stage="creator")
    return party_id


def get_or_create_by_user_id(
    user_id: int | str,
    *,
    origin_source: str = "legacy_backfill",
) -> Optional[str]:
    """
    Link a party to a legacy users.id (for backfill of existing data).
    """
    if not user_id or not is_postgres_runtime():
        return None

    link_hash = _hash_value("user_id", str(user_id))
    party_id = resolve_party_by_link("user_id", link_hash)
    if party_id:
        return party_id

    party_id = create_party(
        origin_source=origin_source,
        lifecycle_stage="identified",
    )
    if not party_id:
        return None

    add_identity_link(
        party_id=party_id,
        link_type="user_id",
        link_value_hash=link_hash,
        link_value_preview=f"user#{user_id}",
        source=origin_source,
        confidence_level="high",
        confidence_score=1.0,
        is_primary=True,
        verified=True,
    )
    return party_id


# =====================================================================
# Maintenance
# =====================================================================

def _touch_party(
    party_id: str,
    *,
    is_creator: Optional[bool] = None,
    is_customer: Optional[bool] = None,
    lifecycle_stage: Optional[str] = None,
    last_activity_at_now: bool = False,
) -> None:
    """
    Updates selected columns on parties. All params optional; only changed
    fields are written.
    """
    if not is_postgres_runtime() or not party_id:
        return
    pool = _get_pg_pool()
    if pool is None:
        return

    set_parts: list[str] = []
    args: list[Any] = []
    if is_creator is not None:
        set_parts.append("is_creator = %s")
        args.append(is_creator)
    if is_customer is not None:
        set_parts.append("is_customer = %s")
        args.append(is_customer)
    if lifecycle_stage is not None:
        set_parts.append("lifecycle_stage = %s")
        args.append(lifecycle_stage)
    if last_activity_at_now:
        set_parts.append("last_activity_at = NOW()")
    set_parts.append("updated_at = NOW()")

    if not set_parts:
        return

    args.append(party_id)
    sql = f"UPDATE parties SET {', '.join(set_parts)} WHERE party_id = %s"

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
        conn.commit()


def mark_activity(party_id: str) -> None:
    """Stamp last_activity_at = NOW(). Called by event_writer after insert."""
    _touch_party(party_id, last_activity_at_now=True)
