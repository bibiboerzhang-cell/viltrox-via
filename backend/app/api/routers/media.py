"""
api/routers/media.py — shared submission media playback endpoints
"""
from __future__ import annotations

import json
import mimetypes
import os
import hashlib
import html
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.core.config import UPLOAD_DIR
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.core.security import get_current_user
from app.db.repositories.assets import get_submission_asset
from app.services.media.access_logging import install_media_proxy_access_log_filter
from app.services.media.storage import resolve_local_media_path
from app.domains.media import cached_image_file, cached_video_file, cached_video_redirect_url, cached_video_url_for_item, is_failure_placeholder_cache


install_media_proxy_access_log_filter()
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
router = APIRouter(tags=["media"])
logger = get_logger(__name__)
VKPI_IMAGE_PROXY_CACHE_DIR = UPLOAD_DIR / "vkpi_media_cache"
VKPI_IMAGE_PROXY_MAX_BYTES = 8 * 1024 * 1024
VKPI_IMAGE_PROXY_TIMEOUT_SEC = max(2, min(8, int(os.getenv("VKPI_IMAGE_PROXY_TIMEOUT_SEC", "4"))))
VKPI_VIDEO_PROXY_MAX_BYTES = int(os.getenv("VKPI_VIDEO_PROXY_MAX_BYTES", str(220 * 1024 * 1024)))
VKPI_VIDEO_PROXY_CHUNK_BYTES = 512 * 1024
VKPI_IMAGE_ALLOWED_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".xx.fbcdn.net",
    ".ytimg.com",
    ".img.youtube.com",
    ".googleusercontent.com",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
    ".byteoversea.com",
    ".apifyusercontent.com",
    ".redd.it",
    ".redditmedia.com",
    ".twimg.com",
)
VKPI_VIDEO_ALLOWED_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".xx.fbcdn.net",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
    ".byteoversea.com",
    ".akamaized.net",
    ".googlevideo.com",
    ".apifyusercontent.com",
    ".redd.it",
    ".redditmedia.com",
    ".twimg.com",
)
_SAFE_RANGE_RE = re.compile(r"^bytes=\d*-\d*$")
_TRANSPARENT_IMAGE_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" viewBox="0 0 1 1"><rect width="1" height="1" fill="none"/></svg>"""
_PRIVATE_VIDEO_CACHE_HEADERS = {
    "Cache-Control": "private, max-age=300, must-revalidate",
    # Range offsets refer to the stored representation.  Explicit identity
    # prevents the app-wide GZip middleware from transforming 206 bodies and
    # dropping Content-Length, which can break browser/CDN seeking semantics.
    "Content-Encoding": "identity",
    "Vary": "Cookie, Authorization",
}


def _require_workspace_media_access(request: Request) -> dict:
    """Require the same HttpOnly login cookie/header used by the workspace.

    Native ``<video>`` range requests cannot attach an application-managed
    Authorization header, but they do include same-origin cookies.  The V-KPI
    media URLs are same-origin (and the dev server proxies ``/api``), so the
    existing login cookie is the compatible authentication boundary here.
    """

    user = get_current_user(request)
    if not user:
        # Private workspace middleware denies anonymous internal APIs with 403;
        # keep the legacy non-/admin media path on the same observable contract.
        raise HTTPException(
            status_code=403,
            detail="Forbidden",
            headers={"Cache-Control": "no-store", "Vary": "Cookie, Authorization"},
        )
    return user


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


def _allowed_external_image_url(raw_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(html.unescape(str(raw_url or "").strip()))
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid image url")
    host = parsed.hostname or ""
    if not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in VKPI_IMAGE_ALLOWED_HOST_SUFFIXES):
        raise HTTPException(status_code=400, detail="image host not allowed")
    return urllib.parse.urlunparse(parsed), host


def _allowed_external_video_url(raw_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(html.unescape(str(raw_url or "").strip()))
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid video url")
    host = parsed.hostname or ""
    if not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in VKPI_VIDEO_ALLOWED_HOST_SUFFIXES):
        raise HTTPException(status_code=400, detail="video host not allowed")
    return urllib.parse.urlunparse(parsed), host


def _safe_range_header(raw_range: str | None) -> str:
    text = str(raw_range or "").strip()
    return text if _SAFE_RANGE_RE.fullmatch(text) else ""


def _upstream_video_headers(host: str, range_header: str = "") -> dict[str, str]:
    base_host = host.split(".", 1)[-1] if "." in host else host
    headers = {
        "Accept": "video/mp4,video/*,*/*;q=0.8",
        "Referer": f"https://{base_host}/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    }
    if range_header:
        headers["Range"] = range_header
    return headers


def _header_int(value: str | None) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return 0


def _stream_upstream_response(upstream):
    try:
        while True:
            chunk = upstream.read(VKPI_VIDEO_PROXY_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        upstream.close()


def _cached_external_image_path(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    VKPI_IMAGE_PROXY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return VKPI_IMAGE_PROXY_CACHE_DIR / digest, VKPI_IMAGE_PROXY_CACHE_DIR / f"{digest}.content-type"


def _fetch_external_image_once(url: str, host: str, *, send_referer: bool) -> tuple[bytes, str]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    }
    if send_referer:
        headers["Referer"] = f"https://{host.split('.', 1)[-1]}/"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=VKPI_IMAGE_PROXY_TIMEOUT_SEC) as response:  # nosec B310 - host allowlist above.
        content_type = str(response.headers.get("content-type") or "image/jpeg").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=502, detail="upstream is not image")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > VKPI_IMAGE_PROXY_MAX_BYTES:
                raise HTTPException(status_code=413, detail="image too large")
            chunks.append(chunk)
    return b"".join(chunks), content_type


def _fetch_external_image(url: str, host: str) -> tuple[bytes, str, bool]:
    """Fetch an allowlisted platform image. Returns (data, content_type, ok).

    ok=False means upstream failed (expired signature / blocked network) and data is
    the transparent fallback SVG — callers must NOT cache it as a real image.
    头像瞬时失败修(2026-07-21 新面孔灰占位案):第一枪失败后立刻重试一枪且不带 Referer
    (签名 CDN 冷抓偶发超时/部分边缘对非本站 Referer 403);仍失败才降级占位。
    """
    try:
        data, content_type = _fetch_external_image_once(url, host, send_referer=True)
        return data, content_type, True
    except HTTPException:
        raise
    except Exception as first_exc:
        first_reason = getattr(first_exc, "code", None) or type(first_exc).__name__
        try:
            data, content_type = _fetch_external_image_once(url, host, send_referer=False)
            return data, content_type, True
        except HTTPException:
            raise
        except urllib.error.HTTPError as exc:
            logger.info(
                "media.image_proxy_upstream_unavailable",
                extra={"host": host, "status": exc.code, "first_reason": str(first_reason)},
            )
            return _TRANSPARENT_IMAGE_SVG, "image/svg+xml", False
        except Exception as exc:
            logger.info(
                "media.image_proxy_fetch_failed",
                extra={"host": host, "reason": type(exc).__name__, "first_reason": str(first_reason)},
            )
            return _TRANSPARENT_IMAGE_SVG, "image/svg+xml", False


@router.get("/api/submissions/{submission_id}/video")
def serve_submission_video(submission_id: int, request: Request):
    row = _require_submission_media_access(request, submission_id)
    return resolve_video_response(row)


@router.get("/api/submissions/{submission_id}/poster")
def serve_submission_poster(submission_id: int, request: Request):
    row = _require_submission_media_access(request, submission_id)
    return resolve_poster_response(row)


@router.get("/api/admin/vkpi/media/image-proxy")
def serve_vkpi_external_image(request: Request, url: str = Query(..., min_length=8, max_length=4096)):
    """Proxy and cache allowed external platform images for admin UI rendering.

    Instagram CDN URLs often break when hot-linked directly from localhost. This
    endpoint keeps the frontend same-origin while still using the real platform
    image URL. It is intentionally allowlisted to avoid becoming an open proxy.
    """
    if not get_current_user(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    normalized_url, host = _allowed_external_image_url(url)
    cache_path, content_type_path = _cached_external_image_path(normalized_url)
    if cache_path.exists() and content_type_path.exists():
        # 自愈:历史 bug 曾把"上游失败的透明 SVG"写进缓存(毒缓存),命中时删除并重抓,
        # 不再永久性地把失败假装成真图。
        if not is_failure_placeholder_cache(cache_path, content_type_path):
            return FileResponse(
                cache_path,
                media_type=content_type_path.read_text().strip() or "image/jpeg",
                headers={"Cache-Control": "private, max-age=86400"},
            )
        cache_path.unlink(missing_ok=True)
        content_type_path.unlink(missing_ok=True)
    data, content_type, ok = _fetch_external_image(normalized_url, host)
    if not ok:
        # 上游失败(签名过期/网络不可达):返回透明占位但绝不写缓存、不允许缓存,
        # 让下一次请求仍有机会拿到真图;X 头供排障区分"真图"与"占位"。
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "no-store", "X-VKPI-Media-Fallback": "upstream_unavailable"},
        )
    cache_path.write_bytes(data)
    content_type_path.write_text(content_type)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/api/admin/vkpi/media/image-cache/{digest}")
def serve_vkpi_cached_image(digest: str):
    cached = cached_image_file(digest)
    if not cached:
        raise HTTPException(status_code=404, detail="cached image not found")
    cache_path, content_type = cached
    return FileResponse(
        cache_path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/api/vkpi-media/image-cache/{digest}")
def serve_public_vkpi_cached_image(digest: str):
    cached = cached_image_file(digest)
    if not cached:
        raise HTTPException(status_code=404, detail="cached image not found")
    cache_path, content_type = cached
    return FileResponse(
        cache_path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@router.get("/api/admin/vkpi/media/video-cache/lookup")
def lookup_vkpi_cached_video(
    request: Request,
    platform: str = Query(..., min_length=1, max_length=40),
    video_id: str = Query(..., min_length=1, max_length=240),
):
    if not get_current_user(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    cached_url = cached_video_url_for_item(platform, video_id)
    return {"hit": bool(cached_url), "cached_url": cached_url or ""}


@router.head("/api/admin/vkpi/media/video-cache/{digest}", include_in_schema=False)
@router.get("/api/admin/vkpi/media/video-cache/{digest}")
def serve_vkpi_cached_video(digest: str, request: Request):
    _require_workspace_media_access(request)
    cached = cached_video_file(digest)
    if not cached:
        redirect_url = cached_video_redirect_url(digest)
        if redirect_url:
            return RedirectResponse(url=redirect_url, status_code=307, headers=_PRIVATE_VIDEO_CACHE_HEADERS)
        raise HTTPException(status_code=404, detail="cached video not found")
    cache_path, content_type = cached
    return FileResponse(
        cache_path,
        media_type=content_type,
        headers=_PRIVATE_VIDEO_CACHE_HEADERS,
    )


@router.head("/api/vkpi-media/video-cache/{digest}", include_in_schema=False)
@router.get("/api/vkpi-media/video-cache/{digest}")
def serve_public_vkpi_cached_video(digest: str, request: Request):
    _require_workspace_media_access(request)
    cached = cached_video_file(digest)
    if not cached:
        redirect_url = cached_video_redirect_url(digest)
        if redirect_url:
            return RedirectResponse(url=redirect_url, status_code=307, headers=_PRIVATE_VIDEO_CACHE_HEADERS)
        raise HTTPException(status_code=404, detail="cached video not found")
    cache_path, content_type = cached
    return FileResponse(
        cache_path,
        media_type=content_type,
        headers=_PRIVATE_VIDEO_CACHE_HEADERS,
    )


@router.get("/api/admin/vkpi/media/video-redirect")
def redirect_vkpi_external_video(request: Request, url: str = Query(..., min_length=8, max_length=4096)):
    """Authenticated allowlisted redirect for platform video playback in admin UI.

    Video URLs from Instagram/TikTok/Apify are large and often short-lived. We
    intentionally redirect instead of caching bytes so the backend does not
    become a large-file proxy, while still avoiding arbitrary open redirects.
    """
    if not get_current_user(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    normalized_url, _host = _allowed_external_video_url(url)
    return RedirectResponse(
        url=normalized_url,
        status_code=307,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/admin/vkpi/media/video-proxy")
def proxy_vkpi_external_video(request: Request, url: str = Query(..., min_length=8, max_length=4096)):
    """Stream allowed platform videos through the admin backend.

    Direct CDN playback from Instagram/TikTok often fails in local browser
    sessions because the browser makes unauthenticated cross-origin media range
    requests. This endpoint keeps playback same-origin, preserves Range
    requests, and remains allowlisted so it cannot become an arbitrary proxy.
    """
    if not get_current_user(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    normalized_url, host = _allowed_external_video_url(url)
    range_header = _safe_range_header(request.headers.get("range"))
    upstream_request = urllib.request.Request(
        normalized_url,
        headers=_upstream_video_headers(host, range_header),
    )
    try:
        upstream = urllib.request.urlopen(upstream_request, timeout=30)  # nosec B310 - host allowlist above.
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail="upstream video unavailable") from exc
    except Exception as exc:
        logger.warning("media.video_proxy_fetch_failed", extra={"host": host}, exc_info=True)
        raise HTTPException(status_code=502, detail="upstream video fetch failed") from exc

    content_type = str(upstream.headers.get("content-type") or "video/mp4").split(";", 1)[0].strip().lower()
    content_length = _header_int(upstream.headers.get("content-length"))
    if not range_header and content_length and content_length > VKPI_VIDEO_PROXY_MAX_BYTES:
        upstream.close()
        raise HTTPException(status_code=413, detail="video too large for non-range proxy request")

    headers: dict[str, str] = {
        "Accept-Ranges": upstream.headers.get("accept-ranges") or "bytes",
        "Cache-Control": "private, max-age=300",
    }
    for name in ("content-length", "content-range", "etag", "last-modified"):
        value = upstream.headers.get(name)
        if value:
            headers[name.title()] = value

    status_code = int(getattr(upstream, "status", 200) or 200)
    if status_code not in {200, 206}:
        upstream.close()
        raise HTTPException(status_code=502, detail="upstream video returned invalid status")

    return StreamingResponse(
        _stream_upstream_response(upstream),
        status_code=status_code,
        media_type=content_type or "video/mp4",
        headers=headers,
    )
