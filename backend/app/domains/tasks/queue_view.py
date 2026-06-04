"""Read-only task queue projection for the V-KPI sidebar board.

This module intentionally does not enqueue, mutate, or mark anything. It only
projects active and recently-finished work from already-registered tables.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn


ACTIVE_STATUSES = {"queued", "retrying", "processing", "running", "in_progress", "started"}
TERMINAL_STATUSES = {
    "done",
    "success",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "partial_done",
    "prefilter_rejected",
    "all_providers_failed",
    "ai_budget_hard_stop",
    "budget_disabled",
    "not_configured",
}

STATUS_ALIASES = {
    "success": "done",
    "all_providers_failed": "failed",
    "ai_budget_hard_stop": "failed",
    "budget_disabled": "failed",
    "not_configured": "failed",
    "in_progress": "running",
    "started": "running",
}

STAGE_LABELS = {
    "queued": "排队",
    "search": "搜索",
    "thinking": "思考",
    "summarizing": "总结",
}


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return default
    return parsed if parsed is not None else default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normal_status(value: Any) -> str:
    raw = _text(value).lower() or "queued"
    return STATUS_ALIASES.get(raw, raw)


def _timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _infer_kind(source: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    data = payload if isinstance(payload, dict) else {}
    haystack = " ".join(
        [
            source,
            job_type,
            purpose,
            _text(data.get("derive_method")),
            _text(data.get("target_type")),
            _text(data.get("prompt")),
            _text(data.get("script")),
        ]
    ).lower()
    if "final_v1" in haystack or "video_analysis" in haystack or "video" in haystack:
        return "video深析"
    if any(word in haystack for word in ("url", "profile", "crawl", "scan", "resolve", "download", "ingest", "sync")):
        return "搜索/抓取"
    if any(word in haystack for word in ("report", "brief", "summary")):
        return "报告生成"
    if any(word in haystack for word in ("cache_extract", "deep_result", "post_process", "backfill")):
        return "总结沉淀"
    if source == "llm_calls" or any(word in haystack for word in ("gemini", "claude", "openai", "llm", "score")):
        return "LLM分析"
    return "任务"


def _infer_stage(status: str, kind: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    if status == "queued":
        return "queued"
    data = payload if isinstance(payload, dict) else {}
    haystack = " ".join(
        [
            kind,
            job_type,
            purpose,
            _text(data.get("derive_method")),
            _text(data.get("target_type")),
            _text(data.get("prompt")),
        ]
    ).lower()
    if any(word in haystack for word in ("report", "summary", "cache_extract", "deep_result", "post_process", "backfill")):
        return "summarizing"
    if any(word in haystack for word in ("gemini", "claude", "openai", "llm", "final_v1", "video_analysis", "score")):
        return "thinking"
    if any(word in haystack for word in ("url", "profile", "crawl", "scan", "resolve", "download", "ingest", "sync")):
        return "search"
    return "thinking"


def _target_from_payload(payload: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    fallback = fallback or {}
    target_type = _text(data.get("target_type") or fallback.get("target_type"))
    target_id = _text(data.get("target_id") or fallback.get("target_id"))
    source_url = _text(data.get("source_url") or data.get("url") or fallback.get("source_url"))
    label = _text(data.get("prompt") or data.get("summary") or fallback.get("label"))
    target = {
        "target_type": target_type or None,
        "target_id": target_id or None,
        "source_url": source_url or None,
        "label": label[:180] if label else None,
        "platform": _text(data.get("platform") or fallback.get("platform")) or None,
    }
    return {key: value for key, value in target.items() if value not in (None, "")}


def _make_item(
    *,
    source: str,
    row_id: Any,
    raw_status: Any,
    job_type: str = "",
    purpose: str = "",
    payload: Any = None,
    created_at: Any = None,
    updated_at: Any = None,
    target: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = _normal_status(raw_status)
    kind = _infer_kind(source, job_type=job_type, purpose=purpose, payload=payload)
    stage = _infer_stage(status, kind, job_type=job_type, purpose=purpose, payload=payload)
    item = {
        "id": str(row_id),
        "source": source,
        "kind": kind,
        "job_type": job_type or purpose or "",
        "target": target or _target_from_payload(payload),
        "status": status,
        "raw_status": _text(raw_status),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "created_at": _timestamp(created_at),
        "updated_at": _timestamp(updated_at or created_at),
    }
    if extra:
        item.update(extra)
    return _jsonable(item)


def _query_apify_jobs(cutoff: datetime, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    active_rows = conn.execute(
        """
        SELECT id, job_type, payload, status, last_error, created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('queued', 'retrying', 'processing', 'running')
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT id, job_type, payload, status, last_error, created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('done', 'failed', 'blocked')
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    def convert(row: Any) -> dict[str, Any]:
        data = dict(row)
        payload = _loads(data.get("payload"), {})
        return _make_item(
            source="apify_jobs",
            row_id=data.get("id"),
            raw_status=data.get("status"),
            job_type=_text(data.get("job_type")),
            payload=payload,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            extra={"error": _text(data.get("last_error")) or None},
        )

    return [convert(row) for row in active_rows], [convert(row) for row in recent_rows]


def _query_job_execution_ledger(cutoff: datetime, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    active_rows = conn.execute(
        """
        SELECT id, task_id, job_type, status, stage, summary, payload_json, result_json, error_message,
               submission_id, user_id, created_at, updated_at, started_at, finished_at
        FROM job_execution_ledger
        WHERE status IN ('queued', 'retrying', 'processing', 'running')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT id, task_id, job_type, status, stage, summary, payload_json, result_json, error_message,
               submission_id, user_id, created_at, updated_at, started_at, finished_at
        FROM job_execution_ledger
        WHERE status IN ('done', 'failed', 'cancelled', 'timeout', 'partial_done', 'prefilter_rejected')
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    def convert(row: Any) -> dict[str, Any]:
        data = dict(row)
        payload = _loads(data.get("payload_json"), {})
        target = _target_from_payload(
            payload,
            fallback={
                "target_id": data.get("submission_id"),
                "target_type": "submission" if data.get("submission_id") else "",
                "label": data.get("summary"),
            },
        )
        item = _make_item(
            source="ledger",
            row_id=data.get("task_id") or data.get("id"),
            raw_status=data.get("status"),
            job_type=_text(data.get("job_type")),
            payload={**(payload if isinstance(payload, dict) else {}), "stage": data.get("stage"), "summary": data.get("summary")},
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            target=target,
            extra={
                "ledger_id": data.get("id"),
                "error": _text(data.get("error_message")) or None,
                "started_at": _timestamp(data.get("started_at")),
                "finished_at": _timestamp(data.get("finished_at")),
            },
        )
        if data.get("stage") and item["stage"] == "thinking" and str(data.get("stage")).lower() in {"ingest", "crawl"}:
            item["stage"] = "search"
            item["stage_label"] = STAGE_LABELS["search"]
        return item

    return [convert(row) for row in active_rows], [convert(row) for row in recent_rows]


def _query_llm_calls(cutoff: datetime, limit: int, scan_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT id, call_uid, provider, model, purpose, status, created_at, metadata_json, latency_ms, cost_cents
        FROM vkpi_llm_calls
        ORDER BY id DESC
        LIMIT ?
        """,
        (scan_limit,),
    ).fetchall()

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        raw_status = _text(data.get("status")).lower()
        created_at = data.get("created_at")
        created_dt = _as_datetime(created_at)
        is_active = raw_status in ACTIVE_STATUSES
        is_recent = raw_status in TERMINAL_STATUSES and created_dt is not None and created_dt >= cutoff
        if not is_active and not is_recent:
            continue
        metadata = _loads(data.get("metadata_json"), {})
        item = _make_item(
            source="llm_calls",
            row_id=data.get("call_uid") or data.get("id"),
            raw_status=raw_status,
            purpose=_text(data.get("purpose")),
            payload={**(metadata if isinstance(metadata, dict) else {}), "provider": data.get("provider"), "model": data.get("model")},
            created_at=created_at,
            updated_at=created_at,
            target=_target_from_payload(metadata if isinstance(metadata, dict) else {}, fallback={"label": data.get("purpose")}),
            extra={
                "llm_call_id": data.get("id"),
                "provider": data.get("provider"),
                "model": data.get("model"),
                "latency_ms": data.get("latency_ms"),
                "cost_cents": data.get("cost_cents"),
            },
        )
        if is_active:
            active.append(item)
        else:
            recent.append(item)
    return active[:limit], recent[:limit]


def _active_status_rank(item: dict[str, Any]) -> int:
    status = str(item.get("status") or "")
    if status in {"running", "processing"}:
        return 0
    if status == "retrying":
        return 1
    if status == "queued":
        return 2
    return 3


def _recent_sort_key(item: dict[str, Any]) -> str:
    return str(item.get("updated_at") or item.get("created_at") or "")


def get_task_queue(*, limit: int = 50, recent_minutes: int = 10, include_llm_calls: bool = True) -> dict[str, Any]:
    """Return a compact read-only projection for 2-3s polling."""

    safe_limit = max(1, min(int(limit or 50), 100))
    safe_recent_minutes = max(1, min(int(recent_minutes or 10), 120))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=safe_recent_minutes)

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    sources = ["apify_jobs", "job_execution_ledger"]

    apify_active, apify_recent = _query_apify_jobs(cutoff, safe_limit)
    ledger_active, ledger_recent = _query_job_execution_ledger(cutoff, safe_limit)
    active.extend(apify_active)
    active.extend(ledger_active)
    recent.extend(apify_recent)
    recent.extend(ledger_recent)

    llm_scan_limit = max(200, safe_limit * 5)
    if include_llm_calls:
        llm_active, llm_recent = _query_llm_calls(cutoff, safe_limit, llm_scan_limit)
        active.extend(llm_active)
        recent.extend(llm_recent)
        sources.append("vkpi_llm_calls")

    active = sorted(active, key=_recent_sort_key, reverse=True)
    active = sorted(active, key=_active_status_rank)[:safe_limit]
    recent = sorted(recent, key=_recent_sort_key, reverse=True)[:safe_limit]
    active_status_counts = Counter(str(item.get("status") or "") for item in active)
    recent_status_counts = Counter(str(item.get("status") or "") for item in recent)
    active_stage_counts = Counter(str(item.get("stage") or "") for item in active)

    return {
        "status": "ready",
        "source": "apify_jobs+job_execution_ledger+vkpi_llm_calls" if include_llm_calls else "apify_jobs+job_execution_ledger",
        "query": {
            "limit": safe_limit,
            "recent_minutes": safe_recent_minutes,
            "include_llm_calls": bool(include_llm_calls),
            "llm_scan_strategy": "latest_id_window",
            "llm_scan_limit": llm_scan_limit if include_llm_calls else 0,
        },
        "counts": {
            "active_total": len(active),
            "recent_total": len(recent),
            "queued": active_status_counts.get("queued", 0),
            "running": active_status_counts.get("running", 0) + active_status_counts.get("processing", 0) + active_status_counts.get("retrying", 0),
            "active_by_status": dict(active_status_counts),
            "recent_by_status": dict(recent_status_counts),
            "active_by_stage": dict(active_stage_counts),
        },
        "active": active,
        "recent": recent,
        "diagnostics": {
            "sources": sources,
            "indexes_used": {
                "apify_jobs": "idx_apify_jobs_status_created(status, created_at)",
                "job_execution_ledger": "idx_job_execution_ledger_status_updated(status, updated_at)",
                "vkpi_llm_calls": "pkey id desc window; no status/created_at index in phase 1",
            },
            "llm_calls_coverage": "registered LLM calls only; Gateway-bypassing naked calls are not visible in phase 1",
            "write_db": False,
            "llm_calls": False,
            "worker_touched": False,
        },
    }
