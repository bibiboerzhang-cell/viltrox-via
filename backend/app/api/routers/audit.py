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
import json
import re
import math
from functools import partial
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, Field

# ── 请求模型、工具函数和数据库连接 ──
from app.api.dependencies.auth import get_user
from app.schemas.audit import AuditRequest
from app.core.config import AUDIT_RATE_LIMIT_MAX, AUDIT_RATE_LIMIT_WINDOW_SEC
from app.core.logging import get_logger
from app.utils.urls import valid_url, detect_platform
from app.utils.handles import extract_handle_from_url
from app.db.connection import db_read, db_write, get_conn
from app.services.security.rate_limiter import rate_limit
from app.services.trust import enforce_dynamic_submission_guard
from app.services.jobs.backpressure import enforce_queue_backpressure
from app.services.activities.attribution import record_submission

# ── 核心业务服务导入 ──
from app.services.audit.similarity import (
    classify_product, parse_gear_from_caption,
    detect_gear_mentions, detect_viltrox, analyze_comments_for_spam
)
from app.services.ai.analyzers.claude_text import check_content_similarity
from app.services.scoring.core import compute_weighted_scores
from app.services.scoring.risk import compute_risk
from app.services.scoring.campaign import compute_creator_score, compute_campaign_score, compute_ratios
from app.db.repositories.submissions import (
    save_submission,
    create_submission_stub,
    get_submission_status,
)
from app.db.repositories.assets import (
    attach_uploaded_asset_to_submission,
    register_submission_asset,
    set_submission_video_r2_key,
)
from app.services.media.storage import normalize_uploaded_video_payload
from app.services.scoring.creator import update_creator_profile
from app.services.scoring.benchmark import update_genre_benchmark

# ── 爬虫与 AI 服务导入 ──
from app.services.scraping.platform_router import scrape_url
from app.services.scraping.ytdlp_enhanced import validate_video_publish_date
from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
from app.services.ai.analyzers.claude_text import analyze_text_content
from app.services.ai.analyzers.claude_vision import analyze_video_with_claude, analyze_url_content_smart

# ── 环境变量与队列模型 ──
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE
from app.services.ai.orchestrator import VideoJobInput

router = APIRouter(tags=["audit"])
logger = get_logger(__name__)


def _resolve_uploaded_video_payload_sync(uploaded_video) -> dict | None:
    if not uploaded_video:
        return None
    payload = uploaded_video.model_dump() if hasattr(uploaded_video, "model_dump") else dict(uploaded_video)
    asset_id = int(payload.get("asset_id") or 0)
    if asset_id <= 0:
        return payload
    row = get_conn().execute(
        """
        SELECT id, storage_key, mime_type, size_bytes
        FROM submission_assets
        WHERE id=?
        """,
        (asset_id,),
    ).fetchone()
    if not row:
        return normalize_uploaded_video_payload(payload)
    storage_key = str(row["storage_key"] or "").strip()
    payload["asset_id"] = int(row["id"] or asset_id)
    payload["storage_key"] = storage_key
    if storage_key.startswith("videos/"):
        payload["r2_key"] = payload.get("r2_key") or storage_key
    elif storage_key and not payload.get("path"):
        payload["path"] = storage_key
    payload["mime_type"] = payload.get("mime_type") or str(row["mime_type"] or "")
    if not payload.get("size_mb") and row["size_bytes"]:
        payload["size_mb"] = round(int(row["size_bytes"]) / (1024 * 1024), 2)
    return normalize_uploaded_video_payload(payload)


def _check_similarity_sync(handle_for_sim: str, title: str, platform: str, url: str):
    sim_conn = get_conn()
    return check_content_similarity(
        sim_conn,
        handle_for_sim,
        title,
        platform,
        url=url,
    )


def _auto_create_verification_sync(platform_for_ver: str, handle_for_ver: str) -> None:
    conn_v = get_conn()
    cv = conn_v.cursor()
    plat_key = platform_for_ver.lower()
    handle_clean = handle_for_ver.lstrip("@")
    existing = cv.execute(
        "SELECT id, status FROM verifications WHERE platform=? AND handle=?",
        (plat_key, handle_clean),
    ).fetchone()
    if existing:
        return
    import random

    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    auto_code = "VLTRX-" + "".join(random.choices(chars, k=6))
    cv.execute(
        "INSERT INTO verifications (created_at,platform,handle,code,status,note) VALUES (?,?,?,?,?,?)",
        (
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            plat_key,
            handle_clean,
            auto_code,
            "pending",
            f"Auto-created from submission. Ask creator to DM {auto_code} to @viltrox.official",
        ),
    )
    conn_v.commit()


def _create_submission_stub_sync(
    current_uid: int | None,
    request_url: str,
    title: str,
    caption: str,
    raw_text: str,
    platform: str,
    extracted_handle: str,
    uploaded_video_path: str,
    uploaded_video_filename: str,
) -> int:
    return create_submission_stub(
        user_id=current_uid,
        url=request_url,
        title=title,
        caption=caption,
        raw_text=raw_text,
        platform=platform,
        extracted_handle=extracted_handle,
        uploaded_video_path=uploaded_video_path,
        uploaded_video_filename=uploaded_video_filename,
    )


def _emit_submission_to_party_layer(
    *,
    submission_id: int,
    user_id: int | None,
    platform: str,
    url: str,
    extracted_handle: str,
    title: str,
) -> None:
    """
    Phase 1 wire: submission create → party (via user_id) → creator.submission_created event.

    Silently no-ops on SQLite dev or if party layer not applied.
    """
    from app.db.connection import is_postgres_runtime

    if not is_postgres_runtime():
        return

    from app.services.party.party_service import get_or_create_by_user_id, _touch_party
    from app.services.party.event_writer import emit_creator_submission_created

    party_id: str | None = None
    creator_code = ""
    if user_id:
        party_id = get_or_create_by_user_id(user_id, origin_source="creator_api")
        if party_id:
            _touch_party(party_id, is_creator=True, lifecycle_stage="creator")

        # Opportunistic: pick creator_code from users if available
        try:
            from app.db.connection import get_conn
            row = get_conn().execute(
                "SELECT creator_code FROM users WHERE id = ? LIMIT 1",
                (int(user_id),),
            ).fetchone()
            if row:
                creator_code = str(row["creator_code"] or "")
        except Exception:
            logger.debug("party.creator_code_lookup_failed", exc_info=True)

    emit_creator_submission_created(
        party_id=party_id,
        submission_id=submission_id,
        platform=platform or "",
        url=url or "",
        creator_code=creator_code,
        handle=extracted_handle or "",
        extra={"title": title or ""},
    )


def _bind_uploaded_asset_sync(
    submission_id: int,
    resolved_uploaded_video: dict | None,
    uploaded_video_path: str,
):
    asset = attach_uploaded_asset_to_submission(
        submission_id=submission_id,
        asset_id=int((resolved_uploaded_video or {}).get("asset_id") or 0),
        r2_key=str((resolved_uploaded_video or {}).get("r2_key") or ""),
        local_path=uploaded_video_path,
    )
    if asset is None and (str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path):
        asset_id = register_submission_asset(
            submission_id=submission_id,
            asset_role="uploaded_video",
            storage_key=str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path,
            mime_type=str((resolved_uploaded_video or {}).get("mime_type") or "application/octet-stream"),
            size_bytes=int(float((resolved_uploaded_video or {}).get("size_mb") or 0) * 1024 * 1024),
        )
        asset = {
            "id": asset_id,
            "submission_id": submission_id,
            "storage_key": str((resolved_uploaded_video or {}).get("r2_key") or "") or uploaded_video_path,
        }
    storage_key = str((asset or {}).get("storage_key") or "").strip()
    if storage_key.startswith("videos/"):
        set_submission_video_r2_key(submission_id, storage_key, force=False)
    return asset


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
    """
    旧同步模式: 当场分析, 当场返回完整结果。
    适用于:
    - 前端还没改成轮询模式
    - 需要立即看到完整结果的场景
    注意: 这个接口会阻塞很久 (10-60秒), 后续应迁移到 /api/audit/v2
    """
    if getattr(request.app.state, "job_queue", None) is not None:
        response = await audit_async(request, req)
        response["deprecated_sync"] = True
        response["message"] = "Synchronous audit is deprecated; request was queued instead."
        return response

    current_uid = current_user["id"] if current_user else None
    extracted_handle = ""
    scraped = {
        "scraped_ok": False,
        "title": "", "caption": "", "scraped_text": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False, "shares": False, "favorites": False},
        "visible_comments": [],
        "error": "No URL provided",
    }

    if req.url and valid_url(req.url):
        scraped = await scrape_url(req.url)

    title    = req.title.strip()   or scraped["title"]
    caption  = req.caption.strip() or scraped["caption"]
    raw_text = req.raw_text.strip() or scraped["scraped_text"]

    # ── Video analysis (Claude Vision) ──
    video_analysis_result = None
    video_text = ""
    resolved_uploaded_video = await db_read(partial(_resolve_uploaded_video_payload_sync, req.uploaded_video))

    if resolved_uploaded_video and resolved_uploaded_video.get("path"):
        video_text = f"{req.uploaded_video.filename} {title} {caption} {raw_text}"
        video_analysis_result = analyze_video_with_claude(
            str(resolved_uploaded_video.get("path") or ""),
            str(resolved_uploaded_video.get("filename") or req.uploaded_video.filename),
            creator_handle=extracted_handle or req.user_handle or ""
        ) or {}
        vr = video_analysis_result
        if vr.get("brand_elements"):
            video_text += " " + " ".join(vr["brand_elements"])
        if vr.get("products_detected"):
            video_text += " " + " ".join(vr["products_detected"])
        if vr.get("notes"):
            video_text += " " + vr["notes"]
        if vr.get("content_topic"):
            video_text += " " + vr["content_topic"]
    elif resolved_uploaded_video:
        video_text = f"{resolved_uploaded_video.get('filename') or req.uploaded_video.filename} {title} {caption} {raw_text}"
    else:
        # ── URL submission: smart multi-layer analysis ──
        video_text = f"{title} {caption} {raw_text}"
        if req.url and (ANTHROPIC_AVAILABLE or GEMINI_AVAILABLE):
            handle_for_analysis = req.user_handle or extract_handle_from_url(req.url) or ""
            _platform_early = detect_platform(req.url) if req.url else "Unknown"
            video_analysis_result = await analyze_url_content_smart(
                url=req.url,
                title=title,
                caption=caption,
                scraped_text=scraped.get("scraped_text", ""),
                og_image=scraped.get("og_image", ""),
                platform=_platform_early,
                creator_handle=handle_for_analysis,
            ) or {}
            vr = video_analysis_result
            if vr.get("brand_elements"):
                video_text += " " + " ".join(vr["brand_elements"])
            if vr.get("products_detected"):
                video_text += " " + " ".join(vr["products_detected"])
            if vr.get("viltrox_products_all"):
                video_text += " " + " ".join(vr["viltrox_products_all"])

    # ── Similarity / spam detection ──
    handle_for_sim = req.user_handle or (
        next(iter(req.linked_handles.values()), "") if req.linked_handles else ""
    )
    similarity_result = await db_read(
        partial(
            _check_similarity_sync,
            handle_for_sim,
            title or (req.uploaded_video.filename if req.uploaded_video else ""),
            detect_platform(req.url) if req.url else "Uploaded Video",
            req.url or "",
        )
    )

    if similarity_result.get("hard_reject"):
        return {
            "status": "rejected",
            "rejection_code": "duplicate_or_spam",
            "rejection_reason": similarity_result["reason"],
            "platform": detect_platform(req.url) if req.url else "Uploaded Video",
            "viltrox_detected": False,
            "detection_status": "rejected",
            "final_score": 0, "creator_score": 0,
            "overall_score": 0, "risk_score": 0,
            "recommendation": "Rejected — " + similarity_result["reason"],
            "memo": similarity_result["reason"],
        }

    vr = video_analysis_result or {}

    metrics = {
        "views":     req.metrics.views     or scraped["metrics"]["views"],
        "likes":     req.metrics.likes     or scraped["metrics"]["likes"],
        "comments":  req.metrics.comments  or scraped["metrics"]["comments"],
        "shares":    req.metrics.shares    or scraped["metrics"]["shares"],
        "favorites": req.metrics.favorites or scraped["metrics"]["favorites"],
    }

    metrics_available = dict(scraped["metrics_available"])
    if req.metrics.views:     metrics_available["views"]     = True
    if req.metrics.likes:     metrics_available["likes"]     = True
    if req.metrics.comments:  metrics_available["comments"]  = True
    if req.metrics.shares:    metrics_available["shares"]    = True
    if req.metrics.favorites: metrics_available["favorites"] = True

    full_text = " ".join([title, caption, raw_text, video_text]).strip()
    platform  = detect_platform(req.url) if req.url else "Uploaded Video"
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

    # ── Ownership verification ──
    ownership_verified = True
    ownership_note = ""

    if req.url and not req.uploaded_video:
        def norm_handle(h: str) -> str:
            return h.lower().strip().lstrip("@").split("?")[0].rstrip("/")

        all_linked_norms = {norm_handle(v) for v in (req.linked_handles or {}).values() if v}
        OFFICIAL = {"viltrox.official","viltrox.usa","viltrox_official","viltroxofficial","唯卓仕官方"}

        if extracted_handle:
            submitted_norm = norm_handle(extracted_handle)
            if submitted_norm in OFFICIAL:
                ownership_verified = True
            elif all_linked_norms and submitted_norm not in all_linked_norms:
                return {
                    "status": "rejected",
                    "rejection_code": "ownership_mismatch",
                    "rejection_reason": (
                        f"⛔ 投稿被拒绝：检测到账号 @{submitted_norm} 未绑定到您的账户。\n\n"
                        "请勿提交他人内容。如需提交此账号的内容，请先在「账号管理」中绑定该平台账号。\n\n"
                        "This submission was rejected: account @" + submitted_norm +
                        " is not linked to your profile."
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
            elif not all_linked_norms:
                ownership_verified = False
                ownership_note = "No linked accounts — please link a platform account first"
        elif req.url and not extracted_handle:
            ownership_verified = False
            ownership_note = "Could not verify account ownership from URL"

    product_match = classify_product(full_text)
    gear_mentions = detect_gear_mentions(full_text)
    brand = detect_viltrox(full_text, req.hints.model_dump())

    OFFICIAL = {"viltrox.official","viltrox.usa","viltrox_official","viltroxofficial","唯卓仕官方"}
    handle_norm = extracted_handle.lstrip("@").lower()
    if handle_norm in OFFICIAL and brand["status"] != "not_detected":
        brand["status"] = "confirmed"
        brand["confirmed"] = True
        if "Official Viltrox account" not in brand["evidence"]:
            brand["evidence"].insert(0, "Official Viltrox account")

    if video_analysis_result and video_analysis_result.get("analyzed"):
        vr = video_analysis_result
        if vr.get("viltrox_detected"):
            conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
            forced_status = conf_map.get(vr.get("confidence", "low"), "suspected")
            if brand["status"] != "confirmed":
                brand["status"] = forced_status
                brand["confirmed"] = (forced_status == "confirmed")
            brand["evidence"] = list(set(brand["evidence"] + vr.get("brand_elements", [])))
            if vr.get("logo_visible"):
                brand["auto_flags"]["logo"] = True
            if vr.get("product_visible"):
                brand["auto_flags"]["product"] = True
            for ct in vr.get("content_types", []):
                if ct not in brand["content_types"]:
                    brand["content_types"].append(ct)
        if vr.get("products_detected") and product_match["confidence"] == "none":
            for pd in vr["products_detected"]:
                pm2 = classify_product(pd)
                if pm2["confidence"] != "none":
                    product_match = pm2
                    break

    if product_match["confidence"] in {"high", "medium"}:
        brand["auto_flags"]["product"] = True

    comment_spam  = analyze_comments_for_spam(scraped["visible_comments"])
    risk          = compute_risk(metrics, metrics_available, comment_spam)
    creator_score = compute_creator_score(metrics.get("views", 0), metrics.get("likes", 0), metrics.get("comments", 0), metrics.get("shares", 0))
    content_score = 30 if brand["confirmed"] else 0
    campaign      = compute_campaign_score(
        content_score = content_score,
        views     = metrics.get("views", 0),
        likes     = metrics.get("likes", 0),
        comments  = metrics.get("comments", 0),
        shares    = metrics.get("shares", 0),
        favorites = metrics.get("favorites", 0),
    )
    final_score   = max(0, campaign["raw_score"] - risk["penalty"]) if brand["confirmed"] else 0

    has_video = bool(req.uploaded_video)
    if has_video:
        final_score = min(400, final_score + 50)

    hint_bonus = 0
    if req.hints.logo:    hint_bonus += 15
    if req.hints.product: hint_bonus += 12
    if req.hints.voice:   hint_bonus += 10
    if req.hints.review:  hint_bonus += 10
    final_score = min(400, final_score + hint_bonus)

    if video_analysis_result and video_analysis_result.get("viltrox_detected"):
        bonus = vr.get("brand_score_bonus", 0)
        final_score = min(400, final_score + bonus)

    if not ownership_verified:
        final_score   = 0
        overall_score = 0
        brand["status"] = "unverified_ownership"
        recommendation = "Rejected — submitted URL does not match linked account"
    else:
        overall_score = creator_score if not brand["confirmed"] else round((final_score / 4) * 0.7 + creator_score * 0.3)

    video_type_labels = {
        "review": "Product Review", "tutorial": "Tutorial / How-to",
        "lifestyle": "Lifestyle / Vlog", "photography": "Photography Showcase",
        "unboxing": "Unboxing", "comparison": "Lens Comparison",
        "cinematic": "Cinematic / Film", "travel": "Travel / Outdoor",
    }
    detected_types = brand.get("content_types", [])
    if video_analysis_result and video_analysis_result.get("content_types"):
        for ct in video_analysis_result["content_types"]:
            if ct not in detected_types:
                detected_types.append(ct)
    video_type_summary = " · ".join(
        video_type_labels.get(t, t.capitalize()) for t in detected_types
    ) if detected_types else "General / Unclassified"

    if brand["status"] == "confirmed":
        recommendation = "Eligible for brand campaign pool"
    elif brand["status"] == "suspected":
        recommendation = "Pending manual review"
    else:
        recommendation = "Rejected — no Viltrox content detected"
        final_score = 0
        overall_score = 0
        creator_score = 0

    if brand["status"] == "confirmed":
        memo = f"Status=confirmed. Platform={platform}. Campaign Score={final_score}, Creator Score={creator_score}, Overall Score={overall_score}. Evidence: {' / '.join(brand['evidence'])}. Type: {video_type_summary}."
        if video_analysis_result and video_analysis_result.get("analyzed"):
            memo += f" [Video] {vr.get('notes','')}"
    elif brand["status"] == "suspected":
        memo = f"Status=suspected. Manual review recommended. Creator Score={creator_score}. Evidence: {' / '.join(brand['evidence'])}. Type: {video_type_summary}."
    else:
        memo = f"Status=not_detected. Content not related to Viltrox — no points awarded. All scores zeroed. Type: {video_type_summary}."
        if video_analysis_result and video_analysis_result.get("error"):
            memo += f" Video analysis: {video_analysis_result['error']}"

    video_analysis = None
    if resolved_uploaded_video:
        if not title:
            title = str(resolved_uploaded_video.get("filename") or "")
        video_analysis = {
            "uploaded": True,
            "asset_id": int(resolved_uploaded_video.get("asset_id") or 0),
            "r2_key": str(resolved_uploaded_video.get("r2_key") or ""),
            "filename": str(resolved_uploaded_video.get("filename") or ""),
            "mime_type": str(resolved_uploaded_video.get("mime_type") or ""),
            "size_mb": float(resolved_uploaded_video.get("size_mb") or 0),
            "analyzed": video_analysis_result.get("analyzed", False) if video_analysis_result else False,
            "frames_checked": video_analysis_result.get("frames_checked", 0) if video_analysis_result else 0,
            "method": video_analysis_result.get("method", "none") if video_analysis_result else "none",
            "viltrox_in_video": video_analysis_result.get("viltrox_detected", False) if video_analysis_result else False,
            "confidence": video_analysis_result.get("confidence", "none") if video_analysis_result else "none",
            "brand_elements": video_analysis_result.get("brand_elements", []) if video_analysis_result else [],
            "products_found": video_analysis_result.get("products_detected", []) if video_analysis_result else [],
            "logo_visible": video_analysis_result.get("logo_visible", False) if video_analysis_result else False,
            "product_visible": video_analysis_result.get("product_visible", False) if video_analysis_result else False,
            "score_bonus": video_analysis_result.get("brand_score_bonus", 0) if video_analysis_result else 0,
            "vision_notes": video_analysis_result.get("notes", "") if video_analysis_result else "",
            "error": video_analysis_result.get("error") if video_analysis_result else None,
            "camera_mentions": gear_mentions["camera_mentions"],
            "lens_mentions": gear_mentions["lens_mentions"],
            "camera_body":    video_analysis_result.get("camera_body") if video_analysis_result else None,
            "camera_brand":   video_analysis_result.get("camera_brand") if video_analysis_result else None,
            "viltrox_lens":   video_analysis_result.get("viltrox_lens") if video_analysis_result else None,
            "other_lens":     video_analysis_result.get("other_lens") if video_analysis_result else None,
            "flash":          video_analysis_result.get("flash") if video_analysis_result else None,
            "adapter":        video_analysis_result.get("adapter") if video_analysis_result else None,
            "accessories":    video_analysis_result.get("accessories", []) if video_analysis_result else [],
            "gear_combo":     video_analysis_result.get("gear_combo", "") if video_analysis_result else "",
            "content_genre":  video_analysis_result.get("content_genre", "") if video_analysis_result else "",
            "content_topic":  video_analysis_result.get("content_topic", "") if video_analysis_result else "",
            "content_summary": video_analysis_result.get("content_summary", "") if video_analysis_result else "",
            "production_quality": video_analysis_result.get("production_quality", "") if video_analysis_result else "",
            "audience_fit":   video_analysis_result.get("audience_fit", "") if video_analysis_result else "",
            "content_types":  video_analysis_result.get("content_types", []) if video_analysis_result else [],
            "notes":          video_analysis_result.get("notes", "") if video_analysis_result else "",
            "quality_scores":      video_analysis_result.get("quality_scores", {}) if video_analysis_result else {},
            "quality_overall":     video_analysis_result.get("quality_overall", 0) if video_analysis_result else 0,
            "quality_summary":     video_analysis_result.get("quality_summary", "") if video_analysis_result else "",
            "reference_value":     video_analysis_result.get("reference_value", "") if video_analysis_result else "",
            "reference_reasons":   video_analysis_result.get("reference_reasons", []) if video_analysis_result else [],
            "improvements":        video_analysis_result.get("improvements", []) if video_analysis_result else [],
            "marketing_potential": video_analysis_result.get("marketing_potential", "") if video_analysis_result else "",
            "marketing_notes":     video_analysis_result.get("marketing_notes", "") if video_analysis_result else "",
            "tech_score":          video_analysis_result.get("tech_score", 0) if video_analysis_result else 0,
            "marketing_score":     video_analysis_result.get("marketing_score", 0) if video_analysis_result else 0,
            "best_frame_path":     video_analysis_result.get("best_frame_path", "") if video_analysis_result else "",
            "timestamps":          video_analysis_result.get("timestamps", []) if video_analysis_result else [],
        }
    else:
        video_analysis = {
            "uploaded": False,
            "analyzed": True,
            "method": "text_analysis",
            "og_image":       scraped.get("og_image", ""),
            "camera_body":    vr.get("camera_body"),
            "camera_brand":   vr.get("camera_brand"),
            "viltrox_lens":   vr.get("viltrox_lens"),
            "other_lens":     vr.get("other_lens"),
            "flash":          vr.get("flash"),
            "adapter":        vr.get("adapter"),
            "accessories":    vr.get("accessories", []),
            "gear_combo":     vr.get("gear_combo", ""),
            "brand_elements": vr.get("brand_elements", []),
            "products_found": vr.get("products_detected", []),
            "content_genre":  vr.get("content_genre", ""),
            "content_topic":  vr.get("content_topic", ""),
            "content_summary": vr.get("content_summary", ""),
            "production_quality": vr.get("production_quality", ""),
            "audience_fit":   vr.get("audience_fit", ""),
            "content_types":  vr.get("content_types", []),
            "notes":          vr.get("notes", ""),
            "camera_mentions": gear_mentions["camera_mentions"],
            "lens_mentions":   gear_mentions["lens_mentions"],
            "quality_scores":      vr.get("quality_scores", {}),
            "quality_overall":     vr.get("quality_overall", 0),
            "quality_summary":     vr.get("quality_summary", ""),
            "reference_value":     vr.get("reference_value", ""),
            "reference_reasons":   vr.get("reference_reasons", []),
            "improvements":        vr.get("improvements", []),
            "marketing_potential": vr.get("marketing_potential", ""),
            "marketing_notes":     vr.get("marketing_notes", ""),
            "tech_score":          vr.get("tech_score", 0),
            "marketing_score":     vr.get("marketing_score", 0),
            "timestamps":          vr.get("timestamps", []),
            "per_image_analysis":  vr.get("per_image_analysis", []),
        }

    result = {
        "status": "success",
        "url": req.url,
        "platform": platform,
        "extracted_handle": extracted_handle,
        "ownership_verified": ownership_verified,
        "ownership_note": ownership_note,
        "video_type_summary": video_type_summary,
        "title": title,
        "caption": caption,
        "scraped_text": raw_text,
        "scraped_ok": scraped["scraped_ok"],
        "scrape_error": scraped["error"],
        "metrics": metrics,
        "metrics_available": metrics_available,
        "detection_status": brand["status"],
        "viltrox_detected": brand["confirmed"],
        "evidence": brand["evidence"],
        "content_types": brand["content_types"],
        "auto_flags": brand["auto_flags"],
        "product_match": product_match,
        "gear_mentions": gear_mentions,
        "scores": {
            "content_score": campaign["content_score"],
            "campaign_interaction_score": campaign["campaign_interaction_score"],
            "creator_score": creator_score,
            "overall_score": overall_score,
            "risk_score": risk["risk_score"],
            "raw_score": campaign["raw_score"],
            "final_score": final_score,
        },
        "risk": risk,
        "ratios": compute_ratios(metrics.get("views",0), metrics.get("likes",0), metrics.get("comments",0), metrics.get("shares",0), metrics.get("favorites",0)),
        "recommendation": recommendation,
        "memo": memo,
        "visible_comments": scraped["visible_comments"],
        "comment_spam": comment_spam,
        "video_analysis": video_analysis,
        "tech_score":     vr.get("tech_score", 0),
        "marketing_score": vr.get("marketing_score", 0),
        "similarity": similarity_result,
        "needs_manual_review": (
            similarity_result.get("needs_review", False) or
            (video_analysis_result or {}).get("needs_manual_review", False)
        ),
        "manual_review_reason": " | ".join(filter(None, [
            similarity_result.get("reason", ""),
            (video_analysis_result or {}).get("manual_review_reason", ""),
        ])) or None,
    }

    # ── Update creator profile ──
    handle_for_profile = extracted_handle or req.user_handle or ""
    if handle_for_profile and video_analysis_result:
        update_creator_profile(handle_for_profile, video_analysis_result, platform)

    # ── Update genre benchmark + compute percentile ──
    genre_for_bench = vr.get("content_genre", "")
    tech_s  = vr.get("tech_score", 0)
    mkt_s   = vr.get("marketing_score", 0)
    percentiles = {"percentile_tech": 0, "percentile_mkt": 0}
    if genre_for_bench and tech_s > 0:
        percentiles = update_genre_benchmark(genre_for_bench, tech_s, mkt_s)
    result["percentile_tech"]         = percentiles["percentile_tech"]
    result["percentile_mkt"]          = percentiles["percentile_mkt"]
    result["content_genre"]           = genre_for_bench
    result["vertical_category"]       = vr.get("vertical_category", "")
    result["vertical_tech_score"]     = vr.get("vertical_tech_score", 0)
    result["vertical_mkt_score"]      = vr.get("vertical_mkt_score", 0)
    result["community_value"]         = vr.get("community_value", 0)
    result["product_showcase_score"]  = vr.get("product_showcase_score", 0)
    result["brand_exposure_score"]    = vr.get("brand_exposure_score", 0)
    result["storytelling_score"]      = vr.get("storytelling_score", 0)
    result["tech_status"]             = vr.get("tech_status", "")
    result["tech_floor"]              = vr.get("tech_floor", {})
    result["logo_detected"]           = vr.get("logo_detected", 0)
    result["product_closeup_count"]   = vr.get("product_closeup_count", 0)
    result["brand_exposure_detail"]   = vr.get("brand_exposure_detail", {})

    # ── Auto-add to verification queue ──
    handle_for_ver = extracted_handle or req.user_handle or ""
    platform_for_ver = platform if platform != "Uploaded Video" else (
        list(req.linked_handles.keys())[0] if req.linked_handles else "direct"
    )
    if handle_for_ver:
        try:
            await db_write(partial(_auto_create_verification_sync, platform_for_ver, handle_for_ver))
        except Exception:
            logger.exception(
                "audit.auto_create_verification_failed",
                extra={"platform": platform_for_ver, "handle": handle_for_ver},
            )

    save_submission(result, user_id=current_uid)
    return result
