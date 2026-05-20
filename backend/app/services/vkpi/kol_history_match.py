"""Historical KOL pool matching for live discovery results."""
from __future__ import annotations

import json
import re
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _table_exists(conn, table: str) -> bool:
    try:
        if is_postgres_runtime():
            row = conn.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name = ?
                LIMIT 1
                """,
                (table,),
            ).fetchone()
            return bool(row)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def normalize_history_handle(value: Any) -> str:
    raw = _text(value).lower()
    if not raw:
        return ""
    raw = raw.replace("https://", "").replace("http://", "").replace("www.", "")
    raw = raw.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = [part for part in raw.split("/") if part]
    if parts:
        if "@" in parts[-1] or parts[-1] not in {"instagram.com", "youtube.com", "tiktok.com", "x.com", "twitter.com"}:
            raw = parts[-1]
    raw = raw.lstrip("@")
    return re.sub(r"[^a-z0-9._-]+", "", raw)


def _platform(value: Any) -> str:
    raw = _text(value).lower()
    aliases = {
        "ig": "instagram",
        "instagram": "instagram",
        "youtube": "youtube",
        "yt": "youtube",
        "tiktok": "tiktok",
        "tik tok": "tiktok",
        "x": "x",
        "twitter": "x",
        "facebook": "facebook",
        "reddit": "reddit",
    }
    return aliases.get(raw, raw)


def _known_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text and text.lower() != "unknown creator":
            return text
    return ""


def _candidate_handles(item: dict[str, Any]) -> list[str]:
    values = [
        item.get("handle"),
        item.get("username"),
        item.get("ownerUsername"),
        item.get("channel_handle"),
        item.get("channel_name") if str(item.get("channel_name") or "").strip().startswith("@") else "",
        item.get("profile_url"),
        item.get("channel_url"),
    ]
    handles: list[str] = []
    for value in values:
        handle = normalize_history_handle(value)
        if handle and handle not in handles:
            handles.append(handle)
    return handles


def _candidate_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for value in (
        item.get("channel_name"),
        item.get("display_name"),
        item.get("media_name"),
        item.get("creator_name"),
        item.get("ownerFullName"),
        item.get("owner_name"),
    ):
        text = _known_text(value)
        if text and len(text) >= 3 and text.lower() != _text(item.get("search_query")).lower() and text not in names:
            names.append(text)
    return names


def _pool_raw(row: dict[str, Any]) -> dict[str, Any]:
    return _json_loads(row.get("raw_platform_data"), {}) or {}


def _cooperation_summary(conn, pool_id: int, raw: dict[str, Any]) -> dict[str, Any]:
    evidence = raw.get("evidence_summary") if isinstance(raw.get("evidence_summary"), dict) else {}
    cooperation_count = _int(evidence.get("cooperation_rows"))
    profile_rows = _int(evidence.get("kol_profile_rows"))
    risk_rows = _int(evidence.get("risk_rows"))
    evidence_count = _int(evidence.get("evidence_count"))
    recent: list[dict[str, Any]] = []
    if _table_exists(conn, "vkpi_legacy_cooperations_staging"):
        try:
            count_row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM vkpi_legacy_cooperations_staging
                WHERE matched_kol_pool_id=?
                """,
                (int(pool_id),),
            ).fetchone()
            cooperation_count = max(cooperation_count, _int(count_row["n"] if count_row else 0))
            rows = conn.execute(
                """
                SELECT product, project, status, cooperation_date, content_link
                FROM vkpi_legacy_cooperations_staging
                WHERE matched_kol_pool_id=?
                ORDER BY (cooperation_date IS NULL) ASC, cooperation_date DESC, id DESC
                LIMIT 5
                """,
                (int(pool_id),),
            ).fetchall()
            recent = [dict(row) for row in rows]
        except Exception:
            recent = []
    return {
        "cooperation_count": cooperation_count,
        "profile_rows": profile_rows,
        "risk_rows": risk_rows,
        "evidence_count": evidence_count,
        "recent_cooperations": recent,
    }


def _history_payload(conn, row: dict[str, Any], *, match_type: str, confidence: float) -> dict[str, Any]:
    raw = _pool_raw(row)
    summary = _cooperation_summary(conn, int(row.get("id") or 0), raw)
    return {
        "matched": True,
        "source": "vkpi_kol_pool",
        "match_type": match_type,
        "match_confidence": confidence,
        "kol_pool_id": _int(row.get("id")),
        "linked_main_kol_id": _int(row.get("linked_main_kol_id")),
        "platform": _platform(row.get("platform")),
        "handle": _text(row.get("handle")),
        "display_name": _text(row.get("display_name")),
        "profile_url": _text(row.get("profile_url")),
        "avatar_url": _text(row.get("avatar_url")),
        "followers": _int(row.get("followers")),
        "avg_views": _int(row.get("avg_views")),
        "source_type": _text(row.get("source_type")),
        "source_ref": _text(row.get("source_ref")),
        "sync_status": _text(row.get("sync_status")),
        **summary,
    }


def _fetch_pool_by_handles(conn, platform: str, handles: list[str]) -> tuple[dict[str, Any] | None, str]:
    if not handles:
        return None, ""
    placeholders = ",".join(["?"] * len(handles))
    params: list[Any] = []
    platform_clause = ""
    if platform:
        platform_clause = "AND lower(platform)=?"
        params.append(platform)
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_pool
        WHERE replace(lower(COALESCE(handle, '')), '@', '') IN ({placeholders})
          {platform_clause}
        ORDER BY
          CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
          id DESC
        LIMIT 1
        """,
        (*handles, *params),
    ).fetchall()
    if rows:
        return dict(rows[0]), "handle_exact"
    if _table_exists(conn, "vkpi_kol_pool_aliases"):
        alias_params: list[Any] = []
        alias_platform_clause = ""
        if platform:
            alias_platform_clause = "AND lower(a.platform)=?"
            alias_params.append(platform)
        alias_rows = conn.execute(
            f"""
            SELECT p.*
            FROM vkpi_kol_pool_aliases a
            JOIN vkpi_kol_pool p ON p.id=a.kol_pool_id
            WHERE replace(lower(COALESCE(a.handle, '')), '@', '') IN ({placeholders})
              {alias_platform_clause}
            ORDER BY COALESCE(a.confidence, 0) DESC, p.id DESC
            LIMIT 1
            """,
            (*handles, *alias_params),
        ).fetchall()
        if alias_rows:
            return dict(alias_rows[0]), "alias_exact"
    return None, ""


def _fetch_pool_by_names(conn, platform: str, names: list[str]) -> tuple[dict[str, Any] | None, str]:
    for name in names:
        params: list[Any] = [name.lower()]
        platform_clause = ""
        if platform:
            platform_clause = "AND lower(platform)=?"
            params.append(platform)
        row = conn.execute(
            f"""
            SELECT *
            FROM vkpi_kol_pool
            WHERE lower(COALESCE(display_name, ''))=?
              {platform_clause}
            ORDER BY
              CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
              id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row:
            return dict(row), "display_name_exact"
    return None, ""


def find_history_match(item: dict[str, Any], *, platform: str = "") -> dict[str, Any] | None:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    normalized_platform = _platform(item.get("platform") or platform)
    handles = _candidate_handles(item)
    row, match_type = _fetch_pool_by_handles(conn, normalized_platform, handles)
    confidence = 0.96
    if not row:
        row, match_type = _fetch_pool_by_names(conn, normalized_platform, _candidate_names(item))
        confidence = 0.78
    if not row:
        return None
    return _history_payload(conn, row, match_type=match_type, confidence=confidence)


def annotate_platform_items(items: list[dict[str, Any]], *, platform: str = "") -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for item in items:
        row = dict(item or {})
        match = find_history_match(row, platform=platform)
        if match:
            row["historical_match"] = match
            row["history_kol_pool_id"] = match.get("kol_pool_id")
            row["history_linked_main_kol_id"] = match.get("linked_main_kol_id")
            row["history_cooperation_count"] = match.get("cooperation_count")
            row["history_source_type"] = match.get("source_type")
            row["history_match_confidence"] = match.get("match_confidence")
            if not _text(row.get("avatar_url")) and match.get("avatar_url"):
                row["avatar_url"] = match.get("avatar_url")
            if not _int(row.get("follower_count")) and match.get("followers"):
                row["follower_count"] = match.get("followers")
        annotated.append(row)
    return annotated


def _tokens(query: str, parsed: dict[str, Any]) -> list[str]:
    values = [str(item).lower() for item in parsed.get("keywords") or [] if str(item or "").strip()]
    values.extend(re.findall(r"[a-z0-9_.-]{3,}", str(query or "").lower()))
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped[:8]


def search_pool_for_natural(query: str, parsed: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    platform = _platform(parsed.get("platform"))
    tokens = _tokens(query, parsed)
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("lower(platform)=?")
        params.append(platform)
    if tokens:
        parts = []
        for token in tokens:
            parts.append("(lower(handle) LIKE ? OR lower(display_name) LIKE ? OR lower(bio) LIKE ? OR lower(profile_url) LIKE ? OR lower(raw_platform_data) LIKE ?)")
            like = f"%{token}%"
            params.extend([like, like, like, like, like])
        where.append("(" + " OR ".join(parts) + ")")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_pool
        {clause}
        ORDER BY
          CASE WHEN source_type='legacy_excel_p2d' THEN 0 ELSE 1 END,
          COALESCE(viltrox_fit_score, 0) DESC,
          COALESCE(followers, 0) DESC,
          id DESC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        history = _history_payload(conn, data, match_type="natural_pool_search", confidence=0.7)
        if parsed.get("requires_collaboration") and not _int(history.get("cooperation_count")):
            continue
        base_score = _int(data.get("viltrox_fit_score"), 42)
        score = min(100, max(base_score, 42) + min(24, _int(history.get("cooperation_count")) * 6))
        reasons = []
        if history.get("cooperation_count"):
            reasons.append(f"历史合作 {history.get('cooperation_count')} 条")
        if data.get("source_type"):
            reasons.append(f"来源 {data.get('source_type')}")
        results.append(
            {
                "id": f"pool:{data.get('id')}",
                "kol_pool_id": data.get("id"),
                "source_kind": "kol_pool",
                "platform": data.get("platform"),
                "handle": data.get("handle"),
                "channel_name": data.get("display_name") or data.get("handle"),
                "display_name": data.get("display_name") or data.get("handle"),
                "media_name": data.get("display_name") or data.get("handle"),
                "profile_url": data.get("profile_url"),
                "avatar_url": data.get("avatar_url"),
                "follower_count": data.get("followers"),
                "snapshot_follower_count": data.get("followers"),
                "content_count": data.get("posts_count"),
                "avg_views": data.get("avg_views"),
                "country": data.get("country"),
                "primary_topic": data.get("primary_topic") or data.get("content_style") or "",
                "score": score,
                "natural_match_score": score,
                "natural_match_reasons": reasons,
                "project_count": history.get("cooperation_count"),
                "cooperation_count": history.get("cooperation_count"),
                "history_cooperation_count": history.get("cooperation_count"),
                "history_kol_pool_id": history.get("kol_pool_id"),
                "history_source_type": history.get("source_type"),
                "history_match_confidence": history.get("match_confidence"),
                "sync_status": data.get("sync_status"),
                "source_type": data.get("source_type"),
                "historical_match": history,
            }
        )
    return results[:limit]
