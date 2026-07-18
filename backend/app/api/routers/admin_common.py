"""Shared helpers for admin routers."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import asyncio
import secrets as _secrets
from functools import partial
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Query, UploadFile, Request, HTTPException
from fastapi.responses import FileResponse

from app.db.connection import db_read, db_write, get_conn, is_postgres_runtime, table_exists
from app.core.logging import get_logger
from app.api.routers.media import resolve_poster_response, resolve_video_response
from app.core.config import (
    ADMIN_READ_CACHE_TTL_SEC,
    ADMIN_STATS_CACHE_TTL_SEC,
    UPLOAD_DIR,
    CREATOR_DIR,
)
from app.core.security import require_admin, require_admin_async, get_current_user, invalidate_user_cache, hash_password
from app.services.cache import cache_clear, cache_delete, cache_get, cache_invalidate_admin, cache_set, cached
from app.db.repositories.users import mark_social_account_verified, refresh_user_social_verified
from app.services.creator_program import build_affiliate_ops_snapshot, sync_creator_program_state
from app.services.creator_public import (
    delete_creator_shop_hero,
    list_creator_shop_heroes,
    upsert_creator_shop_hero,
)
from app.services.audit_log import record_admin_action
from app.services.security.rate_limiter import rate_limit

# ── 1. 统一导入 Schema ──
from app.schemas.admin import ManualSubmissionRequest, ManualApproveRequest, ReanalyzeRequest, VerifyRegisterRequest
from app.schemas.rewards import RewardItemRequest

# ── 2. 导入轻量级业务服务 (重度 AI/爬虫移至函数内局部导入防崩溃) ──
from app.services.scoring.benchmark import update_genre_benchmark, get_all_benchmarks
from app.services.rewards.points import auto_award_points, reverse_submission_points

logger = get_logger(__name__)

def _admin_cache_key(name: str, **params: Any) -> str:
    if not params:
        return f"admin_{name}"
    parts = [f"{k}={params[k]}" for k in sorted(params)]
    return f"admin_{name}:" + "|".join(parts)


def _admin_cache_get_or_build(name: str, builder, ttl: int, **params: Any):
    key = _admin_cache_key(name, **params)
    cached = cache_get(key)
    if cached is not None:
        return cached
    value = builder()
    cache_set(key, value, ttl=ttl)
    return value


def _invalidate_admin_cache() -> None:
    cache_invalidate_admin()
    cache_delete("public:rewards:list")
    cache_clear(prefix="public:rewards")


def _refresh_user_points_state(user_id: int | None, reason: str) -> None:
    if not user_id:
        return
    uid = int(user_id)
    invalidate_user_cache(uid)
    cache_clear(prefix=f"creator:{uid}:")
    try:
        sync_creator_program_state(uid, reason=reason)
    except Exception:
        logger.warning(
            "admin.user_points_state_sync_failed",
            extra={"user_id": uid, "reason": reason},
            exc_info=True,
        )


def _load_submission_row(submission_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
    return dict(row) if row else None


def _mark_submission_reanalyze(submission_id: int) -> None:
    conn = get_conn()
    conn.execute(
        """UPDATE submissions
           SET job_status='queued',
               detection_status='queued',
               error_message='',
               started_at=NULL,
               finished_at=NULL,
               memo=COALESCE(memo, '') || ?
           WHERE id=?""",
        (f" [Reanalyze queued at {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}]", submission_id),
    )
    conn.commit()


def _load_cached_insights_row():
    conn = get_conn()
    return conn.execute("SELECT value, updated_at FROM insights_cache WHERE key='main'").fetchone()


def _update_redemption_record(rid: int, status: str, tracking_number: str, admin_note: str) -> None:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE redemptions SET status=?, tracking_number=?, admin_note=? WHERE id=?",
        (status, tracking_number, admin_note, rid),
    )
    conn.commit()
    return cur.rowcount == 1


def _table_columns(conn, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}
    except Exception:
        return set()


def _ensure_redemption_ops_schema(conn) -> None:
    columns = _table_columns(conn, "redemptions")
    additions = {
        "approved_at": "TEXT DEFAULT ''",
        "packed_at": "TEXT DEFAULT ''",
        "shipped_at": "TEXT DEFAULT ''",
        "delivered_at": "TEXT DEFAULT ''",
        "shipping_carrier": "TEXT DEFAULT ''",
        "warehouse_staff_id": "INTEGER",
        "notification_log_json": "TEXT DEFAULT '{}'",
    }
    changed = False
    for name, ddl in additions.items():
        if name in columns:
            continue
        try:
            conn.execute(f"ALTER TABLE redemptions ADD COLUMN {name} {ddl}")
            changed = True
        except Exception:
            logger.debug("admin.redemptions_add_column_failed", extra={"column": name}, exc_info=True)
    if changed:
        conn.commit()


def _transition_redemption_record(
    rid: int,
    new_status: str,
    *,
    admin_id: int,
    note: str = "",
    tracking_number: str = "",
    shipping_carrier: str = "",
) -> dict[str, Any] | None:
    conn = get_conn()
    _ensure_redemption_ops_schema(conn)
    row = conn.execute("SELECT * FROM redemptions WHERE id=?", (int(rid),)).fetchone()
    if not row:
        return None
    current = str(row["status"] or "pending").lower()
    allowed = {
        "pending": {"approved", "packed", "rejected"},
        "approved": {"packed", "rejected"},
        "packed": {"shipped", "rejected"},
        "shipped": {"delivered"},
        "delivered": set(),
        "rejected": set(),
        "cancelled": set(),
        "fulfilled": {"packed", "shipped", "delivered"},
    }
    target = str(new_status or "").strip().lower()
    if target not in {"approved", "packed", "shipped", "delivered", "rejected"}:
        raise ValueError("unsupported redemption status")
    if target not in allowed.get(current, {target}) and target != current:
        raise ValueError(f"invalid transition: {current} -> {target}")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = ["status = ?", "admin_note = COALESCE(NULLIF(?, ''), admin_note)"]
    values: list[Any] = [target, note]
    if target == "approved":
        fields.append("approved_at = COALESCE(NULLIF(approved_at, ''), ?)")
        values.append(now)
    elif target == "packed":
        fields.append("packed_at = COALESCE(NULLIF(packed_at, ''), ?)")
        fields.append("warehouse_staff_id = ?")
        values.extend([now, int(admin_id)])
    elif target == "shipped":
        fields.append("shipped_at = COALESCE(NULLIF(shipped_at, ''), ?)")
        fields.append("tracking_number = COALESCE(NULLIF(?, ''), tracking_number)")
        fields.append("shipping_carrier = COALESCE(NULLIF(?, ''), shipping_carrier)")
        values.extend([now, tracking_number, shipping_carrier])
    elif target == "delivered":
        fields.append("delivered_at = COALESCE(NULLIF(delivered_at, ''), ?)")
        values.append(now)
    elif target == "rejected":
        fields.append("approved_at = COALESCE(approved_at, '')")
        points = int(row["points_cost"] or 0) if "points_cost" in row.keys() else 0
        user_id = int(row["user_id"] or 0) if "user_id" in row.keys() else 0
        if points > 0 and user_id:
            user = conn.execute("SELECT points_balance, points_total FROM users WHERE id=?", (user_id,)).fetchone()
            if user:
                balance = int(user["points_balance"] or 0) + points
                total = max(0, int(user["points_total"] or 0))
                conn.execute("UPDATE users SET points_balance=?, points_total=? WHERE id=?", (balance, total, user_id))
                conn.execute(
                    "INSERT INTO points_log (created_at,user_id,delta,reason,balance_after) VALUES (?,?,?,?,?)",
                    (now, user_id, points, f"Redemption #{rid} rejected refund", balance),
                )
    values.append(int(rid))
    conn.execute(f"UPDATE redemptions SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()
    return dict(conn.execute("SELECT * FROM redemptions WHERE id=?", (int(rid),)).fetchone())


def _grant_points_to_user(uid: int, points: int, reason: str, now: str):
    conn = get_conn()
    user = conn.execute("SELECT id, points_balance FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        return None
    new_balance = max(0, (user["points_balance"] or 0) + points)
    conn.execute(
        "UPDATE users SET points_balance=?, points_total=points_total+? WHERE id=?",
        (new_balance, max(0, points), uid),
    )
    conn.execute(
        "INSERT INTO points_log (created_at, user_id, delta, reason, balance_after) VALUES (?,?,?,?,?)",
        (now, uid, points, reason, new_balance),
    )
    conn.commit()
    return new_balance


def _select_user_ids_for_points_rule(where_sql: str, params: list[Any], limit: int) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        f"SELECT id FROM users {where_sql} ORDER BY id ASC LIMIT ?",
        [*params, int(limit)],
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _update_creator_code_sync(uid: int, new_code: str) -> dict[str, str]:
    conn = get_conn()
    user = conn.execute("SELECT id, creator_code FROM users WHERE id=?", (int(uid),)).fetchone()
    if not user:
        raise LookupError("User not found")
    taken = conn.execute(
        "SELECT id FROM users WHERE UPPER(creator_code)=UPPER(?) AND id != ?",
        (new_code, int(uid)),
    ).fetchone()
    if taken:
        raise ValueError(f"creator_code {new_code} already taken")
    old_code = str(user["creator_code"] or "")
    conn.execute("UPDATE users SET creator_code=? WHERE id=?", (new_code, int(uid)))
    conn.commit()
    return {"old_creator_code": old_code, "creator_code": new_code}


def _load_submission_product_context(submission_id: int):
    conn = get_conn()
    return conn.execute(
        "SELECT url, video_analysis, title FROM submissions WHERE id=?",
        (submission_id,),
    ).fetchone()


def _update_submission_product(
    submission_id: int,
    correct_series: str,
    correct_label: str,
    note: str,
) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE submissions SET product_series=?, product_label=?, "
        "memo=COALESCE(memo,'') || ? WHERE id=?",
        (
            correct_series,
            correct_label,
            f" [Manual correction: {correct_series}/{correct_label}{' - ' + note if note else ''}]",
            submission_id,
        ),
    )
    conn.commit()


def _adjust_user_points(uid: int, delta: int, reason: str, now: str):
    conn = get_conn()
    user = conn.execute("SELECT points_balance, points_total FROM users WHERE id=?", (uid,)).fetchone()
    if not user:
        return None
    new_balance = max(0, int(user["points_balance"] or 0) + delta)
    new_total = max(0, int(user["points_total"] or 0) + delta)
    conn.execute(
        "UPDATE users SET points_balance=?, points_total=? WHERE id=?",
        (new_balance, new_total, uid),
    )
    conn.execute(
        "INSERT INTO points_log (created_at,user_id,delta,reason,balance_after) VALUES (?,?,?,?,?)",
        (now, uid, delta, reason, new_balance),
    )
    conn.commit()
    return new_balance


def _update_user_status(uid: int, status: str, note: str) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE users SET status=?, note=? WHERE id=?", (status, note, uid))
    conn.commit()
    updated = cur.rowcount == 1
    if updated:
        invalidate_user_cache(int(uid))
    return updated


def _delete_user_by_id(uid: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    return cur.rowcount == 1


def _creator_code_for_user_id(uid: int) -> str:
    return f"V_{int(uid):06d}"


def _upsert_admin_user_account(payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("valid email required")

    role = str(payload.get("role") or "creator").strip().lower()
    if role not in {"creator", "admin", "student"}:
        raise ValueError("role must be creator, admin, or student")

    status = str(payload.get("status") or "approved").strip().lower()
    if status not in {"pending", "approved", "rejected", "blocked"}:
        raise ValueError("status must be pending, approved, rejected, or blocked")

    name = str(payload.get("name") or email.split("@", 1)[0]).strip()
    password = str(payload.get("password") or "").strip()
    email_verified = 1 if bool(payload.get("email_verified", True)) else 0
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_conn()
    existing = conn.execute("SELECT id, creator_code FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    if existing and password:
        conn.execute(
            """
            UPDATE users
               SET name=?,
                   password_hash=?,
                   status=?,
                   role=?,
                   email_verified=?,
                   creator_code=COALESCE(NULLIF(creator_code, ''), ?)
             WHERE id=?
            """,
            (
                name,
                hash_password(password),
                status,
                role,
                email_verified,
                _creator_code_for_user_id(int(existing["id"])),
                int(existing["id"]),
            ),
        )
        uid = int(existing["id"])
    elif existing:
        conn.execute(
            """
            UPDATE users
               SET name=?,
                   status=?,
                   role=?,
                   email_verified=?,
                   creator_code=COALESCE(NULLIF(creator_code, ''), ?)
             WHERE id=?
            """,
            (
                name,
                status,
                role,
                email_verified,
                _creator_code_for_user_id(int(existing["id"])),
                int(existing["id"]),
            ),
        )
        uid = int(existing["id"])
    else:
        if not password:
            raise ValueError("password required for new user")
        cur = conn.execute(
            """
            INSERT INTO users
                (created_at, email, password_hash, name, status, role, email_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now, email, hash_password(password), name, status, role, email_verified),
        )
        uid = int(cur.lastrowid)
        conn.execute("UPDATE users SET creator_code=? WHERE id=?", (_creator_code_for_user_id(uid), uid))
    conn.commit()
    invalidate_user_cache(uid)
    _refresh_user_points_state(uid, reason="admin_account_upsert")
    row = conn.execute(
        "SELECT id, created_at, email, name, creator_code, status, role, email_verified, points_balance, points_total FROM users WHERE id=?",
        (uid,),
    ).fetchone()
    return dict(row) if row else {}


def _load_user_delete_dependencies(uid: int) -> dict[str, int]:
    conn = get_conn()
    return {
        "submissions": int(conn.execute("SELECT COUNT(*) FROM submissions WHERE user_id=?", (uid,)).fetchone()[0] or 0),
        "redemptions": int(conn.execute("SELECT COUNT(*) FROM redemptions WHERE user_id=?", (uid,)).fetchone()[0] or 0),
        "points_log": int(conn.execute("SELECT COUNT(*) FROM points_log WHERE user_id=?", (uid,)).fetchone()[0] or 0),
    }


def _delete_submission_by_id(submission_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM submissions WHERE id=?", (submission_id,))
    conn.commit()
    return cur.rowcount == 1


def _share_token_cache_key(token: str) -> str:
    return f"vios:share-token:{token}"


def _parse_cached_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None


def _persist_share_token(token: str, created_at: str, expires_at: str) -> None:
    conn = get_conn()
    cache_key = _share_token_cache_key(token)
    payload = json.dumps({"created": created_at, "expires": expires_at})
    conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (cache_key,))
    conn.execute(
        "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) VALUES (?,?,?,?)",
        (cache_key, payload, expires_at, created_at),
    )
    conn.commit()


def _load_share_token_meta(token: str) -> dict[str, Any] | None:
    conn = get_conn()
    cache_key = _share_token_cache_key(token)
    row = conn.execute(
        "SELECT value_json, expires_at FROM persistent_cache WHERE cache_key=?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        meta = json.loads(row["value_json"] or "{}")
    except Exception:
        meta = {}
    expires_text = str(meta.get("expires") or row["expires_at"] or "")
    expires_at = _parse_cached_datetime(expires_text)
    if expires_at is None or expires_at <= datetime.utcnow():
        conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (cache_key,))
        conn.commit()
        return None
    meta["expires"] = expires_text
    return meta


def _register_verification_request(platform: str, handle: str, code: str, created_at: str) -> None:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM verifications WHERE platform=? AND handle=?",
        (platform, handle),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE verifications SET code=?, status='pending', created_at=? WHERE id=?",
            (code, created_at, existing[0]),
        )
    else:
        conn.execute(
            "INSERT INTO verifications (created_at, platform, handle, code, status) VALUES (?,?,?,?,?)",
            (created_at, platform, handle, code, "pending"),
        )
    conn.commit()


def _approve_verification_override(ver_id: int, note: str, approved_at: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, user_id, platform, handle FROM verifications WHERE id=?",
        (ver_id,),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE verifications SET status='approved_override', approved_at=?, note=? WHERE id=?",
        (approved_at, note, ver_id),
    )
    user_id = int(row["user_id"] or 0)
    platform = str(row["platform"] or "").lower().strip()
    handle = str(row["handle"] or "").lstrip("@").strip()
    if user_id > 0 and platform and handle:
        mark_social_account_verified(
            user_id=user_id,
            platform=platform,
            handle=handle.lower(),
            verified_at=approved_at,
        )
    else:
        conn.commit()
    return {"platform": row["platform"] or "", "handle": row["handle"] or ""}


def _reject_verification_record(ver_id: int, note: str) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE verifications SET status='rejected', note=? WHERE id=?", (note, ver_id))
    conn.commit()
    return cur.rowcount == 1

__all__ = [name for name in globals() if not name.startswith("__")]
