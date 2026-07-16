"""
api/routers/verify.py — 创作者账号验证 API
============================================
所有验证相关的 HTTP 端点.

端点列表:
  POST   /api/verify/start          用户开始验证 (输入主页 URL)
  POST   /api/verify/posted         用户点 "I've Posted, Verify Me"
  POST   /api/verify/scan-now       Admin 立刻触发扫描
  GET    /api/verify/status/{id}    查询某条验证状态
  GET    /api/verify/my              查询当前用户所有验证
  POST   /api/verify/{id}/approve   Admin 手动通过
  POST   /api/verify/{id}/reject    Admin 手动拒绝
  GET    /api/verify/queue          Admin 查看 needs_review 队列
"""
from __future__ import annotations

import json
from functools import partial
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_user_required as get_current_user
from app.api.dependencies.admin import get_admin as require_admin
from app.core.logging import get_logger
from app.core.config import VERIFY_CODE_TTL_HOURS
from app.db.connection import db_read, db_write, get_conn
from app.db.repositories.users import mark_social_account_verified
from app.services.security.rate_limiter import rate_limit
from app.services.verification.start_service import start_verification_request
from app.services.verification.viltrox_official import (
    SUPPORTED_PLATFORMS,
    build_profile_url,
    detect_platform_from_profile_url,
    extract_handle_from_profile_url,
    is_protected_handle,
    normalize_claimed_handle,
)


router = APIRouter(prefix="/api/verify", tags=["verify"])
logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# Pydantic 模型
# ──────────────────────────────────────────────────────────────

class VerifyStartRequest(BaseModel):
    profile_url: Optional[str] = Field(default=None, min_length=3, max_length=500)
    platform: Optional[str] = Field(default=None, min_length=2, max_length=50)
    handle: Optional[str] = Field(default=None, min_length=1, max_length=200)


class VerifyStartResponse(BaseModel):
    verification_id: int
    social_account_id: Optional[int] = None
    platform: str
    handle: str
    profile_url: str
    code: str
    expires_at: str
    generated_comment: str
    viltrox_account_url: str
    viltrox_account_name: str
    instructions: list[str]
    status: str  # always "pending" at start
    comment_source: Optional[str] = "template"
    comment_job_id: Optional[str] = None


class VerifyPostedRequest(BaseModel):
    verification_id: int


class VerifyPreviewResponse(BaseModel):
    requested_platform: Optional[str] = None
    platform: str
    handle: str
    profile_url: str
    platform_match: bool
    protected_handle: bool


class VerifyStatusResponse(BaseModel):
    verification_id: int
    platform: str
    handle: str
    status: str
    code: Optional[str] = None
    expires_at: Optional[str] = None
    is_expired: bool = False
    verified_via_comment: bool = False
    match_score: Optional[float] = None
    scan_count: int = 0
    last_scanned_at: Optional[str] = None
    note: Optional[str] = None
    comment_username: Optional[str] = None
    comment_video_url: Optional[str] = None


def _utc_now() -> datetime:
    return datetime.utcnow()


def _format_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _build_expiry_message(expires_at: Optional[str]) -> str:
    if expires_at:
        return f"This verification code expired at {expires_at}. Generate a new code and post a fresh comment."
    return "This verification code expired. Generate a new code and post a fresh comment."


def _is_verification_expired(row: dict) -> bool:
    expires_at = _parse_utc_timestamp(row.get("expires_at"))
    if not expires_at:
        return False
    return _utc_now() >= expires_at


def _resolve_verification_identity(payload: VerifyStartRequest) -> dict:
    profile_url = (payload.profile_url or "").strip()
    requested_platform = (payload.platform or "").strip().lower()
    requested_handle = (payload.handle or "").strip()

    if profile_url:
        detected_platform = detect_platform_from_profile_url(profile_url)
        if not detected_platform:
            raise HTTPException(400, "Could not detect platform from URL")
        extracted_handle = extract_handle_from_profile_url(profile_url, detected_platform)
        if not extracted_handle:
            raise HTTPException(400, "Could not extract username from URL")
        platform = detected_platform
        handle = normalize_claimed_handle(extracted_handle, platform)
        canonical_profile_url = build_profile_url(platform, handle)
    else:
        platform = requested_platform
        if not platform:
            raise HTTPException(400, "platform required when profile_url is empty")
        handle = normalize_claimed_handle(requested_handle, platform)
        if not handle:
            raise HTTPException(400, "handle required when profile_url is empty")
        canonical_profile_url = build_profile_url(platform, handle)

    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(400, f"Platform {platform} not yet supported. Supported: {SUPPORTED_PLATFORMS}")

    platform_match = not requested_platform or requested_platform == platform
    return {
        "requested_platform": requested_platform or None,
        "platform": platform,
        "handle": handle,
        "profile_url": canonical_profile_url,
        "platform_match": platform_match,
    }


def _mark_verification_expired(verification_id: int, reason: str) -> None:
    conn = get_conn()
    now = _format_utc(_utc_now())
    conn.execute(
        """
        UPDATE verifications
        SET status = 'expired',
            last_scanned_at = ?,
            note = ?
        WHERE id = ? AND status IN ('pending', 'awaiting_scan')
        """,
        (now, reason, verification_id),
    )
    conn.commit()


def _mark_verification_posted(
    verification_id: int,
    user_id: int,
    posted_at: str,
) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM verifications WHERE id = ? AND user_id = ?",
        (verification_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Verification not found")

    verification = dict(row)
    if _is_verification_expired(verification):
        now_local = _format_utc(_utc_now())
        conn.execute(
            """
            UPDATE verifications
            SET status = 'expired',
                last_scanned_at = ?,
                note = ?
            WHERE id = ?
            """,
            (now_local, _build_expiry_message(verification.get("expires_at")), verification_id),
        )
        conn.commit()
        raise HTTPException(410, _build_expiry_message(verification.get("expires_at")))
    if verification.get("status") not in ("pending", "awaiting_scan"):
        return {
            "ok": True,
            "status": verification.get("status"),
            "message": f"Already in status: {verification.get('status')}",
        }

    conn.execute(
        """
        UPDATE verifications
        SET status = 'awaiting_scan', posted_at = ?
        WHERE id = ?
        """,
        (posted_at, verification_id),
    )
    conn.commit()
    return {
        "ok": True,
        "status": "awaiting_scan",
        "message": "Got it! We'll verify your account within 24 hours. You can check status on your dashboard.",
        "verification_id": verification_id,
    }


def _load_verification_status_row(verification_id: int, user_id: int):
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM verifications WHERE id = ? AND user_id = ?",
        (verification_id, user_id),
    ).fetchone()


def _list_user_verifications(user_id: int):
    conn = get_conn()
    return conn.execute(
        """
        SELECT id, platform, handle, status, match_score,
               scan_count, created_at, last_scanned_at, generated_comment,
               expires_at, note, comment_id
        FROM verifications
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    ).fetchall()


def _load_verification_queue(status: str, limit: int):
    conn = get_conn()
    return conn.execute(
        """
        SELECT v.*, u.email as user_email, u.creator_code as user_code
        FROM verifications v
        LEFT JOIN users u ON v.user_id = u.id
        WHERE v.status = ?
        ORDER BY v.created_at DESC
        LIMIT ?
        """,
        (status, limit),
    ).fetchall()


def _approve_verification_override(verification_id: int, admin_email: str, approved_at: str) -> None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, user_id, platform, handle FROM verifications WHERE id = ?",
        (verification_id,),
    ).fetchone()
    if not row:
        return
    conn.execute(
        """
        UPDATE verifications
        SET status = 'approved_override',
            approved_at = ?,
            note = COALESCE(note, '') || ' | Manually approved by ' || ?
        WHERE id = ?
        """,
        (approved_at, admin_email, verification_id),
    )
    user_id = int(row["user_id"] or 0)
    platform = str(row["platform"] or "").lower().strip()
    handle = str(row["handle"] or "").lstrip("@").strip().lower()
    if user_id > 0 and platform and handle:
        mark_social_account_verified(
            user_id=user_id,
            platform=platform,
            handle=handle,
            verified_at=approved_at,
        )
    else:
        conn.commit()


def _reject_verification_override(
    verification_id: int,
    admin_email: str,
    rejected_at: str,
    reason: str,
) -> None:
    conn = get_conn()
    conn.execute(
        """
        UPDATE verifications
        SET status = 'failed',
            last_scanned_at = ?,
            note = COALESCE(note, '') || ' | Rejected by ' || ? || ': ' || ?
        WHERE id = ?
        """,
        (rejected_at, admin_email, reason, verification_id),
    )
    conn.commit()


def _load_verification_stats():
    conn = get_conn()
    return conn.execute(
        """
        SELECT status, COUNT(*) as count
        FROM verifications
        GROUP BY status
        """
    ).fetchall()


def _start_verification_request_sync(
    user_id: int,
    platform: str,
    handle: str,
    profile_url: str,
    note: str,
):
    return start_verification_request(
        user_id=user_id,
        platform=platform,
        handle=handle,
        profile_url=profile_url,
        note=note,
    )


@router.post("/preview", response_model=VerifyPreviewResponse)
@rate_limit("verify_binding", max_requests=20, window_sec=300)
async def preview_verification_target(
    payload: VerifyStartRequest,
    current_user: dict = Depends(get_current_user),
):
    identity = _resolve_verification_identity(payload)
    return VerifyPreviewResponse(
        requested_platform=identity["requested_platform"],
        platform=identity["platform"],
        handle=identity["handle"],
        profile_url=identity["profile_url"],
        platform_match=identity["platform_match"],
        protected_handle=is_protected_handle(identity["handle"], identity["platform"]),
    )


# ──────────────────────────────────────────────────────────────
# POST /api/verify/start  — 用户输主页 URL, 系统生成好评
# ──────────────────────────────────────────────────────────────

@router.post("/start", response_model=VerifyStartResponse)
@rate_limit("verify_binding", max_requests=10, window_sec=300)
async def start_verification(
    request: Request,
    payload: VerifyStartRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    流程:
      1. 解析 profile_url → platform + handle
      2. 检查是否在保护名单 (大V直接进 needs_review)
      3. (可选) 调 Apify 抓主页基线 — 这步可省, 节省 Apify 成本
      4. 生成验证码 + GPT 好评
      5. 写入 verifications 表 (status='pending')
      6. 返回好评 + Viltrox 官号链接给前端
    """
    identity = _resolve_verification_identity(payload)
    requested_platform = identity["requested_platform"]
    platform = identity["platform"]
    handle = identity["handle"]
    profile_url = identity["profile_url"]
    if requested_platform and not identity["platform_match"]:
        raise HTTPException(400, f"This URL belongs to {platform}, not {requested_platform}")
    
    # Step 2: 检查保护名单
    is_protected = is_protected_handle(handle, platform)
    initial_status = "pending"  # 正常用户
    note_extra = ""
    if is_protected:
        # 大 V 直接进入需要人工审核 (即使后续验证成功也要人工二次确认)
        note_extra = "Protected handle - flagged for mandatory manual review"
    
    user_id = current_user.get("user_id") or current_user.get("id")
    response_payload = await db_write(
        partial(
            _start_verification_request_sync,
            int(user_id),
            platform,
            handle,
            profile_url,
            note_extra,
        )
    )
    response_payload["status"] = initial_status
    response_payload["comment_source"] = "template"
    queue = getattr(request.app.state, "job_queue", None)
    if queue is not None:
        response_payload["comment_job_id"] = await queue.enqueue(
            "verification_prepare_comment",
            {
                "verification_id": response_payload["verification_id"],
                "code": response_payload["code"],
                "platform": platform,
                "handle": handle,
            },
        )
    return VerifyStartResponse(**response_payload)


# ──────────────────────────────────────────────────────────────
# POST /api/verify/posted  — 用户点 "I've Posted, Verify Me"
# ──────────────────────────────────────────────────────────────

@router.post("/posted")
@rate_limit("verify_binding", max_requests=10, window_sec=300)
async def mark_as_posted(
    request: Request,
    payload: VerifyPostedRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    用户复制好评 + 在 Viltrox 官号评论后, 点这个按钮.
    系统:
      1. 把 status 改为 awaiting_scan
      2. (可选) 立刻触发一次扫描尝试
    """
    user_id = current_user.get("user_id") or current_user.get("id")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    queue = getattr(request.app.state, "job_queue", None)
    # Do not mutate to awaiting_scan unless the durable follow-up can be
    # recorded.  Otherwise the user sees a successful write that can never be
    # consumed by the provider worker.
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    response = await db_write(partial(_mark_verification_posted, payload.verification_id, int(user_id), now))
    if response.get("status") == "awaiting_scan":
        response["job_id"] = await queue.enqueue(
            "verification_scan_single",
            {
                "verification_id": payload.verification_id,
                "requested_by": str(user_id),
            },
            lock_key=f"verification_scan_single:{int(payload.verification_id)}",
            timeout_seconds=900,
        )
        response["scan_status"] = "queued"
    return response


# ──────────────────────────────────────────────────────────────
# GET /api/verify/status/{id}  — 查询某条验证状态
# ──────────────────────────────────────────────────────────────

@router.get("/status/{verification_id}", response_model=VerifyStatusResponse)
async def get_verification_status(
    verification_id: int,
    current_user: dict = Depends(get_current_user),
):
    """前端轮询用"""
    user_id = current_user.get("user_id") or current_user.get("id")

    row = await db_read(partial(_load_verification_status_row, verification_id, int(user_id)))
    
    if not row:
        raise HTTPException(404, "Verification not found")
    
    v = dict(row)
    if _is_verification_expired(v) and v.get("status") in {"pending", "awaiting_scan"}:
        reason = _build_expiry_message(v.get("expires_at"))
        await db_write(partial(_mark_verification_expired, verification_id, reason))
        v["status"] = "expired"
        v["note"] = reason
        v["last_scanned_at"] = _format_utc(_utc_now())
    return VerifyStatusResponse(
        verification_id=v["id"],
        platform=v.get("platform", ""),
        handle=v.get("handle", ""),
        status=v.get("status", ""),
        code=v.get("code"),
        expires_at=v.get("expires_at"),
        is_expired=v.get("status") == "expired" or _is_verification_expired(v),
        verified_via_comment=bool(v.get("comment_id")) and v.get("status") == "verified",
        match_score=v.get("match_score"),
        scan_count=v.get("scan_count", 0) or 0,
        last_scanned_at=v.get("last_scanned_at"),
        note=v.get("note"),
        comment_username=v.get("comment_username"),
        comment_video_url=v.get("comment_video_url"),
    )


# ──────────────────────────────────────────────────────────────
# GET /api/verify/my  — 当前用户的所有验证
# ──────────────────────────────────────────────────────────────

@router.get("/my")
async def list_my_verifications(
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("user_id") or current_user.get("id")

    rows = await db_read(partial(_list_user_verifications, int(user_id)))
    
    return {
        "verifications": [dict(r) for r in rows],
        "total": len(rows),
    }


# ══════════════════════════════════════════════════════════════
# ADMIN 端点
# ══════════════════════════════════════════════════════════════

@router.post("/scan-now")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def admin_trigger_scan(
    request: Request,
    platform: Optional[str] = Body(default=None, embed=True),
    only_oldest_n: Optional[int] = Body(default=None, embed=True),
    sync: bool = Body(default=False, embed=True),
    admin_user: dict = Depends(require_admin),
):
    """
    Admin 立刻触发扫描.
    可选指定 platform 或只扫最早的 N 条.
    """
    logger.info(
        "verify.admin_manual_scan_triggered",
        extra={"requested_by": admin_user.get("email", "")},
    )
    del sync  # legacy flag retained in the request contract; direct execution is fenced off.
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    task_id = await queue.enqueue(
        "verification_scan_pending",
        {
            "platform": platform,
            "only_oldest_n": only_oldest_n,
            "requested_by": admin_user.get("email", ""),
        },
        lock_key=f"verification_scan_pending:{str(platform or 'all').lower()}:{int(only_oldest_n or 0)}",
        timeout_seconds=1800,
    )
    return {
        "ok": True,
        "status": "queued",
        "job_id": task_id,
        "progressive": True,
        "initial_stage": "queued",
        "message": "Verification scan queued",
    }


@router.post("/{verification_id}/scan")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def admin_rescan_single(
    request: Request,
    verification_id: int,
    sync: bool = Body(default=False, embed=True),
    admin_user: dict = Depends(require_admin),
):
    """Admin 重新扫描某一条"""
    del sync  # legacy flag retained; every provider attempt is durable.
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    task_id = await queue.enqueue(
        "verification_scan_single",
        {
            "verification_id": verification_id,
            "requested_by": admin_user.get("email", ""),
        },
        lock_key=f"verification_scan_single:{int(verification_id)}",
        timeout_seconds=900,
    )
    return {
        "ok": True,
        "status": "queued",
        "job_id": task_id,
        "verification_id": verification_id,
        "progressive": True,
        "initial_stage": "queued",
    }


@router.get("/queue")
async def admin_review_queue(
    status: str = Query(default="needs_review"),
    limit: int = Query(default=50, le=200),
    admin_user: dict = Depends(require_admin),
):
    """
    Admin 审核队列.
    默认查 needs_review, 也可以查别的状态.
    """

    rows = await db_read(partial(_load_verification_queue, status, limit))
    
    return {
        "queue": [dict(r) for r in rows],
        "total": len(rows),
        "filter_status": status,
    }


@router.post("/{verification_id}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_approve(
    verification_id: int,
    admin_user: dict = Depends(require_admin),
):
    """Admin 手动通过, 但不冒充成评论区真实验证"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    await db_write(partial(_approve_verification_override, verification_id, admin_user.get("email", ""), now))
    
    return {"ok": True, "verification_id": verification_id, "status": "approved_override"}


@router.post("/{verification_id}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_reject(
    verification_id: int,
    reason: str = Body(default="", embed=True),
    admin_user: dict = Depends(require_admin),
):
    """Admin 手动拒绝"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    await db_write(partial(_reject_verification_override, verification_id, admin_user.get("email", ""), now, reason))
    
    return {"ok": True, "verification_id": verification_id, "status": "failed"}


@router.get("/admin/stats")
async def admin_stats(
    admin_user: dict = Depends(require_admin),
):
    """Admin dashboard 顶部数字卡"""

    rows = await db_read(_load_verification_stats)
    
    counts = {r["status"]: r["count"] for r in rows}
    
    return {
        "pending": counts.get("pending", 0),
        "awaiting_scan": counts.get("awaiting_scan", 0),
        "verified": counts.get("verified", 0),
        "approved_override": counts.get("approved_override", 0),
        "expired": counts.get("expired", 0),
        "needs_review": counts.get("needs_review", 0),
        "failed": counts.get("failed", 0),
        "total": sum(counts.values()),
    }
