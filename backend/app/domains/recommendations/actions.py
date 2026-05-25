"""Write-side bridges for Product Analysis recommendations."""
from __future__ import annotations

import os
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claims as kol_claims
from app.domains.kol import pool as kol_pool
from app.domains.recommendations import outcomes as outcome_collector
from app.services.vkpi._utils import json_dumps, json_loads, utcnow_iso
from app.domains.attribution import link_center
from app.domains import audit
from app.domains.access import scope
from app.domains.projects import workflow
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id


def _utcnow() -> str:
    return utcnow_iso()


def _json(value: Any) -> str:
    return json_dumps(value or {})


def _loads(value: Any, default: Any = None) -> Any:
    return json_loads(value, default)


def _recommendation_context(recommendation_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)).fetchone()
    if not row:
        raise LookupError("recommendation not found")
    rec = dict(row)
    pool: dict[str, Any] = {}
    if rec.get("kol_pool_id"):
        pool = kol_pool.get_item(int(rec["kol_pool_id"])).get("item") or {}
    launch: dict[str, Any] = {}
    if rec.get("launch_id"):
        launch_row = conn.execute("SELECT * FROM vkpi_product_launches WHERE id=?", (int(rec["launch_id"]),)).fetchone()
        launch = dict(launch_row) if launch_row else {}
    return rec, pool, launch


def _platform_profile_url(platform: str, handle: str, profile_url: str = "") -> str:
    if profile_url:
        return profile_url
    clean_platform = str(platform or "").strip().lower()
    clean_handle = str(handle or "").strip().lstrip("@")
    if not clean_handle:
        return ""
    if clean_platform in {"youtube", "yt"}:
        return f"https://www.youtube.com/@{clean_handle}"
    if clean_platform in {"instagram", "ig"}:
        return f"https://www.instagram.com/{clean_handle}/"
    if clean_platform in {"tiktok", "tt"}:
        return f"https://www.tiktok.com/@{clean_handle}"
    if clean_platform in {"x", "twitter"}:
        return f"https://x.com/{clean_handle}"
    if clean_platform == "reddit":
        return f"https://www.reddit.com/user/{clean_handle}/"
    if clean_platform in {"facebook", "fb"}:
        return f"https://www.facebook.com/{clean_handle}"
    return profile_url


def _active_claim(kol_id: int) -> dict[str, Any]:
    row = get_conn().execute(
        "SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active' ORDER BY claimed_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    ).fetchone()
    return dict(row) if row else {}


def _short_link_url(slug: str) -> str:
    base_url = str(os.environ.get("VKPI_SHORTLINK_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").strip()
    if base_url:
        return f"{base_url.rstrip('/')}/go/{slug}"
    return f"/go/{slug}"


def _shopify_destination(payload: dict[str, Any], launch: dict[str, Any]) -> tuple[str, str]:
    metadata = _loads(launch.get("metadata_json"), {}) or {}
    candidates = [
        ("payload", payload.get("destination_url") or payload.get("shopify_url")),
        ("launch", launch.get("shopify_url") or metadata.get("shopify_url") or metadata.get("destination_url")),
        ("env", os.environ.get("VKPI_DEFAULT_SHOPIFY_URL")),
        ("fallback", "https://www.viltrox.com/"),
    ]
    for source, url in candidates:
        clean_url = str(url or "").strip()
        if clean_url:
            return clean_url, source
    return "https://www.viltrox.com/", "fallback"


def _create_shopify_link_for_project(
    *,
    recommendation_id: int,
    rec: dict[str, Any],
    launch: dict[str, Any],
    kol: dict[str, Any],
    project: dict[str, Any],
    payload: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    project_id = int(project.get("id") or 0)
    if not project_id or payload.get("auto_create_link") is False:
        return {}, ""
    destination_url, destination_source = _shopify_destination(payload, launch)
    handle = str(rec.get("handle") or kol.get("channel_name") or "kol").strip().lstrip("@")
    product_sku = str(payload.get("product_sku") or launch.get("product_sku") or "")
    link = link_center.create_link(
        {
            "destination_url": destination_url,
            "link_type": "shopify",
            "platform": "shopify",
            "product_sku": product_sku,
            "campaign_name": project.get("project_uid") or project.get("project_name") or "",
            "kol_id": int(kol.get("id") or 0) or None,
            "project_id": project_id,
            "status": "live",
            "utm_source": str(rec.get("platform") or kol.get("platform") or "kol"),
            "utm_medium": "kol",
            "utm_campaign": product_sku or str(launch.get("name") or "vkpi"),
            "utm_content": handle,
            "metadata": {
                "source": "product_recommendation",
                "recommendation_id": int(recommendation_id),
                "launch_id": rec.get("launch_id"),
                "kol_pool_id": rec.get("kol_pool_id"),
                "generated_for": "shopify",
                "destination_source": destination_source,
            },
        },
        staff=staff,
    )
    short_url = _short_link_url(str(link.get("slug") or ""))
    get_conn().execute(
        "UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?",
        (short_url, _utcnow(), project_id),
    )
    get_conn().commit()
    project["shopify_link"] = short_url
    return link, short_url


def _update_kol_from_pool(kol_id: int, pool: dict[str, Any]) -> None:
    if not kol_id or not pool:
        return
    updates = {
        "avatar_url": pool.get("avatar_url"),
        "profile_url": pool.get("profile_url") or _platform_profile_url(str(pool.get("platform") or ""), str(pool.get("handle") or "")),
        "contact_email": pool.get("email"),
        "follower_count": pool.get("followers"),
        "avg_views": pool.get("avg_views"),
        "notes": pool.get("bio"),
        "contact_raw_json": pool.get("raw_platform_data"),
    }
    sets: list[str] = []
    params: list[Any] = []
    for column, value in updates.items():
        if value is None or value == "":
            continue
        sets.append(f"{column}=?")
        params.append(value)
    if not sets:
        return
    sets.append("updated_at=?")
    params.extend([_utcnow(), int(kol_id)])
    get_conn().execute(f"UPDATE kols SET {', '.join(sets)} WHERE id=?", params)


def _ensure_main_kol_for_recommendation(recommendation_id: int, *, staff: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    rec, pool, launch = _recommendation_context(recommendation_id)
    if rec.get("linked_main_kol_id"):
        row = get_conn().execute("SELECT * FROM kols WHERE id=?", (int(rec["linked_main_kol_id"]),)).fetchone()
        if row:
            return rec, pool, launch, dict(row)
    platform = str(pool.get("platform") or rec.get("platform") or "other")
    handle = str(pool.get("handle") or rec.get("handle") or "").strip().lstrip("@")
    if not handle:
        raise ValueError("recommendation has no handle")
    profile_url = _platform_profile_url(platform, handle, str(pool.get("profile_url") or ""))
    lookup_result = kol_claims.lookup(
        {
            "platform": platform,
            "handle_or_url": profile_url or handle,
            "url": profile_url,
            "channel_url": profile_url,
            "profile_url": profile_url,
            "email": pool.get("email") or "",
            "contact_email": pool.get("email") or "",
            "owner_name": pool.get("display_name") or rec.get("display_name") or handle,
            "avatar_url": pool.get("avatar_url") or "",
            "follower_count": pool.get("followers") or 0,
            "avg_views": pool.get("avg_views") or 0,
            "notes": pool.get("bio") or "",
            "contact_raw": _loads(pool.get("raw_platform_data"), {}),
            "create_if_missing": True,
        },
        staff=staff,
    )
    kol = lookup_result.get("kol") or {}
    kol_id = int(kol.get("id") or 0)
    if not kol_id:
        raise LookupError("kol could not be created")
    _update_kol_from_pool(kol_id, pool)
    get_conn().execute("UPDATE vkpi_kol_recommendations SET linked_main_kol_id=?, updated_at=? WHERE id=?", (kol_id, _utcnow(), int(recommendation_id)))
    if rec.get("kol_pool_id"):
        get_conn().execute("UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?", (kol_id, _utcnow(), int(rec["kol_pool_id"])))
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM kols WHERE id=?", (kol_id,)).fetchone()
    return rec, pool, launch, dict(row) if row else kol


def _claim_recommendation_kol(recommendation_id: int, *, staff: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    rec, pool, launch, kol = _ensure_main_kol_for_recommendation(recommendation_id, staff=staff)
    kol_id = int(kol.get("id") or 0)
    actor = resolve_staff_id(staff)
    active = _active_claim(kol_id)
    if active:
        claim_staff_id = int(active.get("staff_id") or 0)
        if actor and claim_staff_id not in {0, actor} and not scope.can_view_all(staff):
            raise ValueError("kol already claimed by another staff")
        claim = active
    else:
        claim = (kol_claims.claim(
            kol_id,
            {
                "metadata": {
                    "source": "product_recommendation",
                    "recommendation_id": int(recommendation_id),
                    "kol_pool_id": rec.get("kol_pool_id"),
                    "launch_id": rec.get("launch_id"),
                    "note": note,
                }
            },
            staff=staff,
        ).get("claim") or {})
    now = _utcnow()
    get_conn().execute("UPDATE vkpi_kol_recommendations SET linked_main_kol_id=?, status=?, updated_at=? WHERE id=?", (kol_id, "claimed", now, int(recommendation_id)))
    outcome_collector.record(int(recommendation_id), "claimed", context={"kol_id": kol_id, "claim_id": claim.get("id")})
    get_conn().commit()
    return {"recommendation": dict(get_conn().execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)).fetchone()), "kol": kol, "claim": claim, "pool": pool, "launch": launch}


def _create_project_from_recommendation(recommendation_id: int, payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    claimed = _claim_recommendation_kol(recommendation_id, staff=staff, note=str(payload.get("note") or ""))
    rec = claimed["recommendation"]
    launch = claimed.get("launch") or {}
    kol = claimed.get("kol") or {}
    product_name = str(payload.get("product_name") or launch.get("product_name") or launch.get("name") or "")
    product_sku = str(payload.get("product_sku") or launch.get("product_sku") or "")
    handle = str(rec.get("handle") or kol.get("channel_name") or "KOL")
    project_name = str(payload.get("project_name") or "").strip() or f"{product_name or 'Viltrox'} KOL - {handle}"
    project = workflow.create_project(
        {
            "project_name": project_name,
            "kol_id": int(kol.get("id") or 0),
            "product_sku": product_sku,
            "product_name": product_name,
            "platform": rec.get("platform") or kol.get("platform") or "",
            "stage": str(payload.get("stage") or "discovery"),
            "source_type": "product_recommendation",
            "note": str(payload.get("note") or f"Created from recommendation {recommendation_id}"),
            "metadata": {
                "source": "product_recommendation",
                "recommendation_id": int(recommendation_id),
                "launch_id": rec.get("launch_id"),
                "kol_pool_id": rec.get("kol_pool_id"),
            },
        },
        staff=staff,
    )
    project_id = int(project.get("id") or 0)
    if project_id:
        get_conn().execute(
            "UPDATE vkpi_kol_claims SET project_id=COALESCE(project_id, ?), updated_at=? WHERE kol_id=? AND status='active'",
            (project_id, _utcnow(), int(kol.get("id") or 0)),
        )
    link: dict[str, Any] = {}
    link_error = ""
    try:
        link, short_url = _create_shopify_link_for_project(
            recommendation_id=recommendation_id,
            rec=rec,
            launch=launch,
            kol=kol,
            project=project,
            payload=payload,
            staff=staff,
        )
        if short_url:
            project["shopify_link"] = short_url
    except Exception as exc:
        link_error = str(exc)
    now = _utcnow()
    get_conn().execute("UPDATE vkpi_kol_recommendations SET status=?, updated_at=? WHERE id=?", ("project_created", now, int(recommendation_id)))
    outcome_collector.record(int(recommendation_id), "project_created", context={"project_id": project_id, "kol_id": kol.get("id"), "link_id": link.get("id")})
    get_conn().execute(
        "INSERT INTO vkpi_recommendation_feedback (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json) VALUES (?,?,?,?,?,?)",
        (int(recommendation_id), "create_project", str(payload.get("note") or ""), resolve_staff_id(staff) or None, now, _json({"project_id": project_id, "kol_id": kol.get("id"), "link_id": link.get("id"), "link_error": link_error, **payload})),
    )
    get_conn().commit()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="recommendation_create_project", target_type="project", target_id=project_id, metadata={"recommendation_id": int(recommendation_id), "kol_id": kol.get("id"), "link_id": link.get("id"), "link_error": link_error})
    updated = get_conn().execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)).fetchone()
    return {"recommendation": dict(updated) if updated else {}, "kol": kol, "claim": claimed.get("claim") or {}, "project": project, "link": link, "link_error": link_error, "adapter_status": "executed", "external_side_effect": True}


def _record_action_feedback_once(
    recommendation_id: int,
    feedback_type: str,
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    note: str = "",
) -> bool:
    """Record a real operator action as feedback without duplicating repeat clicks."""

    clean_type = str(feedback_type or "").strip().lower()
    if not clean_type:
        return False
    existing = get_conn().execute(
        """
        SELECT id
        FROM vkpi_recommendation_feedback
        WHERE recommendation_id=? AND feedback_type=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(recommendation_id), clean_type),
    ).fetchone()
    if existing:
        return False
    get_conn().execute(
        """
        INSERT INTO vkpi_recommendation_feedback
            (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            int(recommendation_id),
            clean_type,
            note,
            resolve_staff_id(staff) or None,
            _utcnow(),
            _json({"source_action": clean_type, **(payload or {})}),
        ),
    )
    return True


def action_recommendation(recommendation_id: int, action: str, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    payload = payload or {}
    clean = str(action or "").strip().lower()
    _recommendation_context(recommendation_id)
    if clean == "claim":
        claimed = _claim_recommendation_kol(recommendation_id, staff=staff, note=str(payload.get("note") or ""))
        feedback_inserted = _record_action_feedback_once(
            recommendation_id,
            "claim",
            payload,
            staff=staff,
            note=str(payload.get("note") or ""),
        )
        get_conn().commit()
        audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="recommendation_claim", target_type="recommendation", target_id=recommendation_id, metadata={"kol_id": (claimed.get("kol") or {}).get("id"), "claim_id": (claimed.get("claim") or {}).get("id"), **payload})
        return {**claimed, "feedback_inserted": feedback_inserted, "adapter_status": "executed", "external_side_effect": True}
    if clean == "create_project":
        return _create_project_from_recommendation(recommendation_id, payload, staff=staff)

    status_map = {"shortlist": "shortlisted", "reject": "rejected", "feedback": "feedback"}
    if clean not in status_map:
        raise ValueError("unsupported recommendation action")
    now = _utcnow()
    if clean in {"shortlist", "reject"}:
        get_conn().execute("UPDATE vkpi_kol_recommendations SET status=?, updated_at=? WHERE id=?", (status_map[clean], now, int(recommendation_id)))
        outcome_collector.record(int(recommendation_id), "shortlisted" if clean == "shortlist" else "rejected", note=str(payload.get("reason") or ""), context=payload)
        feedback_inserted = _record_action_feedback_once(
            recommendation_id,
            clean,
            payload,
            staff=staff,
            note=str(payload.get("note") or payload.get("reason") or ""),
        )
    else:
        get_conn().execute(
            "INSERT INTO vkpi_recommendation_feedback (recommendation_id, feedback_type, note, created_by_staff_id, created_at, metadata_json) VALUES (?,?,?,?,?,?)",
            (int(recommendation_id), clean, str(payload.get("note") or payload.get("reason") or ""), resolve_staff_id(staff) or None, now, _json(payload)),
        )
        feedback_inserted = True
    get_conn().commit()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type=f"recommendation_{clean}", target_type="recommendation", target_id=recommendation_id, metadata=payload)
    updated = get_conn().execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)).fetchone()
    return {"recommendation": dict(updated) if updated else {}, "feedback_inserted": feedback_inserted, "adapter_status": "recorded", "external_side_effect": False}
