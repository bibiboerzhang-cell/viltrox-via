"""Read-only projection of registered LLM call outcomes.

Database access and queue item shaping stay injected by ``queue_view`` so its
existing monkeypatch boundaries and public helper API remain stable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

def query_llm_calls(
    cutoff: datetime,
    limit: int,
    scan_limit: int,
    *,
    get_conn: Callable[[], Any],
    make_item: Callable[..., dict[str, Any]],
    target_from_payload: Callable[..., dict[str, Any]],
    loads: Callable[[Any, Any], Any],
    text: Callable[[Any], str],
    as_datetime: Callable[[Any], datetime | None],
    active_statuses: set[str],
    terminal_statuses: set[str],
    runtime_reason_contract: Callable[[str, Any], dict[str, Any] | None],
    authoritative_llm_status: Callable[[str, Any], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project active and recent registered Gateway calls without raw prompts."""

    rows = get_conn().execute(
        """
        SELECT id, call_uid, provider, model, purpose, status, fallback_used,
               created_at, metadata_json, latency_ms, cost_cents
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
        raw_status = text(data.get("status")).lower()
        created_at = data.get("created_at")
        created_dt = as_datetime(created_at)
        is_active = raw_status in active_statuses
        is_recent = (
            raw_status in terminal_statuses
            and created_dt is not None
            and created_dt >= cutoff
        )
        if not is_active and not is_recent:
            continue
        metadata = loads(data.get("metadata_json"), {})
        reason_contract = runtime_reason_contract(raw_status, metadata)
        reason_fields = (
            {
                "reason_code": reason_contract.get("code"),
                "reason_category": reason_contract.get("category"),
                "reason_retryable": bool(reason_contract.get("retryable")),
            }
            if reason_contract
            else {}
        )
        fallback_used = bool(data.get("fallback_used"))
        provider = text(data.get("provider"))
        fallback_mode = (
            "rule_v0"
            if fallback_used and provider == "rule_v0"
            else "provider_fallback"
            if fallback_used and raw_status == "success"
            else "safe_fallback"
            if fallback_used
            else None
        )
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        item = make_item(
            source="llm_calls",
            row_id=data.get("call_uid") or data.get("id"),
            raw_status=raw_status,
            purpose=text(data.get("purpose")),
            payload={
                **safe_metadata,
                "provider": data.get("provider"),
                "model": data.get("model"),
            },
            created_at=created_at,
            updated_at=created_at,
            target=target_from_payload(
                safe_metadata,
                fallback={"label": data.get("purpose")},
            ),
            extra={
                "llm_call_id": data.get("id"),
                "provider": data.get("provider"),
                "model": data.get("model"),
                "purpose": data.get("purpose"),
                "latency_ms": data.get("latency_ms"),
                "cost_cents": data.get("cost_cents"),
                "fallback_used": fallback_used,
                "fallback_mode": fallback_mode,
                # Only bounded progress/correlation keys are projected; raw
                # prompt and provider exception content stay out of the item.
                "task_binding": safe_metadata.get("task_binding"),
                "parent_job_id": safe_metadata.get("parent_job_id"),
                "phase": safe_metadata.get("phase"),
                "subphase": safe_metadata.get("subphase"),
                "attempt_index": safe_metadata.get("attempt_index"),
                "attempt_total": safe_metadata.get(
                    "attempt_total", safe_metadata.get("total")
                ),
                **reason_fields,
            },
        )
        authoritative_status = authoritative_llm_status(
            raw_status, reason_contract
        )
        if item.get("status") != authoritative_status:
            # A policy/readiness hold inside a legacy failure wrapper is not a
            # provider execution failure.
            item["status"] = authoritative_status
        (active if is_active else recent).append(item)
    return active[:limit], recent[:limit]


__all__ = ["query_llm_calls"]
