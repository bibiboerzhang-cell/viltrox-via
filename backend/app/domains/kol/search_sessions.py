"""Unified KOL Pool search-session state.

This module records smart URL/profile/text recall orchestration state. It only
writes the session tables introduced by migration 103 and must not update
vkpi_kol_pool scoring fields.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn


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


def create_session(
    *,
    query_text: str,
    query_type: str = "unknown",
    source: str = "smart_kol_input",
    input_payload: dict[str, Any] | None = None,
    status: str = "planned",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_search_sessions
          (query_text, query_type, source, status, created_by, input_payload_json, result_summary_json)
        VALUES (?, ?, ?, ?, ?, ?::jsonb, '{}'::jsonb)
        RETURNING *
        """,
        (
            _text(query_text),
            _normalize_query_type(query_type),
            _text(source) or "smart_kol_input",
            _normalize_status(status),
            _staff_user_id(staff),
            _json_dumps(input_payload or {}),
        ),
    ).fetchone()
    conn.commit()
    return _row_to_session(row)


def list_sessions(*, limit: int = 20, status: str = "") -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))
    normalized_status = _normalize_status(status) if status else ""
    conn = get_conn()
    if normalized_status:
        rows = conn.execute(
            """
            SELECT *
            FROM vkpi_kol_search_sessions
            WHERE status=?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_status, safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM vkpi_kol_search_sessions
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return {
        "status": "ready",
        "count": len(rows),
        "items": [_row_to_session(row) for row in rows],
    }


def list_history(
    *,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
) -> dict[str, Any]:
    """Return recent search sessions with compact item previews for history UI."""
    safe_limit = max(1, min(int(limit or 20), 50))
    safe_item_limit = max(0, min(int(item_limit or 5), 10))
    normalized_status = _normalize_status(status) if status else ""
    normalized_query_type = _normalize_query_type(query_type) if query_type else ""

    where: list[str] = []
    params: list[Any] = []
    if normalized_status:
        where.append("status=?")
        params.append(normalized_status)
    if normalized_query_type:
        where.append("query_type=?")
        params.append(normalized_query_type)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_sessions
        {where_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    sessions = [_row_to_session(row) for row in rows]
    if not sessions:
        return {
            "status": "ready",
            "count": 0,
            "items": [],
            "filters": {
                "status": normalized_status,
                "query_type": normalized_query_type,
                "limit": safe_limit,
                "item_limit": safe_item_limit,
            },
        }

    session_ids = [int(session["id"]) for session in sessions if _int_or_none(session.get("id"))]
    placeholders = ", ".join(["?"] * len(session_ids))
    item_rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id IN ({placeholders})
        ORDER BY session_id, rank NULLS LAST, id
        """,
        tuple(session_ids),
    ).fetchall()

    grouped: dict[int, list[dict[str, Any]]] = {int(session_id): [] for session_id in session_ids}
    for row in item_rows:
        item = _row_to_item(row)
        grouped.setdefault(int(item.get("session_id") or 0), []).append(item)

    history_items: list[dict[str, Any]] = []
    for session in sessions:
        session_id = int(session["id"])
        all_items = grouped.get(session_id, [])
        counts = _item_counts(all_items)
        preview_items = all_items[:safe_item_limit] if safe_item_limit else []
        active_items = [
            item
            for item in all_items
            if _text(item.get("status")) in {"queued", "running", "already_queued"}
        ]
        result_summary = _dict(session.get("result_summary"))
        history_items.append(
            {
                **session,
                "item_count": len(all_items),
                "items_preview": preview_items,
                "active_items": active_items[:3],
                "counts": counts,
                "summary": {
                    "kind": result_summary.get("kind"),
                    "platform": result_summary.get("platform"),
                    "url_type": result_summary.get("url_type"),
                    "in_pool": result_summary.get("in_pool"),
                    "items_written": result_summary.get("items_written"),
                    "matched_kol_pool_id": result_summary.get("matched_kol_pool_id"),
                    "viltrox_fit_score_untouched": result_summary.get("viltrox_fit_score_untouched"),
                },
            }
        )

    return {
        "status": "ready",
        "count": len(history_items),
        "items": history_items,
        "filters": {
            "status": normalized_status,
            "query_type": normalized_query_type,
            "limit": safe_limit,
            "item_limit": safe_item_limit,
        },
    }


def get_session(session_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    item_rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id),),
    ).fetchall()
    session = _row_to_session(row)
    items = [_row_to_item(item) for item in item_rows]
    session["items"] = items
    session["count"] = len(items)
    session["counts"] = _item_counts(items)
    return session


def get_session_item(session_id: int, item_id: int) -> dict[str, Any]:
    conn = get_conn()
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


def update_session_result_summary(
    session_id: int,
    *,
    status: str,
    summary_patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge a small orchestration summary into one search session."""

    conn = get_conn()
    row = conn.execute(
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    summary = _loads(dict(row).get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    summary.update(_dict(summary_patch))
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=?,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        RETURNING *
        """,
        (_normalize_status(status), _json_dumps(summary), int(session_id)),
    ).fetchone()
    conn.commit()
    return _row_to_session(updated)


def update_item_profile_execution(
    session_id: int,
    item_id: int,
    *,
    profile_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist profile-crawl execution result for a discovery item."""
    conn = get_conn()
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
    next_status = "ready" if status_text == "ready" else "failed" if "failed" in status_text or status_text in {"crawl_failed", "unsupported"} else "partial"
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
        "representative_video_analysis": profile_flow.get("representative_video_analysis"),
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
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
        "SELECT result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
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
            "viltrox_fit_score_untouched": payload["profile_execute"].get("viltrox_fit_score_untouched"),
        }
    )
    summary["profile_materialization"] = profile_materialization
    session_status = "partial" if next_status == "failed" else "ready"
    _update_session(conn, int(session_id), status=session_status, summary=summary)
    conn.commit()
    return _row_to_item(updated)


def mark_items_profile_queued(
    session_id: int,
    *,
    item_ids: list[int],
    job_id: int,
    reason: str = "session_advance_queued",
    plan_items: list[dict[str, Any]] | None = None,
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
    conn = get_conn()
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

    conn = get_conn()
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
    conn = get_conn()
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


def _item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    for item in items:
        status = _text(item.get("status")) or "unknown"
        stage = _text(item.get("stage")) or "identified"
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {"by_status": by_status, "by_stage": by_stage}


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
    dedupe_key = _text(item.get("dedupe_key")) or f"item:{_text(item.get('item_type'))}:{_text(item.get('source_url'))}:{_text(item.get('kol_pool_id'))}"
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
) -> dict[str, Any]:
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not existing:
        raise LookupError(f"search session not found: {session_id}")
    written = [_upsert_item(conn, int(session_id), item) for item in items]
    _update_session(conn, int(session_id), status=status, summary=summary or {"items_written": len(written)})
    conn.commit()
    return {
        "status": "ready",
        "session_id": int(session_id),
        "items_written": len(written),
        "items": written,
    }


def ensure_session_for_result(
    *,
    session_id: int | None,
    create: bool,
    query_text: str,
    query_type: str,
    source: str,
    input_payload: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if session_id:
        return get_session(int(session_id))
    if create:
        return create_session(
            query_text=query_text,
            query_type=query_type,
            source=source,
            input_payload=input_payload or {},
            status="planned",
            staff=staff,
        )
    return None


def attach_url_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    item = _url_result_item(int(session_id), result)
    session_status = _session_status_from_url_result(result)
    summary = {
        "kind": "url_deep_crawl",
        "url_type": result.get("url_type"),
        "platform": result.get("platform"),
        "execute": bool(result.get("execute")),
        "in_pool": bool(result.get("in_pool")),
        "matched_kol_pool_id": result.get("matched_kol_pool_id"),
        "item_status": item.get("status"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    recorded = record_items(int(session_id), [item], status=session_status, summary=summary)
    recorded["jobs_linked"] = _link_job_payloads(int(session_id), recorded.get("items") or [])
    return recorded


def attach_recall_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    rank = 1
    buckets = _dict(result.get("buckets"))
    for bucket_name in ("creator", "reviewer"):
        for raw in _list(buckets.get(bucket_name)):
            if not isinstance(raw, dict):
                continue
            kol_pool_id = _int_or_none(raw.get("kol_pool_id") or raw.get("id"))
            source_url = _text(raw.get("profile_url") or raw.get("url"))
            score = _float_or_none(raw.get("recall_rank_score") or raw.get("vector_score"))
            items.append(
                {
                    "dedupe_key": f"recall:{kol_pool_id or source_url or rank}",
                    "item_type": "recall_candidate",
                    "status": "matched",
                    "stage": "identified",
                    "rank": rank,
                    "score": score,
                    "kol_pool_id": kol_pool_id,
                    "source_url": source_url,
                    "payload": {
                        "bucket": bucket_name,
                        "handle": raw.get("handle"),
                        "display_name": raw.get("display_name"),
                        "platform": raw.get("platform"),
                        "profile_type": raw.get("profile_type"),
                        "followers": raw.get("followers"),
                        # 问题1 头像修:recall_candidate 会话项此前漏写 avatar_url(new_creator/existing_kol 都写了),
                        # 致历史回填掉头像。透传 _build_item 已填的 avatar_url。
                        "avatar_url": raw.get("avatar_url"),
                        "recall_rank_score": raw.get("recall_rank_score"),
                        "vector_score": raw.get("vector_score"),
                        "type_score": raw.get("type_score"),
                        "evidence": raw.get("evidence"),
                    },
                }
            )
            rank += 1
    summary = {
        "kind": "kol_recall",
        "items_written": len(items),
        "diagnostics": result.get("diagnostics"),
        "query": result.get("query"),
    }
    return record_items(int(session_id), items, status="ready", summary=summary)


def attach_new_discovery_result(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Attach platform-discovery candidates to an existing smart-search session."""
    items: list[dict[str, Any]] = []
    rank = 1
    for raw in _list(result.get("existing_matches")):
        if not isinstance(raw, dict):
            continue
        kol_pool_id = _int_or_none(raw.get("history_kol_pool_id") or _dict(raw.get("historical_match")).get("kol_pool_id"))
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
        items.append(
            {
                "dedupe_key": f"existing:{kol_pool_id or source_url or rank}",
                "item_type": "existing_kol",
                "status": "matched",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("history_match_confidence") or _dict(raw.get("historical_match")).get("match_confidence")),
                "kol_pool_id": kol_pool_id,
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": raw.get("platform"),
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
                    "avatar_url": raw.get("avatar_url"),
                    "historical_match": raw.get("historical_match"),
                },
            }
        )
        rank += 1
    for raw in _list(result.get("new_creators")):
        if not isinstance(raw, dict):
            continue
        source_url = _text(raw.get("channel_url") or raw.get("source_url"))
        handle = _text(raw.get("handle") or raw.get("channel_name"))
        platform = _text(raw.get("platform") or (result.get("platforms") or [""])[0])
        items.append(
            {
                "dedupe_key": f"new:{platform}:{handle or source_url or rank}",
                "item_type": "new_creator",
                "status": "identified",
                "stage": "identified",
                "rank": rank,
                "score": _float_or_none(raw.get("score") or raw.get("vector_score")),
                "source_url": source_url,
                "payload": {
                    "source": "platform_discovery",
                    "platform": platform,
                    "handle": raw.get("handle"),
                    "channel_name": raw.get("channel_name"),
                    "sample_title": raw.get("sample_title"),
                    "source_url": raw.get("source_url"),
                    "channel_url": raw.get("channel_url"),
                    "avatar_url": raw.get("avatar_url"),
                    "thumbnail_url": raw.get("thumbnail_url"),
                    "views": raw.get("views"),
                    "likes": raw.get("likes"),
                    "comments": raw.get("comments"),
                    "avg_views": raw.get("avg_views"),
                    "published": raw.get("published"),
                    "search_query": raw.get("search_query") or result.get("query"),
                    "market": raw.get("market") or result.get("market"),
                },
            }
        )
        rank += 1

    existing_summary: dict[str, Any] = {}
    try:
        existing_summary = _dict(get_session(int(session_id)).get("result_summary"))
    except Exception:
        existing_summary = {}
    discovery_summary = {
        "kind": "platform_discovery",
        "query": result.get("query"),
        "status": result.get("status"),
        "platforms": result.get("platforms"),
        "counts": result.get("counts"),
        "provider_calls": result.get("provider_calls"),
        "platform_results": result.get("platform_results"),
        "errors": result.get("errors"),
        "viltrox_fit_score_untouched": True,
    }
    summary = {
        **existing_summary,
        "new_discovery": discovery_summary,
    }
    status = "ready"
    if result.get("status") in {"partial", "failed"}:
        status = "partial"
    recorded = record_items(int(session_id), items, status=status, summary=summary)
    recorded["new_discovery"] = discovery_summary
    return recorded


def _session_status_from_url_result(result: dict[str, Any]) -> str:
    if not result.get("execute"):
        return "ready"
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    status = _text(video_flow.get("status") or profile_flow.get("status") or result.get("status")).lower()
    if status == "queued":
        return "running"
    if status in {"already_queued"}:
        return "running"
    if status in {"already_analyzed", "ready"}:
        return "ready"
    if status in {"failed", "creator_unresolved", "profile_crawl_failed", "crawl_failed"}:
        return "failed"
    return "partial" if result.get("execute") else "ready"


def _url_result_item(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
    del session_id
    url = _dict(result.get("url"))
    video_flow = _dict(result.get("video_flow"))
    profile_flow = _dict(result.get("profile_flow"))
    evidence_result = _dict(video_flow.get("evidence_result"))
    enqueue_result = _dict(video_flow.get("enqueue_result"))
    enqueue_job = _dict(enqueue_result.get("job"))
    normalized_url = _text(url.get("normalized") or url.get("input") or result.get("source_url"))
    url_type = _text(result.get("url_type"))
    item_type = "url_video" if url_type == "video" else "url_profile" if url_type == "profile" else "unknown"
    kol_pool_id = _int_or_none(video_flow.get("kol_pool_id") or profile_flow.get("kol_pool_id") or result.get("matched_kol_pool_id"))
    evidence_id = _int_or_none(video_flow.get("evidence_id") or evidence_result.get("evidence_id"))
    job_id = _int_or_none(enqueue_job.get("id") or enqueue_result.get("id") or enqueue_result.get("job_id"))
    status = _text(video_flow.get("status") or profile_flow.get("status"))
    if not status:
        status = "matched" if result.get("in_pool") else "identified"
    if status == "ready" and not result.get("execute"):
        status = "identified"
    stage = "analysis" if status in {"queued", "already_queued", "already_analyzed"} else "identified"
    if profile_flow and item_type == "url_profile":
        stage = "profile"
    return {
        "dedupe_key": f"{item_type}:{normalized_url or result.get('video_id') or result.get('handle') or 'unknown'}",
        "item_type": item_type,
        "status": _normalize_status(status, item=True),
        "stage": stage,
        "rank": 1,
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "job_id": job_id,
        "source_url": normalized_url,
        "payload": {
            "url_type": result.get("url_type"),
            "platform": result.get("platform"),
            "video_id": result.get("video_id"),
            "handle": result.get("handle"),
            "channel_id": result.get("channel_id"),
            "creator_identity": result.get("creator_identity") or video_flow.get("creator_identity"),
            "video_metadata": result.get("video_metadata") or video_flow.get("video_metadata"),
            "profile_flow": _compact_flow(profile_flow),
            "video_flow": _compact_flow(video_flow),
            "in_pool": result.get("in_pool"),
            "matched_kol_pool_id": result.get("matched_kol_pool_id"),
            "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched") or video_flow.get("viltrox_fit_score_untouched") or profile_flow.get("viltrox_fit_score_untouched"),
        },
    }


def _link_job_payloads(session_id: int, items: list[dict[str, Any]]) -> int:
    conn = get_conn()
    linked = 0
    for item in items:
        job_id = _int_or_none(item.get("job_id"))
        item_id = _int_or_none(item.get("id"))
        if not job_id:
            continue
        row = conn.execute(
            "SELECT id, payload FROM apify_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if not row:
            continue
        payload = _loads(dict(row).get("payload"), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["search_session_id"] = int(session_id)
        if item_id:
            payload["search_session_item_id"] = int(item_id)
        payload["search_session_item_status"] = item.get("status")
        payload["search_session_stage"] = item.get("stage")
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb WHERE id=?",
            (_json_dumps(payload), int(job_id)),
        )
        linked += 1
    if linked:
        conn.commit()
    return linked


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
