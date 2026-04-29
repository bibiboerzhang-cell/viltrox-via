"""
api/routers/admin.py — 管理后台路由 (/api/admin/*)
"""
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
from app.core.security import require_admin, require_admin_async, get_current_user, invalidate_user_cache
from app.services.cache import cache_clear, cache_delete, cache_get, cache_invalidate_admin, cache_set, cached
from app.db.repositories.users import mark_social_account_verified, refresh_user_social_verified
from app.services.creator_program import build_affiliate_ops_snapshot, sync_creator_program_state
from app.services.creator_public import (
    delete_creator_shop_hero,
    list_creator_shop_heroes,
    upsert_creator_shop_hero,
)
from app.services.security.rate_limiter import rate_limit

# ── 1. 统一导入 Schema ──
from app.schemas.admin import ManualSubmissionRequest, ManualApproveRequest, ReanalyzeRequest, VerifyRegisterRequest
from app.schemas.rewards import RewardItemRequest

# ── 2. 导入轻量级业务服务 (重度 AI/爬虫移至函数内局部导入防崩溃) ──
from app.services.scoring.benchmark import update_genre_benchmark, get_all_benchmarks
from app.services.rewards.points import auto_award_points, reverse_submission_points

router = APIRouter(tags=["admin"])
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
    return cur.rowcount == 1


def _delete_user_by_id(uid: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    return cur.rowcount == 1


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

# ── Social accounts admin ──
@router.get("/api/admin/social-accounts")
def admin_list_social_accounts(request: Request, verified: str = ""):
    require_admin(request)

    def _build():
        conn = get_conn()
        base = "SELECT sa.*,u.email,u.name as user_name FROM user_social_accounts sa LEFT JOIN users u ON sa.user_id=u.id"
        if verified == "0": q = base + " WHERE sa.verified=0 ORDER BY sa.id DESC"
        elif verified == "1": q = base + " WHERE sa.verified=1 ORDER BY sa.id DESC"
        else: q = base + " ORDER BY sa.id DESC"
        return {"accounts": [dict(r) for r in conn.execute(q).fetchall()]}

    return _admin_cache_get_or_build(
        "social_accounts",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        verified=verified or "all",
    )

@router.post("/api/admin/social-accounts/{account_id}/verify")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_verify_social(account_id: int, request: Request):
    require_admin(request)
    raise HTTPException(
        409,
        "Strict verification mode is enabled. Accounts can only become verified when the comment scanner detects the active code.",
    )

@router.post("/api/admin/social-accounts/{account_id}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_reject_social(account_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM user_social_accounts WHERE id=?", (account_id,)).fetchone()
    conn.execute("DELETE FROM user_social_accounts WHERE id=?", (account_id,))
    conn.commit()
    if row and int(row["user_id"] or 0) > 0:
        refresh_user_social_verified(
            int(row["user_id"]),
            reason="admin_social_account_rejected",
            context={"account_id": int(account_id)},
        )
    _invalidate_admin_cache()
    return {"status":"rejected"}


# ── Users admin ──
@router.get("/api/admin/users")
def admin_list_users(request: Request, status: str = Query(default="")):
    require_admin(request)

    def _build():
        conn = get_conn()
        q = "SELECT id, created_at, email, name, creator_code, status, role, points_balance, points_total, last_login, note FROM users"
        rows = conn.execute(q + (" WHERE status=? ORDER BY id DESC" if status else " ORDER BY id DESC"),
                            ((status,) if status else ())).fetchall()
        return {"users": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "users",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/users/{uid}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_approve_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "approved", payload.get("note", ""))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "approved"}

@router.post("/api/admin/users/{uid}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_reject_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "rejected", payload.get("note", "Rejected"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "rejected"}

@router.post("/api/admin/users/{uid}/block")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_block_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "blocked", payload.get("reason", "Blocked by admin"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "blocked"}

@router.post("/api/admin/users/{uid}/unblock")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_unblock_user(uid: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _update_user_status(uid, "approved", payload.get("reason", "Unblocked by admin"))
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "approved"}

@router.delete("/api/admin/users/{uid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_delete_user(uid: int, request: Request):
    require_admin(request)
    deps = _load_user_delete_dependencies(uid)
    if any(deps.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "User has historical records and cannot be hard-deleted safely",
                "dependencies": deps,
            },
        )
    deleted = _delete_user_by_id(uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    _invalidate_admin_cache()
    return {"status": "success"}


# ── Submissions admin ──
@router.get("/api/admin/submissions")
def admin_submissions(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, le=200),
    platform: str = Query(default=""),
    status: str = Query(default=""),
    series: str = Query(default=""),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()

        conditions = []
        params: List[Any] = []
        if platform:
            conditions.append("s.platform = ?")
            params.append(platform)
        if status:
            conditions.append("s.detection_status = ?")
            params.append(status)
        if series:
            conditions.append("s.product_series = ?")
            params.append(series)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * limit
        total = c.execute(f"SELECT COUNT(*) FROM submissions s {where}", params).fetchone()[0]
        rows = c.execute(
            f"""SELECT s.*,
                       u.name        AS user_name,
                       u.email       AS user_email,
                       u.creator_code AS user_creator_code
                FROM submissions s
                LEFT JOIN users u ON s.user_id = u.id
                {where}
                ORDER BY s.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            d["display_name"] = d.get("user_name") or d.get("extracted_handle") or "—"
            items.append(d)
        return {"total": total, "page": page, "limit": limit, "items": items}

    return _admin_cache_get_or_build(
        "submissions",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        page=page,
        limit=limit,
        platform=platform or "all",
        status=status or "all",
        series=series or "all",
    )


@router.get("/api/admin/creator/{handle}")
def get_creator_profile_api(handle: str, request: Request):
    require_admin(request)
    from app.services.scoring.creator import get_creator_profile
    profile = get_creator_profile(handle)
    if not profile:
        return {"found": False, "handle": handle}
    return {"found": True, **profile}

@router.get("/api/admin/creators")
def list_creators(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        profiles: list[dict[str, Any]] = []
        user_rows = conn.execute(
            """
            SELECT
                u.id,
                u.created_at,
                u.email,
                u.name,
                u.creator_code,
                u.status,
                u.role,
                u.points_balance,
                u.points_total,
                u.tier_status,
                u.trust_score,
                (SELECT COUNT(*)
                   FROM submissions s
                  WHERE s.user_id=u.id
                    AND LOWER(COALESCE(s.detection_status, '')) NOT IN ('rejected', 'failed', 'prefilter_rejected', 'error')) AS submissions,
                (SELECT ROUND(AVG(COALESCE(s.overall_score, s.final_score, 0)), 1)
                   FROM submissions s
                  WHERE s.user_id=u.id) AS avg_score,
                (SELECT MAX(s.created_at)
                   FROM submissions s
                  WHERE s.user_id=u.id) AS last_seen,
                (SELECT usa.platform
                   FROM user_social_accounts usa
                  WHERE usa.user_id=u.id
                  ORDER BY usa.verified DESC, usa.id DESC
                  LIMIT 1) AS primary_platform,
                (SELECT usa.handle
                   FROM user_social_accounts usa
                  WHERE usa.user_id=u.id
                  ORDER BY usa.verified DESC, usa.id DESC
                  LIMIT 1) AS primary_handle
            FROM users u
            WHERE COALESCE(u.creator_code, '') <> ''
               OR COALESCE(u.role, '') IN ('creator', 'admin')
            ORDER BY submissions DESC, u.id DESC
            LIMIT 500
            """
        ).fetchall()
        for row in user_rows:
            profiles.append(
                {
                    "id": int(row["id"] or 0),
                    "user_id": int(row["id"] or 0),
                    "email": row["email"] or "",
                    "display_name": row["name"] or row["email"] or "",
                    "handle": row["primary_handle"] or row["creator_code"] or "",
                    "primary_handle": row["primary_handle"] or "",
                    "creator_code": row["creator_code"] or "",
                    "status": row["status"] or "",
                    "role": row["role"] or "",
                    "tier_status": row["tier_status"] or "",
                    "points_balance": int(row["points_balance"] or 0),
                    "points_total": int(row["points_total"] or 0),
                    "trust_score": float(row["trust_score"] or 0),
                    "submissions": int(row["submissions"] or 0),
                    "submission_count": int(row["submissions"] or 0),
                    "valid_videos": int(row["submissions"] or 0),
                    "avg_score": float(row["avg_score"] or 0),
                    "score": float(row["avg_score"] or 0),
                    "platform": row["primary_platform"] or "—",
                    "primary_platform": row["primary_platform"] or "—",
                    "last_seen": row["last_seen"] or row["created_at"] or "",
                }
            )
        for p in CREATOR_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if any(str(item.get("handle") or "").lower() == str(data.get("handle") or p.stem).lower() for item in profiles):
                    continue
                profiles.append({
                    "handle": data.get("handle", p.stem),
                    "platform": data.get("platform", ""),
                    "submission_count": data.get("submission_count", 0),
                    "cameras": data.get("cameras", []),
                    "viltrox_lenses": data.get("viltrox_lenses", []),
                    "last_seen": data.get("last_seen", ""),
                    "competitor_brands_seen": data.get("competitor_brands_seen", []),
                })
            except Exception:
                logger.warning("admin.creator_profile_read_failed", extra={"path": str(p)}, exc_info=True)
        profiles.sort(key=lambda x: x["submission_count"], reverse=True)
        return {"total": len(profiles), "creators": profiles}

    return _admin_cache_get_or_build(
        "creators",
        _build,
        ttl=max(10, ADMIN_READ_CACHE_TTL_SEC),
    )


@router.get("/api/admin/creator-public/shop-heroes")
def admin_list_creator_shop_heroes(request: Request, user_id: int = Query(0)):
    require_admin(request)
    try:
        return {"status": "success", "shopHeroes": list_creator_shop_heroes(int(user_id), include_inactive=True)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/admin/creator-public/shop-heroes")
async def admin_upsert_creator_shop_hero(request: Request):
    require_admin(request)
    try:
        payload = await request.json()
        hero = upsert_creator_shop_hero(payload if isinstance(payload, dict) else {})
        _invalidate_admin_cache()
        return {"status": "success", "shopHero": hero}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/admin/creator-public/shop-heroes/{hero_id}")
def admin_delete_creator_shop_hero(hero_id: str, request: Request):
    require_admin(request)
    try:
        result = delete_creator_shop_hero(hero_id)
        _invalidate_admin_cache()
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/admin/stats")
def admin_stats(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        total     = c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        confirmed = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='confirmed'").fetchone()[0]
        suspected = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='suspected'").fetchone()[0]
        not_detected = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='not_detected'").fetchone()[0]
        avg_final   = c.execute("SELECT ROUND(AVG(final_score),1) FROM submissions WHERE detection_status='confirmed'").fetchone()[0] or 0
        avg_creator = c.execute("SELECT ROUND(AVG(creator_score),1) FROM submissions").fetchone()[0] or 0

        agg = c.execute(
            "SELECT COALESCE(SUM(views),0), COALESCE(SUM(likes),0), "
            "COALESCE(SUM(comments),0), COALESCE(SUM(shares),0), COALESCE(SUM(favorites),0) "
            "FROM submissions"
        ).fetchone()
        total_views, total_likes, total_comments, total_shares, total_favorites = agg

        handles = c.execute("SELECT CASE WHEN extracted_handle!='' THEN extracted_handle ELSE url END FROM submissions").fetchall()
        unique_creators = len(set(h[0] for h in handles if h[0]))

        by_date = c.execute("SELECT substr(created_at,1,10) as day, COUNT(*) as n FROM submissions WHERE created_at >= date('now','-90 days') GROUP BY day ORDER BY day ASC").fetchall()
        by_platform = c.execute("SELECT platform, COUNT(*) as n FROM submissions GROUP BY platform ORDER BY n DESC").fetchall()
        by_series = c.execute("SELECT product_series, COUNT(*) as n FROM submissions WHERE product_series!='' GROUP BY product_series ORDER BY n DESC").fetchall()
        by_status = c.execute("SELECT detection_status, COUNT(*) as n FROM submissions GROUP BY detection_status").fetchall()
        top_scores = c.execute("SELECT id, created_at, platform, title, overall_score, final_score, creator_score, recommendation FROM submissions ORDER BY overall_score DESC LIMIT 10").fetchall()

        try:
            pending_ver = conn.execute("SELECT COUNT(*) FROM verifications WHERE status='pending'").fetchone()[0] if table_exists("verifications") else 0
        except Exception:
            logger.warning("admin.pending_verification_count_failed", exc_info=True)
            pending_ver = 0

        return {
            "total":             total,
            "confirmed":         confirmed,
            "suspected":         suspected,
            "not_detected":      not_detected,
            "avg_final_score":   round(avg_final or 0, 1),
            "avg_creator_score": round(avg_creator or 0, 1),
            "total_views":       total_views,
            "total_likes":       total_likes,
            "total_comments":    total_comments,
            "total_shares":      total_shares,
            "total_favorites":   total_favorites,
            "unique_creators":   unique_creators,
            "by_date":           [{"date": r[0], "count": r[1]} for r in by_date],
            "by_platform":       [{"platform": r[0], "count": r[1]} for r in by_platform],
            "by_series":         [{"series": r[0], "count": r[1]} for r in by_series],
            "by_status":         [{"status": r[0], "count": r[1]} for r in by_status],
            "top_scores":        [dict(zip(["id","created_at","platform","title","overall_score","final_score","creator_score","recommendation"], r)) for r in top_scores],
            "pending_verifications": pending_ver,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    return _admin_cache_get_or_build(
        "stats",
        _build,
        ttl=ADMIN_STATS_CACHE_TTL_SEC,
    )


@router.delete("/api/admin/submissions/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def delete_submission(submission_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    reversed_points: dict[str, Any] | None = None
    try:
        row = conn.execute("SELECT id FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Submission not found")
        reversed_points = reverse_submission_points(
            submission_id,
            reason=f"Submission #{submission_id} deleted by admin",
            conn=conn,
            commit=False,
        )
        cur = conn.execute("DELETE FROM submissions WHERE id=?", (submission_id,))
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Submission not found")
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("admin.delete_submission_failed", extra={"submission_id": submission_id})
        raise HTTPException(status_code=500, detail="Could not delete submission")
    _refresh_user_points_state((reversed_points or {}).get("user_id"), reason="submission_delete")
    _invalidate_admin_cache()
    return {"status": "deleted", "id": submission_id}


# ── Approve / Reject ──
@router.post("/api/admin/submissions/manual")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_add_submission(req: ManualSubmissionRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = (
        now, req.platform, req.url, req.extracted_handle, req.title,
        req.detection_status, req.product_series, req.product_label,
        req.final_score, req.creator_score, req.overall_score,
        req.views, req.likes, req.comments, req.shares,
        req.recommendation, req.memo or f"Manually added at {now}",
    )
    sql = """INSERT INTO submissions
        (created_at, platform, url, extracted_handle, title,
         detection_status, product_series, product_label,
         final_score, creator_score, overall_score, risk_score,
         views, likes, comments, shares, favorites,
         recommendation, memo, scraped_ok)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,0,?,?,0)"""
    if is_postgres_runtime():
        c.execute(sql + " RETURNING id", params)
        inserted = c.fetchone()
        new_id = inserted["id"] if inserted else 0
    else:
        c.execute(sql, params)
        new_id = c.lastrowid
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "created", "id": new_id}


# ── Account Verification Endpoints ──
@router.post("/api/verify/register")
@rate_limit("verify_binding", max_requests=10, window_sec=300)
def register_verification(req: VerifyRegisterRequest, request: Request):
    require_admin(request)
    _register_verification_request(
        req.platform.lower(),
        req.handle.lstrip("@"),
        req.code,
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _invalidate_admin_cache()
    return {"status": "registered", "code": req.code}

@router.get("/api/admin/verifications")
def list_verifications(request: Request, status: str = ""):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        if status:
            rows = c.execute("SELECT * FROM verifications WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM verifications ORDER BY created_at DESC").fetchall()
        cols = [d[0] for d in c.description]
        return {"items": [dict(zip(cols, r)) for r in rows]}

    return _admin_cache_get_or_build(
        "verifications",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/verifications/{ver_id}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def approve_verification(ver_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    row = _approve_verification_override(
        ver_id,
        payload.get("note", ""),
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Verification not found")
    _invalidate_admin_cache()
    return {"status": "approved_override", "platform": row["platform"], "handle": row["handle"]}

@router.post("/api/admin/verifications/{ver_id}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def reject_verification(ver_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _reject_verification_record(ver_id, payload.get("note", ""))
    if not updated:
        raise HTTPException(status_code=404, detail="Verification not found")
    _invalidate_admin_cache()
    return {"status": "rejected"}

@router.delete("/api/admin/verifications/{ver_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def delete_verification(ver_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM verifications WHERE id=?", (ver_id,))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "deleted"}


@router.post("/api/admin/reanalyze/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def reanalyze_submission(submission_id: int, request: Request, req: ReanalyzeRequest = None):
    await require_admin_async(request)
    from app.services.ai.orchestrator import VideoJobInput

    if req is None: req = ReanalyzeRequest()
    row = await db_read(partial(_load_submission_row, submission_id))
    if not row: return {"status": "error", "message": "Submission not found"}
    r_dict = dict(row)

    # Build a VideoJobInput from existing submission
    submission_url = req.url or r_dict["url"] or ""
    platform = r_dict["platform"] or ""
    handle = r_dict["extracted_handle"] or ""
    title = r_dict["title"] or ""

    # For uploaded videos, pass the file path
    uploaded_video = None
    if platform == "Uploaded Video" or not submission_url:
        video_path = r_dict.get("video_path") or ""
        if not video_path:
            try:
                va_ex = json.loads(r_dict.get("video_analysis") or "{}")
                video_path = va_ex.get("path", "") or ""
            except Exception:
                logger.warning(
                    "admin.reanalyze_video_analysis_parse_failed",
                    extra={"submission_id": submission_id},
                    exc_info=True,
                )
        if video_path and os.path.exists(video_path):
            uploaded_video = {
                "path": video_path,
                "filename": os.path.basename(video_path),
            }
        else:
            return {"status": "error", "message": "Video file not found. Re-upload to analyze."}

    queued_metrics = {
        "views": r_dict.get("views", 0) or 0,
        "likes": r_dict.get("likes", 0) or 0,
        "comments": r_dict.get("comments", 0) or 0,
        "shares": r_dict.get("shares", 0) or 0,
        "favorites": r_dict.get("favorites", 0) or 0,
    }

    job = VideoJobInput(
        submission_id=submission_id,
        url=submission_url,
        title=title,
        handle=handle,
        platform=platform,
        caption=r_dict.get("caption", "") or "",
        scraped_text=r_dict.get("raw_text", "") or "",
        gpt_already_done=False,
        uploaded_video=uploaded_video,
        metrics=queued_metrics,
        hints={},
    )

    try:
        await db_write(partial(_mark_submission_reanalyze, submission_id))
        queue = getattr(request.app.state, "job_queue", None)
        if queue is None:
            raise RuntimeError("job queue not available")
        task_id = await queue.enqueue(
            "audit_submission",
            job,
            submission_id=submission_id,
        )
        return {
            "status": "queued",
            "submission_id": submission_id,
            "job_id": task_id,
            "message": "Reanalysis queued",
        }
    except Exception as e:
        logger.exception("admin.reanalyze_failed", extra={"submission_id": submission_id})
        return {"status": "error", "message": "Could not queue reanalysis"}
    finally:
        _invalidate_admin_cache()

@router.get("/api/videos/{submission_id}")
def serve_video(submission_id: int):
    row = _load_submission_row(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return resolve_video_response(row)


@router.get("/api/admin/best_frame/{submission_id}")
def serve_best_frame(submission_id: int):
    row = _load_submission_row(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return resolve_poster_response(row)

@router.post("/api/admin/approve/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_approve(submission_id: int, request: Request, req: ManualApproveRequest = None):
    require_admin(request)
    if req is None: req = ManualApproveRequest()
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")

    r = dict(row)
    hint_bonus = sum([15 if req.hints and req.hints.get("logo") else 0,
                      12 if req.hints and req.hints.get("product") else 0,
                      10 if req.hints and req.hints.get("voice") else 0,
                      10 if req.hints and req.hints.get("review") else 0])

    new_campaign = min(400, req.campaign_score if req.campaign_score is not None else (r.get("final_score") or 0) + hint_bonus)
    new_creator  = req.creator_score  if req.creator_score  is not None else r.get("creator_score") or 0
    new_overall  = req.overall_score  if req.overall_score  is not None else round(new_campaign * 0.7 + new_creator * 0.3)
    new_series   = req.product_series if req.product_series else r.get("product_series") or "VILTROX"
    new_label    = req.product_label  if req.product_label  else r.get("product_label")

    memo = r.get("memo") or ""
    if hint_bonus > 0: memo += f" [Manual hint bonus applied: +{hint_bonus}]"
    if req.memo_append: memo += f" [Admin note: {req.memo_append}]"

    try:
        c.execute(
            """UPDATE submissions
               SET detection_status=?, recommendation=?, final_score=?, creator_score=?,
                   overall_score=?, product_series=?, product_label=?, memo=?
               WHERE id=?""",
            ("confirmed", "Approved by admin review", new_campaign, new_creator, new_overall, new_series, new_label, memo, submission_id),
        )

        try:
            va_row = conn.execute("SELECT video_analysis, tech_score FROM submissions WHERE id=?", (submission_id,)).fetchone()
            if va_row and not va_row["tech_score"]:
                va_data = json.loads(va_row["video_analysis"] or "{}") if va_row["video_analysis"] else {}
                ts = va_data.get("tech_score", 0); ms = va_data.get("marketing_score", 0); genre = va_data.get("content_genre", "")
                if ts > 0:
                    pct = update_genre_benchmark(genre, ts, ms) if genre else {}
                    c.execute(
                        "UPDATE submissions SET tech_score=?, marketing_score=?, content_genre=?, percentile_tech=?, percentile_mkt=? WHERE id=?",
                        (ts, ms, genre, pct.get("percentile_tech", 0), pct.get("percentile_mkt", 0), submission_id),
                    )
        except Exception:
            logger.exception("admin.approve_score_sync_failed", extra={"submission_id": submission_id})

        pts_result = auto_award_points(
            submission_id,
            r.get("extracted_handle", ""),
            new_campaign,
            conn=conn,
            commit=False,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("admin.manual_approve_failed", extra={"submission_id": submission_id})
        raise HTTPException(status_code=500, detail="Could not approve submission")

    _refresh_user_points_state(pts_result.get("user_id"), reason="points_award")
    try:
        old_series = (r.get("product_series") or "").strip()
        old_label = (r.get("product_label") or "").strip()
        if (new_series or new_label) and (new_series != old_series or new_label != old_label):
            learned_text = " ".join(
                filter(
                    None,
                    [
                        r.get("title") or "",
                        r.get("memo") or "",
                        (json.loads(r.get("video_analysis") or "{}").get("notes", "") if r.get("video_analysis") else ""),
                    ],
                )
            )
            from app.services.audit.learning import record_correction
            record_correction(
                submission_id=submission_id,
                url=r.get("url") or "",
                correct_series=new_series or old_series,
                correct_label=new_label or old_label,
                learned_text=learned_text,
                note=req.memo_append or "",
            )
    except Exception as e:
        logger.exception("admin.approve_learning_sync_failed", extra={"submission_id": submission_id})
    _invalidate_admin_cache()
    return {"status": "approved", "id": submission_id, "campaign_score": new_campaign, "points_awarded": pts_result.get("points", 0)}

@router.post("/api/admin/reject/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_reject(submission_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    conn = get_conn()
    c = conn.cursor()
    cur = c.execute(
        "UPDATE submissions SET detection_status=?, recommendation=?, final_score=?, creator_score=?, overall_score=? WHERE id=?",
        ("not_detected", f"Rejected: {payload.get('note', 'Rejected by admin')}", 0, 0, 0, submission_id),
    )
    conn.commit()
    if cur.rowcount != 1:
        raise HTTPException(status_code=404, detail="Submission not found")
    _invalidate_admin_cache()
    return {"status": "rejected", "id": submission_id}

# ── VIOS Dashboard ──
@router.get("/api/vios/dashboard")
def vios_dashboard(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        total, confirmed, suspected, not_detected = c.execute("""SELECT COUNT(*), SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END), SUM(CASE WHEN detection_status='suspected' THEN 1 ELSE 0 END), SUM(CASE WHEN detection_status='not_detected' THEN 1 ELSE 0 END) FROM submissions""").fetchone()
        views, likes, comments, shares = c.execute("SELECT COALESCE(SUM(views),0), COALESCE(SUM(likes),0), COALESCE(SUM(comments),0), COALESCE(SUM(shares),0) FROM submissions").fetchone()
        avg_campaign, avg_creator = c.execute("SELECT ROUND(AVG(final_score),1), ROUND(AVG(creator_score),1) FROM submissions WHERE detection_status='confirmed'").fetchone()
        products = c.execute("SELECT product_series, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes, ROUND(AVG(final_score),0) as avg_score FROM submissions WHERE product_series IS NOT NULL AND product_series != '' GROUP BY product_series ORDER BY cnt DESC LIMIT 15").fetchall()
        platforms = c.execute("SELECT platform, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes, ROUND(AVG(creator_score),1) as avg_creator FROM submissions GROUP BY platform ORDER BY cnt DESC").fetchall()
        top_creators = c.execute("SELECT extracted_handle, platform, COUNT(*) as submissions, ROUND(AVG(creator_score),0) as avg_creator, COALESCE(SUM(views),0) as total_views, COALESCE(SUM(likes),0) as total_likes, MAX(final_score) as best_score, MAX(detection_status) as status FROM submissions WHERE extracted_handle IS NOT NULL AND extracted_handle != '' GROUP BY extracted_handle, platform ORDER BY avg_creator DESC, total_views DESC LIMIT 20").fetchall()
        trend = c.execute("SELECT DATE(created_at) as day, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes FROM submissions WHERE created_at >= DATE('now', '-30 days') GROUP BY day ORDER BY day").fetchall()
        score_dist = c.execute("SELECT SUM(CASE WHEN final_score >= 300 THEN 1 ELSE 0 END) as elite, SUM(CASE WHEN final_score >= 200 AND final_score < 300 THEN 1 ELSE 0 END) as high, SUM(CASE WHEN final_score >= 100 AND final_score < 200 THEN 1 ELSE 0 END) as mid, SUM(CASE WHEN final_score > 0 AND final_score < 100 THEN 1 ELSE 0 END) as low, SUM(CASE WHEN final_score = 0 THEN 1 ELSE 0 END) as zero FROM submissions").fetchone()
        pending_ver = c.execute("SELECT COUNT(*) FROM verifications WHERE status='pending'").fetchone()[0] if table_exists("verifications") else 0
        recent = c.execute("SELECT id, created_at, platform, extracted_handle, title, detection_status, product_series, final_score, creator_score, views, likes, recommendation FROM submissions ORDER BY created_at DESC LIMIT 20").fetchall()
        return {
            "summary": {"total": total or 0, "confirmed": confirmed or 0, "suspected": suspected or 0, "not_detected": not_detected or 0, "avg_campaign": avg_campaign or 0, "avg_creator": avg_creator or 0, "total_views": views or 0, "total_likes": likes or 0, "total_comments": comments or 0, "total_shares": shares or 0, "pending_verifications": pending_ver, "score_dist": {"elite": score_dist[0] or 0, "high":  score_dist[1] or 0, "mid":   score_dist[2] or 0, "low":   score_dist[3] or 0, "zero":  score_dist[4] or 0}},
            "products": [{"series": r[0], "count": r[1], "views": r[2], "likes": r[3], "avg_score": r[4]} for r in products],
            "platforms": [{"platform": r[0], "count": r[1], "views": r[2], "likes": r[3], "avg_creator": r[4]} for r in platforms],
            "creators": [{"handle": r[0], "platform": r[1], "submissions": r[2], "avg_creator": r[3], "total_views": r[4], "total_likes": r[5], "best_score": r[6], "status": r[7]} for r in top_creators],
            "trend": [{"date": r[0], "count": r[1], "views": r[2], "likes": r[3]} for r in trend],
            "recent": [dict(zip(["id","created_at","platform","handle","title","status","product","campaign","creator","views","likes","rec"], r)) for r in recent],
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    return _admin_cache_get_or_build(
        "vios_dashboard",
        _build,
        ttl=ADMIN_STATS_CACHE_TTL_SEC,
    )

@router.get("/api/vios/share-token")
def generate_share_token(request: Request):
    require_admin(request)
    token = _secrets.token_urlsafe(16)
    now = datetime.utcnow()
    expires = now + timedelta(days=7)
    _persist_share_token(
        token,
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return {"token": token, "expires_in": "7 days"}

@router.get("/api/vios/verify-token/{token}")
def verify_share_token(token: str):
    meta = _load_share_token_meta(token)
    if meta:
        return {"valid": True, "meta": meta}
    return {"valid": False}

@router.get("/api/admin/rewards")
def admin_list_rewards(request: Request, status: str = Query(default="")):
    require_admin(request)

    def _build():
        conn = get_conn()
        if status: rows = conn.execute("SELECT * FROM reward_catalog WHERE status=? ORDER BY sort_order ASC, id DESC", (status,)).fetchall()
        else: rows = conn.execute("SELECT * FROM reward_catalog ORDER BY sort_order ASC, id DESC").fetchall()
        return {"rewards": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "rewards",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/rewards")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_create_reward(req: RewardItemRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = (
        now,
        now,
        req.title.strip(),
        req.description.strip(),
        req.category.strip(),
        int(req.points_cost),
        req.meta_label.strip(),
        req.image_url.strip(),
        int(req.stock),
        int(req.sort_order),
        req.status.strip() or "draft",
    )
    sql = "INSERT INTO reward_catalog (created_at, updated_at, title, description, category, points_cost, meta_label, image_url, stock, sort_order, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        reward_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        reward_id = int(cur.lastrowid)
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "success", "id": reward_id}

@router.patch("/api/admin/rewards/{rid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_update_reward(rid: int, req: RewardItemRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET updated_at=?, title=?, description=?, category=?, points_cost=?, meta_label=?, image_url=?, stock=?, sort_order=?, status=? WHERE id=?",
        (now, req.title.strip(), req.description.strip(), req.category.strip(), int(req.points_cost), req.meta_label.strip(), req.image_url.strip(), int(req.stock), int(req.sort_order), req.status.strip() or "draft", rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "updated"}

@router.post("/api/admin/rewards/{rid}/publish")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_publish_reward(rid: int, request: Request):
    admin = require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET status='published', updated_at=?, published_at=?, published_by=? WHERE id=?", (now, now, admin["id"], rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "published"}

@router.post("/api/admin/rewards/{rid}/archive")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_archive_reward(rid: int, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET status='archived', updated_at=? WHERE id=?", (now, rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "archived"}

@router.delete("/api/admin/rewards/{rid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_delete_reward(rid: int, request: Request):
    """永久删除奖品（不是下架）。如果有 redemption 关联会保留但显示为 deleted。"""
    require_admin(request)
    conn = get_conn()
    in_use = conn.execute(
        "SELECT COUNT(*) FROM redemptions WHERE reward_id=?", (rid,)
    ).fetchone()[0]
    if in_use > 0:
        # Soft delete: mark as archived instead
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE reward_catalog SET status='archived', updated_at=? WHERE id=?",
            (now, rid)
        )
        conn.commit()
        _invalidate_admin_cache()
        return {"status": "archived", "message": f"已存在 {in_use} 条兑换记录，已改为下架而非删除"}
    conn.execute("DELETE FROM reward_catalog WHERE id=?", (rid,))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "deleted"}


# ── Points log API ──
@router.get("/api/admin/points-log")
def admin_points_log(
    request: Request,
    uid: int = 0,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=20, le=500),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        params: list[Any] = []
        where = ""
        if uid:
            where = "WHERE pl.user_id=?"
            params.append(uid)
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM points_log pl LEFT JOIN users u ON pl.user_id=u.id {where}",
                params,
            ).fetchone()[0]
            or 0
        )
        offset = (page - 1) * limit
        rows = conn.execute(
            f"""SELECT pl.*,u.email
                FROM points_log pl
                LEFT JOIN users u ON pl.user_id=u.id
                {where}
                ORDER BY pl.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "page": page, "limit": limit, "log": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "points_log",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        uid=uid or "all",
        page=page,
        limit=limit,
    )

@router.post("/api/admin/users/{uid}/adjust_points")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_adjust_points(uid: int, request: Request):
    await require_admin_async(request)
    body   = await request.json()
    delta  = int(body.get("delta", 0))
    reason = body.get("reason", "Admin adjustment")
    if delta == 0: return {"status":"error","message":"delta cannot be 0"}
    now  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_bal = await db_write(partial(_adjust_user_points, uid, delta, reason, now))
    if new_bal is None:
        return {"status":"error","message":"User not found"}
    _refresh_user_points_state(uid, reason="admin_adjust_points")
    _invalidate_admin_cache()
    return {"status":"success","new_balance":new_bal,"delta":delta}

# ── Leaderboard ──
@router.get("/api/admin/leaderboard")
def get_leaderboard(request: Request, period: str = Query(default="month")):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        if period == "month": date_filter = "AND s.created_at >= date('now', '-30 days')"
        elif period == "year": date_filter = "AND s.created_at >= date('now', '-365 days')"
        else: date_filter = ""

        rows = c.execute(f"""
            SELECT s.extracted_handle,
                   COALESCE(MAX(u.name), '') AS user_name,
                   COALESCE(MAX(u.creator_code), '') AS creator_code,
                   COALESCE(MAX(s.platform), '') AS platform,
                   COUNT(*) as submissions,
                   COALESCE(SUM(s.views), 0) as total_views,
                   COALESCE(SUM(s.likes), 0) as total_likes,
                   COALESCE(SUM(s.comments), 0) as total_comments,
                   COALESCE(SUM(s.shares), 0) as total_shares,
                   COALESCE(SUM(s.favorites), 0) as total_favorites,
                   ROUND(AVG(s.overall_score), 0) as avg_score,
                   MAX(s.overall_score) as best_score,
                   COALESCE(SUM(s.final_score), 0) as total_campaign_score,
                   COALESCE(SUM(COALESCE(s.points_awarded, 0) + COALESCE(s.points_pending, 0)), 0) as total_points,
                   GROUP_CONCAT(DISTINCT s.platform) as platforms
            FROM submissions s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.extracted_handle IS NOT NULL AND s.extracted_handle != ''
                  AND s.detection_status = 'confirmed' {date_filter}
            GROUP BY s.extracted_handle
            ORDER BY total_views DESC, avg_score DESC LIMIT 50
        """).fetchall()
        cols = ["handle","user_name","creator_code","platform","submissions","total_views","total_likes","total_comments","total_shares","total_favorites","avg_score","best_score","total_campaign_score","total_points","platforms"]

        items = []
        for i, r in enumerate(rows):
            d = dict(zip(cols, r))
            d["rank"] = i + 1
            d["estimated_points"] = int(d.get("total_points") or 0)
            d["display_name"] = d.get("user_name") or d.get("handle") or "—"
            items.append(d)
        return {"period": period, "total": len(items), "items": items}

    return _admin_cache_get_or_build(
        "leaderboard",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        period=period,
    )

# ── Insights ──
@router.get("/api/admin/insights")
async def get_insights(request: Request, days: int = Query(default=90), refresh: bool = Query(default=False)):
    await require_admin_async(request)
    from app.db.repositories.insights import compute_market_insights
    if not refresh:
        try:
            row = await db_read(_load_cached_insights_row)
            if row:
                cache_dt = datetime.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
                if (datetime.utcnow() - cache_dt).total_seconds() / 3600 < 24:
                    return json.loads(row["value"])
        except Exception:
            logger.warning("admin.insights_cache_read_failed", exc_info=True)
    return await asyncio.to_thread(compute_market_insights, days)

@router.get("/api/admin/benchmarks")
def get_benchmarks(request: Request):
    require_admin(request)
    return get_all_benchmarks()

@router.get("/api/admin/creator/{handle}/growth")
def get_creator_growth(handle: str, request: Request):
    require_admin(request)
    from app.services.scoring.creator import get_creator_profile, compute_creator_trend

    def _build():
        clean_handle = handle.lstrip("@")
        profile = (
            get_creator_profile(clean_handle)
            or get_creator_profile(handle)
            or get_creator_profile("@" + clean_handle)
            or {}
        )

        try:
            conn = get_conn()
            handle_variants = [clean_handle, "@" + clean_handle, handle]
            rows = conn.execute(
                "SELECT id, created_at, content_genre, tech_score, marketing_score, "
                "overall_score, final_score, percentile_tech, percentile_mkt, "
                "platform, title, video_analysis FROM submissions "
                "WHERE extracted_handle IN (?,?,?) ORDER BY created_at ASC",
                handle_variants
            ).fetchall()
            submissions_timeline = []
            agg_cameras = set(profile.get("cameras", []))
            agg_lenses = set(profile.get("viltrox_lenses", []))
            agg_competitors = set(profile.get("competitor_brands_seen", []))
            scores_for_avg = []

            for r in rows:
                row = dict(r)
                va_str = row.pop("video_analysis", None)
                if va_str:
                    try:
                        va = json.loads(va_str) if isinstance(va_str, str) else va_str
                        if va.get("camera_body"):
                            agg_cameras.add(va["camera_body"])
                        if va.get("viltrox_lens"):
                            agg_lenses.add(va["viltrox_lens"])
                        for vp in (va.get("viltrox_products_all") or []):
                            agg_lenses.add(vp)
                        for cb in (va.get("competitor_brands") or []):
                            agg_competitors.add(cb)
                    except Exception:
                        logger.warning(
                            "admin.creator_growth_video_analysis_parse_failed",
                            extra={"handle": clean_handle},
                            exc_info=True,
                        )
                if row.get("tech_score") and row.get("marketing_score"):
                    scores_for_avg.append({
                        "tech": row["tech_score"],
                        "mkt": row["marketing_score"],
                        "overall": row.get("overall_score", 0),
                    })
                submissions_timeline.append(row)
        except Exception as e:
            logger.exception("admin.creator_growth_query_failed", extra={"handle": clean_handle})
            submissions_timeline = []
            agg_cameras = set(profile.get("cameras", []))
            agg_lenses = set(profile.get("viltrox_lenses", []))
            agg_competitors = set(profile.get("competitor_brands_seen", []))
            scores_for_avg = []

        if not profile and not submissions_timeline:
            return {"error": "Creator not found"}

        normalized_handle = clean_handle
        submission_count = profile.get("submission_count") or len(submissions_timeline)
        last_seen = profile.get("last_seen") or (
            submissions_timeline[-1].get("created_at", "") if submissions_timeline else ""
        )
        avg_scores = profile.get("avg_scores") or (
            {
                "tech": round(sum(s["tech"] for s in scores_for_avg) / len(scores_for_avg), 1),
                "mkt":  round(sum(s["mkt"]  for s in scores_for_avg) / len(scores_for_avg), 1),
                "overall": round(sum(s["overall"] for s in scores_for_avg) / len(scores_for_avg), 1),
            } if scores_for_avg else {}
        )

        trend = profile.get("trend") or (
            compute_creator_trend(profile.get("score_history", []))
            if len(profile.get("score_history", [])) >= 2
            else {"direction": "new"}
        )

        benchmarks = get_all_benchmarks()
        genre = profile.get("genre") or (submissions_timeline[-1]["content_genre"] if submissions_timeline else "")
        bench = benchmarks.get(genre, {})

        return {
            "handle": normalized_handle,
            "submission_count": submission_count,
            "last_seen": last_seen,
            "cameras": sorted(agg_cameras),
            "viltrox_lenses": sorted(agg_lenses),
            "competitor_brands_seen": sorted(agg_competitors),
            "avg_scores": avg_scores,
            "weak_areas": profile.get("weak_areas", []),
            "trend": trend,
            "score_history": profile.get("score_history", []),
            "submissions_timeline": submissions_timeline,
            "genre_benchmark": bench,
        }

    return _admin_cache_get_or_build(
        "creator_growth",
        _build,
        ttl=max(10, ADMIN_READ_CACHE_TTL_SEC),
        handle=handle,
    )

@router.get("/api/admin/redemptions")
def admin_get_redemptions(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=20, le=500),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        total = int(conn.execute("SELECT COUNT(*) FROM redemptions").fetchone()[0] or 0)
        offset = (page - 1) * limit
        rows = conn.execute("""
            SELECT r.*, u.email, u.name as user_name
            FROM redemptions r
            LEFT JOIN users u ON r.user_id = u.id
            ORDER BY r.id DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return {"total": total, "page": page, "limit": limit, "items": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "redemptions",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        page=page,
        limit=limit,
    )


@router.get("/api/admin/affiliate")
def admin_get_affiliate_ops(request: Request, limit: int = Query(default=200, ge=20, le=600)):
    require_admin(request)

    def _build():
        return build_affiliate_ops_snapshot(limit=limit)

    return _admin_cache_get_or_build(
        "affiliate_ops",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        limit=limit,
    )


@router.post("/api/admin/redemptions/{rid}/update")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_update_redemption(rid: int, request: Request):
    await require_admin_async(request)
    body = await request.json()
    updated = await db_write(
        partial(
            _update_redemption_record,
            rid,
            body.get("status", "pending"),
            body.get("tracking_number", ""),
            body.get("admin_note", body.get("note", "")),
        )
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Redemption not found")
    _invalidate_admin_cache()
    return {"status": "updated"}


@router.post("/api/admin/users/{uid}/grant_points")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_grant_points(uid: int, request: Request):
    await require_admin_async(request)
    body = await request.json()
    points = int(body.get("points", 0))
    reason = body.get("reason", "Admin grant")
    if points <= 0:
        return {"status": "error", "message": "Points must be greater than 0"}
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_bal = await db_write(partial(_grant_points_to_user, uid, points, reason, now))
    if new_bal is None:
        return {"status": "error", "message": "User not found"}
    _refresh_user_points_state(uid, reason="admin_grant_points")
    _invalidate_admin_cache()
    return {"status": "success", "new_balance": new_bal}


# ──────────────────────────────────────────────
# Product Learning System
# 管理员手动纠正识别错误，系统记住下次自动应用
# ──────────────────────────────────────────────

@router.get("/api/admin/product_catalog")
def get_product_catalog(request: Request):
    """返回完整产品目录，前端纠正下拉框用"""
    require_admin(request)
    from app.core.constants import PRODUCT_RULES

    def _build():
        catalog = []
        seen = set()
        for item in PRODUCT_RULES:
            key = f"{item['series']}|{item['label']}"
            if key not in seen:
                seen.add(key)
                catalog.append({
                    "series": item["series"],
                    "label": item["label"],
                })
        try:
            from app.db.repositories.knowledge import list_product_knowledge_rules
            for item in list_product_knowledge_rules(limit=500):
                key = f"{item['series']}|{item['label']}"
                if key not in seen:
                    seen.add(key)
                    catalog.append({
                        "series": item["series"],
                        "label": item["label"],
                    })
        except Exception:
            logger.warning("admin.product_catalog_rule_load_failed", exc_info=True)
        return {"total": len(catalog), "items": catalog}

    return _admin_cache_get_or_build(
        "product_catalog",
        _build,
        ttl=60,
    )


@router.post("/api/admin/submissions/{submission_id}/correct")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def correct_submission_product(submission_id: int, request: Request):
    """
    管理员手动纠正一条投稿的产品识别。

    Body:
        {
            "correct_series": "DL",
            "correct_label": "AF 90mm F3.5 DL",
            "note": "DJI Inspire 3 + 90mm DL镜头"
        }
    """
    await require_admin_async(request)
    from app.services.audit.learning import record_correction

    body = await request.json()
    correct_series = body.get("correct_series", "").strip()
    correct_label = body.get("correct_label", "").strip()
    note = body.get("note", "").strip()

    if not correct_series or not correct_label:
        return {"status": "error", "message": "Both correct_series and correct_label required"}

    row = await db_read(partial(_load_submission_product_context, submission_id))
    if not row:
        return {"status": "error", "message": "Submission not found"}

    url = row["url"] or ""
    title = row["title"] or ""

    # Build learned text from video_analysis for keyword extraction
    learned_text = title
    try:
        va = json.loads(row["video_analysis"] or "{}")
        learned_text += " " + (va.get("notes") or "")
        learned_text += " " + (va.get("camera_body") or "")
        learned_text += " " + (va.get("viltrox_lens") or "")
        learned_text += " " + " ".join(va.get("brand_elements") or [])
        learned_text += " " + " ".join(va.get("products_detected") or [])
        learned_text += " " + " ".join(va.get("viltrox_products_all") or [])
    except Exception:
        logger.warning("admin.correct_submission_video_analysis_parse_failed", extra={"submission_id": submission_id}, exc_info=True)

    # Update the submission immediately
    await db_write(partial(_update_submission_product, submission_id, correct_series, correct_label, note))

    # Record the correction for future learning
    result = await asyncio.to_thread(
        record_correction,
        submission_id=submission_id,
        url=url,
        correct_series=correct_series,
        correct_label=correct_label,
        learned_text=learned_text,
        note=note,
    )
    _invalidate_admin_cache()

    return {
        "status": "success",
        "submission_id": submission_id,
        "correct_series": correct_series,
        "correct_label": correct_label,
        "learning": result,
    }


@router.get("/api/admin/learning/stats")
def learning_stats(request: Request):
    """学习系统统计"""
    require_admin(request)
    from app.services.audit.learning import get_correction_stats
    return get_correction_stats()


@router.get("/api/admin/learning/corrections")
def list_corrections(request: Request, limit: int = Query(default=100, le=500)):
    """列出所有学习记录"""
    require_admin(request)
    from app.services.audit.learning import list_all_corrections
    return {"items": list_all_corrections(limit)}


@router.delete("/api/admin/learning/corrections")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def delete_correction_endpoint(request: Request):
    """删除某个 URL 的学习记录"""
    await require_admin_async(request)
    from app.services.audit.learning import delete_correction
    body = await request.json()
    url = body.get("url", "")
    if not url:
        return {"status": "error", "message": "url required"}
    deleted = await asyncio.to_thread(delete_correction, url)
    if deleted:
        _invalidate_admin_cache()
    return {"status": "ok" if deleted else "not_found", "deleted": deleted}
