"""yt-dlp backed item video caching for V-KPI media assets."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.services.scraping.ytdlp import download_video_ytdlp
from app.domains.media.cache_core import (
    PUBLIC_VIDEO_CACHE_PREFIX,
    VIDEO_CACHE_DIR,
    VIDEO_CACHE_GC_RESERVE_BYTES,
    VideoCacheCancelled,
    _atomic_write_json,
    _atomic_write_text,
    _record_media_cache_asset,
    _sha256_file,
    _text,
    _upload_to_r2_if_enabled,
    _utcnow,
    _video_cache_paths,
    _video_item_failure_sidecar,
    _video_item_sidecar_path,
    _video_max_file_bytes,
    run_video_cache_gc,
)

def _cache_video_for_item_via_ytdlp(
    *,
    platform_key: str,
    video_key: str,
    page_url: str,
    force_refresh: bool,
    timeout: int,
    progress_callback: Any | None,
    cancel_check: Any | None,
) -> dict[str, Any]:
    def _maybe_cancel() -> None:
        if cancel_check is not None and cancel_check():
            raise VideoCacheCancelled("video cache cancelled")

    def _maybe_progress(pct: int, text: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0, min(100, int(pct))), text)

    _maybe_cancel()
    digest, cache_path, content_type_path = _video_cache_paths(page_url)
    sidecar_path = _video_item_sidecar_path(platform_key, video_key)
    if cache_path.exists() and content_type_path.exists() and not force_refresh:
        content_type = content_type_path.read_text(encoding="utf-8").strip() or "video/mp4"
        cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
        r2_result = _upload_to_r2_if_enabled(
            media_kind="video",
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url=page_url,
            platform=platform_key,
            external_id=video_key,
        )
        if r2_result.get("cache_url"):
            cache_url = str(r2_result["cache_url"])
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": page_url,
            "digest": digest,
            "cached_url": cache_url,
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "storage_backend": r2_result.get("storage_backend") or "local",
            "r2_key": r2_result.get("r2_key") or "",
            "updated_at": _utcnow(),
            "resolver": "yt-dlp",
        }
        _atomic_write_json(sidecar_path, sidecar)
        return {"status": "cached", "cached": True, **sidecar}

    max_file_bytes = _video_max_file_bytes()
    gc_result = run_video_cache_gc(target_free_bytes=max(VIDEO_CACHE_GC_RESERVE_BYTES, max_file_bytes))
    if gc_result.get("free_bytes", 0) < min(max_file_bytes, VIDEO_CACHE_GC_RESERVE_BYTES):
        return {
            "status": "skipped",
            "cached": False,
            "skipped": True,
            "skip_reason": "global_cache_full",
            "platform": platform_key,
            "video_id": video_key,
            "resolver": "yt-dlp",
            "gc": gc_result,
        }

    try:
        _maybe_progress(20, "yt-dlp 解析视频")
        VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vkpi-ytdlp-", dir=str(VIDEO_CACHE_DIR)) as tmpdir:
            result = download_video_ytdlp(page_url, tmpdir, max_seconds=max(30, min(180, int(timeout or 30) * 4)))
            _maybe_cancel()
            if not result.get("success") or not result.get("path"):
                error = _text(result.get("error"))[:300]
                no_video_source = "no video stream" in error.lower() or "no video formats found" in error.lower()
                if no_video_source:
                    _video_item_failure_sidecar(
                        platform_key=platform_key,
                        video_key=video_key,
                        source_url=page_url,
                        status="failed",
                        reason="yt_dlp_no_video_stream",
                        error=error,
                        resolver="yt-dlp",
                        retryable=False,
                    )
                return {
                    "status": "failed",
                    "cached": False,
                    "platform": platform_key,
                    "video_id": video_key,
                    "reason": "yt_dlp_failed",
                    "resolver": "yt-dlp",
                    "error": error,
                }
            downloaded = Path(str(result["path"]))
            if not downloaded.exists():
                return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "yt_dlp_missing_file", "resolver": "yt-dlp"}
            size_bytes = downloaded.stat().st_size
            if size_bytes > max_file_bytes:
                _video_item_failure_sidecar(
                    platform_key=platform_key,
                    video_key=video_key,
                    source_url=page_url,
                    status="skipped",
                    reason="too_large",
                    resolver="yt-dlp",
                    retryable=False,
                    metadata={"content_length": size_bytes, "max_file_bytes": max_file_bytes},
                )
                return {
                    "status": "skipped",
                    "cached": False,
                    "skipped": True,
                    "skip_reason": "too_large",
                    "platform": platform_key,
                    "video_id": video_key,
                    "content_length": size_bytes,
                    "resolver": "yt-dlp",
                }
            _maybe_progress(55, "写入视频缓存")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(downloaded, cache_path)
        content_type = "video/mp4"
        _atomic_write_text(content_type_path, content_type)
        cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
        r2_result = _upload_to_r2_if_enabled(
            media_kind="video",
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url=page_url,
            platform=platform_key,
            external_id=video_key,
        )
        if r2_result.get("cache_url"):
            cache_url = str(r2_result["cache_url"])
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": page_url,
            "digest": digest,
            "cached_url": cache_url,
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "storage_backend": r2_result.get("storage_backend") or "local",
            "r2_key": r2_result.get("r2_key") or "",
            "updated_at": _utcnow(),
            "resolver": "yt-dlp",
        }
        _record_media_cache_asset(
            {
                "media_kind": "video",
                "platform": platform_key,
                "external_id": video_key,
                "source_url": page_url,
                "digest": digest,
                "checksum": _sha256_file(cache_path),
                "content_type": content_type,
                "size_bytes": cache_path.stat().st_size,
                "storage_backend": sidecar["storage_backend"],
                "local_path": str(cache_path),
                "r2_key": sidecar["r2_key"],
                "cache_url": cache_url,
                "status": "cached",
                "metadata": {"gc": gc_result, "resolver": "yt-dlp", "r2_status": r2_result.get("r2_status"), "r2_error": r2_result.get("r2_error")},
            }
        )
        _maybe_progress(80, "写入视频 sidecar")
        _atomic_write_json(sidecar_path, sidecar)
        return {"status": "cached", "cached": True, **sidecar, "gc": gc_result}
    except VideoCacheCancelled:
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        raise
    except Exception as exc:
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        error = str(exc)[:300]
        no_video_source = "no video stream" in error.lower() or "no video formats found" in error.lower()
        if no_video_source:
            _video_item_failure_sidecar(
                platform_key=platform_key,
                video_key=video_key,
                source_url=page_url,
                status="failed",
                reason="yt_dlp_no_video_stream",
                error=error,
                resolver="yt-dlp",
                retryable=False,
            )
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": exc.__class__.__name__, "resolver": "yt-dlp", "error": error}

