"""Pure orchestration runtime for the asynchronous audit submission route."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable


_OFFICIAL_HANDLES = {
    "viltrox.official",
    "viltrox.usa",
    "viltrox_official",
    "viltroxofficial",
    "唯卓仕官方",
}


@dataclass(frozen=True)
class AuditSubmitDependencies:
    to_thread: Callable[..., Any]
    enforce_submission_guard: Callable[..., Any]
    valid_url: Callable[[str], bool]
    http_exception: Any
    enforce_queue_backpressure: Callable[..., Any]
    detect_platform: Callable[[str], str]
    extract_handle_from_url: Callable[[str], str]
    db_read: Callable[..., Any]
    db_write: Callable[..., Any]
    check_similarity_sync: Callable[..., Any]
    validate_video_publish_date: Callable[..., Any]
    resolve_uploaded_video_payload_sync: Callable[..., Any]
    create_submission_stub_sync: Callable[..., Any]
    emit_submission_to_party_layer: Callable[..., Any]
    record_submission: Callable[..., Any]
    bind_uploaded_asset_sync: Callable[..., Any]
    normalize_uploaded_video_payload: Callable[..., Any]
    video_job_input: Callable[..., Any]
    logger: Any


@dataclass(frozen=True)
class AuditSubmissionFields:
    current_uid: int | None
    title: str
    caption: str
    raw_text: str
    platform: str
    extracted_handle: str


def _validate_request(req: Any, deps: AuditSubmitDependencies) -> None:
    if req.url and not deps.valid_url(req.url):
        raise deps.http_exception(status_code=400, detail="Invalid URL")
    has_uploaded_video = bool(
        req.uploaded_video
        and (
            req.uploaded_video.asset_id
            or req.uploaded_video.r2_key
            or req.uploaded_video.path
        )
    )
    if not req.url and not has_uploaded_video:
        raise deps.http_exception(
            status_code=400,
            detail="URL or uploaded video required",
        )


def _submission_fields(
    req: Any,
    current_uid: int | None,
    deps: AuditSubmitDependencies,
) -> AuditSubmissionFields:
    title = (req.title or "").strip()
    caption = (req.caption or "").strip()
    raw_text = (req.raw_text or "").strip()
    platform = deps.detect_platform(req.url) if req.url else "Uploaded Video"
    extracted_handle = deps.extract_handle_from_url(req.url) if req.url else ""
    if not extracted_handle and req.user_handle:
        handle = req.user_handle.strip()
        if (
            handle
            and not handle.startswith("@")
            and not handle.startswith("u/")
            and not handle.startswith("http")
        ):
            handle = "@" + handle
        extracted_handle = handle
    if not extracted_handle and req.linked_handles:
        linked_for_platform = req.linked_handles.get(platform.lower(), "")
        if linked_for_platform:
            extracted_handle = linked_for_platform
    if not extracted_handle and req.uploaded_video and req.linked_handles:
        for handle_value in req.linked_handles.values():
            if handle_value:
                extracted_handle = handle_value
                break
    return AuditSubmissionFields(
        current_uid=current_uid,
        title=title,
        caption=caption,
        raw_text=raw_text,
        platform=platform,
        extracted_handle=extracted_handle,
    )


def _normalise_handle(handle: str) -> str:
    return handle.lower().strip().lstrip("@").split("?")[0].rstrip("/")


def _ownership_rejection(
    req: Any,
    fields: AuditSubmissionFields,
) -> dict[str, Any] | None:
    if not req.url or req.uploaded_video:
        return None
    linked_norms = {
        _normalise_handle(value)
        for value in (req.linked_handles or {}).values()
        if value
    }
    if not fields.extracted_handle:
        return None
    submitted_norm = _normalise_handle(fields.extracted_handle)
    if (
        submitted_norm in _OFFICIAL_HANDLES
        or not linked_norms
        or submitted_norm in linked_norms
    ):
        return None
    return {
        "status": "rejected",
        "rejection_code": "ownership_mismatch",
        "rejection_reason": (
            f"⛔ 投稿被拒绝：检测到账号 @{submitted_norm} 未绑定到您的账户。\n\n"
            "请勿提交他人内容。如需提交此账号的内容，请先在「账号管理」中绑定该平台账号。\n\n"
            "This submission was rejected: account @"
            + submitted_norm
            + " is not linked to your profile. Do not submit other people's content."
        ),
        "platform": fields.platform,
        "extracted_handle": fields.extracted_handle,
        "linked_handles": req.linked_handles,
        "url": req.url,
        "viltrox_detected": False,
        "detection_status": "rejected",
        "final_score": 0,
        "creator_score": 0,
        "overall_score": 0,
        "risk_score": 0,
        "recommendation": "Rejected — account not linked",
        "memo": (
            f"Hard reject: @{submitted_norm} not in linked accounts "
            f"{list(linked_norms)}"
        ),
    }


def _similarity_title(req: Any, fields: AuditSubmissionFields) -> str:
    return fields.title or (
        req.uploaded_video.filename if req.uploaded_video else ""
    )


async def _similarity_result(
    req: Any,
    fields: AuditSubmissionFields,
    deps: AuditSubmitDependencies,
) -> dict[str, Any]:
    handle_for_similarity = req.user_handle or (
        next(iter(req.linked_handles.values()), "")
        if req.linked_handles
        else ""
    )
    return await deps.db_read(
        partial(
            deps.check_similarity_sync,
            handle_for_similarity,
            _similarity_title(req, fields),
            fields.platform,
            req.url or "",
        )
    )


def _similarity_rejection(
    similarity: dict[str, Any],
    platform: str,
) -> dict[str, Any] | None:
    if not similarity.get("hard_reject"):
        return None
    reason = similarity["reason"]
    return {
        "status": "rejected",
        "rejection_code": "duplicate_or_spam",
        "rejection_reason": reason,
        "platform": platform,
        "viltrox_detected": False,
        "detection_status": "rejected",
        "final_score": 0,
        "creator_score": 0,
        "overall_score": 0,
        "risk_score": 0,
        "recommendation": "Rejected — " + reason,
        "memo": reason,
    }


async def _publish_rejection(
    req: Any,
    fields: AuditSubmissionFields,
    deps: AuditSubmitDependencies,
) -> dict[str, Any] | None:
    if not req.url or req.uploaded_video:
        return None
    publish_check = await deps.to_thread(
        deps.validate_video_publish_date,
        req.url,
        fields.platform,
        None,
    )
    if publish_check.get("valid", True):
        return None
    reason = publish_check.get(
        "reason",
        "Video is outside the allowed publish window",
    )
    return {
        "status": "rejected",
        "rejection_code": "stale_video",
        "rejection_reason": reason,
        "platform": fields.platform,
        "extracted_handle": fields.extracted_handle,
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


async def _resolve_uploaded_video(
    req: Any,
    fields: AuditSubmissionFields,
    deps: AuditSubmitDependencies,
) -> tuple[dict[str, Any] | None, str, str]:
    resolved = await deps.db_read(
        partial(deps.resolve_uploaded_video_payload_sync, req.uploaded_video)
    )
    path = str(
        (resolved or {}).get("analysis_path")
        or (resolved or {}).get("path")
        or ""
    )
    deps.logger.info(
        "audit.upload_payload_bound",
        extra={
            "uploaded_video": bool(resolved),
            "uploaded_video_asset_id": int((resolved or {}).get("asset_id") or 0),
            "user_id": fields.current_uid,
        },
    )
    filename = str((resolved or {}).get("filename") or "")
    return resolved, path, filename


async def _create_submission(
    req: Any,
    fields: AuditSubmissionFields,
    uploaded_video_path: str,
    uploaded_video_filename: str,
    deps: AuditSubmitDependencies,
) -> int:
    return await deps.db_write(
        partial(
            deps.create_submission_stub_sync,
            fields.current_uid,
            req.url or "",
            fields.title,
            fields.caption,
            fields.raw_text,
            fields.platform,
            fields.extracted_handle,
            uploaded_video_path,
            uploaded_video_filename,
        )
    )


def _emit_submission_events(
    submission_id: int,
    req: Any,
    fields: AuditSubmissionFields,
    deps: AuditSubmitDependencies,
) -> None:
    try:
        deps.emit_submission_to_party_layer(
            submission_id=submission_id,
            user_id=fields.current_uid,
            platform=fields.platform,
            url=req.url or "",
            extracted_handle=fields.extracted_handle,
            title=fields.title,
        )
    except Exception:
        deps.logger.debug(
            "phase1 party-layer emit failed for submission (non-fatal)",
            exc_info=True,
        )
    try:
        deps.record_submission(submission_id, fields.current_uid)
    except Exception:
        deps.logger.warning(
            "audit.activity_submission_attribution_failed",
            extra={
                "submission_id": submission_id,
                "user_id": fields.current_uid,
            },
            exc_info=True,
        )


async def _bind_uploaded_video(
    req: Any,
    submission_id: int,
    resolved: dict[str, Any] | None,
    uploaded_video_path: str,
    deps: AuditSubmitDependencies,
) -> dict[str, Any] | None:
    if not req.uploaded_video:
        return resolved
    asset = await deps.db_write(
        partial(
            deps.bind_uploaded_asset_sync,
            submission_id,
            resolved,
            uploaded_video_path,
        )
    )
    if not asset:
        return resolved
    asset_storage_key = str((asset or {}).get("storage_key") or "")
    previous_storage_key = str((resolved or {}).get("storage_key") or "")
    previous_r2_key = str((resolved or {}).get("r2_key") or "")
    return deps.normalize_uploaded_video_payload(
        {
            **(resolved or {}),
            "asset_id": int((asset or {}).get("id") or 0),
            "storage_key": asset_storage_key or previous_storage_key,
            "r2_key": (
                asset_storage_key
                if asset_storage_key.startswith("videos/")
                else previous_r2_key
            ),
            "path": uploaded_video_path,
        }
    )


def _build_job(
    req: Any,
    fields: AuditSubmissionFields,
    submission_id: int,
    resolved_uploaded_video: dict[str, Any] | None,
    deps: AuditSubmitDependencies,
) -> Any:
    return deps.video_job_input(
        submission_id=submission_id,
        url=req.url or "",
        title=fields.title,
        handle=fields.extracted_handle or req.user_handle or "",
        platform=fields.platform,
        caption=fields.caption,
        scraped_text=fields.raw_text,
        og_image="",
        user_id=fields.current_uid,
        user_handle=req.user_handle or "",
        linked_handles=req.linked_handles or {},
        uploaded_video=resolved_uploaded_video,
        hints=req.hints.model_dump() if req.hints else {},
        metrics=req.metrics.model_dump() if req.metrics else {},
    )


async def _enqueue_job(
    queue: Any,
    job: Any,
    submission_id: int,
    current_uid: int | None,
    deps: AuditSubmitDependencies,
) -> str:
    try:
        if queue is None:
            raise RuntimeError("job queue not available")
        return await queue.enqueue(
            "audit_submission",
            job,
            submission_id=submission_id,
        )
    except Exception:
        deps.logger.exception(
            "audit.enqueue_failed",
            extra={
                "submission_id": submission_id,
                "user_id": current_uid,
            },
        )
        return "enqueue_failed"


def _queued_result(
    *,
    task_id: str,
    submission_id: int,
    fields: AuditSubmissionFields,
    queue_pressure: Any,
) -> dict[str, Any]:
    return {
        "status": "queued",
        "job_id": task_id,
        "submission_id": submission_id,
        "platform": fields.platform,
        "extracted_handle": fields.extracted_handle,
        "message": "Analysis started — poll /api/submissions/{id}/status for results",
        "queue": queue_pressure,
    }


async def audit_async_runtime(
    request: Any,
    req: Any,
    current_user: dict[str, Any] | None,
    *,
    deps: AuditSubmitDependencies,
) -> dict[str, Any]:
    current_uid = current_user["id"] if current_user else None
    await deps.to_thread(
        deps.enforce_submission_guard,
        request,
        current_user,
        "audit_submit",
    )
    _validate_request(req, deps)
    queue = getattr(request.app.state, "job_queue", None)
    queue_pressure = await deps.enforce_queue_backpressure(
        queue,
        job_type="audit_submission",
    )
    fields = _submission_fields(req, current_uid, deps)
    rejection = _ownership_rejection(req, fields)
    if rejection is not None:
        return rejection
    similarity = await _similarity_result(req, fields, deps)
    rejection = _similarity_rejection(similarity, fields.platform)
    if rejection is not None:
        return rejection
    rejection = await _publish_rejection(req, fields, deps)
    if rejection is not None:
        return rejection
    resolved, uploaded_path, uploaded_filename = await _resolve_uploaded_video(
        req,
        fields,
        deps,
    )
    submission_id = await _create_submission(
        req,
        fields,
        uploaded_path,
        uploaded_filename,
        deps,
    )
    _emit_submission_events(submission_id, req, fields, deps)
    resolved = await _bind_uploaded_video(
        req,
        submission_id,
        resolved,
        uploaded_path,
        deps,
    )
    job = _build_job(req, fields, submission_id, resolved, deps)
    task_id = await _enqueue_job(
        queue,
        job,
        submission_id,
        current_uid,
        deps,
    )
    return _queued_result(
        task_id=task_id,
        submission_id=submission_id,
        fields=fields,
        queue_pressure=queue_pressure,
    )


__all__ = ["AuditSubmitDependencies", "audit_async_runtime"]
