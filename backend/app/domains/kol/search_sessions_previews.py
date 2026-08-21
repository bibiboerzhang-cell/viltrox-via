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
                SELECT id, display_name, email, contact_channels, other_contacts_json,
                       audience_estimated_json
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
