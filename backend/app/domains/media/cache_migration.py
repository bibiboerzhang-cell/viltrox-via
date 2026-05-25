"""R2 migration helpers for V-KPI media cache assets."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domains.media.cache_core import (
    MEDIA_CACHE_STORAGE,
    VIDEO_CACHE_DIR,
    _atomic_write_json,
    _cached_asset_url_by_digest,
    _legacy_bare_cache_entries,
    _media_cache_r2_enabled,
    _read_json_file,
    _sidecar_entries,
    _text,
    _upload_to_r2_if_enabled,
    _utcnow,
)

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

