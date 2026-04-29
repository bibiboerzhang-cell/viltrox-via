"""
api/routers/uploads.py — 文件上传路由
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, Request

from app.core.logging import get_logger
from app.core.config import (
    UPLOAD_DIR,
    UPLOAD_DUPLICATE_PHASH_DISTANCE,
    UPLOAD_DUPLICATE_PHASH_MATCHES,
    UPLOAD_FINGERPRINT_CONCURRENCY,
    UPLOAD_R2_CONCURRENCY,
    UPLOAD_RATE_LIMIT_MAX,
    UPLOAD_RATE_LIMIT_WINDOW_SEC,
)
from app.db.connection import db_read, db_write
from app.db.repositories.assets import (
    find_duplicate_submission_asset,
    register_submission_asset,
    save_asset_fingerprints,
)
from app.services.media.r2 import upload_file as r2_upload, delete_object as r2_delete
from app.services.media.fingerprints import probe_video_fingerprints
from app.services.security.rate_limiter import rate_limit
from app.services.security.mime_check import IMAGE_MIME_TYPES, VIDEO_MIME_TYPES, validate_upload_magic
from app.core.security import get_current_user, require_admin
from app.services.trust import enforce_dynamic_submission_guard

router = APIRouter(tags=["uploads"])
logger = get_logger(__name__)

MAX_VIDEO_MB = 500
MAX_VIDEO_BYTES = MAX_VIDEO_MB * 1024 * 1024
MAX_REWARD_IMAGE_MB = 10
MAX_REWARD_IMAGE_BYTES = MAX_REWARD_IMAGE_MB * 1024 * 1024
_FINGERPRINT_SEMAPHORE = asyncio.Semaphore(max(1, int(UPLOAD_FINGERPRINT_CONCURRENCY or 1)))
_R2_SEMAPHORE = asyncio.Semaphore(max(1, int(UPLOAD_R2_CONCURRENCY or 1)))


def _save_upload_sync(src_file, dst_path, *, max_bytes: int | None = None):
    """同步文件拷贝 — 必须用 asyncio.to_thread 调用"""
    total = 0
    src_file.seek(0)
    with open(dst_path, "wb") as buffer:
        while True:
            chunk = src_file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"File too large: limit is {round(max_bytes / (1024 * 1024), 2)} MB")
            buffer.write(chunk)


def _cleanup_video_upload_artifacts(save_path: Path, r2_key: str) -> None:
    save_path.unlink(missing_ok=True)
    if r2_key:
        try:
            r2_delete(r2_key)
        except Exception:
            logger.warning("upload.video_cleanup_r2_failed", extra={"r2_key": r2_key}, exc_info=True)

@router.post("/api/upload/video")
@rate_limit("upload_video", max_requests=UPLOAD_RATE_LIMIT_MAX, window_sec=UPLOAD_RATE_LIMIT_WINDOW_SEC)
async def upload_video(request: Request):
    user = await asyncio.to_thread(get_current_user, request)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Please sign in before uploading a video.")
    await asyncio.to_thread(enforce_dynamic_submission_guard, request, user, "upload_video")
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile) and not (
        hasattr(file, "filename") and hasattr(file, "file")
    ):
        raise HTTPException(status_code=400, detail="Video file is required.")
    title = str(form.get("title") or "")
    notes = str(form.get("notes") or "")
    content_type = file.content_type or ""
    if content_type and content_type not in VIDEO_MIME_TYPES and not content_type.startswith("video/"):
        return {"status": "error", "message": f"File type not allowed: {content_type}"}
    ext = Path(file.filename or "video").suffix or ".mp4"
    video_id = f"vid_{uuid.uuid4().hex[:10]}"
    save_path = UPLOAD_DIR / f"{video_id}{ext}"
    try:
        await asyncio.to_thread(_save_upload_sync, file.file, save_path, max_bytes=MAX_VIDEO_BYTES)
    except ValueError as exc:
        save_path.unlink(missing_ok=True)
        return {"status": "error", "message": str(exc)}
    except Exception:
        save_path.unlink(missing_ok=True)
        logger.exception("upload.video_save_failed", extra={"filename": file.filename or "", "content_type": content_type})
        return {"status": "error", "message": "Could not persist uploaded video"}
    if not await asyncio.to_thread(validate_upload_magic, save_path, "video"):
        save_path.unlink(missing_ok=True)
        return {"status": "error", "message": "File content does not look like an allowed video"}
    size_mb = round(save_path.stat().st_size / (1024 * 1024), 2)

    async with _FINGERPRINT_SEMAPHORE:
        fingerprint_summary = await asyncio.to_thread(probe_video_fingerprints, str(save_path))

    def _check_duplicate():
        return find_duplicate_submission_asset(
            checksum=fingerprint_summary.get("file_sha256", ""),
            frame_hashes=[
                item.get("fingerprint_value", "")
                for item in fingerprint_summary.get("frame_hashes", [])
            ],
            max_phash_distance=UPLOAD_DUPLICATE_PHASH_DISTANCE,
            min_phash_matches=UPLOAD_DUPLICATE_PHASH_MATCHES,
        )

    duplicate = await db_read(_check_duplicate)
    if duplicate:
        save_path.unlink(missing_ok=True)
        matched_submission_id = int(duplicate.get("matched_submission_id") or 0)
        duplicate_label = "exact same upload" if duplicate.get("match_type") == "checksum" else "highly similar video"
        return {
            "status": "rejected",
            "message": (
                f"Upload rejected: {duplicate_label} already exists"
                + (f" (submission #{matched_submission_id})" if matched_submission_id else "")
            ),
            "duplicate": duplicate,
        }

    # ── R2 上传 + submission_assets 登记 ──
    r2_key = f"videos/{video_id}{ext}"
    try:
        async with _R2_SEMAPHORE:
            await asyncio.to_thread(
                r2_upload,
                str(save_path),
                r2_key,
                content_type or "application/octet-stream",
            )
        logger.info("upload video r2 ok | key=%s | video_id=%s", r2_key, video_id)
    except Exception as e:
        logger.warning("upload video r2 failed | video_id=%s | error=%s", video_id, e)
        r2_key = ""
    try:
        def _record_asset():
            asset_id = register_submission_asset(
                submission_id=0,
                asset_role="uploaded_video_pending",
                storage_key=r2_key or str(save_path),
                mime_type=content_type or "application/octet-stream",
                size_bytes=int(save_path.stat().st_size),
                duration_ms=int(fingerprint_summary.get("duration_ms") or 0),
                width=int(fingerprint_summary.get("width") or 0),
                height=int(fingerprint_summary.get("height") or 0),
                checksum=str(fingerprint_summary.get("file_sha256") or ""),
            )
            save_asset_fingerprints(asset_id, fingerprint_summary.get("frame_hashes", []))
            return asset_id
        asset_id = await db_write(_record_asset)
    except Exception as e:
        logger.warning("upload video asset register failed | video_id=%s | error=%s", video_id, e)
        await asyncio.to_thread(_cleanup_video_upload_artifacts, save_path, r2_key)
        return {"status": "error", "message": "Could not register uploaded video asset"}

    return {
        "status": "success", "video_id": video_id,
        "filename": file.filename, "mime_type": content_type or "application/octet-stream",
        "size_mb": size_mb, "r2_key": r2_key,
        "asset_id": asset_id,
        "checksum": fingerprint_summary.get("file_sha256", ""),
        "frame_hash_count": len(fingerprint_summary.get("frame_hashes", [])),
        "title": title, "notes": notes,
    }


@router.post("/api/admin/upload/reward-image")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def admin_upload_reward_image(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    content_type = file.content_type or ""
    if content_type not in IMAGE_MIME_TYPES:
        return {"status": "error", "message": f"File type not allowed: {content_type}"}
    img_dir = UPLOAD_DIR / "reward_images"
    img_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "image").suffix or ".png"
    img_id = f"reward_{uuid.uuid4().hex[:10]}"
    save_path = img_dir / f"{img_id}{ext}"
    try:
        await asyncio.to_thread(_save_upload_sync, file.file, save_path, max_bytes=MAX_REWARD_IMAGE_BYTES)
    except ValueError as exc:
        save_path.unlink(missing_ok=True)
        return {"status": "error", "message": str(exc)}
    except Exception:
        save_path.unlink(missing_ok=True)
        logger.exception("upload.reward_image_save_failed", extra={"filename": file.filename or "", "content_type": content_type})
        return {"status": "error", "message": "Could not persist reward image"}
    if not await asyncio.to_thread(validate_upload_magic, save_path, "image"):
        save_path.unlink(missing_ok=True)
        return {"status": "error", "message": "File content does not look like an allowed image"}
    return {"status": "success", "image_url": f"/uploads/reward_images/{img_id}{ext}"}
