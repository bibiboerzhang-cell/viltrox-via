"""
services/system/trust_admin.py — Admin-facing trust tooling with legacy schema compatibility.

This project already had a runtime trust system backed by a legacy
`trust_events` table:
    id, user_id, event_type, score_delta, new_total, context_json, created_at

The v5 admin package expects a richer schema. Until a hard table migration is
done, this module adapts to the current table and aliases fields into the v5
shape for the admin UI.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


DEFAULT_THRESHOLDS = {
    "trusted": 70,
    "watching": 50,
    "flagged": 20,
    "blocked": 0,
}


def _event_schema(conn) -> str:
    cols = {
        str(row["name"] if isinstance(row, dict) else row[1])
        for row in conn.execute("PRAGMA table_info(trust_events)").fetchall()
    }
    return "legacy" if "event_type" in cols else "v5"


def _normalize_thresholds(raw: dict[str, Any] | None) -> dict[str, int]:
    payload = dict(raw or {})
    if "watch_below" in payload:
        payload["watching"] = payload.get("watch_below")
    if "flag_below" in payload:
        payload["flagged"] = payload.get("flag_below")
    if "block_below" in payload:
        payload["blocked"] = payload.get("block_below")
    merged = {**DEFAULT_THRESHOLDS, **payload}
    return {
        "trusted": int(merged.get("trusted", DEFAULT_THRESHOLDS["trusted"])),
        "watching": int(merged.get("watching", DEFAULT_THRESHOLDS["watching"])),
        "flagged": int(merged.get("flagged", DEFAULT_THRESHOLDS["flagged"])),
        "blocked": int(merged.get("blocked", DEFAULT_THRESHOLDS["blocked"])),
    }


def _status_for_score(score: float, thresholds: dict[str, int]) -> str:
    if score < thresholds["blocked"]:
        return "blocked"
    if score < thresholds["flagged"]:
        return "flagged"
    if score < thresholds["watching"]:
        return "watching"
    if score >= thresholds["trusted"]:
        return "trusted"
    return "normal"


def _decode_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _event_row_to_dict(row: Any, *, legacy: bool) -> dict[str, Any]:
    item = dict(row)
    metadata = _decode_json(item.get("metadata_json" if not legacy else "context_json"), {})
    delta = float(item.get("delta" if not legacy else "score_delta") or 0)
    event_kind = str(item.get("event_kind" if not legacy else "event_type") or "")
    occurred_at = str(item.get("occurred_at" if not legacy else "created_at") or "")
    return {
        "event_id": item.get("id"),
        "user_id": item.get("user_id"),
        "user_handle": item.get("user_handle") or "",
        "kind": event_kind,
        "delta": delta,
        "is_positive": delta > 0,
        "metadata": metadata,
        "occurred_at": occurred_at,
        "score_after": item.get("score_after") if not legacy else item.get("new_total"),
        "actor_type": item.get("actor_type") if not legacy else metadata.get("actor_type"),
        "actor_id": item.get("actor_id") if not legacy else metadata.get("actor_id"),
    }


def _get_thresholds() -> dict[str, int]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM settings_kv WHERE key='trust_thresholds'"
        ).fetchone()
        if row and row["value"]:
            return _normalize_thresholds(_decode_json(row["value"], {}))
    except Exception:
        logger.debug("trust.thresholds_lookup_failed", exc_info=True)
    return dict(DEFAULT_THRESHOLDS)


def _record_event(
    *,
    user_id: int,
    event_kind: str,
    delta: int,
    actor_type: str,
    actor_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn = get_conn()
    legacy = _event_schema(conn) == "legacy"
    current_row = conn.execute(
        "SELECT trust_score FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    score_before = float(current_row["trust_score"] or 30.0) if current_row else 30.0
    score_after = max(-100.0, min(100.0, score_before + float(delta)))

    ctx = {
        **(metadata or {}),
        "actor_type": actor_type,
        "actor_id": actor_id,
    }

    if legacy:
        conn.execute(
            """INSERT INTO trust_events
                (user_id, event_type, score_delta, new_total, context_json, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (user_id, event_kind, float(delta), float(score_after), json.dumps(ctx)),
        )
    else:
        conn.execute(
            """INSERT INTO trust_events
                (user_id, event_kind, delta, score_before, score_after,
                 actor_type, actor_id, metadata_json, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                user_id,
                event_kind,
                int(delta),
                score_before,
                score_after,
                actor_type,
                actor_id,
                json.dumps(metadata or {}),
            ),
        )

    thresholds = _get_thresholds()
    conn.execute(
        "UPDATE users SET trust_score = ?, trust_status = ? WHERE id = ?",
        (score_after, _status_for_score(score_after, thresholds), user_id),
    )
    conn.commit()


def list_events(
    *,
    pos: bool | None = None,
    user_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> dict:
    conn = get_conn()
    legacy = _event_schema(conn) == "legacy"
    delta_col = "e.score_delta" if legacy else "e.delta"
    time_col = "e.created_at" if legacy else "e.occurred_at"
    where: list[str] = []
    params: list[Any] = []
    if pos is True:
        where.append(f"{delta_col} > 0")
    elif pos is False:
        where.append(f"{delta_col} < 0")
    if user_id is not None:
        where.append("e.user_id = ?")
        params.append(user_id)
    if from_date:
        where.append(f"{time_col} >= ?")
        params.append(from_date)
    if to_date:
        where.append(f"{time_col} <= ?")
        params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT e.*, u.creator_code AS user_handle
            FROM trust_events e
            LEFT JOIN users u ON e.user_id = u.id
            {where_sql}
            ORDER BY {time_col} DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return {"events": [_event_row_to_dict(row, legacy=legacy) for row in rows]}


def list_users(*, status: str | None = None, order_by: str = "score_asc") -> dict:
    conn = get_conn()
    where, params = [], []
    if status:
        where.append("trust_status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_map = {
        "score_asc": "trust_score ASC",
        "violations_desc": "violation_count DESC",
        "recent": "COALESCE(flagged_at, blocked_at, created_at) DESC",
    }
    rows = conn.execute(
        f"""SELECT id, creator_code AS handle, email, name, trust_score,
                   trust_status, violation_count, flagged_at, flagged_reason,
                   blocked_at, blocked_reason, created_at
            FROM users
            {where_sql}
            ORDER BY {order_map.get(order_by, 'trust_score ASC')}
            LIMIT 200""",
        params,
    ).fetchall()
    return {"users": [dict(row) for row in rows]}


def user_detail(user_id: int) -> dict:
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return {"error": "not found"}
    legacy = _event_schema(conn) == "legacy"
    time_col = "created_at" if legacy else "occurred_at"
    events = conn.execute(
        f"SELECT * FROM trust_events WHERE user_id = ? ORDER BY {time_col} DESC LIMIT 50",
        (user_id,),
    ).fetchall()
    payload = dict(user)
    payload["handle"] = payload.get("creator_code") or ""
    return {
        "user": payload,
        "events": [_event_row_to_dict(row, legacy=legacy) for row in events],
        "violations": int(payload.get("violation_count") or 0),
    }


def distribution() -> dict:
    conn = get_conn()
    buckets = [
        {"range": "<0", "sql": "trust_score < 0"},
        {"range": "0-19", "sql": "trust_score >= 0 AND trust_score < 20"},
        {"range": "20-49", "sql": "trust_score >= 20 AND trust_score < 50"},
        {"range": "50-69", "sql": "trust_score >= 50 AND trust_score < 70"},
        {"range": "70+", "sql": "trust_score >= 70"},
    ]
    out = []
    for bucket in buckets:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM users WHERE {bucket['sql']}"
        ).fetchone()
        out.append({"range": bucket["range"], "count": int(row["n"] or 0)})
    summary = conn.execute(
        "SELECT COUNT(*) AS total_users, ROUND(CAST(AVG(trust_score) AS numeric), 2) AS avg_score FROM users"
    ).fetchone()
    return {
        "buckets": out,
        "total_users": int(summary["total_users"] or 0),
        "avg_score": float(summary["avg_score"] or 0),
    }


def list_rules() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM trust_rules ORDER BY id").fetchall()
    thresholds = _get_thresholds()
    return {
        "positive": [dict(row) for row in rows if int(row["is_positive"] or 0) == 1],
        "negative": [dict(row) for row in rows if int(row["is_positive"] or 0) == 0],
        "thresholds": thresholds,
    }


def update_rule(rule_id: int, body: dict, admin_id: int) -> None:
    conn = get_conn()
    fields, params = [], []
    for col in ("delta", "description", "enabled"):
        if col in body:
            fields.append(f"{col} = ?")
            value = body[col]
            if col == "enabled":
                value = 1 if value else 0
            params.append(value)
    if not fields:
        return
    params.extend([admin_id, rule_id])
    conn.execute(
        f"UPDATE trust_rules SET {', '.join(fields)}, updated_at = datetime('now'), updated_by = ? WHERE id = ?",
        params,
    )
    conn.commit()


def set_thresholds(body: dict, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings_kv (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )"""
    )
    normalized = _normalize_thresholds(body)
    conn.execute(
        """INSERT INTO settings_kv (key, value, updated_at)
           VALUES ('trust_thresholds', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (json.dumps(normalized),),
    )
    conn.commit()


def block_user(user_id: int, reason: str, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE users
           SET trust_status = 'blocked',
               blocked_at = datetime('now'),
               blocked_reason = ?
           WHERE id = ?""",
        (reason, user_id),
    )
    conn.commit()
    _record_event(
        user_id=user_id,
        event_kind="admin_block",
        delta=-50,
        actor_type="admin",
        actor_id=admin_id,
        metadata={"reason": reason},
    )


def unblock_user(user_id: int, reason: str, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE users
           SET trust_status = 'normal',
               blocked_at = NULL,
               blocked_reason = NULL
           WHERE id = ?""",
        (user_id,),
    )
    conn.commit()
    _record_event(
        user_id=user_id,
        event_kind="admin_unblock",
        delta=10,
        actor_type="admin",
        actor_id=admin_id,
        metadata={"reason": reason},
    )


def flag_user(user_id: int, reason: str, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE users
           SET trust_status = 'flagged',
               flagged_at = datetime('now'),
               flagged_reason = ?,
               violation_count = COALESCE(violation_count, 0) + 1
           WHERE id = ?""",
        (reason, user_id),
    )
    conn.commit()
    _record_event(
        user_id=user_id,
        event_kind="admin_flag",
        delta=-10,
        actor_type="admin",
        actor_id=admin_id,
        metadata={"reason": reason},
    )


def clear_flag(user_id: int, admin_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE users
           SET trust_status = 'normal',
               flagged_at = NULL,
               flagged_reason = NULL
           WHERE id = ?""",
        (user_id,),
    )
    conn.commit()
    _record_event(
        user_id=user_id,
        event_kind="admin_clear_flag",
        delta=5,
        actor_type="admin",
        actor_id=admin_id,
    )


def adjust_score(user_id: int, delta: int, reason: str, admin_id: int) -> None:
    _record_event(
        user_id=user_id,
        event_kind="admin_adjust",
        delta=int(delta),
        actor_type="admin",
        actor_id=admin_id,
        metadata={"reason": reason},
    )
