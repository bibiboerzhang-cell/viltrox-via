"""Pure serialization/normalization helpers for KOL search sessions.

Behavior-preserving move out of ``search_sessions.py``. These are all pure
functions (no DB access) covering JSON (de)serialization, value coercion,
status/query-type normalization, row→dict mappers, item counting, and flow
compaction. Re-exported by ``search_sessions`` to keep all call sites stable.

This module never writes ``viltrox_fit_score`` (no fit writes whatsoever).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


SESSION_QUERY_TYPES = {"url_video", "url_profile", "text_recall", "unknown"}
SESSION_STATUSES = {"planned", "running", "ready", "partial", "failed", "cancelled"}
ITEM_STATUSES = {
    "planned",
    "identified",
    "matched",
    "queued",
    "running",
    "ready",
    "partial",
    "failed",
    "skipped",
    "already_queued",
    "already_analyzed",
    "unknown",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


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
    if text in {"dry_run_ready", "resolved"}:
        return "identified" if item else "ready"
    if text in {"done", "completed"}:
        return "ready"
    if text in {"would_create", "would_reuse", "created", "reused"}:
        return "matched" if item else "ready"
    if text in {"error", "crawl_failed", "profile_crawl_failed", "creator_unresolved"}:
        return "failed"
    if text in {"unsupported_platform", "skipped_tiktok_video_resolver_known_issue"}:
        return "skipped" if item else "partial"
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
    items: list[dict[str, Any]] = []
    for raw in _list(flow.get("items"))[:12]:
        if not isinstance(raw, dict):
            continue
        metadata = _dict(raw.get("metadata"))
        evidence = _dict(raw.get("evidence_result"))
        enqueue = _dict(raw.get("enqueue_result"))
        items.append(
            {
                "status": raw.get("status"),
                "error": raw.get("error"),
                "title": metadata.get("title"),
                "content_url": metadata.get("content_url"),
                "evidence_id": evidence.get("evidence_id"),
                "job_id": _dict(enqueue.get("job")).get("id") or enqueue.get("job_id"),
            }
        )
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
