"""
db/repositories/viltrox_matrix.py — official Viltrox matrix roster + scan persistence
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.services.deepsight.constants import OFFICIAL_MATRIX


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any, default: Any) -> str:
    data = default if value is None else value
    return json.dumps(data, ensure_ascii=False)


def _loads_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _account_key(platform: str, handle: str) -> str:
    return f"{str(platform or '').strip().lower()}::{str(handle or '').strip().lower()}"


def _normalize_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@")


def _account_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "platform": str(row["platform"] or ""),
        "handle": str(row["handle"] or ""),
        "name": str(row["name"] or ""),
        "source_key": str(row["source_key"] or "official_matrix"),
        "is_active": bool(row["is_active"]),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _run_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "run_key": str(row["run_key"] or ""),
        "status": str(row["status"] or "completed"),
        "started_at": str(row["started_at"] or ""),
        "completed_at": str(row["completed_at"] or ""),
        "total_accounts": int(row["total_accounts"] or 0),
        "scanned_accounts": int(row["scanned_accounts"] or 0),
        "total_posts": int(row["total_posts"] or 0),
        "total_views": int(row["total_views"] or 0),
        "total_likes": int(row["total_likes"] or 0),
        "total_comments": int(row["total_comments"] or 0),
        "aggregate": _loads_json(row["aggregate_json"], {}),
        "error_message": str(row["error_message"] or ""),
        "created_at": str(row["created_at"] or ""),
    }


def sync_viltrox_official_accounts(reset: bool = False) -> list[dict[str, Any]]:
    conn = get_conn()
    now = _utcnow()
    official = [
        {
            "platform": str(item.get("platform") or "").strip().lower(),
            "handle": _normalize_handle(item.get("handle") or ""),
            "name": str(item.get("name") or item.get("handle") or "").strip(),
        }
        for item in OFFICIAL_MATRIX
        if str(item.get("platform") or "").strip() and _normalize_handle(item.get("handle") or "")
    ]
    official_keys = {_account_key(item["platform"], item["handle"]) for item in official}

    if reset:
        conn.execute("UPDATE viltrox_matrix_accounts SET is_active=0, updated_at=?", (now,))

    for item in official:
        conn.execute(
            """
            INSERT INTO viltrox_matrix_accounts (
                platform, handle, name, source_key, is_active, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(platform, handle) DO UPDATE SET
                name=excluded.name,
                source_key=excluded.source_key,
                is_active=excluded.is_active,
                updated_at=excluded.updated_at
            """,
            (
                item["platform"],
                item["handle"],
                item["name"],
                "official_matrix",
                1,
                now,
                now,
            ),
        )

    rows = conn.execute(
        "SELECT id, platform, handle FROM viltrox_matrix_accounts WHERE source_key='official_matrix'"
    ).fetchall()
    for row in rows:
        key = _account_key(row["platform"], row["handle"])
        if key not in official_keys:
            conn.execute(
                "UPDATE viltrox_matrix_accounts SET is_active=0, updated_at=? WHERE id=?",
                (now, int(row["id"])),
            )

    conn.commit()
    return list_viltrox_official_accounts(active_only=True)


def list_viltrox_official_accounts(active_only: bool = True) -> list[dict[str, Any]]:
    conn = get_conn()
    sql = """
        SELECT id, platform, handle, name, source_key, is_active, created_at, updated_at
        FROM viltrox_matrix_accounts
        WHERE source_key='official_matrix'
    """
    params: list[Any] = []
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY platform ASC, name COLLATE NOCASE ASC, handle COLLATE NOCASE ASC"
    rows = conn.execute(sql, params).fetchall()
    return [_account_from_row(row) for row in rows]


def save_viltrox_scan_snapshot(scan_data: dict[str, Any]) -> dict[str, Any]:
    accounts = sync_viltrox_official_accounts(reset=False)
    conn = get_conn()
    now = _utcnow()
    run_key = f"vx-scan-{now}-{secrets.token_hex(4)}"
    results = list(scan_data.get("results") or [])
    aggregate = dict(scan_data.get("aggregate") or {})
    total_accounts = int(scan_data.get("total") or len(accounts) or len(results))
    scanned_accounts = int(scan_data.get("scanned") or len(results))
    has_errors = any(str(item.get("error") or "").strip() for item in results)
    status = "partial" if has_errors else "completed"

    conn.execute(
        """
        INSERT INTO viltrox_matrix_scan_runs (
            run_key, status, started_at, completed_at, total_accounts, scanned_accounts,
            total_posts, total_views, total_likes, total_comments, aggregate_json,
            error_message, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_key,
            status,
            str(scan_data.get("timestamp") or now),
            now,
            total_accounts,
            scanned_accounts,
            int(aggregate.get("total_posts") or 0),
            int(aggregate.get("total_views") or 0),
            int(aggregate.get("total_likes") or 0),
            int(aggregate.get("total_comments") or 0),
            _json(aggregate, {}),
            "",
            now,
        ),
    )
    run_row = conn.execute(
        "SELECT * FROM viltrox_matrix_scan_runs WHERE run_key=?",
        (run_key,),
    ).fetchone()
    run_id = int(run_row["id"])

    account_map = {
        _account_key(item["platform"], item["handle"]): item
        for item in accounts
    }
    result_map = {
        _account_key(item.get("platform") or "", item.get("handle") or ""): item
        for item in results
    }

    for account in accounts:
        account_key = _account_key(account["platform"], account["handle"])
        result = result_map.get(account_key) or {}
        stats = dict(result.get("stats") or {})
        posts = list(result.get("posts") or [])
        error_message = str(result.get("error") or "").strip()
        conn.execute(
            """
            INSERT INTO viltrox_matrix_scan_accounts (
                run_id, account_id, total_posts, total_views, total_likes, total_comments,
                duration_sec, error_message, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                int(account["id"]),
                int(stats.get("total_posts") or len(posts)),
                int(stats.get("total_views") or 0),
                int(stats.get("total_likes") or 0),
                int(stats.get("total_comments") or 0),
                float(result.get("duration_sec") or 0.0),
                error_message[:500],
                now,
            ),
        )
        for post in posts:
            conn.execute(
                """
                INSERT INTO viltrox_matrix_scan_posts (
                    run_id, account_id, title, post_url, thumbnail_url, views, likes,
                    comments, shares, published_at, content_type, raw_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    int(account["id"]),
                    str(post.get("title") or "")[:600],
                    str(post.get("url") or "")[:1000],
                    str(post.get("thumbnail") or "")[:1000],
                    int(post.get("views") or 0),
                    int(post.get("likes") or 0),
                    int(post.get("comments") or 0),
                    int(post.get("shares") or 0),
                    str(post.get("published") or "")[:120],
                    str(post.get("type") or "")[:60],
                    _json(post, {}),
                    now,
                ),
            )

    conn.commit()
    return _run_from_row(run_row)


def get_latest_viltrox_scan_bundle() -> dict[str, Any]:
    conn = get_conn()
    accounts = list_viltrox_official_accounts(active_only=True)
    run_row = conn.execute(
        """
        SELECT *
        FROM viltrox_matrix_scan_runs
        ORDER BY completed_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not run_row:
        return {
            "run": {},
            "accounts": accounts,
            "scan_accounts": [],
            "posts": [],
        }
    run = _run_from_row(run_row)
    scan_accounts = [
        dict(row)
        for row in conn.execute(
            """
            SELECT sa.run_id, sa.account_id, sa.total_posts, sa.total_views, sa.total_likes,
                   sa.total_comments, sa.duration_sec, sa.error_message, sa.created_at,
                   a.platform, a.handle, a.name
            FROM viltrox_matrix_scan_accounts sa
            JOIN viltrox_matrix_accounts a ON a.id = sa.account_id
            WHERE sa.run_id=?
            ORDER BY a.platform ASC, a.name COLLATE NOCASE ASC
            """,
            (run["id"],),
        ).fetchall()
    ]
    posts = [
        dict(row)
        for row in conn.execute(
            """
            SELECT sp.run_id, sp.account_id, sp.title, sp.post_url, sp.thumbnail_url, sp.views,
                   sp.likes, sp.comments, sp.shares, sp.published_at, sp.content_type,
                   sp.raw_json, sp.created_at, a.platform, a.handle, a.name
            FROM viltrox_matrix_scan_posts sp
            JOIN viltrox_matrix_accounts a ON a.id = sp.account_id
            WHERE sp.run_id=?
            ORDER BY sp.published_at DESC, sp.id DESC
            """,
            (run["id"],),
        ).fetchall()
    ]
    return {
        "run": run,
        "accounts": accounts,
        "scan_accounts": scan_accounts,
        "posts": posts,
    }


__all__ = [
    "get_latest_viltrox_scan_bundle",
    "list_viltrox_official_accounts",
    "save_viltrox_scan_snapshot",
    "sync_viltrox_official_accounts",
]
