"""KOL lifecycle event collector (pure-memory timeline).

This module is physically isolated from KOL scoring: it never reads or writes
``vkpi_kol_pool.viltrox_fit_score`` / ``rule_v0``. The v1 main path is a
read-only aggregate over assignments + evidence + content_posts + jobs +
favorites; ``record_lifecycle_event`` is an optional idempotent writer for the
additive ``vkpi_kol_lifecycle_events`` table (no touches_v6_fit column).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn

# event_type values constrained by chk_vkpi_kol_lifecycle_events_type (migration 143).
LIFECYCLE_EVENT_TYPES = (
    "discovered",
    "favorited",
    "assigned",
    "shipped",
    "published",
    "analyzed",
    "failed",
)

# Stages that imply a shipment occurred.
_SHIPPED_STAGES = ("content_posted", "reviewed")
# content_posts statuses that imply a published deliverable.
_PUBLISHED_STATUSES = ("matched", "retrospective_ready")


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


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _sort_key(event: dict[str, Any]) -> str:
    # Newest first; missing timestamps sort last.
    return _iso(event.get("occurred_at")) or ""


def _event(event_type: str, ref_type: str, ref_id: Any, occurred_at: Any, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "ref_type": ref_type,
        "ref_id": "" if ref_id is None else str(ref_id),
        "occurred_at": _iso(occurred_at),
        "detail_json": _jsonable(detail or {}),
    }


def collect_lifecycle_events(kol_pool_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    """Pure-aggregate lifecycle timeline for one KOL (read-only, no table write)."""

    kol_pool_id = int(kol_pool_id)
    safe_limit = max(1, min(int(limit or 200), 1000))
    conn = get_conn()
    events: list[dict[str, Any]] = []

    # discovered: vkpi_kol_pool.created_at (read-only).
    kol_row = conn.execute(
        "SELECT id, created_at FROM vkpi_kol_pool WHERE id=?",
        (kol_pool_id,),
    ).fetchone()
    if not kol_row:
        return []
    kol = dict(kol_row)
    events.append(
        _event("discovered", "kol_pool", kol.get("id"), kol.get("created_at"), {"source": "vkpi_kol_pool.created_at"})
    )

    # favorited: vkpi_kol_pool_favorites (107).
    fav_rows = conn.execute(
        """
        SELECT id, staff_id, note, created_at
        FROM vkpi_kol_pool_favorites
        WHERE kol_pool_id=?
        ORDER BY created_at DESC
        """,
        (kol_pool_id,),
    ).fetchall()
    for row in fav_rows:
        item = dict(row)
        events.append(
            _event(
                "favorited",
                "favorite",
                item.get("id"),
                item.get("created_at"),
                {"staff_id": item.get("staff_id"), "note": item.get("note")},
            )
        )

    # assigned + shipped: vkpi_project_kol_assignments (084).
    assignment_rows = conn.execute(
        """
        SELECT id, project_id, stage, stage_status, tracking_number, created_at, updated_at
        FROM vkpi_project_kol_assignments
        WHERE kol_pool_id=?
        ORDER BY created_at DESC
        """,
        (kol_pool_id,),
    ).fetchall()
    for row in assignment_rows:
        item = dict(row)
        events.append(
            _event(
                "assigned",
                "assignment",
                item.get("id"),
                item.get("created_at"),
                {"project_id": item.get("project_id"), "stage": item.get("stage")},
            )
        )
        stage = str(item.get("stage") or "")
        tracking = str(item.get("tracking_number") or "").strip()
        if stage in _SHIPPED_STAGES or tracking:
            events.append(
                _event(
                    "shipped",
                    "assignment",
                    item.get("id"),
                    item.get("updated_at") or item.get("created_at"),
                    {
                        "project_id": item.get("project_id"),
                        "stage": stage,
                        "tracking_number": tracking or None,
                    },
                )
            )

    # published: vkpi_project_content_posts (129).
    post_rows = conn.execute(
        """
        SELECT id, project_id, platform, content_url, status, published_at,
               view_count, like_count, comment_count
        FROM vkpi_project_content_posts
        WHERE kol_pool_id=?
          AND status = ANY(?)
        ORDER BY published_at DESC NULLS LAST, id DESC
        """,
        (kol_pool_id, list(_PUBLISHED_STATUSES)),
    ).fetchall()
    for row in post_rows:
        item = dict(row)
        events.append(
            _event(
                "published",
                "content_post",
                item.get("id"),
                item.get("published_at"),
                {
                    "project_id": item.get("project_id"),
                    "platform": item.get("platform"),
                    "content_url": item.get("content_url"),
                    "status": item.get("status"),
                    "view_count": item.get("view_count"),
                },
            )
        )

    # analyzed: vkpi_kol_llm_deep_analysis_results status='ready' (102).
    analyzed_rows = conn.execute(
        """
        SELECT id, analysis_kind, source_evidence_id, provider, created_at
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=?
          AND status='ready'
        ORDER BY created_at DESC, id DESC
        """,
        (kol_pool_id,),
    ).fetchall()
    for row in analyzed_rows:
        item = dict(row)
        events.append(
            _event(
                "analyzed",
                "deep_analysis",
                item.get("id"),
                item.get("created_at"),
                {
                    "analysis_kind": item.get("analysis_kind"),
                    "source_evidence_id": item.get("source_evidence_id"),
                    "provider": item.get("provider"),
                },
            )
        )

    # failed: apify_jobs status='failed' with payload kol_pool_id match (095).
    failed_rows = conn.execute(
        """
        SELECT id, job_type, last_error, updated_at, created_at
        FROM apify_jobs
        WHERE status='failed'
          AND payload->>'kol_pool_id' = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (str(kol_pool_id),),
    ).fetchall()
    for row in failed_rows:
        item = dict(row)
        events.append(
            _event(
                "failed",
                "apify_job",
                item.get("id"),
                item.get("updated_at") or item.get("created_at"),
                {"job_type": item.get("job_type"), "last_error": item.get("last_error")},
            )
        )

    events.sort(key=_sort_key, reverse=True)
    return events[:safe_limit]


def record_lifecycle_event(
    kol_pool_id: int,
    event_type: str,
    *,
    ref_type: str = "",
    ref_id: str = "",
    occurred_at: str | None = None,
    detail_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Optional idempotent writer for vkpi_kol_lifecycle_events.

    v1 main path is read-only aggregation; this is a thin helper for callers
    that want a durable event. It never touches scoring fields. event_type must
    belong to the CHECK set (LIFECYCLE_EVENT_TYPES).
    """

    kol_pool_id = int(kol_pool_id)
    event_type = str(event_type or "").strip()
    if event_type not in LIFECYCLE_EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type!r}")
    ref_type = str(ref_type or "")
    ref_id = str(ref_id or "")
    detail = detail_json or {}
    conn = get_conn()

    # Idempotent: skip if same (kol, type, ref_type, ref_id) already recorded.
    existing = conn.execute(
        """
        SELECT id
        FROM vkpi_kol_lifecycle_events
        WHERE kol_pool_id=?
          AND event_type=?
          AND ref_type=?
          AND ref_id=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (kol_pool_id, event_type, ref_type, ref_id),
    ).fetchone()
    if existing:
        return {"written": False, "event_id": int(dict(existing)["id"]), "reason": "already_recorded"}

    if occurred_at:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_lifecycle_events
                (kol_pool_id, event_type, ref_type, ref_id, occurred_at, detail_json)
            VALUES (?, ?, ?, ?, ?, ?::jsonb)
            RETURNING id
            """,
            (
                kol_pool_id,
                event_type,
                ref_type,
                ref_id,
                occurred_at,
                json.dumps(_jsonable(detail), ensure_ascii=False, default=str),
            ),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_lifecycle_events
                (kol_pool_id, event_type, ref_type, ref_id, detail_json)
            VALUES (?, ?, ?, ?, ?::jsonb)
            RETURNING id
            """,
            (
                kol_pool_id,
                event_type,
                ref_type,
                ref_id,
                json.dumps(_jsonable(detail), ensure_ascii=False, default=str),
            ),
        ).fetchone()
    conn.commit()
    event_id = int(dict(row)["id"]) if row else None
    return {"written": True, "event_id": event_id}
