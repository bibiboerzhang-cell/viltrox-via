"""Read-only contact and audience preview hydration for search-session items."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domains.kol.search_sessions_serde import (
    _compact_audience_preview,
    _compact_contact_preview,
    _loads,
    _text,
    project_public_profile_text,
)
from app.domains.kol.search_sessions_identity_projection import (
    POOL_ACCOUNT_GATE_BIO_FIELD,
)


_SESSION_CREATOR_ITEM_TYPES = {
    "existing_kol",
    "new_creator",
    "online_qualified_candidate",
    "recall_candidate",
}
_DEAD_AVATAR_STATES = {"expired", "invalid", "missing"}
_SESSION_AVATAR_FIELDS = (
    "avatar_url",
    "avatar_url_status",
    "avatar_upstream_status",
    "avatar_url_source",
    "avatar_fallback",
    "avatar_health",
)


def _project_session_snapshot_avatar(item: dict[str, Any]) -> bool:
    """Attach request-time avatar health to one historical creator snapshot.

    Older search rows often contain a real ``avatar_url`` but predate the
    explicit health fields.  Leaving those rows untouched makes the API call a
    present URL ``missing`` and also prevents an existing local cache hit from
    replacing the external URL.  This projection is read-only: it revalidates
    the URL, consults only the existing image cache, and never calls a provider.
    """
    from app.domains.kol.pool_read_projection import project_pool_avatar

    if _text(item.get("item_type")) not in _SESSION_CREATOR_ITEM_TYPES:
        return False
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
    if payload is None:
        return False

    before = tuple(payload.get(key) for key in _SESSION_AVATAR_FIELDS)
    projection = project_pool_avatar({"avatar_url": payload.get("avatar_url")})
    source = _text(projection.get("avatar_url_source"))
    if source == "pool_avatar_url":
        projection["avatar_url_source"] = "session_snapshot_avatar"
        health = projection.get("avatar_health")
        if isinstance(health, dict):
            projection["avatar_health"] = {**health, "source": "session_snapshot_avatar"}
        # A stable-looking third-party URL is displayable, but it is not a
        # locally materialized asset.  Keep that distinction explicit instead
        # of closing the durable gap by relabelling an external reference.
        if (
            _text(projection.get("avatar_url")).startswith(("http://", "https://"))
            and _text(projection.get("avatar_url_status")).lower() == "durable"
        ):
            projection["avatar_url_status"] = "external"
            projection["avatar_health"] = {
                **(projection.get("avatar_health") or {}),
                "status": "external",
            }
    payload.update({key: projection.get(key) for key in _SESSION_AVATAR_FIELDS})
    return before != tuple(payload.get(key) for key in _SESSION_AVATAR_FIELDS)


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


def _pool_profile_identity_matches(
    item: dict[str, Any],
    pool_profile: dict[str, Any],
) -> bool:
    """Accept the explicit Pool link unless supplied native identity conflicts."""
    from app.domains.kol.identity import canonical_creator_aliases

    session_aliases = _creator_aliases_without_pool(item)
    if not session_aliases:
        return True
    pool_aliases = {
        alias
        for alias in canonical_creator_aliases(pool_profile)
        if not alias.startswith("pool:")
    }
    return bool(session_aliases.intersection(pool_aliases))


def _apply_pool_bio_for_account_gate(
    item: dict[str, Any],
    pool_profile: dict[str, Any],
) -> bool:
    """Attach bounded transient evidence only when the session snapshot lacks it."""
    if _text(item.get("item_type")) not in _SESSION_CREATOR_ITEM_TYPES:
        return False
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
    if payload is None or _text(payload.get("bio") or payload.get("description")):
        return False
    if not _pool_profile_identity_matches(item, pool_profile):
        return False
    public_bio = project_public_profile_text(pool_profile.get("bio"), limit=1000)
    if not public_bio:
        return False
    payload[POOL_ACCOUNT_GATE_BIO_FIELD] = public_bio
    return True


def _apply_durable_pool_avatar_fallback(
    item: dict[str, Any],
    pool_profile: dict[str, Any],
) -> bool:
    """Project a linked Pool avatar into an absent/dead or prewarmed slot."""
    from app.domains.kol.pool_read_projection import (
        is_local_cached_avatar_url,
        project_pool_avatar,
    )
    from app.services.intelligence.account_scan_helpers import _avatar_url_policy

    if _text(item.get("item_type")) not in _SESSION_CREATOR_ITEM_TYPES:
        return False
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
    if payload is None:
        return False
    # ``_avatar_url_policy`` intentionally validates upstream HTTP(S) URLs and
    # therefore calls our local cache route invalid.  Preserve an exact
    # 64-hex cache path before applying that upstream-only policy; otherwise a
    # linked Pool external URL can incorrectly overwrite a better durable copy.
    if is_local_cached_avatar_url(payload.get("avatar_url")):
        return False
    current_url, current_state = _avatar_url_policy(payload.get("avatar_url"))
    declared_state = _text(payload.get("avatar_url_status")).lower()
    current_dead = current_state in _DEAD_AVATAR_STATES or declared_state in _DEAD_AVATAR_STATES
    current_ephemeral = current_state == "ephemeral" or declared_state == "ephemeral"
    if current_url and not current_dead and not current_ephemeral:
        return False

    pool_projection = project_pool_avatar(pool_profile)
    pool_url = _text(pool_projection.get("avatar_url"))
    pool_state = _text(pool_projection.get("avatar_url_status")).lower()
    pool_source = _text(pool_projection.get("avatar_url_source"))
    if not pool_url or pool_state not in {"durable", "external"}:
        return False
    # A live signed URL remains honest while no cache exists.  Replace it only
    # when the exact linked Pool row proves that this source has already been
    # materialized into the reviewed local cache.  Do not let an unrelated
    # direct Pool URL silently overwrite a still-live historical avatar.
    if current_ephemeral and pool_source != "local_prewarm_cache":
        return False

    # ``kol_pool_id`` is the exact row link. If the session also carries
    # creator identity, require it to agree with that row before projecting.
    if not _pool_profile_identity_matches(item, pool_profile):
        return False

    local_materialized = (
        pool_source == "local_prewarm_cache"
        or is_local_cached_avatar_url(pool_url)
    )
    payload["avatar_url"] = pool_url
    payload["avatar_url_status"] = "durable" if local_materialized else "external"
    payload["avatar_url_source"] = (
        "local_prewarm_cache" if local_materialized else "pool_external_read_fallback"
    )
    payload["avatar_upstream_status"] = _text(pool_projection.get("avatar_upstream_status"))
    payload["avatar_fallback"] = ""
    payload["avatar_health"] = {
        "status": payload["avatar_url_status"],
        "upstream_status": payload["avatar_upstream_status"],
        "source": payload["avatar_url_source"],
        "fallback": "",
    }
    return True


def hydrate_session_item_avatar_fallbacks(
    conn: Any,
    items: list[dict[str, Any]],
    *,
    logger: Any,
) -> int:
    """Read current linked Pool avatars without mutating historical rows."""
    for item in items:
        _project_session_snapshot_avatar(item)
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

    # Classify every historical creator snapshot, including discoveries which
    # have not yet been linked to a Pool row.  This closes the projection loss
    # where the URL was present but its health field was absent.
    for item in items:
        _project_session_snapshot_avatar(item)

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
                       platform, handle, profile_url, avatar_url, bio,
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
                _apply_pool_bio_for_account_gate(item, profile)
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
