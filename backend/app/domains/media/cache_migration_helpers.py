"""Per-entry processing for local media-cache R2 migration."""
from __future__ import annotations

from typing import Any


def new_state() -> dict[str, Any]:
    return {
        "scanned": 0,
        "eligible": 0,
        "migrated": 0,
        "skipped": 0,
        "failed": 0,
        "legacy_scanned": 0,
        "legacy_eligible": 0,
        "legacy_migrated": 0,
        "sample": [],
    }


def _append_sample(state: dict[str, Any], item: dict[str, Any]) -> None:
    if len(state["sample"]) < 8:
        state["sample"].append(item)


def _process_sidecar_entry(
    entry: dict[str, Any],
    state: dict[str, Any],
    *,
    execute: bool,
    platform_filter: str,
    ops: dict[str, Any],
) -> None:
    sidecar_path = entry.get("sidecar_path")
    if not isinstance(sidecar_path, ops["Path"]):
        return
    sidecar = ops["_read_json_file"](sidecar_path) or {}
    platform_key = ops["_text"](sidecar.get("platform")).lower()
    if platform_filter and platform_key != platform_filter:
        return
    state["scanned"] += 1
    digest = ops["_text"](sidecar.get("digest"))
    cache_path = ops["VIDEO_CACHE_DIR"] / digest
    content_type_path = ops["VIDEO_CACHE_DIR"] / f"{digest}.content-type"
    if ops["_text"](sidecar.get("storage_backend")) == "r2" and ops["_text"](sidecar.get("r2_key")):
        state["skipped"] += 1
        return
    if not digest or not cache_path.exists() or not content_type_path.exists():
        state["skipped"] += 1
        _append_sample(
            state,
            {
                "status": "skipped",
                "reason": "missing_local_file",
                "platform": platform_key,
                "video_id": ops["_text"](sidecar.get("video_id")),
            },
        )
        return
    state["eligible"] += 1
    content_type = (
        content_type_path.read_text(encoding="utf-8").strip()
        or ops["_text"](sidecar.get("content_type"))
        or "video/mp4"
    )
    if not execute:
        _append_sample(
            state,
            {
                "status": "would_migrate",
                "platform": platform_key,
                "video_id": ops["_text"](sidecar.get("video_id")),
                "size_bytes": int(entry.get("size_bytes") or 0),
                "digest": digest,
            },
        )
        return
    result = ops["_upload_to_r2_if_enabled"](
        media_kind="video",
        digest=digest,
        cache_path=cache_path,
        content_type=content_type,
        source_url=ops["_text"](sidecar.get("source_url")),
        platform=platform_key,
        external_id=ops["_text"](sidecar.get("video_id")),
    )
    if result.get("storage_backend") == "r2":
        state["migrated"] += 1
        updated = dict(sidecar)
        updated["storage_backend"] = "r2"
        updated["r2_key"] = result.get("r2_key") or ""
        if result.get("cache_url"):
            updated["cached_url"] = result["cache_url"]
        updated["updated_at"] = ops["_utcnow"]()
        ops["_atomic_write_json"](sidecar_path, updated)
        _append_sample(
            state,
            {
                "status": "migrated",
                "platform": platform_key,
                "video_id": ops["_text"](sidecar.get("video_id")),
                "r2_key": result.get("r2_key"),
            },
        )
        return
    state["failed"] += 1
    _append_sample(
        state,
        {
            "status": "failed",
            "platform": platform_key,
            "video_id": ops["_text"](sidecar.get("video_id")),
            "reason": result.get("r2_error") or result.get("r2_status") or "unknown",
        },
    )


def process_sidecar_entries(
    entries: Any,
    state: dict[str, Any],
    *,
    safe_limit: int,
    execute: bool,
    platform_filter: str,
    ops: dict[str, Any],
) -> None:
    for entry in entries:
        if state["scanned"] >= safe_limit:
            break
        _process_sidecar_entry(
            entry,
            state,
            execute=execute,
            platform_filter=platform_filter,
            ops=ops,
        )


def _process_legacy_entry(
    entry: dict[str, Any],
    state: dict[str, Any],
    *,
    execute: bool,
    ops: dict[str, Any],
) -> None:
    cache_path = entry.get("cache_path")
    if not isinstance(cache_path, ops["Path"]):
        return
    state["scanned"] += 1
    state["legacy_scanned"] += 1
    media_kind = ops["_text"](entry.get("media_kind"))
    digest = ops["_text"](entry.get("digest")).lower()
    if media_kind not in {"image", "video"} or not digest:
        state["skipped"] += 1
        return
    if ops["_cached_asset_url_by_digest"](media_kind, digest):
        state["skipped"] += 1
        return
    state["eligible"] += 1
    state["legacy_eligible"] += 1
    content_type = ops["_text"](entry.get("content_type")) or "application/octet-stream"
    if not execute:
        _append_sample(
            state,
            {
                "status": "would_migrate_legacy",
                "media_kind": media_kind,
                "size_bytes": int(entry.get("size_bytes") or 0),
                "digest": digest,
            },
        )
        return
    result = ops["_upload_to_r2_if_enabled"](
        media_kind=media_kind,
        digest=digest,
        cache_path=cache_path,
        content_type=content_type,
        source_url="",
    )
    if result.get("storage_backend") == "r2":
        state["migrated"] += 1
        state["legacy_migrated"] += 1
        _append_sample(
            state,
            {
                "status": "migrated_legacy",
                "media_kind": media_kind,
                "digest": digest,
                "r2_key": result.get("r2_key"),
            },
        )
        return
    state["failed"] += 1
    _append_sample(
        state,
        {
            "status": "failed_legacy",
            "media_kind": media_kind,
            "digest": digest,
            "reason": result.get("r2_error") or result.get("r2_status") or "unknown",
        },
    )


def process_legacy_entries(
    entries: Any,
    state: dict[str, Any],
    *,
    safe_limit: int,
    execute: bool,
    platform_filter: str,
    ops: dict[str, Any],
) -> None:
    for entry in entries:
        if state["scanned"] >= safe_limit:
            break
        if platform_filter:
            continue
        _process_legacy_entry(entry, state, execute=execute, ops=ops)
