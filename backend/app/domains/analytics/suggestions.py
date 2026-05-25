"""Outreach suggestion ranking and KOL-pool bridge helpers."""
from __future__ import annotations

import math
import secrets
from typing import Any

from app.db.connection import get_conn
from app.domains.analytics.common import (
    _content_intelligence,
    _db_bool,
    _float,
    _int,
    _is_official_account,
    _json,
    _loads_json,
    _platform_variants,
    _utcnow,
)
from app.domains.analytics.monitor import list_suggestions
from app.services.vkpi.schema import ensure_vkpi_schema
from app.domains.analytics.schema import ensure_vkpi_analytics_schema


def _kol_pool_bridge_score(row: dict[str, Any]) -> float:
    fit = _float(row.get("viltrox_fit_score"), 0.0)
    if fit > 0:
        return round(fit, 2)
    followers = max(0, _int(row.get("followers")))
    views = max(0, _int(row.get("avg_views")))
    engagement = _float(row.get("engagement_rate"), 0.0)
    return round(min(95.0, math.log10(max(10, followers)) * 7 + math.log10(max(10, views)) * 5 + min(30.0, engagement)), 2)


def _bridge_kol_pool_to_suggestions(limit: int = 100, product_sku: str = "") -> dict[str, Any]:
    """Seed Daily Top100 from KOL Pool only when the real suggestions table is empty.

    This is intentionally labelled as a bridge source. KOL Pool may include a
    partial imported list, so the generated suggestions are not presented as a
    full-market crawl.
    """
    ensure_vkpi_analytics_schema()
    try:
        from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

        ensure_vkpi_product_industry_schema()
    except Exception as exc:
        return {"seeded_count": 0, "candidate_source": "none", "message": f"kol_pool_unavailable: {str(exc)[:160]}"}

    safe_limit = max(1, min(100, int(limit or 100)))
    source_sku = str(product_sku or "kol_pool").strip() or "kol_pool"
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_pool
        WHERE COALESCE(handle, '') <> ''
          AND linked_main_kol_id IS NULL
        ORDER BY
          CASE COALESCE(sync_status, '')
            WHEN 'synced' THEN 0
            WHEN 'imported' THEN 1
            ELSE 2
          END,
          COALESCE(viltrox_fit_score, 0) DESC,
          COALESCE(followers, 0) DESC,
          id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    seeded: list[dict[str, Any]] = []
    now = _utcnow()
    for raw in rows:
        item = dict(raw)
        platform = str(item.get("platform") or "").strip().lower()
        handle = str(item.get("handle") or "").strip()
        if not platform or not handle:
            continue
        score = _kol_pool_bridge_score(item)
        views = _int(item.get("avg_views"))
        raw_platform_data = _loads_json(item.get("raw_platform_data"), {}) or {}
        metadata = {
            "source": "kol_pool_bridge",
            "kol_pool_id": item.get("id"),
            "kol_pool_source_type": item.get("source_type"),
            "created_by_staff_id": item.get("created_by_staff_id"),
            "responsible_staff_id": raw_platform_data.get("responsible_staff_id") if isinstance(raw_platform_data, dict) else None,
            "owner_names": raw_platform_data.get("owner_names") if isinstance(raw_platform_data, dict) else [],
            "responsible_staff_match_status": raw_platform_data.get("responsible_staff_match_status") if isinstance(raw_platform_data, dict) else "",
            "sync_status": item.get("sync_status"),
            "note": "KOL Pool bridge; imported lists are partial and do not represent the full market.",
        }
        conn.execute(
            """
            INSERT INTO vkpi_outreach_suggestions
                (suggestion_uid, source_run_id, source_product_sku, detected_at, platform, handle,
                 channel_name, follower_count, engagement_rate, country_code, avatar_url, profile_url,
                 source_video_url, source_video_title, source_view_count, source_like_count,
                 existing_kol_id, worked_before, mention_count, is_viral, priority, score, status,
                 metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, handle, source_product_sku) DO NOTHING
            """,
            (
                f"sug-pool-{secrets.token_hex(8)}",
                None,
                source_sku,
                now,
                platform,
                handle,
                str(item.get("display_name") or handle),
                item.get("followers"),
                item.get("engagement_rate"),
                str(item.get("country") or ""),
                str(item.get("avatar_url") or ""),
                str(item.get("profile_url") or ""),
                "",
                str(item.get("bio") or item.get("display_name") or handle),
                views,
                _int(item.get("avg_likes")),
                _int(item.get("linked_main_kol_id")) or None,
                _db_bool(False),
                1,
                _db_bool(views >= 100000),
                5 if score >= 70 else 2,
                score,
                "new",
                _json(metadata),
            ),
        )
        row = conn.execute(
            "SELECT * FROM vkpi_outreach_suggestions WHERE platform=? AND handle=? AND source_product_sku=?",
            (platform, handle, source_sku),
        ).fetchone()
        if row and str(row["status"] or "") == "new":
            seeded.append(dict(row))
    conn.commit()
    return {"seeded_count": len(seeded), "candidate_source": "kol_pool_bridge" if seeded else "none", "items": seeded}


def _find_matching_kol(row: dict[str, Any]) -> dict[str, Any] | None:
    ensure_vkpi_schema()
    explicit_id = _int(row.get("existing_kol_id"))
    conn = get_conn()
    if explicit_id:
        found = conn.execute("SELECT * FROM kols WHERE id=?", (explicit_id,)).fetchone()
        if found:
            return dict(found)
    handle = str(row.get("handle") or row.get("channel_name") or "").strip().lower()
    if not handle:
        return None
    variants = _platform_variants(str(row.get("platform") or ""))
    if not variants:
        variants = [str(row.get("platform") or "").strip().lower()]
    placeholders = ",".join("?" for _ in variants)
    like_handle = f"%/{handle}%"
    found = conn.execute(
        f"""
        SELECT *
        FROM kols
        WHERE lower(platform) IN ({placeholders})
          AND (
            lower(channel_name)=?
            OR lower(channel_url) LIKE ?
            OR lower(profile_url) LIKE ?
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (*variants, handle, like_handle, like_handle),
    ).fetchone()
    return dict(found) if found else None


def _has_contact_history(row: dict[str, Any], kol: dict[str, Any] | None) -> bool:
    if _is_official_account(row):
        return True
    if not kol:
        return False
    conn = get_conn()
    kol_id = _int(kol.get("id"))
    claims = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE kol_id=?", (kol_id,)).fetchone()
    if _int(claims["n"] if claims else 0) > 0:
        return True
    projects = conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE kol_id=?", (kol_id,)).fetchone()
    if _int(projects["n"] if projects else 0) > 0:
        return True
    status = str(kol.get("contact_status") or "").strip().lower()
    if status and status not in {"cold", "new", "not_contacted", "not-contacted", "uncontacted", "待联系"}:
        return True
    if _int(kol.get("assigned_staff_id")) > 0:
        return True
    return False


def rank_uncontacted_suggestions(limit: int = 100, product_sku: str = "") -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_analytics_schema()
    base_rows = list_suggestions(status="new", limit=max(100, min(1000, int(limit or 100) * 5)), product_sku=product_sku).get("suggestions") or []
    bridge_result: dict[str, Any] = {"seeded_count": 0, "candidate_source": "outreach_suggestions" if base_rows else "none"}
    if not base_rows:
        bridge_result = _bridge_kol_pool_to_suggestions(limit=max(1, min(100, int(limit or 100))), product_sku=product_sku)
        base_rows = list_suggestions(status="new", limit=max(100, min(1000, int(limit or 100) * 5)), product_sku=product_sku or "kol_pool").get("suggestions") or []
    ranked: list[dict[str, Any]] = []
    for base in base_rows:
        row = dict(base)
        kol = _find_matching_kol(row)
        if _has_contact_history(row, kol):
            continue
        intel = _content_intelligence(row)
        quality_score = round(float(row.get("score") or 0) + float(intel.get("score_bonus") or 0), 2)
        if _int(row.get("source_view_count")) >= 100000:
            quality_score += 8
        row["quality_score"] = round(quality_score, 2)
        row["relevance_reason"] = intel["relevance_reason"]
        row["buyer_profile"] = intel["buyer_profile"]
        row["viewer_profile"] = intel["viewer_profile"]
        row["content_angle"] = intel["content_angle"]
        row["matched_competitors"] = intel["matched_competitors"]
        row["matched_intents"] = intel["matched_intents"]
        row["matched_kol_id"] = _int(kol.get("id")) if kol else None
        ranked.append(row)
    ranked.sort(key=lambda item: (float(item.get("quality_score") or 0), _int(item.get("source_view_count")), str(item.get("detected_at") or "")), reverse=True)
    return {
        "items": ranked[: max(1, min(100, int(limit or 100)))],
        "total_candidates": len(base_rows),
        "uncontacted_count": len(ranked),
        "candidate_source": "outreach_suggestions" if bridge_result.get("candidate_source") == "outreach_suggestions" else bridge_result.get("candidate_source", "none"),
        "bridge_seeded_count": _int(bridge_result.get("seeded_count")),
    }
