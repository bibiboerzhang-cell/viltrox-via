"""
services/auth/service.py — auth flow helpers
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.security import make_token, verify_password
from app.core.config import DB_PATH, IS_PRODUCTION, PROJECT_ROOT
from app.core.permissions import staff_context_for_user
from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.users import creator_code_exists, generate_creator_code
from app.services.auth.lockout import LOCKOUT_MINUTES, clear_failed, is_locked_out, record_failed_login


def _row_value(user, key: str, default=None):
    try:
        return user[key]
    except Exception:
        return default


def build_login_payload(user) -> dict:
    user_dict = dict(user)
    staff = staff_context_for_user(user_dict)
    return {
        "status": "success",
        "token": make_token(user["id"], user["role"]),
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "creator_code": user["creator_code"],
            "role": user["role"],
            "points_balance": user["points_balance"],
            "points_pending": user["points_pending"],
            "points_total": user["points_total"],
            "avatar_url": user["avatar_url"],
            "bio": user["bio"],
            "signature": user["signature"],
            "tier_status": _row_value(user, "tier_status", "pending"),
            "trust_score": _row_value(user, "trust_score", 30),
            "trust_updated_at": _row_value(user, "trust_updated_at", ""),
            "permissions": staff.get("permissions", {}),
            "is_owner": bool(staff.get("is_owner")),
            "staff_id": staff.get("id") or staff.get("staff_id") or staff.get("user_id"),
            "employee_code": staff.get("employee_code") or user["creator_code"] or str(user["email"]).split("@")[0],
            "avatar_required": not bool(str(user["avatar_url"] or "").strip()),
        },
    }


def validate_login_credentials(user, password: str, *, client_ip: str = "") -> dict | None:
    if not user:
        return {"status": "error", "message": "Invalid email or password"}
    if is_locked_out(int(user["id"])):
        return {"status": "error", "message": f"Too many failed attempts, try again in {LOCKOUT_MINUTES} minutes"}
    if not verify_password(password, user["password_hash"]):
        record_failed_login(int(user["id"]), client_ip)
        return {"status": "error", "message": "Invalid email or password"}
    clear_failed(int(user["id"]))
    if IS_PRODUCTION and int(_row_value(user, "email_verified", 0) or 0) != 1:
        return {"status": "error", "message": "Please verify your email before signing in"}
    if IS_PRODUCTION and user["status"] == "pending":
        return {"status": "pending", "message": "Your account is awaiting admin approval"}
    if user["status"] == "rejected":
        return {"status": "error", "message": "Account rejected — contact support"}
    return None


def _legacy_row_value(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return default


def _legacy_db_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    parent = PROJECT_ROOT.parent
    for path in sorted(parent.glob("*/submissions.db")):
        resolved = path.resolve()
        if resolved == DB_PATH or resolved in seen or not resolved.exists():
            continue
        candidates.append(resolved)
        seen.add(resolved)
    for extra in (
        (parent / "submissions.db").resolve(),
        (PROJECT_ROOT / "backend" / "submissions.db").resolve(),
    ):
        if extra == DB_PATH or extra in seen or not extra.exists():
            continue
        candidates.append(extra)
        seen.add(extra)
    return candidates


def _find_legacy_user(email: str) -> tuple[Path, sqlite3.Row] | tuple[None, None]:
    normalized = str(email or "").strip().lower()
    for db_path in _legacy_db_candidates():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE lower(email)=? LIMIT 1",
                    (normalized,),
                ).fetchone()
            except sqlite3.OperationalError:
                # Some neighboring runtime databases are not auth databases at all.
                # Skip them instead of breaking the login/import path.
                continue
            if row:
                return db_path, row
        finally:
            conn.close()
    return None, None


def _resolve_creator_code(conn, preferred_code: str) -> str:
    preferred = str(preferred_code or "").strip()
    if preferred and not creator_code_exists(conn, preferred):
        return preferred
    for attempt in range(64):
        candidate = generate_creator_code(conn, offset=attempt)
        if not creator_code_exists(conn, candidate):
            return candidate
    raise RuntimeError("Could not allocate creator_code during legacy user import")


def import_legacy_user_if_available(email: str):
    if is_postgres_runtime():
        return None
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    current_conn = get_conn()
    existing = current_conn.execute(
        "SELECT * FROM users WHERE lower(email)=? LIMIT 1",
        (normalized,),
    ).fetchone()
    if existing:
        return existing
    legacy_path, legacy_user = _find_legacy_user(normalized)
    if not legacy_user or not legacy_path:
        return None

    legacy_conn = sqlite3.connect(legacy_path)
    legacy_conn.row_factory = sqlite3.Row
    try:
        creator_code = _resolve_creator_code(current_conn, _legacy_row_value(legacy_user, "creator_code", ""))
        current_conn.execute(
            """
            INSERT INTO users (
                created_at, email, password_hash, name, creator_code, status, role,
                points_balance, points_pending, points_total, last_login, note,
                email_verified, social_verified, avatar_url, bio, signature,
                tier_status, trust_score, trust_updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _legacy_row_value(legacy_user, "created_at", ""),
                normalized,
                _legacy_row_value(legacy_user, "password_hash", ""),
                _legacy_row_value(legacy_user, "name", ""),
                creator_code,
                _legacy_row_value(legacy_user, "status", "approved"),
                _legacy_row_value(legacy_user, "role", "creator"),
                int(_legacy_row_value(legacy_user, "points_balance", 0) or 0),
                int(_legacy_row_value(legacy_user, "points_pending", 0) or 0),
                int(_legacy_row_value(legacy_user, "points_total", 0) or 0),
                _legacy_row_value(legacy_user, "last_login", ""),
                _legacy_row_value(legacy_user, "note", ""),
                int(_legacy_row_value(legacy_user, "email_verified", 0) or 0),
                int(_legacy_row_value(legacy_user, "social_verified", 0) or 0),
                _legacy_row_value(legacy_user, "avatar_url", ""),
                _legacy_row_value(legacy_user, "bio", ""),
                _legacy_row_value(legacy_user, "signature", ""),
                _legacy_row_value(legacy_user, "tier_status", "pending"),
                float(_legacy_row_value(legacy_user, "trust_score", 30) or 30),
                _legacy_row_value(legacy_user, "trust_updated_at", ""),
            ),
        )
        user_id = int(current_conn.execute("SELECT last_insert_rowid()").fetchone()[0] or 0)
        legacy_user_id = int(_legacy_row_value(legacy_user, "id", 0) or 0)

        social_rows = legacy_conn.execute(
            "SELECT * FROM user_social_accounts WHERE user_id=? ORDER BY id ASC",
            (legacy_user_id,),
        ).fetchall()
        for row in social_rows:
            current_conn.execute(
                """
                INSERT OR IGNORE INTO user_social_accounts (
                    user_id, platform, handle, verified, verified_at, verify_code, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    _legacy_row_value(row, "platform", ""),
                    _legacy_row_value(row, "handle", ""),
                    int(_legacy_row_value(row, "verified", 0) or 0),
                    _legacy_row_value(row, "verified_at", ""),
                    _legacy_row_value(row, "verify_code", ""),
                    _legacy_row_value(row, "created_at", ""),
                ),
            )

        address_rows = legacy_conn.execute(
            "SELECT * FROM user_addresses WHERE user_id=? ORDER BY is_default DESC, id ASC",
            (legacy_user_id,),
        ).fetchall()
        for row in address_rows:
            current_conn.execute(
                """
                INSERT INTO user_addresses (
                    user_id, name, phone, address1, address2, city, state, country, postal_code, is_default
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    _legacy_row_value(row, "name", ""),
                    _legacy_row_value(row, "phone", ""),
                    _legacy_row_value(row, "address1", ""),
                    _legacy_row_value(row, "address2", ""),
                    _legacy_row_value(row, "city", ""),
                    _legacy_row_value(row, "state", ""),
                    _legacy_row_value(row, "country", "US"),
                    _legacy_row_value(row, "postal_code", ""),
                    int(_legacy_row_value(row, "is_default", 0) or 0),
                ),
            )

        submission_id_map: dict[int, int] = {}
        submission_rows = legacy_conn.execute(
            "SELECT * FROM submissions WHERE user_id=? ORDER BY id ASC",
            (legacy_user_id,),
        ).fetchall()
        for row in submission_rows:
            cur = current_conn.execute(
                """
                INSERT INTO submissions (
                    created_at, platform, url, extracted_handle, title, detection_status,
                    product_series, product_label, content_types, final_score, creator_score,
                    overall_score, risk_score, views, likes, comments, shares, favorites,
                    recommendation, memo, evidence, scraped_ok, video_analysis, video_path,
                    user_id, points_awarded, points_status, job_status, raw_text, caption,
                    points_pending, confirm_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _legacy_row_value(row, "created_at", ""),
                    _legacy_row_value(row, "platform", ""),
                    _legacy_row_value(row, "url", ""),
                    _legacy_row_value(row, "extracted_handle", ""),
                    _legacy_row_value(row, "title", ""),
                    _legacy_row_value(row, "detection_status", ""),
                    _legacy_row_value(row, "product_series", ""),
                    _legacy_row_value(row, "product_label", ""),
                    _legacy_row_value(row, "content_types", ""),
                    _legacy_row_value(row, "final_score", 0),
                    _legacy_row_value(row, "creator_score", 0),
                    _legacy_row_value(row, "overall_score", 0),
                    _legacy_row_value(row, "risk_score", 0),
                    _legacy_row_value(row, "views", 0),
                    _legacy_row_value(row, "likes", 0),
                    _legacy_row_value(row, "comments", 0),
                    _legacy_row_value(row, "shares", 0),
                    _legacy_row_value(row, "favorites", 0),
                    _legacy_row_value(row, "recommendation", ""),
                    _legacy_row_value(row, "memo", ""),
                    _legacy_row_value(row, "evidence", ""),
                    _legacy_row_value(row, "scraped_ok", 0),
                    _legacy_row_value(row, "video_analysis", ""),
                    _legacy_row_value(row, "video_path", ""),
                    user_id,
                    _legacy_row_value(row, "points_awarded", 0),
                    _legacy_row_value(row, "points_status", "pending"),
                    _legacy_row_value(row, "job_status", "legacy"),
                    _legacy_row_value(row, "raw_text", ""),
                    _legacy_row_value(row, "caption", ""),
                    _legacy_row_value(row, "points_pending", 0),
                    _legacy_row_value(row, "confirm_at", ""),
                ),
            )
            submission_id_map[int(_legacy_row_value(row, "id", 0) or 0)] = int(cur.lastrowid or 0)

        redemption_rows = legacy_conn.execute(
            "SELECT * FROM redemptions WHERE user_id=? ORDER BY id ASC",
            (legacy_user_id,),
        ).fetchall()
        for row in redemption_rows:
            current_conn.execute(
                """
                INSERT INTO redemptions (
                    created_at, user_id, reward_id, item_name, item_category, points_cost,
                    address_id, address_snapshot, status, tracking_number, admin_note
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _legacy_row_value(row, "created_at", ""),
                    user_id,
                    _legacy_row_value(row, "reward_id", None),
                    _legacy_row_value(row, "item_name", ""),
                    _legacy_row_value(row, "item_category", ""),
                    _legacy_row_value(row, "points_cost", 0),
                    None,
                    _legacy_row_value(row, "address_snapshot", ""),
                    _legacy_row_value(row, "status", "pending"),
                    _legacy_row_value(row, "tracking_number", ""),
                    _legacy_row_value(row, "admin_note", "legacy_import"),
                ),
            )

        points_rows = legacy_conn.execute(
            "SELECT * FROM points_log WHERE user_id=? ORDER BY id ASC",
            (legacy_user_id,),
        ).fetchall()
        for row in points_rows:
            current_conn.execute(
                """
                INSERT INTO points_log (
                    created_at, user_id, submission_id, delta, reason, balance_after
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    _legacy_row_value(row, "created_at", ""),
                    user_id,
                    submission_id_map.get(int(_legacy_row_value(row, "submission_id", 0) or 0)) or None,
                    int(_legacy_row_value(row, "delta", 0) or 0),
                    _legacy_row_value(row, "reason", "Legacy import"),
                    _legacy_row_value(row, "balance_after", 0),
                ),
            )

        current_conn.commit()
        return current_conn.execute(
            "SELECT * FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
    finally:
        legacy_conn.close()
