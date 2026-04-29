"""
api/routers/creator.py — 创作者路由 (/api/creator/*)
"""
from __future__ import annotations

import json
import secrets as _secrets_mod
from datetime import datetime
from functools import partial

from fastapi import APIRouter, Query, Request

from app.core.logging import get_logger
from app.services.cache import cache_clear, cached
from app.db.connection import db_read, db_write, get_conn, is_postgres_runtime
from app.db.repositories.assets import get_submission_asset
from app.db.repositories.users import (
    refresh_user_social_verified,
    sanitize_social_identity,
    upsert_pending_social_account,
)
from app.core.security import get_current_user, get_current_user_async, invalidate_user_cache
from app.services.memory.creator_memory import build_creator_memory_snapshot
from app.services.creator_program import build_creator_program_snapshot
from app.services.media.storage import resolve_local_media_path
from app.utils.handles import normalize_platform, normalize_handle
from app.services.verification.viltrox_official import (
    build_profile_url,
    detect_platform_from_profile_url,
    extract_handle_from_profile_url,
    normalize_claimed_handle,
)
from app.services.security.rate_limiter import rate_limit

router = APIRouter(prefix="/api/creator", tags=["creator"])
logger = get_logger(__name__)


def _is_remote_media_url(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith("http://") or text.startswith("https://")


def _invalidate_creator_cache(user_id: int) -> None:
    cache_clear(prefix=f"creator:{int(user_id)}:")
    cache_clear(prefix=f"{__name__}._cached_creator_program_snapshot:{int(user_id)}")
    cache_clear(prefix=f"{__name__}._cached_creator_memory_snapshot:{int(user_id)}")


@cached(ttl=120)
def _cached_creator_memory_snapshot(user_id: int):
    return build_creator_memory_snapshot(user_id)


@cached(ttl=120)
def _cached_creator_program_snapshot(user_id: int, creator_code: str = ""):
    return build_creator_program_snapshot({"id": int(user_id), "creator_code": creator_code})


async def _get_current_user_async(request: Request):
    return await get_current_user_async(request)


def _delete_social_account_sync(user_id: int, account_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM user_social_accounts WHERE id=? AND user_id=?", (account_id, user_id))
    conn.commit()
    refresh_user_social_verified(int(user_id), reason="social_account_deleted", context={"account_id": int(account_id)})


def _list_creator_addresses_sync(user_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM user_addresses WHERE user_id=? ORDER BY is_default DESC,id DESC",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _insert_creator_address_sync(user_id: int, body: dict[str, str]) -> None:
    conn = get_conn()
    conn.execute("UPDATE user_addresses SET is_default=0 WHERE user_id=?", (user_id,))
    conn.execute(
        """INSERT INTO user_addresses
        (user_id,name,phone,address1,address2,city,state,country,postal_code,is_default)
        VALUES (?,?,?,?,?,?,?,?,?,1)""",
        (
            user_id,
            body.get("name", ""),
            body.get("phone", ""),
            body.get("address1", ""),
            body.get("address2", ""),
            body.get("city", ""),
            body.get("state", ""),
            body.get("country", "US"),
            body.get("postal_code", ""),
        ),
    )
    conn.commit()


def _redeem_reward_sync(user_id: int, reward_id: int, address_id: int | None):
    conn = get_conn()
    reward = conn.execute(
        "SELECT id,title,category,points_cost,stock,status FROM reward_catalog WHERE id=?",
        (reward_id,),
    ).fetchone()
    if not reward:
        return {"status": "error", "message": "Reward not found"}
    if reward["status"] != "published":
        return {"status": "error", "message": "Reward not available"}
    addr_snapshot = "{}"
    if address_id:
        addr = conn.execute(
            "SELECT * FROM user_addresses WHERE id=? AND user_id=?",
            (address_id, user_id),
        ).fetchone()
        if addr:
            addr_snapshot = json.dumps(dict(addr), ensure_ascii=False)
    try:
        if not is_postgres_runtime():
            conn.execute("BEGIN IMMEDIATE")
        if reward["stock"] is not None and int(reward["stock"]) > 0:
            stock_cur = conn.execute("UPDATE reward_catalog SET stock=stock-1 WHERE id=? AND stock>0", (reward_id,))
            if stock_cur.rowcount != 1:
                conn.rollback()
                return {"status": "error", "message": "Out of stock"}
        pts_cur = conn.execute(
            "UPDATE users SET points_balance=points_balance-? WHERE id=? AND points_balance>=?",
            (reward["points_cost"], user_id, reward["points_cost"]),
        )
        if pts_cur.rowcount != 1:
            conn.rollback()
            return {"status": "error", "message": "Insufficient points"}
        conn.execute(
            """INSERT INTO redemptions
            (created_at,user_id,reward_id,item_name,item_category,points_cost,address_id,address_snapshot,status)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                user_id,
                reward["id"],
                reward["title"],
                reward["category"],
                reward["points_cost"],
                address_id,
                addr_snapshot,
                "pending",
            ),
        )
        balance_row = conn.execute("SELECT points_balance FROM users WHERE id=?", (user_id,)).fetchone()
        new_balance = balance_row["points_balance"] if balance_row else 0
        conn.execute(
            "INSERT INTO points_log (created_at,user_id,delta,reason,balance_after) VALUES (?,?,?,?,?)",
            (
                datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                user_id,
                -int(reward["points_cost"]),
                f"Redeem: {reward['title']}",
                new_balance,
            ),
        )
        conn.commit()
        return {"status": "success", "message": "Redemption submitted — we will contact you within 3-5 business days"}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.warning("creator.redeem_rollback_failed", extra={"user_id": user_id, "reward_id": reward_id}, exc_info=True)
        logger.exception("creator.redeem_failed", extra={"user_id": user_id, "reward_id": reward_id})
        return {"status": "error", "message": "Could not submit redemption"}


def _list_points_log_sync(user_id: int):
    conn = get_conn()
    return [dict(row) for row in conn.execute("SELECT * FROM points_log WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,)).fetchall()]


def _update_profile_sync(user_id: int, fields: list[str], vals: list[object]) -> None:
    conn = get_conn()
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", [*vals, user_id])
    conn.commit()


def _delete_address_sync(user_id: int, address_id: int) -> None:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id, is_default FROM user_addresses WHERE id=? AND user_id=?",
        (address_id, user_id),
    ).fetchone()
    if not existing:
        return
    was_default = bool(existing["is_default"])
    conn.execute("DELETE FROM user_addresses WHERE id=? AND user_id=?", (address_id, user_id))
    if was_default:
        fallback = conn.execute(
            "SELECT id FROM user_addresses WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if fallback:
            conn.execute("UPDATE user_addresses SET is_default=1 WHERE id=?", (int(fallback["id"]),))
    conn.commit()


def _set_default_address_sync(user_id: int, address_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE user_addresses SET is_default=0 WHERE user_id=?", (user_id,))
    conn.execute("UPDATE user_addresses SET is_default=1 WHERE id=? AND user_id=?", (address_id, user_id))
    conn.commit()


@router.get("/social-accounts")
def get_social_accounts(request: Request):
    user = get_current_user(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    conn = get_conn()
    rows = conn.execute("SELECT * FROM user_social_accounts WHERE user_id=? ORDER BY id",
                         (user["id"],)).fetchall()
    accounts = []
    fixups = []
    for row in rows:
        account = dict(row)
        platform, handle, valid = sanitize_social_identity(
            account.get("platform", ""),
            account.get("handle", ""),
        )
        if valid:
            if platform != account.get("platform") or handle != account.get("handle"):
                fixups.append((platform, handle, int(account["id"])))
            account["platform"] = platform
            account["handle"] = handle
        else:
            account["invalid_handle"] = 1
            account["handle"] = ""
        accounts.append(account)
    if fixups:
        for platform, handle, account_id in fixups:
            conn.execute(
                "UPDATE user_social_accounts SET platform=?, handle=? WHERE id=?",
                (platform, handle, account_id),
            )
        conn.commit()
    return {"accounts": accounts}


@router.post("/social-accounts")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def add_social_account(request: Request):
    user = await _get_current_user_async(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    raw_handle = (body.get("handle", "") or "").strip()
    raw_platform = (body.get("platform", "") or "").strip()
    if raw_handle.startswith("http://") or raw_handle.startswith("https://"):
        platform = detect_platform_from_profile_url(raw_handle) or ""
        extracted = extract_handle_from_profile_url(raw_handle, platform) if platform else ""
        handle = normalize_claimed_handle(extracted, platform)
        profile_url = raw_handle
    else:
        platform = (normalize_platform(raw_platform) or raw_platform).strip().lower()
        handle = normalize_claimed_handle(raw_handle, platform)
        profile_url = build_profile_url(platform, handle)
    if not platform or not handle:
        return {"status": "error", "message": "platform and handle required"}
    code = "VLX-" + _secrets_mod.token_hex(4).upper()

    def _upsert_social_account():
        try:
            upsert_pending_social_account(
                user_id=int(user["id"]),
                platform=platform,
                handle=handle,
                verify_code=code,
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        return {
            "status": "success",
            "verify_code": code,
            "platform": platform,
            "handle": handle,
            "profile_url": profile_url,
            "message": f"Paste '{code}' into a comment on one of the latest 10 {platform} posts for auto verification",
        }

    try:
        result = await db_write(_upsert_social_account)
        if result.get("status") == "success":
            _invalidate_creator_cache(int(user["id"]))
        return result
    except Exception:
        logger.exception("creator.social_account_upsert_failed", extra={"user_id": int(user["id"])})
        return {"status": "error", "message": "Could not save social account"}


@router.delete("/social-accounts/{account_id}")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def delete_social_account(account_id: int, request: Request):
    user = await _get_current_user_async(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    await db_write(partial(_delete_social_account_sync, int(user["id"]), account_id))
    _invalidate_creator_cache(int(user["id"]))
    return {"status": "deleted"}


@router.get("/addresses")
def creator_get_addresses(request: Request):
    user = get_current_user(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    return {"addresses": _list_creator_addresses_sync(int(user["id"]))}


@router.post("/addresses")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def creator_add_address(request: Request):
    user = await _get_current_user_async(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    await db_write(partial(_insert_creator_address_sync, int(user["id"]), body))
    _invalidate_creator_cache(int(user["id"]))
    return {"status": "success"}


@router.post("/redeem")
@rate_limit("creator_redeem", max_requests=6, window_sec=300)
async def creator_redeem(request: Request):
    user = await _get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    try:
        reward_id = int(body.get("reward_id", 0) or 0)
    except Exception:
        return {"status": "error", "message": "Invalid reward_id"}
    if reward_id <= 0:
        return {"status": "error", "message": "reward_id required"}
    address_id = body.get("address_id")
    result = await db_write(partial(_redeem_reward_sync, int(user["id"]), reward_id, address_id))
    if result.get("status") == "success":
        invalidate_user_cache(user["id"])
    return result


@router.get("/redemptions")
def creator_get_redemptions(request: Request):
    user = get_current_user(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    conn = get_conn()
    rows = conn.execute("SELECT * FROM redemptions WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return {"redemptions": [dict(r) for r in rows]}


@router.get("/submissions")
def creator_get_submissions(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    user = get_current_user(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    conn = get_conn()
    rows = conn.execute("""SELECT id,created_at,platform,title,detection_status,
               final_score,overall_score,recommendation,views,likes,
               product_label,url,video_path,video_analysis,points_awarded
        FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT ?""", (user["id"], limit)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        raw_video_path = str(item.get("video_path") or "").strip()
        video_analysis = {}
        try:
            video_analysis = json.loads(item.get("video_analysis") or "{}")
        except Exception:
            video_analysis = {}
        direct_video_url = raw_video_path if _is_remote_media_url(raw_video_path) else ""
        analysis_path = video_analysis.get("path") if isinstance(video_analysis, dict) else ""
        if not direct_video_url and _is_remote_media_url(analysis_path):
            direct_video_url = str(analysis_path).strip()
        local_video_path = resolve_local_media_path(raw_video_path)
        local_analysis_path = resolve_local_media_path(analysis_path)
        if isinstance(video_analysis, dict):
            video_analysis.pop("path", None)
        asset = get_submission_asset(int(item["id"]))
        asset_key = str((asset or {}).get("storage_key") or "").strip()
        local_asset_path = resolve_local_media_path(asset_key)
        has_video = bool(
            direct_video_url
            or local_video_path
            or local_analysis_path
            or video_analysis.get("r2_key")
            or asset_key.startswith("videos/")
            or local_asset_path
        )
        item["video_url"] = direct_video_url or (f"/api/submissions/{item['id']}/video" if has_video else "")
        item["best_frame_url"] = f"/api/submissions/{item['id']}/poster" if has_video else ""
        item["has_video"] = has_video
        item["asset_key"] = asset_key
        item["video_path"] = ""
        item["video_analysis"] = video_analysis
        items.append(item)
    return {"submissions": items}


@router.get("/points-log")
def creator_points_log(request: Request):
    user = get_current_user(request)
    if not user: return {"status": "error", "message": "Not authenticated"}
    return {"log": _list_points_log_sync(int(user["id"]))}


@router.patch("/profile")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def update_profile(request: Request):
    user = await _get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    body = await request.json()
    fields, vals = [], []
    for col in ["name", "avatar_url", "bio", "signature"]:
        if col in body:
            fields.append(f"{col}=?")
            vals.append(body[col])
    if not fields:
        return {"status": "error", "message": "No fields to update"}
    await db_write(partial(_update_profile_sync, int(user["id"]), fields, vals))
    invalidate_user_cache(user["id"])
    _invalidate_creator_cache(int(user["id"]))
    return {"status": "success", "message": "Profile updated"}


@router.get("/memory")
def creator_memory(request: Request):
    user = get_current_user(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    snapshot = _cached_creator_memory_snapshot(int(user["id"]))
    snapshot["creator_code"] = user.get("creator_code", "")
    snapshot["status"] = "success"
    return snapshot


@router.get("/program")
def creator_program(request: Request):
    user = get_current_user(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    summary = _cached_creator_program_snapshot(int(user["id"]), str(user.get("creator_code") or ""))
    summary["status"] = "success"
    return summary


@router.delete("/addresses/{address_id}")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def delete_address(address_id: int, request: Request):
    user = await _get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    await db_write(partial(_delete_address_sync, int(user["id"]), address_id))
    _invalidate_creator_cache(int(user["id"]))
    return {"status": "deleted"}


@router.patch("/addresses/{address_id}/default")
@rate_limit("creator_profile_mutation", max_requests=20, window_sec=300)
async def set_default_address(address_id: int, request: Request):
    user = await _get_current_user_async(request)
    if not user:
        return {"status": "error", "message": "Not authenticated"}
    await db_write(partial(_set_default_address_sync, int(user["id"]), address_id))
    _invalidate_creator_cache(int(user["id"]))
    return {"status": "success"}
