"""Persistence primitives shared by KOL API and durable workers.

This module deliberately lives below the HTTP layer so background workers do
not need to import router modules to persist platform-search results or audit
activity.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db.connection import get_conn, is_postgres_runtime


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_id(conn, cur, table: str) -> int:
    lastrowid = getattr(cur, "lastrowid", None)
    if lastrowid not in (None, 0):
        return int(lastrowid)
    if is_postgres_runtime():
        row = conn.execute(
            "SELECT currval(pg_get_serial_sequence(?, 'id')) AS id",
            (table,),
        ).fetchone()
        if row:
            return int(row["id"])
    raise RuntimeError(f"Could not resolve inserted id for {table}")


def _int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _staff_identity(staff: dict) -> dict[str, object]:
    return {
        "staff_id": int(staff.get("id") or 0),
        "user_id": int(staff.get("user_id") or 0),
        "staff_name": str(
            staff.get("name")
            or staff.get("email")
            or f"staff#{staff.get('id') or staff.get('user_id') or 0}"
        ),
    }


def _log_activity(
    conn,
    staff: dict,
    action_type: str,
    *,
    target_type: str = "",
    target_id: int | None = None,
    query: str = "",
    platform: str = "",
    market: str = "",
    api_provider: str = "",
    api_calls: int = 0,
    result_count: int = 0,
    metadata: dict | None = None,
) -> None:
    ident = _staff_identity(staff)
    conn.execute(
        """
        INSERT INTO kol_activity_log
            (staff_id, user_id, staff_name, action_type, target_type, target_id, query,
             platform, market, api_provider, api_calls, result_count, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ident["staff_id"],
            ident["user_id"],
            ident["staff_name"],
            action_type,
            target_type,
            target_id,
            query,
            platform,
            market,
            api_provider,
            int(api_calls or 0),
            int(result_count or 0),
            json.dumps(metadata or {}, ensure_ascii=False),
            _now(),
        ),
    )


def _log_activity_commit(
    staff: dict,
    action_type: str,
    *,
    target_type: str = "",
    target_id: int | None = None,
    query: str = "",
    platform: str = "",
    market: str = "",
    api_provider: str = "",
    api_calls: int = 0,
    result_count: int = 0,
    metadata: dict | None = None,
) -> None:
    conn = get_conn()
    _log_activity(
        conn,
        staff,
        action_type,
        target_type=target_type,
        target_id=target_id,
        query=query,
        platform=platform,
        market=market,
        api_provider=api_provider,
        api_calls=api_calls,
        result_count=result_count,
        metadata=metadata,
    )
    conn.commit()


def _known_candidate_text(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "unknown creator":
            return text
    return ""


def _candidate_payload_from_item(item: dict, body: dict) -> dict:
    channel_name = _known_candidate_text(
        item.get("channel_name"),
        item.get("display_name"),
        item.get("ownerFullName"),
        item.get("owner_name"),
        item.get("handle"),
        item.get("username"),
        body.get("query"),
    ) or "Unknown creator"
    handle = _known_candidate_text(
        item.get("handle"),
        item.get("username"),
        item.get("ownerUsername"),
        channel_name,
    )
    return {
        "platform": item.get("platform") or body.get("platform") or "",
        "channel_name": channel_name,
        "channel_url": item.get("channel_url") or "",
        "handle": handle,
        "country": body.get("market") or item.get("market") or "",
        "niche": body.get("niche") or "",
        "source_url": item.get("source_url") or "",
        "sample_title": item.get("sample_title") or "",
        "follower_count": _int(item.get("follower_count")),
        "avg_views": _int(item.get("avg_views") or item.get("views")),
        "contact_email": "",
        "status": "new",
        "search_query": body.get("query") or item.get("search_query") or "",
        "market": body.get("market") or item.get("market") or "",
        "notes": "",
    }


def _upsert_candidate(conn, payload: dict) -> int:
    source_url = str(payload.get("source_url") or "").strip()
    if source_url:
        existing = conn.execute(
            "SELECT id FROM kol_candidates WHERE source_url = ?",
            (source_url,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE kol_candidates
                SET avg_views = ?, sample_title = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _int(payload.get("avg_views")),
                    payload.get("sample_title", ""),
                    _now(),
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO kol_candidates
            (platform, channel_name, channel_url, handle, country, niche, source_url, sample_title,
             follower_count, avg_views, contact_email, status, search_query, market, notes, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("platform", ""),
            payload.get("channel_name", "Unknown creator"),
            payload.get("channel_url", ""),
            payload.get("handle", ""),
            payload.get("country", ""),
            payload.get("niche", ""),
            payload.get("source_url", ""),
            payload.get("sample_title", ""),
            _int(payload.get("follower_count")),
            _int(payload.get("avg_views")),
            payload.get("contact_email", ""),
            payload.get("status", "new"),
            payload.get("search_query", ""),
            payload.get("market", ""),
            payload.get("notes", ""),
            _now(),
            _now(),
        ),
    )
    return _insert_id(conn, cur, "kol_candidates")


def _persist_search_candidates(
    items: list[dict],
    body: dict,
    platform: str,
    market: str,
) -> list[int]:
    conn = get_conn()
    candidate_ids = []
    request_body = {**body, "platform": platform, "market": market}
    for item in items:
        candidate_ids.append(
            _upsert_candidate(conn, _candidate_payload_from_item(item, request_body))
        )
    conn.commit()
    return candidate_ids


def _persist_platform_search_result(
    items: list[dict],
    body: dict,
    platform: str,
    market: str,
    *,
    staff: dict,
    query: str,
    api_provider: str,
) -> list[int]:
    """Persist candidates and their audit row as one atomic transaction."""
    conn = get_conn()
    candidate_ids: list[int] = []
    request_body = {**body, "platform": platform, "market": market}
    try:
        for item in items:
            candidate_ids.append(
                _upsert_candidate(
                    conn,
                    _candidate_payload_from_item(item, request_body),
                )
            )
        _log_activity(
            conn,
            staff,
            "platform_search",
            query=query,
            platform=platform,
            market=market,
            api_provider=api_provider,
            api_calls=1,
            result_count=len(items),
            metadata={
                "saved_candidates": len(candidate_ids),
                "history_matches": sum(
                    1 for item in items if item.get("historical_match")
                ),
                "niche": body.get("niche", ""),
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return candidate_ids
