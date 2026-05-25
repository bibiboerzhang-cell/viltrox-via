"""
api/routers/audit.py — 核心审计/分析接口 (真分流版)

改动说明:
- /api/audit/v2 现在秒回 queued + submission_id
- 重活全部由 worker 后台执行
- 保留 /api/audit/sync 做兼容（旧同步模式，可选）
- 新增 /api/submissions/{id}/status 轮询端点
"""
from __future__ import annotations
import asyncio
from functools import partial

from fastapi import APIRouter, Request, HTTPException, Depends

# ── 请求模型、工具函数和数据库连接 ──
from app.api.dependencies.auth import get_user
from app.schemas.audit import AuditRequest
from app.core.config import AUDIT_RATE_LIMIT_MAX, AUDIT_RATE_LIMIT_WINDOW_SEC
from app.core.logging import get_logger
from app.utils.urls import valid_url, detect_platform
from app.utils.handles import extract_handle_from_url
from app.db.connection import db_read, db_write
from app.services.security.rate_limiter import rate_limit
from app.services.trust import enforce_dynamic_submission_guard
from app.services.jobs.backpressure import enforce_queue_backpressure
from app.services.activities.attribution import record_submission
from app.services.media.storage import normalize_uploaded_video_payload

# ── 核心业务服务导入 ──
from app.db.repositories.submissions import get_submission_status

# ── 爬虫与 AI 服务导入 ──
from app.services.scraping.ytdlp_enhanced import validate_video_publish_date

# ── 环境变量与队列模型 ──
from app.services.ai.orchestrator import VideoJobInput
from app.api.routers.audit_helpers import (
    _bind_uploaded_asset_sync,
    _check_similarity_sync,
    _create_submission_stub_sync,
    _emit_submission_to_party_layer,
    _resolve_uploaded_video_payload_sync,
)
from app.api.routers.audit_legacy_sync import run_audit_sync

router = APIRouter(tags=["audit"])
logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 真分流版: 秒回 queued, 重活给 worker
# ──────────────────────────────────────────────

@router.post("/api/audit")
@router.post("/api/audit/v2")
@rate_limit("audit_submit", max_requests=AUDIT_RATE_LIMIT_MAX, window_sec=AUDIT_RATE_LIMIT_WINDOW_SEC)
async def audit_async(
    request: Request,
    req: AuditRequest,
    current_user: dict | None = Depends(get_user),
):
    """
    真分流入口:
    1. 认证用户
    2. 轻量校验
    3. 相似度/重复检测（快速拒绝）
    4. 落 stub submission
    5. 入队
    6. 秒回 queued
    """
    current_uid = current_user["id"] if current_user else None
    await asyncio.to_thread(enforce_dynamic_submission_guard, request, current_user, "audit_submit")

    # ── 1. 轻量校验 ──
    if req.url and not valid_url(req.url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    if not req.url and not (
        req.uploaded_video
        and (req.uploaded_video.asset_id or req.uploaded_video.r2_key or req.uploaded_video.path)
    ):
        raise HTTPException(status_code=400, detail="URL or uploaded video required")

    queue = getattr(request.app.state, "job_queue", None)
    queue_pressure = await enforce_queue_backpressure(queue, job_type="audit_submission")

    # ── 2. 基础字段 ──
    title    = (req.title or "").strip()
    caption  = (req.caption or "").strip()
    raw_text = (req.raw_text or "").strip()
    platform = detect_platform(req.url) if req.url else "Uploaded Video"

    extracted_handle = extract_handle_from_url(req.url) if req.url else ""
    if not extracted_handle and req.user_handle:
        h = req.user_handle.strip()
        if h and not h.startswith("@") and not h.startswith("u/") and not h.startswith("http"):
            h = "@" + h
        extracted_handle = h
    if not extracted_handle and req.linked_handles:
        plat_key = platform.lower()
        linked_for_plat = req.linked_handles.get(plat_key, "")
        if linked_for_plat:
            extracted_handle = linked_for_plat
    if not extracted_handle and req.uploaded_video and req.linked_handles:
        for _, handle_val in req.linked_handles.items():
            if handle_val:
                extracted_handle = handle_val
                break

    # ── 3. Ownership 快速校验 (URL submissions only) ──
    if req.url and not req.uploaded_video:
        def norm_handle(h: str) -> str:
            return h.lower().strip().lstrip("@").split("?")[0].rstrip("/")

        all_linked_norms = {norm_handle(v) for v in (req.linked_handles or {}).values() if v}
        OFFICIAL = {"viltrox.official", "viltrox.usa", "viltrox_official", "viltroxofficial", "唯卓仕官方"}

        if extracted_handle:
            submitted_norm = norm_handle(extracted_handle)
            if submitted_norm not in OFFICIAL and all_linked_norms and submitted_norm not in all_linked_norms:
                return {
                    "status": "rejected",
                    "rejection_code": "ownership_mismatch",
                    "rejection_reason": (
                        f"⛔ 投稿被拒绝：检测到账号 @{submitted_norm} 未绑定到您的账户。\n\n"
                        "请勿提交他人内容。如需提交此账号的内容，请先在「账号管理」中绑定该平台账号。\n\n"
                        "This submission was rejected: account @" + submitted_norm +
                        " is not linked to your profile. Do not submit other people's content."
                    ),
                    "platform": platform,
                    "extracted_handle": extracted_handle,
                    "linked_handles": req.linked_handles,
                    "url": req.url,
                    "viltrox_detected": False,
                    "detection_status": "rejected",
                    "final_score": 0, "creator_score": 0,
                    "overall_score": 0, "risk_score": 0,
                    "recommendation": "Rejected — account not linked",
                    "memo": f"Hard reject: @{submitted_norm} not in linked accounts {list(all_linked_norms)}",
                }

    # ── 4. 相似度快速拒绝 ──
    handle_for_sim = req.user_handle or (
        next(iter(req.linked_handles.values()), "") if req.linked_handles else ""
    )
    similarity_result = await db_read(
        partial(
            _check_similarity_sync,
            handle_for_sim,
            title or (req.uploaded_video.filename if req.uploaded_video else ""),
            platform,
            req.url or "",
        )
    )

    if similarity_result.get("hard_reject"):
        return {
            "status": "rejected",
            "rejection_code": "duplicate_or_spam",
            "rejection_reason": similarity_result["reason"],
            "platform": platform,
            "viltrox_detected": False,
            "detection_status": "rejected",
            "final_score": 0, "creator_score": 0,
            "overall_score": 0, "risk_score": 0,
            "recommendation": "Rejected — " + similarity_result["reason"],
            "memo": similarity_result["reason"],
        }

    if req.url and not req.uploaded_video:
        publish_check = await asyncio.to_thread(
            validate_video_publish_date,
            req.url,
            platform,
            None,
        )
        if not publish_check.get("valid", True):
            return {
                "status": "rejected",
                "rejection_code": "stale_video",
                "rejection_reason": publish_check.get("reason", "Video is outside the allowed publish window"),
                "platform": platform,
                "extracted_handle": extracted_handle,
                "url": req.url,
                "viltrox_detected": False,
                "detection_status": "rejected",
                "final_score": 0,
                "creator_score": 0,
                "overall_score": 0,
                "risk_score": 0,
                "recommendation": "Rejected — publish date outside rolling window",
                "memo": publish_check.get("reason", ""),
                "publish_date_check": publish_check,
            }

    # ── 5. 落 stub submission ──
    resolved_uploaded_video = await db_read(partial(_resolve_uploaded_video_payload_sync, req.uploaded_video))
    uploaded_video_path = str((resolved_uploaded_video or {}).get("analysis_path") or (resolved_uploaded_video or {}).get("path") or "")
    logger.info(
        "audit.upload_payload_bound",
        extra={
            "uploaded_video": bool(resolved_uploaded_video),
            "uploaded_video_asset_id": int((resolved_uploaded_video or {}).get("asset_id") or 0),
            "user_id": current_uid,
        },
    )
    uploaded_video_filename = str((resolved_uploaded_video or {}).get("filename") or "")

    submission_id = await db_write(
        partial(
            _create_submission_stub_sync,
            current_uid,
            req.url or "",
            title,
            caption,
            raw_text,
            platform,
            extracted_handle,
            uploaded_video_path,
            uploaded_video_filename,
        )
    )

    # ── Phase 1 middleware: party stitch + creator.submission_created event ──
    # Best-effort; never blocks the submission response.
    try:
        _emit_submission_to_party_layer(
            submission_id=submission_id,
            user_id=current_uid,
            platform=platform,
            url=req.url or "",
            extracted_handle=extracted_handle,
            title=title,
        )
    except Exception:
        logger.debug("phase1 party-layer emit failed for submission (non-fatal)", exc_info=True)
    try:
        record_submission(submission_id, current_uid)
    except Exception:
        logger.warning(
            "audit.activity_submission_attribution_failed",
            extra={"submission_id": submission_id, "user_id": current_uid},
            exc_info=True,
        )

    if req.uploaded_video:
        asset = await db_write(
            partial(
                _bind_uploaded_asset_sync,
                submission_id,
                resolved_uploaded_video,
                uploaded_video_path,
            )
        )
        if asset:
            resolved_uploaded_video = normalize_uploaded_video_payload(
                {
                    **(resolved_uploaded_video or {}),
                    "asset_id": int((asset or {}).get("id") or 0),
                    "storage_key": str((asset or {}).get("storage_key") or "") or str((resolved_uploaded_video or {}).get("storage_key") or ""),
                    "r2_key": str((asset or {}).get("storage_key") or "") if str((asset or {}).get("storage_key") or "").startswith("videos/") else str((resolved_uploaded_video or {}).get("r2_key") or ""),
                    "path": uploaded_video_path,
                }
            )

    # ── 6. 打包 job ──
    job = VideoJobInput(
        submission_id=submission_id,
        url=req.url or "",
        title=title,
        handle=extracted_handle or req.user_handle or "",
        platform=platform,
        caption=caption,
        scraped_text=raw_text,
        og_image="",
        user_id=current_uid,
        user_handle=req.user_handle or "",
        linked_handles=req.linked_handles or {},
        uploaded_video=resolved_uploaded_video,
        hints=req.hints.model_dump() if req.hints else {},
        metrics=req.metrics.model_dump() if req.metrics else {},
    )

    # ── 7. 入队 ──
    try:
        if queue is None:
            raise RuntimeError("job queue not available")
        task_id = await queue.enqueue(
            "audit_submission",
            job,
            submission_id=submission_id,
        )
    except Exception as e:
        logger.exception(
            "audit.enqueue_failed",
            extra={"submission_id": submission_id, "user_id": current_uid},
        )
        task_id = "enqueue_failed"

    # ── 8. 秒回 ──
    return {
        "status": "queued",
        "job_id": task_id,
        "submission_id": submission_id,
        "platform": platform,
        "extracted_handle": extracted_handle,
        "message": "Analysis started — poll /api/submissions/{id}/status for results",
        "queue": queue_pressure,
    }


# ──────────────────────────────────────────────
# 轮询端点: 前端查 submission 状态
# ──────────────────────────────────────────────

@router.get("/api/submissions/{submission_id}/status")
def submission_status(submission_id: int):
    result = get_submission_status(submission_id)
    if not result:
        raise HTTPException(status_code=404, detail="Submission not found")
    return result


# ──────────────────────────────────────────────
# 同步模式 (兼容旧前端, 可选保留)
# 如果前端还没改成轮询, 可以暂时用这个
# ──────────────────────────────────────────────

@router.post("/api/audit/sync")
@rate_limit("audit_submit_sync", max_requests=AUDIT_RATE_LIMIT_MAX, window_sec=AUDIT_RATE_LIMIT_WINDOW_SEC)
async def audit_sync(
    request: Request,
    req: AuditRequest,
    current_user: dict | None = Depends(get_user),
):
    return await run_audit_sync(request, req, current_user, audit_async)
