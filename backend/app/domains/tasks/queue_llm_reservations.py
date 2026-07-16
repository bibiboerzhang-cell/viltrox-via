"""Read-only projection of strict LLM budget reservations.

The queue view owns task-shape helpers and database adapters.  They are
injected here so tests can keep replacing the same boundaries without this
module importing the large queue projection back and creating a cycle.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable


def query_llm_reservations(
    cutoff: datetime,
    limit: int,
    *,
    table_exists: Callable[[str], bool],
    get_conn: Callable[[], Any],
    make_item: Callable[..., dict[str, Any]],
    target_from_payload: Callable[..., dict[str, Any]],
    loads: Callable[[Any, Any], Any],
    text: Callable[[Any], str],
    as_datetime: Callable[[Any], datetime | None],
    timestamp: Callable[[Any], str | None],
    logger: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project provider in-flight rows and recent unknown outcomes."""

    if not table_exists("vkpi_llm_budget_reservations"):
        return [], []
    try:
        rows = get_conn().execute(
            """
            SELECT reservation_key,provider,model_name,purpose,state,
                   metadata_json,reserved_at,provider_started_at,updated_at
            FROM vkpi_llm_budget_reservations
            WHERE state IN ('reserved','provider_started','unknown')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit or 1)),),
        ).fetchall()
    except Exception:
        logger.warning("LLM reservation progress projection failed", exc_info=True)
        return [], []

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        state = text(data.get("state")).lower()
        updated_at = data.get("updated_at") or data.get("reserved_at")
        updated_dt = as_datetime(updated_at)
        if state == "reserved":
            status = "queued"
        elif state == "provider_started":
            status = "running"
        elif state == "unknown" and updated_dt is not None and updated_dt >= cutoff:
            status = "triage"
        else:
            continue
        metadata = loads(data.get("metadata_json"), {})
        purpose = text(data.get("purpose"))
        item = make_item(
            source="llm_reservations",
            row_id=data.get("reservation_key"),
            raw_status=status,
            purpose=purpose,
            payload={
                **(metadata if isinstance(metadata, dict) else {}),
                "provider": data.get("provider"),
                "model": data.get("model_name"),
            },
            created_at=data.get("reserved_at"),
            updated_at=updated_at,
            target=target_from_payload(
                metadata if isinstance(metadata, dict) else {},
                fallback={"label": purpose},
            ),
            extra={
                "provider": data.get("provider"),
                "model": data.get("model_name"),
                "purpose": purpose,
                "started_at": timestamp(
                    data.get("provider_started_at") or data.get("reserved_at")
                ),
                "finished_at": timestamp(updated_at) if status == "triage" else None,
                "reason_code": "reservation_outcome_unknown" if status == "triage" else None,
                "reason_category": "runtime" if status == "triage" else None,
                "reason_retryable": False if status == "triage" else None,
                "task_binding": metadata.get("task_binding") if isinstance(metadata, dict) else None,
                "parent_job_id": metadata.get("parent_job_id") if isinstance(metadata, dict) else None,
                "phase": metadata.get("phase") if isinstance(metadata, dict) else None,
                "subphase": metadata.get("subphase") if isinstance(metadata, dict) else None,
                "attempt_index": metadata.get("attempt_index") if isinstance(metadata, dict) else None,
                "attempt_total": metadata.get("attempt_total") if isinstance(metadata, dict) else None,
            },
        )
        (active if status in {"queued", "running"} else recent).append(item)
    return active[:limit], recent[:limit]


def true_llm_reservation_counts(
    conn: Any,
    *,
    table_exists: Callable[[str], bool],
    text: Callable[[Any], str],
    logger: Any,
) -> Counter:
    """Count all strict queued/running reservations without render limits."""

    counts: Counter = Counter()
    if not table_exists("vkpi_llm_budget_reservations"):
        return counts
    try:
        rows = conn.execute(
            """
            SELECT state,COUNT(*) AS n
            FROM vkpi_llm_budget_reservations
            WHERE state IN ('reserved','provider_started')
            GROUP BY state
            """
        ).fetchall()
        for row in rows:
            data = dict(row)
            state = text(data.get("state")).lower()
            counts["queued" if state == "reserved" else "running"] += int(
                data.get("n") or 0
            )
    except Exception:
        logger.warning("LLM reservation active count failed", exc_info=True)
    return counts


__all__ = ["query_llm_reservations", "true_llm_reservation_counts"]
