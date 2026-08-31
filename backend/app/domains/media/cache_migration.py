"""R2 migration helpers for V-KPI media cache assets."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domains.media import cache_migration_helpers
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
    state = cache_migration_helpers.new_state()
    if execute and not _media_cache_r2_enabled():
        return {
            "execute": True,
            "status": "not_configured",
            "message": "R2 env is missing or VKPI_MEDIA_CACHE_STORAGE is not hybrid/r2.",
            "required_env": ["VKPI_MEDIA_CACHE_STORAGE", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"],
        }

    cache_migration_helpers.process_sidecar_entries(
        entries,
        state,
        safe_limit=safe_limit,
        execute=execute,
        platform_filter=platform_filter,
        ops=globals(),
    )
    cache_migration_helpers.process_legacy_entries(
        _legacy_bare_cache_entries(),
        state,
        safe_limit=safe_limit,
        execute=execute,
        platform_filter=platform_filter,
        ops=globals(),
    )

    return {
        "execute": bool(execute),
        "storage": MEDIA_CACHE_STORAGE,
        "r2_configured": _media_cache_r2_enabled(),
        "scanned": state["scanned"],
        "eligible": state["eligible"],
        "migrated": state["migrated"],
        "skipped": state["skipped"],
        "failed": state["failed"],
        "legacy_scanned": state["legacy_scanned"],
        "legacy_eligible": state["legacy_eligible"],
        "legacy_migrated": state["legacy_migrated"],
        "limit": safe_limit,
        "platform": platform_filter or "all",
        "sample": state["sample"],
    }
