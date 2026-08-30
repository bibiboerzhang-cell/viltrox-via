"""单条视频缓存装配层(2026-08-30 从 cache.cache_video_for_item 提出,行为不变)。

职责分段:
- item_keys / guard_payload / cancel_guard:入参归一 + 平台守卫 + 取消闸;
- existing_hit_payload / blocked_payload:命中与近期失败态的短路 payload;
- fetch_and_cache:HEAD 预检 → GC 腾挪 → 本地复用 → 下载(try/回滚边界逐字保持)。

协作符号(_read_json_file / _text / video_cache_item_state / _head_content_length /
_video_item_failure_sidecar / run_video_cache_gc / _upload_to_r2_if_enabled /
_record_media_cache_asset / _sha256_file / _utcnow / _atomic_write_* / _video_cache_paths /
_video_max_file_bytes)一律经门面 cache 在调用时解析——tests 对门面的 monkeypatch 原样生效。
红线:不碰 viltrox_fit_score / rule_v0;零静默异常(失败必落 sidecar 或诚实 payload)。
"""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Any

from app.domains.media.cache_core import (
    ITEM_VIDEO_CACHE_PLATFORMS,
    PUBLIC_VIDEO_CACHE_PREFIX,
    VIDEO_CACHE_CHUNK_BYTES,
    VIDEO_CACHE_GC_RESERVE_BYTES,
    VideoCacheCancelled,
)


def _c() -> Any:
    """调用时解析门面模块:门面上的 monkeypatch / 运行时替换一律生效。"""
    from app.domains.media import cache

    return cache


def item_keys(platform: str, video_id: str) -> tuple[str, str]:
    return str(platform or "").strip().lower(), str(video_id or "").strip()


def guard_payload(platform_key: str, video_key: str) -> dict[str, Any] | None:
    if not platform_key or not video_key:
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "platform_video_id_required"}
    if platform_key == "youtube":
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "youtube_embed_ok", "platform": platform_key, "video_id": video_key}
    if platform_key not in ITEM_VIDEO_CACHE_PLATFORMS:
        return {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "platform_not_supported", "platform": platform_key, "video_id": video_key}
    return None


def cancel_guard(cancel_check: Any | None) -> None:
    if cancel_check is not None and cancel_check():
        raise VideoCacheCancelled("video cache cancelled")


def _progress(progress_callback: Any | None, pct: int, text: str) -> None:
    if progress_callback is not None:
        progress_callback(max(0, min(100, int(pct))), text)


def existing_hit_payload(
    platform_key: str, video_key: str, existing: str, sidecar_path: Path
) -> dict[str, Any]:
    c = _c()
    sidecar = c._read_json_file(sidecar_path) or {}
    return {
        "status": "cached",
        "cached": True,
        "platform": platform_key,
        "video_id": video_key,
        "cached_url": existing,
        "size_bytes": int(sidecar.get("size_bytes") or 0),
        "digest": c._text(sidecar.get("digest")),
        "storage_backend": c._text(sidecar.get("storage_backend")) or "local",
        "r2_key": c._text(sidecar.get("r2_key")),
    }


def blocked_payload(platform_key: str, video_key: str) -> dict[str, Any] | None:
    previous_state = _c().video_cache_item_state(platform_key, video_key)
    if not previous_state.get("blocked"):
        return None
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


def _head_gate_payload(
    platform_key: str,
    video_key: str,
    normalized_url: str,
    host: str,
    *,
    timeout: int,
    max_file_bytes: int,
) -> tuple[int, str, dict[str, Any] | None]:
    c = _c()
    content_length, head_content_type = c._head_content_length(normalized_url, host, timeout=timeout)
    if content_length and content_length > max_file_bytes:
        c._video_item_failure_sidecar(
            platform_key=platform_key,
            video_key=video_key,
            source_url=normalized_url,
            status="skipped",
            reason="too_large",
            retryable=False,
            metadata={"content_length": content_length, "max_file_bytes": max_file_bytes},
        )
        return content_length, head_content_type, {
            "status": "skipped",
            "cached": False,
            "skipped": True,
            "skip_reason": "too_large",
            "platform": platform_key,
            "video_id": video_key,
            "content_length": content_length,
        }
    return content_length, head_content_type, None


def _gc_gate_payload(
    platform_key: str, video_key: str, *, content_length: int, max_file_bytes: int
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    c = _c()
    target_free = max(VIDEO_CACHE_GC_RESERVE_BYTES, content_length or max_file_bytes)
    gc_result = c.run_video_cache_gc(target_free_bytes=target_free)
    if gc_result.get("free_bytes", 0) < (content_length or min(max_file_bytes, VIDEO_CACHE_GC_RESERVE_BYTES)):
        return gc_result, {
            "status": "skipped",
            "cached": False,
            "skipped": True,
            "skip_reason": "global_cache_full",
            "platform": platform_key,
            "video_id": video_key,
            "gc": gc_result,
        }
    return gc_result, None


def _finalize(
    *,
    platform_key: str,
    video_key: str,
    normalized_url: str,
    digest: str,
    cache_path: Path,
    sidecar_path: Path,
    content_type: str,
    size_bytes: int,
    gc_result: dict[str, Any],
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """R2 上传 → sidecar 构造 → 资产台账 → sidecar 落盘(复用/下载两路共用,行为不变)。"""
    c = _c()
    cache_url = f"{PUBLIC_VIDEO_CACHE_PREFIX}/{digest}"
    r2_result = c._upload_to_r2_if_enabled(
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
        "updated_at": c._utcnow(),
    }
    c._record_media_cache_asset(
        {
            "media_kind": "video",
            "platform": platform_key,
            "external_id": video_key,
            "source_url": normalized_url,
            "digest": digest,
            "checksum": c._sha256_file(cache_path),
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
    _progress(progress_callback, 80, "写入视频 sidecar")
    c._atomic_write_json(sidecar_path, sidecar)
    return {"status": "cached", "cached": True, **sidecar, "gc": gc_result}


def _reuse_local(
    *,
    platform_key: str,
    video_key: str,
    normalized_url: str,
    digest: str,
    cache_path: Path,
    content_type_path: Path,
    sidecar_path: Path,
    gc_result: dict[str, Any],
) -> dict[str, Any]:
    content_type = content_type_path.read_text(encoding="utf-8").strip() or "video/mp4"
    return _finalize(
        platform_key=platform_key,
        video_key=video_key,
        normalized_url=normalized_url,
        digest=digest,
        cache_path=cache_path,
        sidecar_path=sidecar_path,
        content_type=content_type,
        size_bytes=cache_path.stat().st_size,
        gc_result=gc_result,
    )


def _response_gate(
    response: Any,
    head_content_type: str,
    *,
    platform_key: str,
    video_key: str,
    normalized_url: str,
    max_file_bytes: int,
) -> tuple[str, dict[str, Any] | None]:
    """响应头闸:(content_type, 拒绝 payload|None)。非视频/超限即落失败 sidecar。"""
    c = _c()
    content_type = str(response.headers.get("content-type") or head_content_type or "video/mp4").split(";", 1)[0].strip().lower()
    if not content_type.startswith("video/") and content_type != "application/octet-stream":
        c._video_item_failure_sidecar(
            platform_key=platform_key,
            video_key=video_key,
            source_url=normalized_url,
            status="failed",
            reason="not_video",
            retryable=False,
            metadata={"content_type": content_type},
        )
        return content_type, {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": "not_video"}
    response_length = int(str(response.headers.get("content-length") or "0") or 0)
    if response_length and response_length > max_file_bytes:
        c._video_item_failure_sidecar(
            platform_key=platform_key,
            video_key=video_key,
            source_url=normalized_url,
            status="skipped",
            reason="too_large",
            retryable=False,
            metadata={"content_length": response_length, "max_file_bytes": max_file_bytes},
        )
        return content_type, {"status": "skipped", "cached": False, "skipped": True, "skip_reason": "too_large", "platform": platform_key, "video_id": video_key, "content_length": response_length}
    return content_type, None


def _stream_to_tmp(
    response: Any,
    tmp_path: Path,
    *,
    platform_key: str,
    video_key: str,
    normalized_url: str,
    max_file_bytes: int,
    cancel_check: Any | None,
) -> dict[str, Any] | None:
    """流式写 .part;中途超限即清 tmp、落失败 sidecar 并回拒绝 payload。"""
    total = 0
    with tmp_path.open("wb") as handle:
        while True:
            cancel_guard(cancel_check)
            chunk = response.read(VIDEO_CACHE_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_file_bytes:
                tmp_path.unlink(missing_ok=True)
                _c()._video_item_failure_sidecar(
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
    return None


def _download(
    *,
    platform_key: str,
    video_key: str,
    normalized_url: str,
    host: str,
    digest: str,
    cache_path: Path,
    content_type_path: Path,
    sidecar_path: Path,
    head_content_type: str,
    max_file_bytes: int,
    gc_result: dict[str, Any],
    timeout: int,
    progress_callback: Any | None,
    cancel_check: Any | None,
) -> dict[str, Any]:
    """下载 + 回滚边界:try/except(取消清理后重抛;异常清理后回 failed)逐字保持。"""
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
        cancel_guard(cancel_check)
        _progress(progress_callback, 30, "下载视频缓存")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - host allowlist above.
            content_type, reject = _response_gate(
                response,
                head_content_type,
                platform_key=platform_key,
                video_key=video_key,
                normalized_url=normalized_url,
                max_file_bytes=max_file_bytes,
            )
            if reject is not None:
                return reject
            oversize = _stream_to_tmp(
                response,
                tmp_path,
                platform_key=platform_key,
                video_key=video_key,
                normalized_url=normalized_url,
                max_file_bytes=max_file_bytes,
                cancel_check=cancel_check,
            )
            if oversize is not None:
                return oversize
        cancel_guard(cancel_check)
        tmp_path.replace(cache_path)
        content_type = content_type if content_type.startswith("video/") else "video/mp4"
        _c()._atomic_write_text(content_type_path, content_type)
        return _finalize(
            platform_key=platform_key,
            video_key=video_key,
            normalized_url=normalized_url,
            digest=digest,
            cache_path=cache_path,
            sidecar_path=sidecar_path,
            content_type=content_type,
            size_bytes=cache_path.stat().st_size,
            gc_result=gc_result,
            progress_callback=progress_callback,
        )
    except VideoCacheCancelled:
        tmp_path.unlink(missing_ok=True)
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        sidecar_path.with_suffix(sidecar_path.suffix + ".tmp").unlink(missing_ok=True)
        return {"status": "failed", "cached": False, "platform": platform_key, "video_id": video_key, "reason": exc.__class__.__name__, "error": str(exc)[:300]}


def fetch_and_cache(
    *,
    platform_key: str,
    video_key: str,
    normalized: tuple[str, str],
    sidecar_path: Path,
    force_refresh: bool,
    timeout: int,
    progress_callback: Any | None,
    cancel_check: Any | None,
) -> dict[str, Any]:
    """HEAD 预检 → GC 腾挪 → 本地复用 → 下载(顺序与守卫行为不变)。"""
    c = _c()
    normalized_url, host = normalized
    digest, cache_path, content_type_path = c._video_cache_paths(normalized_url)
    max_file_bytes = c._video_max_file_bytes()
    timeout = max(1, int(timeout or 30))
    _progress(progress_callback, 10, "视频缓存预检查")

    content_length, head_content_type, too_large = _head_gate_payload(
        platform_key,
        video_key,
        normalized_url,
        host,
        timeout=timeout,
        max_file_bytes=max_file_bytes,
    )
    if too_large is not None:
        return too_large

    gc_result, cache_full = _gc_gate_payload(
        platform_key, video_key, content_length=content_length, max_file_bytes=max_file_bytes
    )
    if cache_full is not None:
        return cache_full

    if cache_path.exists() and content_type_path.exists() and not force_refresh:
        return _reuse_local(
            platform_key=platform_key,
            video_key=video_key,
            normalized_url=normalized_url,
            digest=digest,
            cache_path=cache_path,
            content_type_path=content_type_path,
            sidecar_path=sidecar_path,
            gc_result=gc_result,
        )
    return _download(
        platform_key=platform_key,
        video_key=video_key,
        normalized_url=normalized_url,
        host=host,
        digest=digest,
        cache_path=cache_path,
        content_type_path=content_type_path,
        sidecar_path=sidecar_path,
        head_content_type=head_content_type,
        max_file_bytes=max_file_bytes,
        gc_result=gc_result,
        timeout=timeout,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )
