"""
api/routers/media.py — shared submission media playback endpoints
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.core.security import get_current_user
from app.db.repositories.assets import get_submission_asset
from app.services.media.storage import resolve_local_media_path


FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
router = APIRouter(tags=["media"])
logger = get_logger(__name__)


def _load_submission_media_row(submission_id: int):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, user_id, detection_status, video_path, video_analysis
        FROM submissions WHERE id=?
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


def _submission_is_public(row) -> bool:
    return str(row.get("detection_status") or "").strip().lower() == "confirmed"


def _require_submission_media_access(request: Request, submission_id: int):
    row = _load_submission_media_row(submission_id)
    user = get_current_user(request)
    if user and str(user.get("role") or "").strip().lower() == "admin":
        return row
    if user and int(row.get("user_id") or 0) == int(user.get("id") or 0):
        return row
    if _submission_is_public(row):
        return row
    raise HTTPException(status_code=404, detail="Not found")


def resolve_video_response(row):
    submission_id = int(row.get("id") or 0)
    video_path = row.get("video_path") or ""
    if not video_path:
        try:
            va = json.loads(row.get("video_analysis") or "{}")
            video_path = va.get("analysis_path", "") or va.get("path", "")
        except Exception:
            logger.warning("media.video_analysis_parse_failed", extra={"submission_id": submission_id}, exc_info=True)
            video_path = ""
    video_path = resolve_local_media_path(video_path)
    if video_path:
        media_type = mimetypes.guess_type(video_path)[0] or "video/mp4"
        return FileResponse(video_path, media_type=media_type)

    try:
        from app.services.media.r2 import get_presigned_url

        va = json.loads(row.get("video_analysis") or "{}")
        r2_key = va.get("r2_key", "") or ""
        if not r2_key:
            asset = get_submission_asset(submission_id)
            if asset:
                storage_key = str(asset.get("storage_key") or "").strip()
                if storage_key.startswith("videos/"):
                    r2_key = storage_key
                else:
                    local_path = resolve_local_media_path(storage_key)
                    if local_path:
                        return FileResponse(local_path, media_type="video/mp4")
        if r2_key:
            return RedirectResponse(url=get_presigned_url(r2_key))
    except Exception:
        logger.warning("media.resolve_video_r2_failed", extra={"submission_id": submission_id}, exc_info=True)

    raise HTTPException(status_code=404, detail="Video file not found on disk")


def resolve_poster_response(row):
    try:
        va = json.loads(row.get("video_analysis") or "{}")
        best_frame_path = va.get("best_frame_path", "")
        best_frame_path = resolve_local_media_path(best_frame_path)
        if best_frame_path:
            return FileResponse(best_frame_path, media_type="image/jpeg")
    except Exception:
        logger.warning("media.poster_best_frame_parse_failed", extra={"submission_id": int(row.get('id') or 0)}, exc_info=True)

    video_path = resolve_local_media_path(row.get("video_path") or "")
    if not video_path:
        try:
            va = json.loads(row.get("video_analysis") or "{}")
            video_path = resolve_local_media_path(va.get("analysis_path", "") or va.get("path", "") or "")
        except Exception:
            logger.warning("media.poster_video_path_resolve_failed", extra={"submission_id": int(row.get('id') or 0)}, exc_info=True)
            video_path = ""
    if video_path and FFMPEG_AVAILABLE:
        try:
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "thumb.jpg")
                subprocess.run(
                    ["ffmpeg", "-i", video_path, "-ss", "2", "-vframes", "1", "-vf", "scale=960:-1", "-q:v", "3", out],
                    capture_output=True,
                    timeout=20,
                )
                if os.path.exists(out):
                    with open(out, "rb") as handle:
                        return Response(content=handle.read(), media_type="image/jpeg")
        except Exception:
            logger.warning("media.poster_ffmpeg_extract_failed", extra={"submission_id": int(row.get('id') or 0)}, exc_info=True)

    raise HTTPException(status_code=404, detail="No frame available")


@router.get("/api/submissions/{submission_id}/video")
def serve_submission_video(submission_id: int, request: Request):
    row = _require_submission_media_access(request, submission_id)
    return resolve_video_response(row)


@router.get("/api/submissions/{submission_id}/poster")
def serve_submission_poster(submission_id: int, request: Request):
    row = _require_submission_media_access(request, submission_id)
    return resolve_poster_response(row)
