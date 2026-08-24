"""Read-only contact and audience preview hydration for search-session items."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domains.kol.search_sessions_serde import (
    _compact_audience_preview,
    _compact_contact_preview,
    _loads,
    _text,
)


_SESSION_CREATOR_ITEM_TYPES = {
    "existing_kol",
    "new_creator",
    "online_qualified_candidate",
    "recall_candidate",
}
_DEAD_AVATAR_STATES = {"expired", "invalid", "missing"}


def _creator_aliases_without_pool(item: dict[str, Any]) -> set[str]:
    from app.domains.kol.identity import canonical_creator_aliases

    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    probe = {
        **payload,
        "platform": payload.get("platform") or item.get("platform"),
        "profile_url": payload.get("profile_url") or item.get("source_url"),
        "source_url": item.get("source_url") or payload.get("source_url"),
    }
    return {
        alias
        for alias in canonical_creator_aliases(probe)
        if not alias.startswith("pool:")
    }


def _apply_durable_pool_avatar_fallback(
    item: dict[str, Any],
    pool_profile: dict[str, Any],
) -> bool:
    """Project a linked Pool avatar only into an absent/dead session slot."""
    from app.domains.kol.identity import canonical_creator_aliases
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    if _text(item.get("item_type")) not in _SESSION_CREATOR_ITEM_TYPES:
        return False
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
    if payload is None:
        return False
    current_url, current_state = _avatar_url_policy(payload.get("avatar_url"))
    declared_state = _text(payload.get("avatar_url_status")).lower()
    current_dead = current_state in _DEAD_AVATAR_STATES or declared_state in _DEAD_AVATAR_STATES
    if current_url and not current_dead:
        return False

    pool_url, pool_state = _avatar_url_policy(pool_profile.get("avatar_url"))
    if not pool_url or pool_state != "durable":
        return False

    session_aliases = _creator_aliases_without_pool(item)
    pool_aliases = {
        alias
        for alias in canonical_creator_aliases(pool_profile)
        if not alias.startswith("pool:")
    }
    # ``kol_pool_id`` is the exact row link. If the session also carries
    # creator identity, require it to agree with that row before projecting.
    if session_aliases and not session_aliases.intersection(pool_aliases):
        return False

    payload["avatar_url"] = pool_url
    payload["avatar_url_status"] = "durable"
    payload["avatar_url_source"] = "pool_durable_read_fallback"
    return True


def hydrate_session_item_avatar_fallbacks(
    conn: Any,
    items: list[dict[str, Any]],
    *,
    logger: Any,
) -> int:
    """Read current linked Pool avatars without mutating historical rows."""
    profile_ids = sorted({
        int(item["kol_pool_id"])
        for item in items
        if item.get("kol_pool_id")
        and _text(item.get("item_type")) in _SESSION_CREATOR_ITEM_TYPES
        and isinstance(item.get("payload"), dict)
    })
    if not profile_ids:
        return 0
    try:
        placeholders = ",".join(["?"] * len(profile_ids))
        rows = conn.execute(
            f"""
            SELECT id, platform, handle, profile_url, avatar_url, raw_platform_data
            FROM vkpi_kol_pool
            WHERE id IN ({placeholders})
            """,
            tuple(profile_ids),
        ).fetchall()
        profiles = {int(dict(row)["id"]): dict(row) for row in rows}
        return sum(
            _apply_durable_pool_avatar_fallback(
                item,
                profiles.get(int(item.get("kol_pool_id") or 0), {}),
            )
            for item in items
        )
    except Exception:
        logger.warning("search_sessions.avatar_pool_fallback_failed", exc_info=True)
        return 0


def hydrate_session_item_previews(
    conn: Any,
    items: list[dict[str, Any]],
    *,
    enrichment_status_fn: Callable[..., str],
    logger: Any,
) -> None:
    """Hydrate display names and masked preview state without exposing contacts."""

    # Some materialization paths omit display_name. Batch-read the current pool
    # projection so validation, existing-pool, and discovery lanes agree.
    profile_ids = sorted({
        int(item["kol_pool_id"])
        for item in items
        if item.get("kol_pool_id") and isinstance(item.get("payload"), dict)
    })
    if profile_ids:
        try:
            placeholders = ",".join(["?"] * len(profile_ids))
            profile_rows = conn.execute(
                f"""
                SELECT id, display_name, email, contact_channels,
                       other_contacts_json, audience_estimated_json,
                       platform, handle, profile_url, avatar_url,
                       raw_platform_data
                FROM vkpi_kol_pool
                WHERE id IN ({placeholders})
                """,
                tuple(profile_ids),
            ).fetchall()
            profiles = {int(dict(row)["id"]): dict(row) for row in profile_rows}
            for item in items:
                kol_pool_id = item.get("kol_pool_id")
                if not kol_pool_id or not isinstance(item.get("payload"), dict):
                    continue
                profile = profiles.get(int(kol_pool_id), {})
                _apply_durable_pool_avatar_fallback(item, profile)
                display_name = str(profile.get("display_name") or "").strip()
                if display_name and not str(item["payload"].get("display_name") or "").strip():
                    item["payload"]["display_name"] = display_name
                email = str(profile.get("email") or "").strip()
                channels = _loads(profile.get("contact_channels"), {})
                other_contacts = _loads(profile.get("other_contacts_json"), [])
                contact_count = len(other_contacts) if isinstance(other_contacts, list) else 0
                if isinstance(channels, (dict, list)):
                    contact_count += len(channels)
                contact_ready = bool(email or contact_count)
                item["payload"]["contact_preview"] = _compact_contact_preview(
                    {
                        "status": enrichment_status_fn(
                            item,
                            "contact_enrichment",
                            ready=contact_ready,
                        ),
                        "channel_count": contact_count,
                    }
                )
                audience = _loads(profile.get("audience_estimated_json"), {})
                audience_ready = isinstance(audience, dict) and bool(audience)
                item["payload"]["audience_preview"] = _compact_audience_preview(
                    {
                        "status": enrichment_status_fn(
                            item,
                            "audience_enrichment",
                            ready=audience_ready,
                        ),
                        "method": _text(audience.get("method")) if isinstance(audience, dict) else "",
                        "confidence": audience.get("confidence") if isinstance(audience, dict) else None,
                        "sample_size": audience.get("sample_size") if isinstance(audience, dict) else None,
                        "async": not audience_ready,
                    }
                )
        except Exception:
            logger.warning("search_sessions.profile_preview_backfill_failed", exc_info=True)

    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
        if payload is None:
            continue
        if not isinstance(payload.get("contact_preview"), dict) or not payload["contact_preview"]:
            payload["contact_preview"] = _compact_contact_preview(
                {
                    "status": enrichment_status_fn(
                        item,
                        "contact_enrichment",
                        ready=False,
                    ),
                    "channel_count": 0,
                }
            )
        if not isinstance(payload.get("audience_preview"), dict):
            payload["audience_preview"] = _compact_audience_preview(
                {
                    "status": enrichment_status_fn(
                        item,
                        "audience_enrichment",
                        ready=False,
                    ),
                    "method": "",
                    "confidence": None,
                    "sample_size": None,
                    "async": True,
                }
            )
