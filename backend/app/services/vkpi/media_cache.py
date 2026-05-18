"""Small local cache for official-channel platform images."""
from __future__ import annotations

import hashlib
import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import UPLOAD_DIR, VKPI_VIDEO_CACHE_MAX_FILE_MB, VKPI_VIDEO_CACHE_MAX_TOTAL_GB
from app.core.logging import get_logger


logger = get_logger(__name__)

CACHE_DIR = UPLOAD_DIR / "vkpi_media_cache"
VIDEO_CACHE_DIR = CACHE_DIR / "videos"
VIDEO_ITEM_CACHE_DIR = VIDEO_CACHE_DIR / "item"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = int(os.getenv("VKPI_VIDEO_CACHE_MAX_BYTES", str(64 * 1024 * 1024)))
VIDEO_CACHE_CHUNK_BYTES = 8 * 1024
VIDEO_CACHE_GC_RESERVE_BYTES = 200 * 1024 * 1024
PUBLIC_IMAGE_CACHE_PREFIX = "/api/vkpi-media/image-cache"
PUBLIC_VIDEO_CACHE_PREFIX = "/api/vkpi-media/video-cache"
ITEM_VIDEO_CACHE_PLATFORMS = {"instagram", "tiktok"}
ALLOWED_IMAGE_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".xx.fbcdn.net",
    ".ytimg.com",
    ".googleusercontent.com",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".byteoversea.com",
    ".apifyusercontent.com",
    ".redd.it",
    ".redditmedia.com",
    ".twimg.com",
)
ALLOWED_VIDEO_HOST_SUFFIXES = (
    ".cdninstagram.com",
    ".fbcdn.net",
    ".xx.fbcdn.net",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".byteoversea.com",
    ".akamaized.net",
    ".googlevideo.com",
    ".apifyusercontent.com",
    ".redd.it",
    ".redditmedia.com",
    ".twimg.com",
)
IMAGE_KEYS = {
    "avatar",
    "avatarMedium",
    "avatarThumb",
    "coverUrl",
    "coverPhotoUrl",
    "displayUrl",
    "dynamicCover",
    "image",
    "imageUrl",
    "latestPosts",
    "media",
    "originalCoverUrl",
    "picture",
    "photo_image",
    "profilePicUrl",
    "profilePicUrlHD",
    "profilePictureUrl",
    "thumbnail",
    "thumbnailImage",
    "thumbnailUrl",
    "url",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


class VideoCacheCancelled(Exception):
    """Raised when a video cache task is cancelled at a download boundary."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _video_max_file_bytes() -> int:
    value = os.getenv("VKPI_VIDEO_CACHE_MAX_FILE_MB", str(VKPI_VIDEO_CACHE_MAX_FILE_MB))
    try:
        mb = max(1.0, float(value))
    except (TypeError, ValueError):
        mb = float(VKPI_VIDEO_CACHE_MAX_FILE_MB)
    return int(mb * 1024 * 1024)


def _video_max_total_bytes() -> int:
    value = os.getenv("VKPI_VIDEO_CACHE_MAX_TOTAL_GB", str(VKPI_VIDEO_CACHE_MAX_TOTAL_GB))
    try:
        gb = max(0.01, float(value))
    except (TypeError, ValueError):
        gb = float(VKPI_VIDEO_CACHE_MAX_TOTAL_GB)
    return int(gb * 1024 * 1024 * 1024)


def _video_item_digest(platform: str, video_id: str) -> str:
    key = f"{str(platform or '').strip().lower()}:{str(video_id or '').strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _video_item_sidecar_path(platform: str, video_id: str) -> Path:
    VIDEO_ITEM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEO_ITEM_CACHE_DIR / f"{_video_item_digest(platform, video_id)}.json"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("vkpi video sidecar read failed: %s", exc)
        return None
    return parsed if isinstance(parsed, dict) else None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(value, encoding="utf-8")
    tmp_path.replace(path)


def _parse_ts(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _video_cache_total_bytes() -> int:
    if not VIDEO_CACHE_DIR.exists():
        return 0
    total = 0
    for path in VIDEO_CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith((".content-type", ".tmp", ".part")):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _head_content_length(url: str, host: str, *, timeout: int) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "Accept": "video/mp4,video/*,*/*;q=0.8",
            "Referer": f"https://{host.split('.', 1)[-1]}/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - host allowlist above.
            content_length = int(str(response.headers.get("content-length") or "0") or 0)
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            return content_length, content_type
    except Exception as exc:
        logger.info("vkpi video HEAD precheck skipped: %s", exc)
        return 0, ""


def _sidecar_entries() -> list[dict[str, Any]]:
    if not VIDEO_ITEM_CACHE_DIR.exists():
        return []
    entries: list[dict[str, Any]] = []
    for sidecar_path in VIDEO_ITEM_CACHE_DIR.glob("*.json"):
        sidecar = _read_json_file(sidecar_path)
        if not sidecar:
            continue
        digest = _text(sidecar.get("digest"))
        if not digest:
            continue
        cache_path = VIDEO_CACHE_DIR / digest
        try:
            size = cache_path.stat().st_size if cache_path.exists() else int(sidecar.get("size_bytes") or 0)
        except OSError:
            size = int(sidecar.get("size_bytes") or 0)
        entries.append(
            {
                "sidecar_path": sidecar_path,
                "cache_path": cache_path,
                "content_type_path": VIDEO_CACHE_DIR / f"{digest}.content-type",
                "updated_at": _parse_ts(sidecar.get("updated_at")),
                "size_bytes": max(0, int(size or 0)),
                "digest": digest,
            }
        )
    return sorted(entries, key=lambda item: item.get("updated_at") or 0)


def _delete_video_entry(entry: dict[str, Any]) -> int:
    freed = 0
    for key in ("cache_path", "content_type_path", "sidecar_path"):
        path = entry.get(key)
        if not isinstance(path, Path):
            continue
        try:
            if path.exists():
                freed += path.stat().st_size
                path.unlink()
        except OSError as exc:
            logger.warning("vkpi video cache gc failed to delete %s: %s", path, exc)
    return freed


def _normalize_image_url(raw_url: Any) -> tuple[str, str] | None:
    text = html.unescape(_text(raw_url))
    if not text.startswith(("http://", "https://")):
        return None
    parsed = urllib.parse.urlparse(text)
    host = parsed.hostname or ""
    if not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_IMAGE_HOST_SUFFIXES):
        return None
    return urllib.parse.urlunparse(parsed), host


def _normalize_video_url(raw_url: Any) -> tuple[str, str] | None:
    text = html.unescape(_text(raw_url))
    if not text.startswith(("http://", "https://")):
        return None
    parsed = urllib.parse.urlparse(text)
    host = parsed.hostname or ""
    if not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_VIDEO_HOST_SUFFIXES):
        return None
    return urllib.parse.urlunparse(parsed), host


def _cache_paths(normalized_url: str) -> tuple[str, Path, Path]:
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return digest, CACHE_DIR / digest, CACHE_DIR / f"{digest}.content-type"


def _video_cache_paths(normalized_url: str) -> tuple[str, Path, Path]:
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return digest, VIDEO_CACHE_DIR / digest, VIDEO_CACHE_DIR / f"{digest}.content-type"


def cached_image_url(raw_url: Any) -> str:
    normalized = _normalize_image_url(raw_url)
    if not normalized:
        return ""
    digest, cache_path, content_type_path = _cache_paths(normalized[0])
    if cache_path.exists() and content_type_path.exists():
        return f"{PUBLIC_IMAGE_CACHE_PREFIX}/{digest}"
    return ""


def cached_video_url(raw_url: Any) -> str:
    normalized = _normalize_video_url(raw_url)
    if not normalized:
        return ""
    digest, cache_path, content_type_path = _video_cache_paths(normalized[0])
    if cache_path.exists() and content_type_path.exists():
        return f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    return ""


def cache_image(raw_url: Any, *, timeout: int = 12) -> dict[str, Any]:
    normalized = _normalize_image_url(raw_url)
    if not normalized:
        return {"status": "skipped", "reason": "not_allowlisted"}
    url, host = normalized
    digest, cache_path, content_type_path = _cache_paths(url)
    if cache_path.exists() and content_type_path.exists():
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
    content_type_path.write_text(content_type if content_type.startswith("video/") else "video/mp4")
    return {"status": "cached", "url": f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"}


def cached_image_file(digest: str) -> tuple[Path, str] | None:
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
        return None
    cache_path = CACHE_DIR / digest.lower()
    content_type_path = CACHE_DIR / f"{digest.lower()}.content-type"
    if not cache_path.exists() or not content_type_path.exists():
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


def cached_video_url_for_item(platform: str, video_id: str) -> str | None:
    sidecar_path = _video_item_sidecar_path(platform, video_id)
    if not sidecar_path.exists():
        return None
    sidecar = _read_json_file(sidecar_path)
    if not sidecar:
        return None
    digest = _text(sidecar.get("digest"))
    cached_url = _text(sidecar.get("cached_url"))
    if not digest or not cached_url:
        return None
    if not (VIDEO_CACHE_DIR / digest).exists():
        return None
    return cached_url


def run_video_cache_gc(target_free_bytes: int | None = None) -> dict[str, Any]:
    VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    max_total_bytes = _video_max_total_bytes()
    target = max(0, int(target_free_bytes if target_free_bytes is not None else VIDEO_CACHE_GC_RESERVE_BYTES))
    removed = 0
    freed = 0
    total = _video_cache_total_bytes()
    entries = _sidecar_entries()
    while entries and max_total_bytes - total < target:
        entry = entries.pop(0)
        size = int(entry.get("size_bytes") or 0)
        freed_now = _delete_video_entry(entry)
        removed += 1
        freed += max(freed_now, size)
        total = max(0, total - max(freed_now, size))
    return {
        "removed": removed,
        "freed_bytes": freed,
        "remaining_bytes": _video_cache_total_bytes(),
        "free_bytes": max(0, max_total_bytes - _video_cache_total_bytes()),
        "target_free_bytes": target,
        "max_total_bytes": max_total_bytes,
    }


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
        }

    normalized = _normalize_video_url(url)
    if not normalized:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "not_allowlisted", "platform": platform_key, "video_id": video_key}
    normalized_url, host = normalized
    digest, cache_path, content_type_path = _video_cache_paths(normalized_url)
    max_file_bytes = _video_max_file_bytes()
    timeout = max(1, int(timeout or 30))
    _maybe_progress(10, "视频缓存预检查")

    content_length, head_content_type = _head_content_length(normalized_url, host, timeout=timeout)
    if content_length and content_length > max_file_bytes:
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
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": normalized_url,
            "digest": digest,
            "cached_url": f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}",
            "content_type": content_type,
            "size_bytes": size_bytes,
            "updated_at": _utcnow(),
        }
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
                return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "not_video"}
            response_length = int(str(response.headers.get("content-length") or "0") or 0)
            if response_length and response_length > max_file_bytes:
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
                        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "too_large", "platform": platform_key, "video_id": video_key, "content_length": total}
                    handle.write(chunk)
        _maybe_cancel()
        tmp_path.replace(cache_path)
        content_type = content_type if content_type.startswith("video/") else "video/mp4"
        _atomic_write_text(content_type_path, content_type)
        sidecar = {
            "platform": platform_key,
            "video_id": video_key,
            "source_url": normalized_url,
            "digest": digest,
            "cached_url": f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}",
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "updated_at": _utcnow(),
        }
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
    except Exception:
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
