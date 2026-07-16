"""Item persistence and profile-advance state transitions for search sessions."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.search_sessions_schema import PENDING_ENRICHMENT_STATUSES
from app.domains.kol.search_sessions_serde import (
    _dict,
    _float_or_none,
    _int_or_none,
    _json_dumps,
    _loads,
    _normalize_status,
    _row_to_item,
    _text,
)


GetConn = Callable[[], Any]
UpdateSession = Callable[..., None]
UpsertItem = Callable[[Any, int, dict[str, Any]], dict[str, Any]]


def _session_status_after_profile_item(current_status: str, current_phase: str, item_status: str) -> str:
    if _text(current_status).lower() == "running" and _text(current_phase).lower() in {"base", "profile"}:
        return "running"
    return _normalize_status(item_status)


def get_session_item(
    session_id: int,
    item_id: int,
    *,
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    conn = (get_conn_fn or get_conn)()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND id=?
        """,
        (int(session_id), int(item_id)),
    ).fetchone()
    if not row:
        raise LookupError(f"search session item not found: session={session_id} item={item_id}")
    return _row_to_item(row)


def update_item_profile_execution(
    session_id: int,
    item_id: int,
    *,
    profile_result: dict[str, Any],
    get_conn_fn: GetConn | None = None,
    update_session_fn: UpdateSession | None = None,
) -> dict[str, Any]:
    """Persist profile-crawl execution result for a discovery item."""
    conn = (get_conn_fn or get_conn)()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND id=?
        """,
        (int(session_id), int(item_id)),
    ).fetchone()
    if not row:
        raise LookupError(f"search session item not found: session={session_id} item={item_id}")
    current = _row_to_item(row)
    payload = _dict(current.get("payload")).copy()
    profile_flow = _dict(profile_result.get("profile_flow"))
    status_text = _text(profile_flow.get("status") or profile_result.get("status")).lower()
    next_status = (
        "ready"
        if status_text == "ready"
        else "failed"
        if "failed" in status_text or status_text in {"crawl_failed", "unsupported"}
        else "partial"
    )
    contact_enrichment = _dict(profile_result.get("contact_enrichment"))
    audience_enrichment = _dict(profile_result.get("audience_enrichment"))
    enrichment_statuses = {
        _text(contact_enrichment.get("status")).lower(),
        _text(audience_enrichment.get("status")).lower(),
    }
    if next_status == "ready" and enrichment_statuses & (
        PENDING_ENRICHMENT_STATUSES | {"partial", "error", "failed"}
    ):
        next_status = "partial"
    kol_pool_id = _int_or_none(
        profile_flow.get("kol_pool_id")
        or profile_result.get("matched_kol_pool_id")
        or current.get("kol_pool_id")
    )
    payload["profile_execute"] = {
        "status": status_text or next_status,
        "kol_pool_id": kol_pool_id,
        "operation": profile_flow.get("operation"),
        "run_id": profile_flow.get("run_id"),
        "profile_data": profile_flow.get("profile_data"),
        "write_result": profile_flow.get("write_result"),
        "contact_enrichment": contact_enrichment,
        "audience_enrichment": audience_enrichment,
        "representative_video_analysis": profile_flow.get("representative_video_analysis"),
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids")
        or profile_result.get("viltrox_fit_score_changed_ids")
        or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched")
        if "viltrox_fit_score_untouched" in profile_flow
        else profile_result.get("viltrox_fit_score_untouched"),
    }
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_session_items
        SET status=?,
            stage='profile',
            kol_pool_id=COALESCE(?, kol_pool_id),
            payload_json=?::jsonb,
            updated_at=NOW()
        WHERE session_id=? AND id=?
        RETURNING *
        """,
        (
            next_status,
            kol_pool_id,
            _json_dumps(payload),
            int(session_id),
            int(item_id),
        ),
    ).fetchone()

    session_row = conn.execute(
        "SELECT status, result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    summary = _loads(dict(session_row).get("result_summary_json") if session_row else "{}", {})
    if not isinstance(summary, dict):
        summary = {}
    profile_materialization = _dict(summary.get("profile_materialization")).copy()
    profile_materialization.update(
        {
            "last_item_id": int(item_id),
            "last_status": next_status,
            "last_kol_pool_id": kol_pool_id,
            "contact_enrichment_status": contact_enrichment.get("status"),
            "audience_enrichment_status": audience_enrichment.get("status"),
            "viltrox_fit_score_untouched": payload["profile_execute"].get(
                "viltrox_fit_score_untouched"
            ),
        }
    )
    summary["profile_materialization"] = profile_materialization
    item_rows = conn.execute(
        "SELECT status, payload_json FROM vkpi_kol_search_session_items WHERE session_id=?",
        (int(session_id),),
    ).fetchall()
    progress = _dict(summary.get("progress")).copy()
    profile_ready = 0
    profile_failed = 0
    complete_ready = 0
    complete_partial = 0
    for item_row in item_rows:
        item_data = dict(item_row)
        raw_item_payload = item_data.get("payload_json")
        item_payload = raw_item_payload if isinstance(raw_item_payload, dict) else _loads(raw_item_payload, {})
        profile_execute = _dict(item_payload.get("profile_execute"))
        profile_status = _text(profile_execute.get("status")).lower()
        item_status = _text(item_data.get("status")).lower()
        if item_status in {"ready", "already_analyzed"}:
            complete_ready += 1
        elif item_status == "partial":
            complete_partial += 1
        if profile_status in {"ready", "already_analyzed"}:
            profile_ready += 1
        elif "failed" in profile_status or item_status == "failed":
            profile_failed += 1
    progress.update(
        {
            "base": max(_int_or_none(progress.get("base")) or 0, len(item_rows)),
            "total": _int_or_none(progress.get("total")) or len(item_rows),
            "profile_ready": profile_ready,
            "profile_failed": profile_failed,
            "complete_ready": complete_ready,
            "complete_partial": complete_partial,
        }
    )
    current_session_status = _text(dict(session_row).get("status") if session_row else "").lower()
    current_phase = _text(summary.get("phase")).lower()
    session_status = _session_status_after_profile_item(current_session_status, current_phase, next_status)
    keep_running = session_status == "running"
    summary["phase"] = "profile" if keep_running else ("complete" if session_status == "ready" else "partial")
    summary["progress"] = progress
    (update_session_fn or _update_session)(
        conn,
        int(session_id),
        status=session_status,
        summary=summary,
    )
    conn.commit()
    return _row_to_item(updated)


def mark_items_profile_queued(
    session_id: int,
    *,
    item_ids: list[int],
    job_id: int,
    reason: str = "session_advance_queued",
    plan_items: list[dict[str, Any]] | None = None,
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Mark selected discovery items as queued for one session-advance job."""
    safe_item_ids = sorted({int(item_id) for item_id in item_ids if _int_or_none(item_id)})
    safe_job_id = _int_or_none(job_id)
    if not safe_item_ids or not safe_job_id:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "job_id": safe_job_id,
            "updated_count": 0,
            "items": [],
        }

    plan_by_item_id = {
        _int_or_none(item.get("item_id")): item
        for item in (plan_items or [])
        if isinstance(item, dict) and _int_or_none(item.get("item_id"))
    }
    placeholders = ", ".join(["?"] * len(safe_item_ids))
    conn = (get_conn_fn or get_conn)()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
          AND id IN ({placeholders})
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), *safe_item_ids),
    ).fetchall()

    queued_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        item_id = int(current["id"])
        payload = _dict(current.get("payload")).copy()
        payload["profile_advance_job"] = {
            "status": "queued",
            "job_id": safe_job_id,
            "queued_at": queued_at,
            "reason": _text(reason) or "session_advance_queued",
            "plan": _dict(plan_by_item_id.get(item_id)).get("plan"),
            "viltrox_fit_score_untouched": True,
        }
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='queued',
                stage='profile',
                job_id=?,
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                safe_job_id,
                _json_dumps(payload),
                int(session_id),
                item_id,
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "job_id": safe_job_id,
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def mark_items_profile_running(
    session_id: int,
    *,
    job_id: int,
    reason: str = "session_advance_running",
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Mark queued discovery items as running when the worker claims the job."""
    safe_job_id = _int_or_none(job_id)
    if not safe_job_id:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "job_id": safe_job_id,
            "updated_count": 0,
            "items": [],
        }

    conn = (get_conn_fn or get_conn)()
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=? AND job_id=? AND status='queued'
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), safe_job_id),
    ).fetchall()
    running_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        payload = _dict(current.get("payload")).copy()
        profile_advance_job = _dict(payload.get("profile_advance_job")).copy()
        profile_advance_job.update(
            {
                "status": "running",
                "job_id": safe_job_id,
                "running_at": running_at,
                "reason": _text(reason) or "session_advance_running",
                "viltrox_fit_score_untouched": True,
            }
        )
        payload["profile_advance_job"] = profile_advance_job
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='running',
                stage='profile',
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                _json_dumps(payload),
                int(session_id),
                int(current["id"]),
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "job_id": safe_job_id,
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def mark_items_profile_cancelled(
    session_id: int,
    *,
    job_ids: list[int],
    reason: str = "session_advance_cancelled_by_user",
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Mark queued items as retryable after their queued session-advance job is blocked."""
    safe_job_ids = sorted({int(job_id) for job_id in job_ids if _int_or_none(job_id)})
    if not safe_job_ids:
        return {
            "status": "ready",
            "session_id": int(session_id),
            "updated_count": 0,
            "items": [],
        }

    placeholders = ", ".join(["?"] * len(safe_job_ids))
    conn = (get_conn_fn or get_conn)()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
          AND job_id IN ({placeholders})
          AND status='queued'
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id), *safe_job_ids),
    ).fetchall()

    cancelled_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated_items: list[dict[str, Any]] = []
    for row in rows:
        current = _row_to_item(row)
        payload = _dict(current.get("payload")).copy()
        profile_advance_job = _dict(payload.get("profile_advance_job")).copy()
        profile_advance_job.update(
            {
                "status": "cancelled",
                "cancelled_at": cancelled_at,
                "reason": _text(reason) or "session_advance_cancelled_by_user",
                "viltrox_fit_score_untouched": True,
            }
        )
        payload["profile_advance_job"] = profile_advance_job
        updated = conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='skipped',
                stage='identified',
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE session_id=? AND id=?
            RETURNING *
            """,
            (
                _json_dumps(payload),
                int(session_id),
                int(current["id"]),
            ),
        ).fetchone()
        updated_items.append(_row_to_item(updated))

    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "updated_count": len(updated_items),
        "items": updated_items,
    }


def _update_session(
    conn: Any,
    session_id: int,
    *,
    status: str,
    summary: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=?,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        """,
        (_normalize_status(status), _json_dumps(summary), int(session_id)),
    )


def _upsert_item(conn: Any, session_id: int, item: dict[str, Any]) -> dict[str, Any]:
    dedupe_key = _text(item.get("dedupe_key")) or (
        f"item:{_text(item.get('item_type'))}:{_text(item.get('source_url'))}:"
        f"{_text(item.get('kol_pool_id'))}"
    )
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_search_session_items
          (session_id, dedupe_key, item_type, status, stage, rank, score, kol_pool_id, evidence_id, job_id, source_url, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        ON CONFLICT (session_id, dedupe_key) DO UPDATE
        SET item_type=EXCLUDED.item_type,
            status=EXCLUDED.status,
            stage=EXCLUDED.stage,
            rank=COALESCE(EXCLUDED.rank, vkpi_kol_search_session_items.rank),
            score=COALESCE(EXCLUDED.score, vkpi_kol_search_session_items.score),
            kol_pool_id=COALESCE(EXCLUDED.kol_pool_id, vkpi_kol_search_session_items.kol_pool_id),
            evidence_id=COALESCE(EXCLUDED.evidence_id, vkpi_kol_search_session_items.evidence_id),
            job_id=COALESCE(EXCLUDED.job_id, vkpi_kol_search_session_items.job_id),
            source_url=COALESCE(NULLIF(EXCLUDED.source_url, ''), vkpi_kol_search_session_items.source_url),
            payload_json=EXCLUDED.payload_json,
            updated_at=NOW()
        RETURNING *
        """,
        (
            int(session_id),
            dedupe_key,
            _text(item.get("item_type")) or "unknown",
            _normalize_status(item.get("status"), item=True),
            _text(item.get("stage")) or "identified",
            _int_or_none(item.get("rank")),
            _float_or_none(item.get("score")),
            _int_or_none(item.get("kol_pool_id")),
            _int_or_none(item.get("evidence_id")),
            _int_or_none(item.get("job_id")),
            _text(item.get("source_url")),
            _json_dumps(item.get("payload") or {}),
        ),
    ).fetchone()
    return _row_to_item(row)


def record_items(
    session_id: int,
    items: list[dict[str, Any]],
    *,
    status: str = "ready",
    summary: dict[str, Any] | None = None,
    get_conn_fn: GetConn | None = None,
    upsert_item_fn: UpsertItem | None = None,
    update_session_fn: UpdateSession | None = None,
) -> dict[str, Any]:
    conn = (get_conn_fn or get_conn)()
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not existing:
        raise LookupError(f"search session not found: {session_id}")
    upsert = upsert_item_fn or _upsert_item
    written = [upsert(conn, int(session_id), item) for item in items]
    (update_session_fn or _update_session)(
        conn,
        int(session_id),
        status=status,
        summary=summary or {"items_written": len(written)},
    )
    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "items_written": len(written),
        "items": written,
    }
