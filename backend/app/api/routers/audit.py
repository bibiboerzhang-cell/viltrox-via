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
from app.api.routers.audit_submit_runtime import (
    AuditSubmitDependencies,
    audit_async_runtime,
)

router = APIRouter(tags=["audit"])
logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 真分流版: 秒回 queued, 重活给 worker
# ──────────────────────────────────────────────

def _audit_submit_dependencies() -> AuditSubmitDependencies:
    return AuditSubmitDependencies(
        to_thread=asyncio.to_thread,
        enforce_submission_guard=enforce_dynamic_submission_guard,
        valid_url=valid_url,
        http_exception=HTTPException,
        enforce_queue_backpressure=enforce_queue_backpressure,
        detect_platform=detect_platform,
        extract_handle_from_url=extract_handle_from_url,
        db_read=db_read,
        db_write=db_write,
        check_similarity_sync=_check_similarity_sync,
        validate_video_publish_date=validate_video_publish_date,
        resolve_uploaded_video_payload_sync=_resolve_uploaded_video_payload_sync,
        create_submission_stub_sync=_create_submission_stub_sync,
        emit_submission_to_party_layer=_emit_submission_to_party_layer,
        record_submission=record_submission,
        bind_uploaded_asset_sync=_bind_uploaded_asset_sync,
        normalize_uploaded_video_payload=normalize_uploaded_video_payload,
        video_job_input=VideoJobInput,
        logger=logger,
    )


@router.post("/api/audit")
@router.post("/api/audit/v2")
@rate_limit("audit_submit", max_requests=AUDIT_RATE_LIMIT_MAX, window_sec=AUDIT_RATE_LIMIT_WINDOW_SEC)
async def audit_async(
    request: Request,
    req: AuditRequest,
    current_user: dict | None = Depends(get_user),
):
    """Validate, persist and enqueue one asynchronous audit submission."""

    return await audit_async_runtime(
        request,
        req,
        current_user,
        deps=_audit_submit_dependencies(),
    )


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
