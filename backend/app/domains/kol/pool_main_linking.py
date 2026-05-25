"""Main KOL linking helpers for vkpi_kol_pool rows."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol.pool_common import (
    _candidate_score,
    _clear_kol_pool_read_cache,
    _ensure_main_kol_schema,
    _first_present,
    _int_or_none,
    _json,
    _loads,
    _platform,
    _safe_like,
    _table_columns,
    _utcnow,
)
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def main_candidates(kol_pool_id: int, *, limit: int = 5) -> dict[str, Any]:
    """Find likely kols-table matches for a pool candidate."""

    ensure_vkpi_product_industry_schema()
    _ensure_main_kol_schema()
    conn = get_conn()
    pool_row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not pool_row:
        raise LookupError("kol pool item not found")
    item = dict(pool_row)
    columns = _table_columns(conn, "kols")
    if not columns:
        return {"kol_pool_id": int(kol_pool_id), "item": item, "candidates": []}

    select_columns = [
        col
        for col in (
            "id",
            "channel_name",
            "channel_url",
            "platform",
            "follower_count",
            "avg_views",
            "contact_status",
            "avatar_url",
            "profile_url",
            "created_at",
            "updated_at",
        )
        if col in columns
    ]
    if "id" not in select_columns:
        return {"kol_pool_id": int(kol_pool_id), "item": item, "candidates": []}

    platform = _platform(item.get("platform") or "")
    handle = str(item.get("handle") or "").strip().lower().lstrip("@")
    display = str(item.get("display_name") or "").strip().lower()
    profile_url = str(item.get("profile_url") or "").strip().lower().rstrip("/")
    clauses: list[str] = []
    params: list[Any] = []
    if "platform" in columns and platform:
        clauses.append("LOWER(COALESCE(platform, ''))=?")
        params.append(platform)
    match_parts: list[str] = []
    if profile_url:
        if "channel_url" in columns:
            match_parts.append("LOWER(RTRIM(COALESCE(channel_url, ''), '/'))=?")
            params.append(profile_url)
        if "profile_url" in columns:
            match_parts.append("LOWER(RTRIM(COALESCE(profile_url, ''), '/'))=?")
            params.append(profile_url)
    if handle:
        if "channel_name" in columns:
            match_parts.append("LOWER(COALESCE(channel_name, '')) LIKE ?")
            params.append(_safe_like(handle))
        if "channel_url" in columns:
            match_parts.append("LOWER(COALESCE(channel_url, '')) LIKE ?")
            params.append(_safe_like(handle))
        if "profile_url" in columns:
            match_parts.append("LOWER(COALESCE(profile_url, '')) LIKE ?")
            params.append(_safe_like(handle))
    if display and "channel_name" in columns:
        match_parts.append("LOWER(COALESCE(channel_name, '')) LIKE ?")
        params.append(_safe_like(display))
    if match_parts:
        clauses.append("(" + " OR ".join(match_parts) + ")")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"SELECT {', '.join(select_columns)} FROM kols {where} ORDER BY id DESC LIMIT ?",
        (*params, max(1, min(50, int(limit or 5) * 5))),
    ).fetchall()
    candidates = [dict(row) for row in rows]
    for candidate in candidates:
        candidate["match_score"] = _candidate_score(item, candidate)
        candidate["match_reason"] = _main_candidate_reason(item, candidate)
    candidates.sort(key=lambda row: int(row.get("match_score") or 0), reverse=True)
    return {
        "kol_pool_id": int(kol_pool_id),
        "item": item,
        "candidates": candidates[: max(1, min(20, int(limit or 5)))],
    }


def _main_candidate_reason(pool_item: dict[str, Any], candidate: dict[str, Any]) -> str:
    reasons: list[str] = []
    if _platform(pool_item.get("platform")) == _platform(candidate.get("platform")):
        reasons.append("same platform")
    handle = str(pool_item.get("handle") or "").strip().lower().lstrip("@")
    candidate_name = str(candidate.get("channel_name") or "").lower()
    if handle and handle in candidate_name:
        reasons.append("handle in name")
    profile_url = str(pool_item.get("profile_url") or "").strip().lower().rstrip("/")
    candidate_url = str(candidate.get("channel_url") or candidate.get("profile_url") or "").strip().lower().rstrip("/")
    if profile_url and profile_url == candidate_url:
        reasons.append("same profile url")
    return ", ".join(reasons) or "weak text match"


def promote_to_main(
    kol_pool_id: int,
    *,
    staff: dict[str, Any] | None = None,
    mode: str = "match_or_create",
) -> dict[str, Any]:
    """Link a pool row to kols, creating the main-row when no good match exists."""

    ensure_vkpi_product_industry_schema()
    _ensure_main_kol_schema()
    conn = get_conn()
    pool_row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not pool_row:
        raise LookupError("kol pool item not found")
    item = dict(pool_row)
    linked_id = _int_or_none(item.get("linked_main_kol_id"))
    if linked_id:
        main_row = _get_main_kol(linked_id)
        return {
            "linked": True,
            "mode": "already_linked",
            "kol_pool_id": int(kol_pool_id),
            "main_kol_id": linked_id,
            "item": item,
            "main_kol": main_row,
            "candidates": [main_row] if main_row else [],
        }

    candidates = main_candidates(kol_pool_id, limit=5).get("candidates") or []
    best = candidates[0] if candidates and int(candidates[0].get("match_score") or 0) >= 80 else None
    if best:
        main_kol_id = int(best["id"])
        action = "matched"
    else:
        if mode == "match_only":
            return {
                "linked": False,
                "mode": "no_match",
                "kol_pool_id": int(kol_pool_id),
                "main_kol_id": None,
                "item": item,
                "candidates": candidates,
            }
        main_kol_id = _create_main_kol_from_pool(conn, item, staff=staff)
        action = "created"

    now = _utcnow()
    conn.execute(
        "UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?",
        (main_kol_id, now, int(kol_pool_id)),
    )
    conn.commit()
    _clear_kol_pool_read_cache()
    updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return {
        "linked": True,
        "mode": action,
        "kol_pool_id": int(kol_pool_id),
        "main_kol_id": int(main_kol_id),
        "item": dict(updated) if updated else item,
        "main_kol": _get_main_kol(main_kol_id),
        "candidates": candidates,
    }


def _get_main_kol(kol_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
    return dict(row) if row else {}


def _create_main_kol_from_pool(conn: Any, item: dict[str, Any], *, staff: dict[str, Any] | None = None) -> int:
    columns = _table_columns(conn, "kols")
    if not columns:
        raise RuntimeError("kols table is not available")
    now = _utcnow()
    actor = resolve_staff_id(staff) or None
    display_name = str(item.get("display_name") or item.get("handle") or "KOL").strip() or "KOL"
    profile_url = str(item.get("profile_url") or "")
    raw = _loads(item.get("raw_platform_data"), {}) or {}
    values: dict[str, Any] = {
        "channel_name": display_name,
        "channel_url": profile_url,
        "platform": _platform(item.get("platform") or "other"),
        "country": "",
        "niche": str(_first_present(item.get("primary_topic"), item.get("content_style"), "")),
        "project_name": "",
        "owner_name": "",
        "media_name": display_name,
        "duplicate_flag": "",
        "scale_tier": "",
        "content_type": str(item.get("content_style") or ""),
        "approval_note": "",
        "channel_tags": str(item.get("primary_topic") or ""),
        "affiliate_id": "",
        "affiliate_link": "",
        "discount_code": "",
        "amazon_link": "",
        "short_link": "",
        "primary_category": str(item.get("primary_topic") or ""),
        "promoted_product": "",
        "follower_count": int(_int_or_none(item.get("followers")) or 0),
        "avg_views": int(_int_or_none(item.get("avg_views")) or 0),
        "contact_email": str(item.get("email") or ""),
        "contact_phone": "",
        "contact_status": "candidate",
        "notes": f"Created from KOL Pool #{item.get('id')}: {item.get('platform')}/{item.get('handle')} source={item.get('source_type') or ''} ref={item.get('source_ref') or ''}",
        "assigned_staff_id": actor,
        "created_by_staff_id": actor,
        "created_at": now,
        "updated_at": now,
        "avatar_url": str(item.get("avatar_url") or ""),
        "profile_url": profile_url,
        "contact_links_json": _json([]),
        "contact_raw_json": _json({"kol_pool_id": item.get("id"), "handle": item.get("handle"), "raw_platform_data": raw}),
    }
    insert_columns = [column for column in values if column in columns]
    placeholders = ",".join(["?"] * len(insert_columns))
    conn.execute(
        f"INSERT INTO kols ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    row = conn.execute(
        """
        SELECT id FROM kols
        WHERE platform=? AND channel_name=? AND created_at=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (values["platform"], values["channel_name"], now),
    ).fetchone()
    if not row:
        raise RuntimeError("failed to create main kol")
    return int(row["id"])
