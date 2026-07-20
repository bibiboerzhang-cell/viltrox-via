"""Pure serialization/normalization helpers for KOL search sessions.

Behavior-preserving move out of ``search_sessions.py``. These are all pure
functions (no DB access) covering JSON (de)serialization, value coercion,
status/query-type normalization, row→dict mappers, item counting, and flow
compaction. Re-exported by ``search_sessions`` to keep all call sites stable.

This module never writes ``viltrox_fit_score`` (no fit writes whatsoever).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.coerce import _loads, _text
from app.domains.kol.search_sessions_schema import (
    ITEM_STATUSES,
    SESSION_QUERY_TYPES,
    SESSION_STATUSES,
)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_jsonable(value or {}), ensure_ascii=False, default=str)


def _staff_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int_or_none(staff.get(key))
        if parsed:
            return parsed
    return None


def _normalize_query_type(value: Any) -> str:
    text = _text(value).lower()
    return text if text in SESSION_QUERY_TYPES else "unknown"


def _normalize_status(value: Any, *, item: bool = False) -> str:
    text = _text(value).lower()
    allowed = ITEM_STATUSES if item else SESSION_STATUSES
    if text in allowed:
        return text
    if text in {"dry_run_ready", "ready_to_execute", "resolved", "needs_video_resolution"}:
        return "identified" if item else "ready"
    if text in {"done", "completed"}:
        return "ready"
    if text in {"would_create", "would_reuse", "created", "reused"}:
        return "matched" if item else "ready"
    if text in {"error", "crawl_failed", "profile_crawl_failed", "creator_unresolved"}:
        return "failed"
    if text in {"unsupported_platform", "skipped_tiktok_video_resolver_known_issue"}:
        return "skipped" if item else "partial"
    if text in {"ai_disabled", "not_requested"}:
        return "skipped" if item else "ready"
    # 视频 URL dry-run 的两个真实中间态:URL 已识别、创作者留待后台解析——
    # 诚实映射为「已识别」,不再落成 unknown(unknown 会让历史回放误判成已执行)。
    if text in {"provider_refresh_pending", "creator_not_in_pool"}:
        return "identified" if item else "ready"
    # 官方自有账号的视频:按设计不建档、不做深析,终态=跳过(非失败非排队)。
    if text == "official_channel_video":
        return "skipped" if item else "ready"
    return "unknown" if item else "planned"


def _row_to_session(row: Any) -> dict[str, Any]:
    item = dict(row)
    return _jsonable(
        {
            "id": item.get("id"),
            "query_text": item.get("query_text"),
            "query_type": item.get("query_type"),
            "source": item.get("source"),
            "status": item.get("status"),
            "created_by": item.get("created_by"),
            "input_payload": _loads(item.get("input_payload_json"), {}),
            "result_summary": _loads(item.get("result_summary_json"), {}),
            # R1:人审锁定的候选 kol_pool_id(迁移 176;旧行/缺列回退 [])。
            "approved_kol_ids": _loads(item.get("approved_kol_ids"), []),
            "archived_at": item.get("archived_at"),
            "archived_by": item.get("archived_by"),
            "archive_reason": item.get("archive_reason"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
    )


def _row_to_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    return _jsonable(
        {
            "id": item.get("id"),
            "session_id": item.get("session_id"),
            "dedupe_key": item.get("dedupe_key"),
            "item_type": item.get("item_type"),
            "status": item.get("status"),
            "stage": item.get("stage"),
            "rank": item.get("rank"),
            "score": item.get("score"),
            "kol_pool_id": item.get("kol_pool_id"),
            "evidence_id": item.get("evidence_id"),
            "job_id": item.get("job_id"),
            "source_url": item.get("source_url"),
            "payload": _loads(item.get("payload_json"), {}),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
    )


def _item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for item in items:
        status = _text(item.get("status")) or "unknown"
        stage = _text(item.get("stage")) or "identified"
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {"by_status": by_status, "by_stage": by_stage}


_PUBLIC_PROFILE_DATA_FIELDS = (
    "platform",
    "handle",
    "display_name",
    "channel_name",
    "channel_id",
    "profile_url",
    "avatar_url",
    "followers",
    "subscriber_count",
    "posts_count",
    "bio",
    "last_video_at",
)
_PUBLIC_MEDIA_CACHE_FIELDS = (
    "status",
    "cached",
    "storage_backend",
    "reason",
    "error",
    "skip_reason",
    "retry_after_seconds",
    "updated_at",
)
_SAFE_PUBLIC_CODE = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")
_SENSITIVE_URL_QUERY_MARKERS = (
    "x-amz-credential",
    "x-amz-signature",
    "signature=",
    "credential=",
    "access_token=",
    "token=",
    "api_key=",
    "apikey=",
)


def _compact_public_profile_data(value: Any) -> dict[str, Any]:
    """Keep only profile fields that are already public on the platform."""

    profile = _dict(value)
    compact: dict[str, Any] = {}
    for key in _PUBLIC_PROFILE_DATA_FIELDS:
        item = profile.get(key)
        if item in (None, ""):
            continue
        if isinstance(item, str):
            limit = 1000 if key == "bio" else 2048 if key.endswith("_url") else 240
            item = item[:limit]
        elif not isinstance(item, (int, float, bool)):
            continue
        compact[key] = item
    return compact


def _public_cached_video_url(value: Any) -> str:
    """Return a replay-safe cache URL, never a persisted presigned credential."""

    raw = _text(value)[:4096]
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    lowered_query = parsed.query.lower()
    if any(marker in lowered_query for marker in _SENSITIVE_URL_QUERY_MARKERS):
        return ""
    # Benign cache-busting query strings are unnecessary for a durable history
    # snapshot; removing them also prevents accidental future credential keys.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:2048]


def _public_cache_code(value: Any, *, fallback: str = "") -> str:
    text = _text(value)[:160]
    if not text:
        return ""
    return text if _SAFE_PUBLIC_CODE.fullmatch(text) else fallback


def _compact_public_media_cache(value: Any) -> dict[str, Any]:
    cache = _dict(value)
    compact: dict[str, Any] = {}
    for key in _PUBLIC_MEDIA_CACHE_FIELDS:
        item = cache.get(key)
        if item in (None, ""):
            continue
        if key in {"status", "storage_backend", "reason", "skip_reason"}:
            item = _public_cache_code(item)
        elif key == "error":
            item = _public_cache_code(item, fallback="media_cache_failed")
        elif not isinstance(item, (str, int, float, bool)):
            continue
        if item not in (None, ""):
            compact[key] = item
    return compact


def _compact_ai_analysis(value: Any) -> dict[str, Any]:
    analysis = _dict(value)
    return {
        key: analysis.get(key)
        for key in (
            "state",
            "reason",
            "gate_reason",
            "model_readiness_status",
            "provider_calls_allowed",
            "item_count",
            "not_requested_count",
        )
        if key in analysis
    }


def _compact_video_batch_flow(flow: Any) -> dict[str, Any]:
    if not isinstance(flow, dict):
        return {}
    keep = (
        "enabled",
        "status",
        "limit",
        "requested",
        "candidate_count",
        "skipped_by_incremental",
        "queued",
        "skipped",
        "errors",
        "materialized",
        "reused",
        "worker_touched",
        "viltrox_fit_score_changed_ids",
        "viltrox_fit_score_untouched",
    )
    compact = {key: flow.get(key) for key in keep if key in flow}
    ai_analysis = _compact_ai_analysis(flow.get("ai_analysis"))
    if ai_analysis:
        compact["ai_analysis"] = ai_analysis
    items: list[dict[str, Any]] = []
    for raw in _list(flow.get("items"))[:12]:
        if not isinstance(raw, dict):
            continue
        metadata = _dict(raw.get("metadata"))
        evidence = _dict(raw.get("evidence_result"))
        enqueue = _dict(raw.get("enqueue_result"))
        item = {
            "status": raw.get("status"),
            "error": raw.get("error"),
            "title": metadata.get("title"),
            "content_url": metadata.get("content_url"),
            "evidence_id": evidence.get("evidence_id"),
            "job_id": _dict(enqueue.get("job")).get("id") or enqueue.get("job_id"),
        }
        ai_analysis = _compact_ai_analysis(raw.get("ai_analysis") or enqueue.get("ai_analysis"))
        if ai_analysis:
            item["ai_analysis"] = ai_analysis
        cached_video_url = _public_cached_video_url(raw.get("cached_video_url"))
        if cached_video_url:
            item["cached_video_url"] = cached_video_url
        items.append(item)
    if items:
        compact["items"] = items
    return compact


def _compact_flow(flow: dict[str, Any]) -> dict[str, Any]:
    if not flow:
        return {}
    keep = (
        "status",
        "operation",
        "kol_pool_id",
        "evidence_id",
        "run_id",
        "worker_touched",
        "llm_calls_performed",
        "viltrox_fit_score_changed_ids",
        "viltrox_fit_score_untouched",
        "writes",
        "error",
        "elapsed_ms",
    )
    compact = {key: flow.get(key) for key in keep if key in flow}
    raw_progress = _dict(flow.get("resolution_progress"))
    if raw_progress:
        compact["resolution_progress"] = {
            "version": raw_progress.get("version"),
            "status": raw_progress.get("status"),
            "base_status": raw_progress.get("base_status"),
            "current_step": raw_progress.get("current_step"),
            "updated_at": raw_progress.get("updated_at"),
            "steps": [
                {
                    "key": item.get("key"),
                    "label": item.get("label"),
                    "status": item.get("status"),
                    "reason": _text(item.get("reason"))[:240],
                }
                for item in _list(raw_progress.get("steps"))[:4]
                if isinstance(item, dict)
            ],
        }
    ai_analysis = _compact_ai_analysis(
        flow.get("ai_analysis") or _dict(flow.get("enqueue_result")).get("ai_analysis")
    )
    if ai_analysis:
        compact["ai_analysis"] = ai_analysis
    cached_video_url = _public_cached_video_url(flow.get("cached_video_url"))
    if cached_video_url:
        compact["cached_video_url"] = cached_video_url
    profile_data = _compact_public_profile_data(flow.get("profile_data"))
    if profile_data:
        compact["profile_data"] = profile_data
    for status_key in ("media_cache_status", "video_cache_status", "cache_status"):
        status = _public_cache_code(flow.get(status_key))
        if status:
            compact[status_key] = status
    for error_key in ("media_cache_error", "video_cache_error", "cache_error"):
        error = _public_cache_code(flow.get(error_key), fallback="media_cache_failed")
        if error:
            compact[error_key] = error
    media_cache = _compact_public_media_cache(flow.get("media_cache") or flow.get("video_cache"))
    if media_cache:
        compact["media_cache"] = media_cache
    representative = _compact_video_batch_flow(flow.get("representative_video_analysis"))
    history = _compact_video_batch_flow(flow.get("history_video_evidence"))
    if representative:
        compact["representative_video_analysis"] = representative
    if history:
        compact["history_video_evidence"] = history
    if isinstance(flow.get("account_dossier_extract_job"), dict):
        job = _dict(flow.get("account_dossier_extract_job"))
        compact["account_dossier_extract_job"] = {
            key: job.get(key)
            for key in ("status", "job_id", "kol_pool_id")
            if key in job
        }
    return compact
