"""Outreach suggestion lifecycle actions."""
from __future__ import annotations

import os
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claims as kol_claims
from app.services.vkpi import audit, link_center, workflow
from app.services.vkpi.analytics_common import _actor, _int, _is_official_account, _json, _loads_json, _utcnow
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_analytics import ensure_vkpi_analytics_schema


def _suggestion_to_kol_lookup_body(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    profile_url = str(row.get("profile_url") or "").strip()
    source_video_url = str(row.get("source_video_url") or "").strip()
    handle = str(row.get("handle") or row.get("channel_name") or "").strip()
    return {
        "platform": row.get("platform"),
        "handle": handle,
        "handle_or_url": profile_url or handle,
        "url": profile_url,
        "channel_url": profile_url,
        "profile_url": profile_url,
        "avatar_url": row.get("avatar_url") or "",
        "follower_count": _int(row.get("follower_count")),
        "avg_views": _int(row.get("source_view_count")),
        "email": "",
        "contact_status": "claimed",
        "primary_category": row.get("source_product_sku") or "",
        "promoted_product": row.get("source_product_sku") or "",
        "channel_tags": ",".join(
            str(item)
            for item in [
                row.get("source_product_sku"),
                row.get("platform"),
                *(metadata.get("matched_intents") if isinstance(metadata.get("matched_intents"), list) else []),
            ]
            if item
        ),
        "notes": str(row.get("source_video_title") or ""),
        "contact_links": [item for item in [profile_url, source_video_url] if item],
        "contact_raw": {
            "source": "outreach_suggestion",
            "suggestion_id": row.get("id"),
            "source_product_sku": row.get("source_product_sku"),
            "source_video_url": source_video_url,
            "source_video_title": row.get("source_video_title"),
            "quality_score": row.get("quality_score") or row.get("score"),
            "metadata": metadata,
        },
        "create_if_missing": True,
    }


def claim_suggestion(suggestion_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claim a suggested KOL and bridge it into the main KOL/claim lifecycle."""
    ensure_vkpi_analytics_schema()
    ensure_vkpi_schema()
    actor = _actor(staff)
    if not actor:
        raise ValueError("staff_id required")
    conn = get_conn()
    raw = conn.execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    if not raw:
        raise LookupError("suggestion not found")
    row = dict(raw)
    if str(row.get("status") or "new") not in {"new", "claimed"}:
        raise ValueError(f"suggestion is {row.get('status')}")
    if _is_official_account(row):
        raise ValueError("official account cannot be claimed as outreach suggestion")

    lookup = kol_claims.lookup(_suggestion_to_kol_lookup_body(row), staff=staff)
    kol = lookup.get("kol") or {}
    kol_id = _int(kol.get("id"))
    if not kol_id:
        raise LookupError("kol could not be created")

    existing_claim = lookup.get("claim") or {}
    if existing_claim:
        claim = existing_claim
        claim_status = "already_claimed"
        claim_staff_id = _int(claim.get("staff_id")) or actor
    else:
        claim_result = kol_claims.claim(
            kol_id,
            {
                "staff_id": actor,
                "expires_days": 14,
                "metadata": {
                    "source": "outreach_suggestion",
                    "suggestion_id": int(suggestion_id),
                    "source_product_sku": row.get("source_product_sku"),
                    "source_video_url": row.get("source_video_url"),
                    "quality_score": row.get("quality_score") or row.get("score"),
                },
            },
            staff=staff,
        )
        claim = claim_result.get("claim") or {}
        claim_status = "created"
        claim_staff_id = actor

    now = _utcnow()
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    metadata.update(
        {
            "claim_bridge": {
                "kol_id": kol_id,
                "claim_id": _int(claim.get("id")),
                "claim_status": claim_status,
                "claimed_by_staff_id": claim_staff_id,
                "claimed_at": now,
            }
        }
    )
    conn.execute(
        """
        UPDATE vkpi_outreach_suggestions
        SET status='claimed', existing_kol_id=?, claimed_by_staff_id=?, claimed_at=?, metadata_json=?
        WHERE id=?
        """,
        (kol_id, claim_staff_id or actor, now, _json(metadata), int(suggestion_id)),
    )
    conn.commit()
    audit.log_business_event(
        staff_id=actor,
        action_type="outreach_suggestion_claim",
        target_type="kol",
        target_id=kol_id,
        metadata={
            "suggestion_id": int(suggestion_id),
            "claim_id": _int(claim.get("id")),
            "claim_status": claim_status,
            "source_product_sku": row.get("source_product_sku"),
        },
    )
    updated = conn.execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    return {"suggestion": dict(updated) if updated else {}, "kol": kol, "claim": claim, "claim_status": claim_status}


def _short_link_url(slug: str) -> str:
    base_url = str(os.environ.get("VKPI_SHORTLINK_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
    return f"{base_url.rstrip('/')}/go/{slug}" if base_url else f"/go/{slug}"


def create_project_from_suggestion(suggestion_id: int, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Claim a suggestion, create a project, and generate a Shopify short link."""
    payload = payload or {}
    claimed = claim_suggestion(suggestion_id, staff=staff)
    suggestion = claimed.get("suggestion") or {}
    kol = claimed.get("kol") or {}
    kol_id = _int(kol.get("id"))
    product_sku = str(payload.get("product_sku") or suggestion.get("source_product_sku") or "").strip()
    product_name = str(payload.get("product_name") or product_sku or "Viltrox Product").strip()
    handle = str(kol.get("channel_name") or suggestion.get("handle") or "KOL").strip()
    project = workflow.create_project(
        {
            "project_name": str(payload.get("project_name") or "").strip() or f"{product_name} KOL - {handle}",
            "kol_id": kol_id,
            "product_sku": product_sku,
            "product_name": product_name,
            "platform": suggestion.get("platform") or kol.get("platform") or "",
            "stage": str(payload.get("stage") or "discovery"),
            "source_type": "outreach_suggestion",
            "note": str(payload.get("note") or f"Created from suggestion {suggestion_id}"),
            "metadata": {
                "source": "outreach_suggestion",
                "suggestion_id": int(suggestion_id),
                "source_video_url": suggestion.get("source_video_url"),
                "source_video_title": suggestion.get("source_video_title"),
                "quality_score": suggestion.get("quality_score") or suggestion.get("score"),
            },
        },
        staff=staff,
    )
    project_id = _int(project.get("id"))
    if project_id and kol_id:
        get_conn().execute(
            "UPDATE vkpi_kol_claims SET project_id=COALESCE(project_id, ?), updated_at=? WHERE kol_id=? AND status='active'",
            (project_id, _utcnow(), kol_id),
        )

    link: dict[str, Any] = {}
    link_error = ""
    short_url = ""
    if project_id and payload.get("auto_create_link", True) is not False:
        destination_url = str(payload.get("destination_url") or payload.get("shopify_url") or os.environ.get("VKPI_DEFAULT_SHOPIFY_URL") or "https://www.viltrox.com/").strip()
        try:
            link = link_center.create_link(
                {
                    "destination_url": destination_url,
                    "link_type": "shopify",
                    "platform": "shopify",
                    "product_sku": product_sku,
                    "campaign_name": project.get("project_uid") or project.get("project_name") or "",
                    "kol_id": kol_id,
                    "project_id": project_id,
                    "status": "live",
                    "utm_source": suggestion.get("platform") or kol.get("platform") or "kol",
                    "utm_medium": "kol",
                    "utm_campaign": product_sku or "vkpi",
                    "utm_content": handle.lstrip("@"),
                    "metadata": {
                        "source": "outreach_suggestion",
                        "suggestion_id": int(suggestion_id),
                        "generated_for": "shopify",
                    },
                },
                staff=staff,
            )
            short_url = _short_link_url(str(link.get("slug") or ""))
            if short_url:
                get_conn().execute("UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?", (short_url, _utcnow(), project_id))
                project["shopify_link"] = short_url
        except Exception as exc:
            link_error = str(exc)

    now = _utcnow()
    metadata = _loads_json(suggestion.get("metadata_json"), {}) or {}
    metadata["project_bridge"] = {"project_id": project_id, "link_id": _int(link.get("id")), "link_error": link_error, "created_at": now}
    get_conn().execute("UPDATE vkpi_outreach_suggestions SET status='project_created', metadata_json=? WHERE id=?", (_json(metadata), int(suggestion_id)))
    get_conn().execute(
        "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, "suggestion", str(project.get("stage") or "discovery"), "suggestion_project_created", _actor(staff) or None, str(payload.get("note") or ""), "outreach_suggestion", str(suggestion_id), now, _json({"link_id": link.get("id"), "source": "outreach_suggestion"}), now),
    )
    get_conn().commit()
    audit.log_business_event(
        staff_id=_actor(staff),
        action_type="outreach_suggestion_create_project",
        target_type="project",
        target_id=project_id,
        metadata={"suggestion_id": int(suggestion_id), "kol_id": kol_id, "link_id": link.get("id"), "link_error": link_error},
    )
    updated = get_conn().execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    refreshed_claim = None
    if kol_id:
        refreshed_claim = get_conn().execute(
            "SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active' ORDER BY claimed_at DESC, id DESC LIMIT 1",
            (kol_id,),
        ).fetchone()
    return {
        "suggestion": dict(updated) if updated else {},
        "kol": kol,
        "claim": dict(refreshed_claim) if refreshed_claim else (claimed.get("claim") or {}),
        "project": project,
        "link": link,
        "short_url": short_url,
        "link_error": link_error,
        "adapter_status": "executed",
        "external_side_effect": True,
    }


def dismiss_suggestion(suggestion_id: int, reason: str = "", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    actor = _actor(staff)
    get_conn().execute("UPDATE vkpi_outreach_suggestions SET status='dismissed', dismissed_by_staff_id=?, dismissed_at=?, dismissed_reason=? WHERE id=?", (actor or None, _utcnow(), str(reason or ""), int(suggestion_id)))
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_outreach_suggestions WHERE id=?", (int(suggestion_id),)).fetchone()
    return {"suggestion": dict(row) if row else {}}
