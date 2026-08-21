"""Small local cache for official-channel platform images."""
from __future__ import annotations

import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.media.cache_core import (
    CACHE_DIR,
    IMAGE_KEYS,
    ITEM_VIDEO_CACHE_PLATFORMS,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    PUBLIC_IMAGE_CACHE_PREFIX,
    PUBLIC_VIDEO_CACHE_PREFIX,
    VIDEO_CACHE_CHUNK_BYTES,
    VIDEO_CACHE_DIR,
    VIDEO_CACHE_GC_RESERVE_BYTES,
    VideoCacheCancelled,
    _atomic_write_json,
    _atomic_write_text,
    _cache_paths,
    _cached_asset_url_by_digest,
    _cached_asset_url_for_item,
    _fresh_presigned_asset_url,
    _head_content_length,
    _normalize_image_url,
    _normalize_video_url,
    _public_video_page_url,
    _r2_public_url,
    _read_json_file,
    _record_media_cache_asset,
    _sha256_file,
    _safe_public_asset_url,
    _text,
    _upload_to_r2_if_enabled,
    _utcnow,
    _video_cache_paths,
    _video_item_failure_sidecar,
    _video_item_sidecar_path,
    _video_max_file_bytes,
    run_video_cache_gc,
    video_cache_item_state,
)
from app.domains.media.cache_migration import migrate_local_video_cache_to_r2
from app.domains.media.cache_ytdlp import _cache_video_for_item_via_ytdlp

logger = get_logger(__name__)

# 失败占位防线(2026-07-12):image-proxy 曾把上游失败时的 1x1 透明 SVG 写进磁盘缓存,
# 之后 cached_image_url() 把这份"失败占位"当"已缓存真图"上报(media_status=cached),
# 前端拿到 200 的透明 SVG 渲染成一片空白渐变。读侧一律把"极小的 SVG 缓存"视为无效:
# 真实平台缩略图不可能是几百字节的 SVG。
_FAILURE_PLACEHOLDER_MAX_BYTES = 512


def is_failure_placeholder_cache(cache_path: Path, content_type_path: Path) -> bool:
    """True when the cached image entry is a tiny SVG failure placeholder, not a real image."""
    try:
        if not cache_path.exists() or cache_path.stat().st_size > _FAILURE_PLACEHOLDER_MAX_BYTES:
            return False
        if content_type_path.exists():
            content_type = content_type_path.read_text().strip().lower()
            if content_type and not content_type.startswith("image/svg"):
                return False
        return cache_path.read_bytes().lstrip()[:4] == b"<svg"
    except OSError:
        return False


def cached_image_url(raw_url: Any) -> str:
    normalized = _normalize_image_url(raw_url)
    if not normalized:
        return ""
    digest, cache_path, content_type_path = _cache_paths(normalized[0])
    if cache_path.exists() and content_type_path.exists() and not is_failure_placeholder_cache(cache_path, content_type_path):
        return f"{PUBLIC_IMAGE_CACHE_PREFIX}/{digest}"
    return ""


def cached_video_url(raw_url: Any) -> str:
    normalized = _normalize_video_url(raw_url)
    if not normalized:
        return ""
    digest, cache_path, content_type_path = _video_cache_paths(normalized[0])
    if cache_path.exists() and content_type_path.exists():
        return f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    r2_url = _cached_asset_url_by_digest("video", digest)
    if r2_url:
        return r2_url
    return ""


def cache_image(raw_url: Any, *, timeout: int = 12) -> dict[str, Any]:
    normalized = _normalize_image_url(raw_url)
    if not normalized:
        return {"status": "skipped", "reason": "not_allowlisted"}
    url, host = normalized
    digest, cache_path, content_type_path = _cache_paths(url)
    if cache_path.exists() and content_type_path.exists():
        # 失败占位不算命中:重抓真图,成功后覆盖毒缓存。
        if not is_failure_placeholder_cache(cache_path, content_type_path):
            return {"status": "cached", "url": f"{PUBLIC_IMAGE_CACHE_PREFIX}/{digest}"}

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": f"https://{host.split('.', 1)[-1]}/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - host allowlist above.
            content_type = str(response.headers.get("content-type") or "image/jpeg").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                return {"status": "failed", "reason": "not_image"}
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return {"status": "failed", "reason": "too_large"}
                chunks.append(chunk)
    except Exception as exc:
        return {"status": "failed", "reason": exc.__class__.__name__}

    cache_path.write_bytes(b"".join(chunks))
    content_type_path.write_text(content_type)
    return {"status": "cached", "url": f"{PUBLIC_IMAGE_CACHE_PREFIX}/{digest}"}


def cache_video(raw_url: Any, *, timeout: int = 12, max_bytes: int | None = None) -> dict[str, Any]:
    normalized = _normalize_video_url(raw_url)
    if not normalized:
        return {"status": "skipped", "reason": "not_allowlisted"}
    url, host = normalized
    digest, cache_path, content_type_path = _video_cache_paths(url)
    if cache_path.exists() and content_type_path.exists():
        return {"status": "cached", "url": f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"}

    byte_limit = int(max_bytes or MAX_VIDEO_BYTES)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "video/mp4,video/*,*/*;q=0.8",
            "Referer": f"https://{host.split('.', 1)[-1]}/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - host allowlist above.
            content_type = str(response.headers.get("content-type") or "video/mp4").split(";", 1)[0].strip().lower()
            if not content_type.startswith("video/") and content_type != "application/octet-stream":
                return {"status": "failed", "reason": "not_video"}
            content_length = int(str(response.headers.get("content-length") or "0") or 0)
            if content_length and content_length > byte_limit:
                return {"status": "failed", "reason": "too_large"}
            tmp_path = cache_path.with_suffix(".tmp")
            total = 0
            with tmp_path.open("wb") as handle:
                while True:
                    chunk = response.read(512 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > byte_limit:
                        tmp_path.unlink(missing_ok=True)
                        return {"status": "failed", "reason": "too_large"}
                    handle.write(chunk)
    except Exception as exc:
        return {"status": "failed", "reason": exc.__class__.__name__}

    tmp_path.replace(cache_path)
    content_type = content_type if content_type.startswith("video/") else "video/mp4"
    content_type_path.write_text(content_type)
    cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    r2_result = _upload_to_r2_if_enabled(
        media_kind="video",
        digest=digest,
        cache_path=cache_path,
        content_type=content_type,
        source_url=url,
    )
    if r2_result.get("cache_url"):
        cache_url = str(r2_result["cache_url"])
    _record_media_cache_asset(
        {
            "media_kind": "video",
            "source_url": url,
            "digest": digest,
            "checksum": _sha256_file(cache_path),
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "storage_backend": r2_result.get("storage_backend") or "local",
            "local_path": str(cache_path),
            "r2_key": r2_result.get("r2_key") or "",
            "cache_url": cache_url,
            "status": "cached",
            "metadata": {"r2_status": r2_result.get("r2_status"), "r2_error": r2_result.get("r2_error")},
        }
    )
    return {"status": "cached", "url": cache_url, "r2_key": r2_result.get("r2_key") or "", "storage_backend": r2_result.get("storage_backend") or "local"}


def cached_image_file(digest: str) -> tuple[Path, str] | None:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
        return None
    cache_path = CACHE_DIR / digest.lower()
    content_type_path = CACHE_DIR / f"{digest.lower()}.content-type"
    if not cache_path.exists() or not content_type_path.exists():
        return None
    if is_failure_placeholder_cache(cache_path, content_type_path):
        # 毒缓存(历史失败占位)按 miss 处理 → 端点 404 → 前端走 onError 诚实占位,不再展示透明假图。
        return None
    return cache_path, content_type_path.read_text().strip() or "image/jpeg"


def cached_video_file(digest: str) -> tuple[Path, str] | None:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
        return None
    cache_path = VIDEO_CACHE_DIR / digest.lower()
    content_type_path = VIDEO_CACHE_DIR / f"{digest.lower()}.content-type"
    if not cache_path.exists() or not content_type_path.exists():
        return None
    return cache_path, content_type_path.read_text().strip() or "video/mp4"


def cached_video_redirect_url(digest: str) -> str:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
        return ""
    return _cached_asset_url_by_digest("video", digest.lower())


def cached_video_url_for_item(
    platform: str,
    video_id: str,
    *,
    allow_db_fallback: bool = True,
) -> str | None:
    platform_key = str(platform or "").strip().lower()
    video_key = str(video_id or "").strip()
    if platform_key not in ITEM_VIDEO_CACHE_PLATFORMS or not video_key or len(video_key) > 240:
        return None
    sidecar_path = _video_item_sidecar_path(platform_key, video_key)
    if not sidecar_path.exists():
        return (_cached_asset_url_for_item(platform_key, video_key) or None) if allow_db_fallback else None
    sidecar = _read_json_file(sidecar_path)
    if not sidecar:
        return (_cached_asset_url_for_item(platform_key, video_key) or None) if allow_db_fallback else None
    digest = _text(sidecar.get("digest")).lower()
    valid_digest = len(digest) == 64 and not any(ch not in "0123456789abcdef" for ch in digest)
    if valid_digest and (VIDEO_CACHE_DIR / digest).is_file():
        return f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    if _text(sidecar.get("storage_backend")).lower() == "r2":
        # Sidecars are persistent. Never replay a possibly expired presigned
        # URL; a miss here falls through to DB/r2_key for a fresh presign.
        sidecar_url = _safe_public_asset_url(sidecar.get("cached_url") or sidecar.get("cache_url"))
        if sidecar_url and not sidecar_url.startswith("/"):
            return sidecar_url
        if valid_digest and allow_db_fallback:
            r2_url = _cached_asset_url_by_digest("video", digest)
            if r2_url:
                return r2_url
        public_url = _safe_public_asset_url(_r2_public_url(_text(sidecar.get("r2_key"))))
        if public_url:
            return public_url
        fresh_url = _fresh_presigned_asset_url(
            _text(sidecar.get("r2_key")),
            stage="sidecar_presign",
        )
        if fresh_url:
            return fresh_url
    return (_cached_asset_url_for_item(platform_key, video_key) or None) if allow_db_fallback else None


def cache_video_for_item(
    platform: str,
    video_id: str,
    url: Any,
    force_refresh: bool = False,
    *,
    timeout: int = 30,
    progress_callback: Any | None = None,
    cancel_check: Any | None = None,
) -> dict[str, Any]:
    platform_key = str(platform or "").strip().lower()
    video_key = str(video_id or "").strip()
    if not platform_key or not video_key:
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "platform_video_id_required"}
    if platform_key == "youtube":
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "youtube_embed_ok", "platform": platform_key, "video_id": video_key}
    if platform_key not in ITEM_VIDEO_CACHE_PLATFORMS:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "platform_not_supported", "platform": platform_key, "video_id": video_key}

    def _maybe_cancel() -> None:
        if cancel_check is not None and cancel_check():
            raise VideoCacheCancelled("video cache cancelled")

    def _maybe_progress(pct: int, text: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0, min(100, int(pct))), text)

    _maybe_cancel()
    existing = cached_video_url_for_item(platform_key, video_key)
    sidecar_path = _video_item_sidecar_path(platform_key, video_key)
    if existing and not force_refresh:
        sidecar = _read_json_file(sidecar_path) or {}
        return {
            "status": "cached",
            "cached": True,
            "platform": platform_key,
            "video_id": video_key,
            "cached_url": existing,
            "size_bytes": int(sidecar.get("size_bytes") or 0),
            "digest": _text(sidecar.get("digest")),
            "storage_backend": _text(sidecar.get("storage_backend")) or "local",
            "r2_key": _text(sidecar.get("r2_key")),
        }
    if not force_refresh:
        previous_state = video_cache_item_state(platform_key, video_key)
        if previous_state.get("blocked"):
            return {
                "status": "skipped",
                "cached": False,
                "skipped": True,
                "platform": platform_key,
                "video_id": video_key,
                "skip_reason": previous_state.get("skip_reason") or "recent_failed_source",
                "reason": previous_state.get("reason") or "recent_failed_source",
                "error": previous_state.get("error") or "",
                "resolver": previous_state.get("resolver") or "",
                "retry_after_seconds": previous_state.get("retry_after_seconds") or 0,
            }

    normalized = _normalize_video_url(url)
    page_url = _public_video_page_url(url, platform_key) if not normalized else ""
    if page_url:
        ytdlp_result = _cache_video_for_item_via_ytdlp(
            platform_key=platform_key,
            video_key=video_key,
            page_url=page_url,
            force_refresh=force_refresh,
            timeout=timeout,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        return ytdlp_result
    if not normalized:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "not_allowlisted", "platform": platform_key, "video_id": video_key}
    normalized_url, host = normalized
    digest, cache_path, content_type_path = _video_cache_paths(normalized_url)
    max_file_bytes = _video_max_file_bytes()
    timeout = max(1, int(timeout or 30))
    _maybe_progress(10, "视频缓存预检查")

    content_length, head_content_type = _head_content_length(normalized_url, host, timeout=timeout)
    if content_length and content_length > max_file_bytes:
        _video_item_failure_sidecar(
            platform_key=platform_key,
            video_key=video_key,
            source_url=normalized_url,
            status="skipped",
            reason="too_large",
            retryable=False,
            metadata={"content_length": content_length, "max_file_bytes": max_file_bytes},
        )
        return {
            "status": "skipped",
            "cached": False,
            "skipped": True,
            "skip_reason": "too_large",
            "platform": platform_key,
            "video_id": video_key,
            "content_length": content_length,
        }

    target_free = max(VIDEO_CACHE_GC_RESERVE_BYTES, content_length or max_file_bytes)
    gc_result = run_video_cache_gc(target_free_bytes=target_free)
    if gc_result.get("free_bytes", 0) < (content_length or min(max_file_bytes, VIDEO_CACHE_GC_RESERVE_BYTES)):
        return {
            "status": "skipped",
            "cached": False,
            "skipped": True,
            "skip_reason": "global_cache_full",
            "platform": platform_key,
            "video_id": video_key,
            "gc": gc_result,
        }

    if cache_path.exists() and content_type_path.exists() and not force_refresh:
        content_type = content_type_path.read_text(encoding="utf-8").strip() or "video/mp4"
        size_bytes = cache_path.stat().st_size
        cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
        r2_result = _upload_to_r2_if_enabled(
            media_kind="video",
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url=normalized_url,
            platform=platform_key,
            external_id=video_key,
        )
        if r2_result.get("cache_url"):
            cache_url = str(r2_result["cache_url"])
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": normalized_url,
            "digest": digest,
            "cached_url": cache_url,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage_backend": r2_result.get("storage_backend") or "local",
            "r2_key": r2_result.get("r2_key") or "",
            "updated_at": _utcnow(),
        }
        _record_media_cache_asset(
            {
                "media_kind": "video",
                "platform": platform_key,
                "external_id": video_key,
                "source_url": normalized_url,
                "digest": digest,
                "checksum": _sha256_file(cache_path),
                "content_type": content_type,
                "size_bytes": size_bytes,
                "storage_backend": sidecar["storage_backend"],
                "local_path": str(cache_path),
                "r2_key": sidecar["r2_key"],
                "cache_url": cache_url,
                "status": "cached",
                "metadata": {"gc": gc_result, "r2_status": r2_result.get("r2_status"), "r2_error": r2_result.get("r2_error")},
            }
        )
        _atomic_write_json(sidecar_path, sidecar)
        return {"status": "cached", "cached": True, **sidecar, "gc": gc_result}

    request = urllib.request.Request(
        normalized_url,
        headers={
            "Accept": "video/mp4,video/*,*/*;q=0.8",
            "Referer": f"https://{host.split('.', 1)[-1]}/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        },
    )
    tmp_path = cache_path.with_suffix(".part")
    try:
        _maybe_cancel()
        _maybe_progress(30, "下载视频缓存")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - host allowlist above.
            content_type = str(response.headers.get("content-type") or head_content_type or "video/mp4").split(";", 1)[0].strip().lower()
            if not content_type.startswith("video/") and content_type != "application/octet-stream":
                _video_item_failure_sidecar(
                    platform_key=platform_key,
                    video_key=video_key,
                    source_url=normalized_url,
                    status="failed",
                    reason="not_video",
                    retryable=False,
                    metadata={"content_type": content_type},
                )
                return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "not_video"}
            response_length = int(str(response.headers.get("content-length") or "0") or 0)
            if response_length and response_length > max_file_bytes:
                _video_item_failure_sidecar(
                    platform_key=platform_key,
                    video_key=video_key,
                    source_url=normalized_url,
                    status="skipped",
                    reason="too_large",
                    retryable=False,
                    metadata={"content_length": response_length, "max_file_bytes": max_file_bytes},
                )
                return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "too_large", "platform": platform_key, "video_id": video_key, "content_length": response_length}
            total = 0
            with tmp_path.open("wb") as handle:
                while True:
                    _maybe_cancel()
                    chunk = response.read(VIDEO_CACHE_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_file_bytes:
                        tmp_path.unlink(missing_ok=True)
                        _video_item_failure_sidecar(
                            platform_key=platform_key,
                            video_key=video_key,
                            source_url=normalized_url,
                            status="skipped",
                            reason="too_large",
                            retryable=False,
                            metadata={"content_length": total, "max_file_bytes": max_file_bytes},
                        )
                        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "too_large", "platform": platform_key, "video_id": video_key, "content_length": total}
                    handle.write(chunk)
        _maybe_cancel()
        tmp_path.replace(cache_path)
        content_type = content_type if content_type.startswith("video/") else "video/mp4"
        _atomic_write_text(content_type_path, content_type)
        cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
        r2_result = _upload_to_r2_if_enabled(
            media_kind="video",
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url=normalized_url,
            platform=platform_key,
            external_id=video_key,
        )
        if r2_result.get("cache_url"):
            cache_url = str(r2_result["cache_url"])
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": normalized_url,
            "digest": digest,
            "cached_url": cache_url,
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "storage_backend": r2_result.get("storage_backend") or "local",
            "r2_key": r2_result.get("r2_key") or "",
            "updated_at": _utcnow(),
        }
        _record_media_cache_asset(
            {
                "media_kind": "video",
                "platform": platform_key,
                "external_id": video_key,
                "source_url": normalized_url,
                "digest": digest,
                "checksum": _sha256_file(cache_path),
                "content_type": content_type,
                "size_bytes": cache_path.stat().st_size,
                "storage_backend": sidecar["storage_backend"],
                "local_path": str(cache_path),
                "r2_key": sidecar["r2_key"],
                "cache_url": cache_url,
                "status": "cached",
                "metadata": {"gc": gc_result, "r2_status": r2_result.get("r2_status"), "r2_error": r2_result.get("r2_error")},
            }
        )
        _maybe_progress(80, "写入视频 sidecar")
        _atomic_write_json(sidecar_path, sidecar)
        return {"status": "cached", "cached": True, **sidecar, "gc": gc_result}
    except VideoCacheCancelled:
        tmp_path.unlink(missing_ok=True)
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": exc.__class__.__name__, "error": str(exc)[:300]}


def cache_local_video_file(
    platform: str,
    video_id: str,
    local_path: str,
    *,
    source_url: str = "",
    content_type: str = "video/mp4",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """C3:把 worker 已下载到本地的视频文件登记进视频缓存(复制进 cache 目录 + 传 R2 + 写 sidecar),
    令前端 cached_video_url_for_item(platform, video_id) 取到可播 URL。

    与 cache_video_for_item 共享同一 sidecar 索引键(platform, video_id),但**不二次 HTTP 下载**:
    final_v1 已把视频下到 tmpdir,直接复用那份字节,保证 R2 里就是 Gemini 分析过的同一视频
    (URL 重下走 yt-dlp 对 tiktok/ins 反而易失败——正是当初改用 Apify 直链的原因)。
    """
    platform_key = str(platform or "").strip().lower()
    video_key = str(video_id or "").strip()
    if not platform_key or not video_key:
        return {"status": "failed", "cached": False, "reason": "platform_video_id_required"}
    if platform_key == "youtube":
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "youtube_embed_ok"}
    if platform_key not in ITEM_VIDEO_CACHE_PLATFORMS:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "platform_not_supported"}
    src = Path(str(local_path or ""))
    if not src.exists() or not src.is_file() or src.stat().st_size <= 0:
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "local_file_missing"}

    existing = cached_video_url_for_item(platform_key, video_key)
    if existing and not force_refresh:
        return {"status": "cached", "cached": True, "platform": platform_key, "video_id": video_key, "cached_url": existing}

    size_bytes = int(src.stat().st_size)
    max_file_bytes = _video_max_file_bytes()
    if size_bytes > max_file_bytes:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "too_large", "platform": platform_key, "video_id": video_key, "size_bytes": size_bytes}

    # 缓存键稳定化:优先用 source_url 的 sha256(与 cache_video_for_item 的 HTTP 路径一致),
    # 无 source_url 时退回 平台+video_id+文件内容 摘要,确保同一视频复跑命中同一 cache_path。
    digest_seed = str(source_url or "").strip() or f"local:{platform_key}:{video_key}:{_sha256_file(src)}"
    digest, cache_path, content_type_path = _video_cache_paths(digest_seed)

    gc_result = run_video_cache_gc(target_free_bytes=max(VIDEO_CACHE_GC_RESERVE_BYTES, size_bytes))
    if gc_result.get("free_bytes", 0) < size_bytes:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "global_cache_full", "platform": platform_key, "video_id": video_key, "gc": gc_result}

    ctype = str(content_type or "video/mp4").split(";", 1)[0].strip().lower() or "video/mp4"
    try:
        shutil.copyfile(src, cache_path)
        content_type_path.write_text(ctype, encoding="utf-8")
    except Exception as exc:
        cache_path.unlink(missing_ok=True)
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": exc.__class__.__name__, "error": str(exc)[:300]}

    cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    r2_result = _upload_to_r2_if_enabled(
        media_kind="video",
        digest=digest,
        cache_path=cache_path,
        content_type=ctype,
        source_url=digest_seed,
        platform=platform_key,
        external_id=video_key,
    )
    if r2_result.get("cache_url"):
        cache_url = str(r2_result["cache_url"])
    sidecar = {
        "platform": platform_key,
        "video_id": video_key,
        "source_url": str(source_url or ""),
        "digest": digest,
        "cached_url": cache_url,
        "content_type": ctype,
        "size_bytes": size_bytes,
        "storage_backend": r2_result.get("storage_backend") or "local",
        "r2_key": r2_result.get("r2_key") or "",
        "updated_at": _utcnow(),
    }
    _record_media_cache_asset(
        {
            "media_kind": "video",
            "platform": platform_key,
            "external_id": video_key,
            "source_url": digest_seed,
            "digest": digest,
            "checksum": _sha256_file(cache_path),
            "content_type": ctype,
            "size_bytes": size_bytes,
            "storage_backend": sidecar["storage_backend"],
            "local_path": str(cache_path),
            "r2_key": sidecar["r2_key"],
            "cache_url": cache_url,
            "status": "cached",
            "metadata": {"source": "final_v1_worker_local", "gc": gc_result, "r2_status": r2_result.get("r2_status"), "r2_error": r2_result.get("r2_error")},
        }
    )
    _atomic_write_json(_video_item_sidecar_path(platform_key, video_key), sidecar)
    return {"status": "cached", "cached": True, **sidecar}


def _collect_image_urls(value: Any, *, key_hint: str = "", depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if isinstance(value, str):
        if key_hint in IMAGE_KEYS or _normalize_image_url(value):
            return [value]
        return []
    if isinstance(value, list):
        urls: list[str] = []
        for item in value[:500]:
            urls.extend(_collect_image_urls(item, depth=depth + 1))
        return urls
    if isinstance(value, dict):
        urls = []
        items = sorted(value.items(), key=lambda pair: 0 if str(pair[0]) in IMAGE_KEYS else 1)
        for key, item in items:
            urls.extend(_collect_image_urls(item, key_hint=str(key), depth=depth + 1))
        return urls
    return []


def _package_raw_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    package_dir = Path(_text(raw_payload.get("package_dir"))).expanduser()
    raw_path = package_dir / "raw.json"
    if not raw_path.exists() or not raw_path.is_file():
        return {}
    try:
        parsed = json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("vkpi media package raw payload parse failed for %s: %s", raw_path, exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def prewarm_official_media_cache(metrics: dict[str, Any], raw_payload: dict[str, Any], *, max_images: int = 120, timeout: int = 8) -> dict[str, Any]:
    try:
        max_images = max(0, min(max_images, int(os.getenv("VKPI_MEDIA_CACHE_MAX_IMAGES", str(max_images)))))
    except (TypeError, ValueError):
        pass
    try:
        timeout = max(1, min(timeout, int(os.getenv("VKPI_MEDIA_CACHE_TIMEOUT", str(timeout)))))
    except (TypeError, ValueError):
        pass
    if max_images <= 0:
        return {"attempted": 0, "cached": 0, "failed": 0, "sample": []}
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    package_raw = _package_raw_payload(raw_payload)
    for raw_url in [metrics.get("avatar_url"), *_collect_image_urls(raw_payload), *_collect_image_urls(package_raw)]:
        normalized = _normalize_image_url(raw_url)
        if not normalized or normalized[0] in seen:
            continue
        seen.add(normalized[0])
        result = cache_image(normalized[0], timeout=timeout)
        results.append({"host": normalized[1], "status": result.get("status"), "reason": result.get("reason")})
        if len(results) >= max_images:
            break
    return {
        "attempted": len(results),
        "cached": sum(1 for item in results if item.get("status") == "cached"),
        "failed": sum(1 for item in results if item.get("status") == "failed"),
        "sample": results[:6],
    }
