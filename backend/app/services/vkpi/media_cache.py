"""Small local cache for official-channel platform images."""
from __future__ import annotations

import hashlib
import html
import json
import os
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import UPLOAD_DIR, VKPI_VIDEO_CACHE_MAX_FILE_MB, VKPI_VIDEO_CACHE_MAX_TOTAL_GB
from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.services.scraping.ytdlp import download_video_ytdlp


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
VIDEO_CACHE_FAILURE_RETRY_HOURS = 168
MEDIA_R2_PREFIX = os.getenv("VKPI_MEDIA_CACHE_R2_PREFIX", "vkpi/media-cache").strip().strip("/") or "vkpi/media-cache"
MEDIA_R2_PUBLIC_BASE_URL = (
    os.getenv("VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL")
    or os.getenv("R2_PUBLIC_BASE_URL")
    or ""
).strip().rstrip("/")
MEDIA_CACHE_STORAGE = os.getenv("VKPI_MEDIA_CACHE_STORAGE", "local").strip().lower() or "local"
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


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _media_cache_r2_enabled() -> bool:
    if MEDIA_CACHE_STORAGE not in {"r2", "hybrid", "cloud"}:
        return False
    required = ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
    return all(os.getenv(key, "").strip() for key in required)


def _r2_public_url(r2_key: str) -> str:
    key = _text(r2_key).lstrip("/")
    if not key or not MEDIA_R2_PUBLIC_BASE_URL:
        return ""
    return f"{MEDIA_R2_PUBLIC_BASE_URL}/{urllib.parse.quote(key, safe='/-_.~')}"


def _content_type_ext(content_type: str) -> str:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized == "video/webm":
        return ".webm"
    if normalized in {"video/quicktime", "video/mov"}:
        return ".mov"
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    if normalized == "image/gif":
        return ".gif"
    if normalized.startswith("image/"):
        return ".jpg"
    return ".mp4"


def _asset_uid(media_kind: str, platform: str, external_id: str, source_url: str, digest: str) -> str:
    seed = "|".join([media_kind, platform, external_id, source_url, digest])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _source_url_hash(source_url: str) -> str:
    return hashlib.sha256(_text(source_url).encode("utf-8")).hexdigest() if _text(source_url) else ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_MEDIA_CACHE_SCHEMA_READY = False


def ensure_vkpi_media_cache_schema() -> None:
    global _MEDIA_CACHE_SCHEMA_READY
    if _MEDIA_CACHE_SCHEMA_READY:
        return
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_media_cache_assets (
                id BIGSERIAL PRIMARY KEY,
                asset_uid TEXT NOT NULL UNIQUE,
                media_kind TEXT NOT NULL DEFAULT 'video',
                platform TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_url_hash TEXT NOT NULL DEFAULT '',
                digest TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT '',
                size_bytes BIGINT NOT NULL DEFAULT 0,
                storage_backend TEXT NOT NULL DEFAULT 'local',
                local_path TEXT NOT NULL DEFAULT '',
                r2_key TEXT NOT NULL DEFAULT '',
                cache_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'cached',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_media_cache_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_uid TEXT NOT NULL UNIQUE,
                media_kind TEXT NOT NULL DEFAULT 'video',
                platform TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_url_hash TEXT NOT NULL DEFAULT '',
                digest TEXT NOT NULL DEFAULT '',
                checksum TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT '',
                size_bytes INTEGER NOT NULL DEFAULT 0,
                storage_backend TEXT NOT NULL DEFAULT 'local',
                local_path TEXT NOT NULL DEFAULT '',
                r2_key TEXT NOT NULL DEFAULT '',
                cache_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'cached',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_digest ON vkpi_media_cache_assets (media_kind, digest)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_external ON vkpi_media_cache_assets (platform, external_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_r2 ON vkpi_media_cache_assets (storage_backend, r2_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_source_hash ON vkpi_media_cache_assets (source_url_hash)")
    conn.commit()
    _MEDIA_CACHE_SCHEMA_READY = True


def _record_media_cache_asset(payload: dict[str, Any]) -> None:
    try:
        ensure_vkpi_media_cache_schema()
        conn = get_conn()
        media_kind = _text(payload.get("media_kind")) or "video"
        platform = _text(payload.get("platform")).lower()
        external_id = _text(payload.get("external_id"))
        source_url = _text(payload.get("source_url"))
        digest = _text(payload.get("digest"))
        asset_uid = _text(payload.get("asset_uid")) or _asset_uid(media_kind, platform, external_id, source_url, digest)
        conn.execute(
            """
            INSERT INTO vkpi_media_cache_assets (
                asset_uid, media_kind, platform, external_id, source_url, source_url_hash,
                digest, checksum, content_type, size_bytes, storage_backend, local_path,
                r2_key, cache_url, status, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(asset_uid) DO UPDATE SET
                source_url=excluded.source_url,
                source_url_hash=excluded.source_url_hash,
                digest=excluded.digest,
                checksum=excluded.checksum,
                content_type=excluded.content_type,
                size_bytes=excluded.size_bytes,
                storage_backend=excluded.storage_backend,
                local_path=excluded.local_path,
                r2_key=excluded.r2_key,
                cache_url=excluded.cache_url,
                status=excluded.status,
                metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                asset_uid,
                media_kind,
                platform,
                external_id,
                source_url,
                _source_url_hash(source_url),
                digest,
                _text(payload.get("checksum")),
                _text(payload.get("content_type")),
                int(payload.get("size_bytes") or 0),
                _text(payload.get("storage_backend")) or "local",
                _text(payload.get("local_path")),
                _text(payload.get("r2_key")),
                _text(payload.get("cache_url")),
                _text(payload.get("status")) or "cached",
                _json(payload.get("metadata")),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("vkpi media cache asset record failed: %s", exc)


def _cached_asset_url_by_digest(media_kind: str, digest: str) -> str:
    digest = _text(digest).lower()
    if len(digest) != 64:
        return ""
    try:
        ensure_vkpi_media_cache_schema()
        row = get_conn().execute(
            """
            SELECT cache_url, r2_key
            FROM vkpi_media_cache_assets
            WHERE media_kind=? AND digest=? AND storage_backend='r2' AND status='cached'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (media_kind, digest),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    cache_url = _text(row["cache_url"])
    if cache_url and not cache_url.startswith("/api/"):
        return cache_url
    r2_key = _text(row["r2_key"])
    public_url = _r2_public_url(r2_key)
    if public_url:
        return public_url
    if r2_key and _media_cache_r2_enabled():
        try:
            from app.services.media.r2 import get_presigned_url

            return get_presigned_url(r2_key)
        except Exception as exc:
            logger.warning("vkpi media cache r2 presign failed: %s", exc)
    return ""


def _upload_to_r2_if_enabled(
    *,
    media_kind: str,
    digest: str,
    cache_path: Path,
    content_type: str,
    source_url: str,
    platform: str = "",
    external_id: str = "",
) -> dict[str, Any]:
    if not _media_cache_r2_enabled():
        return {}
    try:
        from app.services.media.r2 import upload_file

        r2_key = f"{MEDIA_R2_PREFIX}/{media_kind}s/{digest}{_content_type_ext(content_type)}"
        upload_file(str(cache_path), r2_key, content_type)
        cache_url = _r2_public_url(r2_key)
        payload = {
            "media_kind": media_kind,
            "platform": platform,
            "external_id": external_id,
            "source_url": source_url,
            "digest": digest,
            "checksum": _sha256_file(cache_path),
            "content_type": content_type,
            "size_bytes": cache_path.stat().st_size,
            "storage_backend": "r2",
            "local_path": str(cache_path),
            "r2_key": r2_key,
            "cache_url": cache_url,
            "status": "cached",
        }
        _record_media_cache_asset(payload)
        return {"storage_backend": "r2", "r2_key": r2_key, "cache_url": cache_url}
    except Exception as exc:
        logger.warning("vkpi media cache r2 upload failed: %s", exc)
        return {"r2_status": "failed", "r2_error": exc.__class__.__name__}


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


def _video_failure_retry_seconds() -> int:
    value = os.getenv("VKPI_VIDEO_CACHE_FAILURE_RETRY_HOURS", str(VIDEO_CACHE_FAILURE_RETRY_HOURS))
    try:
        hours = max(0.0, float(value))
    except (TypeError, ValueError):
        hours = float(VIDEO_CACHE_FAILURE_RETRY_HOURS)
    return int(hours * 60 * 60)


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


def _video_item_failure_sidecar(
    *,
    platform_key: str,
    video_key: str,
    source_url: str,
    status: str,
    reason: str,
    error: str = "",
    resolver: str = "",
    retryable: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "platform": platform_key,
        "video_id": video_key,
        "source_url": source_url,
        "status": status,
        "cached": False,
        "reason": reason,
        "error": error[:500],
        "resolver": resolver,
        "retryable": bool(retryable),
        "updated_at": _utcnow(),
    }
    if metadata:
        payload["metadata"] = metadata
    _atomic_write_json(_video_item_sidecar_path(platform_key, video_key), payload)
    return payload


def video_cache_item_state(platform: str, video_id: str) -> dict[str, Any]:
    sidecar_path = _video_item_sidecar_path(platform, video_id)
    if not sidecar_path.exists():
        return {}
    sidecar = _read_json_file(sidecar_path) or {}
    status = _text(sidecar.get("status"))
    if status not in {"failed", "skipped"}:
        return {}
    retryable = bool(sidecar.get("retryable", True))
    retry_seconds = _video_failure_retry_seconds()
    updated_ts = _parse_ts(sidecar.get("updated_at"))
    age_seconds = max(0, time.time() - updated_ts) if updated_ts else 0
    blocked = (not retryable) or (retry_seconds > 0 and updated_ts > 0 and age_seconds < retry_seconds)
    reason = _text(sidecar.get("reason")) or _text(sidecar.get("skip_reason"))
    return {
        "status": status,
        "cached": False,
        "skip_reason": "recent_failed_source" if blocked else "",
        "reason": reason,
        "error": _text(sidecar.get("error")),
        "resolver": _text(sidecar.get("resolver")),
        "retryable": retryable,
        "blocked": blocked,
        "age_seconds": int(age_seconds),
        "retry_after_seconds": max(0, retry_seconds - int(age_seconds)) if blocked and retry_seconds else 0,
        "updated_at": _text(sidecar.get("updated_at")),
    }


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


def _legacy_bare_cache_entries() -> list[dict[str, Any]]:
    """Return legacy root media-cache files that predate DB/sidecar tracking."""

    if not CACHE_DIR.exists():
        return []
    entries: list[dict[str, Any]] = []
    for cache_path in CACHE_DIR.iterdir():
        if not cache_path.is_file():
            continue
        digest = cache_path.name.lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            continue
        content_type_path = CACHE_DIR / f"{digest}.content-type"
        if not content_type_path.exists():
            continue
        content_type = content_type_path.read_text(encoding="utf-8", errors="ignore").strip().lower()
        if content_type.startswith("image/"):
            media_kind = "image"
        elif content_type.startswith("video/"):
            media_kind = "video"
        else:
            continue
        try:
            stat = cache_path.stat()
        except OSError:
            continue
        entries.append(
            {
                "cache_path": cache_path,
                "content_type_path": content_type_path,
                "content_type": content_type,
                "digest": digest,
                "media_kind": media_kind,
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
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


def _public_video_page_url(value: Any, platform_key: str) -> str:
    url = _text(value)
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if platform_key == "tiktok" and host.endswith("tiktok.com") and "/video/" in parsed.path:
        return url
    return ""


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
        sidecar_url = _text(sidecar.get("cache_url"))
        r2_url = sidecar_url if sidecar_url and not sidecar_url.startswith("/api/") else ""
        r2_url = r2_url or _cached_asset_url_by_digest("video", digest) or _r2_public_url(_text(sidecar.get("r2_key")))
        if _text(sidecar.get("storage_backend")) == "r2" and r2_url:
            return r2_url
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


def migrate_local_video_cache_to_r2(
    *,
    execute: bool = False,
    limit: int = 100,
    platform: str = "",
) -> dict[str, Any]:
    """Migrate existing item sidecar video cache files to R2 when explicitly enabled."""

    safe_limit = max(1, min(100000, int(limit or 100)))
    platform_filter = _text(platform).lower()
    entries = _sidecar_entries()
    scanned = 0
    eligible = 0
    migrated = 0
    skipped = 0
    failed = 0
    legacy_scanned = 0
    legacy_eligible = 0
    legacy_migrated = 0
    sample: list[dict[str, Any]] = []
    if execute and not _media_cache_r2_enabled():
        return {
            "execute": True,
            "status": "not_configured",
            "message": "R2 env is missing or VKPI_MEDIA_CACHE_STORAGE is not hybrid/r2.",
            "required_env": ["VKPI_MEDIA_CACHE_STORAGE", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"],
        }

    for entry in entries:
        if scanned >= safe_limit:
            break
        sidecar_path = entry.get("sidecar_path")
        if not isinstance(sidecar_path, Path):
            continue
        sidecar = _read_json_file(sidecar_path) or {}
        platform_key = _text(sidecar.get("platform")).lower()
        if platform_filter and platform_key != platform_filter:
            continue
        scanned += 1
        digest = _text(sidecar.get("digest"))
        cache_path = VIDEO_CACHE_DIR / digest
        content_type_path = VIDEO_CACHE_DIR / f"{digest}.content-type"
        if _text(sidecar.get("storage_backend")) == "r2" and _text(sidecar.get("r2_key")):
            skipped += 1
            continue
        if not digest or not cache_path.exists() or not content_type_path.exists():
            skipped += 1
            if len(sample) < 8:
                sample.append({"status": "skipped", "reason": "missing_local_file", "platform": platform_key, "video_id": _text(sidecar.get("video_id"))})
            continue
        eligible += 1
        content_type = content_type_path.read_text(encoding="utf-8").strip() or _text(sidecar.get("content_type")) or "video/mp4"
        if not execute:
            if len(sample) < 8:
                sample.append({
                    "status": "would_migrate",
                    "platform": platform_key,
                    "video_id": _text(sidecar.get("video_id")),
                    "size_bytes": int(entry.get("size_bytes") or 0),
                    "digest": digest,
                })
            continue
        result = _upload_to_r2_if_enabled(
            media_kind="video",
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url=_text(sidecar.get("source_url")),
            platform=platform_key,
            external_id=_text(sidecar.get("video_id")),
        )
        if result.get("storage_backend") == "r2":
            migrated += 1
            updated = dict(sidecar)
            updated["storage_backend"] = "r2"
            updated["r2_key"] = result.get("r2_key") or ""
            if result.get("cache_url"):
                updated["cached_url"] = result["cache_url"]
            updated["updated_at"] = _utcnow()
            _atomic_write_json(sidecar_path, updated)
            if len(sample) < 8:
                sample.append({"status": "migrated", "platform": platform_key, "video_id": _text(sidecar.get("video_id")), "r2_key": result.get("r2_key")})
        else:
            failed += 1
            if len(sample) < 8:
                sample.append({"status": "failed", "platform": platform_key, "video_id": _text(sidecar.get("video_id")), "reason": result.get("r2_error") or result.get("r2_status") or "unknown"})

    for entry in _legacy_bare_cache_entries():
        if scanned >= safe_limit:
            break
        if platform_filter:
            continue
        cache_path = entry.get("cache_path")
        if not isinstance(cache_path, Path):
            continue
        scanned += 1
        legacy_scanned += 1
        media_kind = _text(entry.get("media_kind"))
        digest = _text(entry.get("digest")).lower()
        if media_kind not in {"image", "video"} or not digest:
            skipped += 1
            continue
        if _cached_asset_url_by_digest(media_kind, digest):
            skipped += 1
            continue
        eligible += 1
        legacy_eligible += 1
        content_type = _text(entry.get("content_type")) or "application/octet-stream"
        if not execute:
            if len(sample) < 8:
                sample.append(
                    {
                        "status": "would_migrate_legacy",
                        "media_kind": media_kind,
                        "size_bytes": int(entry.get("size_bytes") or 0),
                        "digest": digest,
                    }
                )
            continue
        result = _upload_to_r2_if_enabled(
            media_kind=media_kind,
            digest=digest,
            cache_path=cache_path,
            content_type=content_type,
            source_url="",
        )
        if result.get("storage_backend") == "r2":
            migrated += 1
            legacy_migrated += 1
            if len(sample) < 8:
                sample.append({"status": "migrated_legacy", "media_kind": media_kind, "digest": digest, "r2_key": result.get("r2_key")})
        else:
            failed += 1
            if len(sample) < 8:
                sample.append({"status": "failed_legacy", "media_kind": media_kind, "digest": digest, "reason": result.get("r2_error") or result.get("r2_status") or "unknown"})

    return {
        "execute": bool(execute),
        "storage": MEDIA_CACHE_STORAGE,
        "r2_configured": _media_cache_r2_enabled(),
        "scanned": scanned,
        "eligible": eligible,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed,
        "legacy_scanned": legacy_scanned,
        "legacy_eligible": legacy_eligible,
        "legacy_migrated": legacy_migrated,
        "limit": safe_limit,
        "platform": platform_filter or "all",
        "sample": sample,
    }


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
