"""Core helpers for V-KPI local/R2 media caching."""
from __future__ import annotations

import hashlib
import html
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import UPLOAD_DIR, VKPI_VIDEO_CACHE_MAX_FILE_MB, VKPI_VIDEO_CACHE_MAX_TOTAL_GB
from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

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
    # IG reel/p 页与 TikTok 同走 ytdlp(代理已配);此前仅放行 TikTok,
    # IG evidence 的 content_url 全是页 URL → 一律 not_allowlisted,"IG 视频进 R2"从未通过。
    if platform_key == "instagram" and host.endswith("instagram.com") and ("/reel/" in parsed.path or "/p/" in parsed.path):
        return url
    return ""




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


