"""Unified KOL Pool search-session state.

This module records smart URL/profile/text recall orchestration state. It only
writes the session tables introduced by migration 103 and must not update
vkpi_kol_pool scoring fields.

Pure serde/normalization helpers live in ``search_sessions_serde`` and the
attach-result builders live in ``search_sessions_attach``; both are re-exported
below so all existing call sites keep importing from ``search_sessions``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.connection import get_conn

# Re-export pure serde/normalization helpers (behavior-preserving move).
from app.domains.kol.search_sessions_serde import (
    ITEM_STATUSES,
    SESSION_QUERY_TYPES,
    SESSION_STATUSES,
    _compact_flow,
    _compact_video_batch_flow,
    _dict,
    _float_or_none,
    _int_or_none,
    _item_counts,
    _json_dumps,
    _jsonable,
    _list,
    _loads,
    _normalize_query_type,
    _normalize_status,
    _row_to_item,
    _row_to_session,
    _staff_user_id,
    _text,
)

# Re-export attach-result builders (behavior-preserving move).
from app.domains.kol.search_sessions_attach import (
    _link_job_payloads,
    _session_status_from_url_result,
    _url_result_item,
    attach_new_discovery_result,
    attach_recall_result,
    attach_url_result,
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
    staff: dict[str, Any] | None = None,
    scope_to_staff: bool = True,
) -> dict[str, Any]:
    """Return recent search sessions with compact item previews for history UI.

    每个人的记录不能串:默认按 created_by=当前登录人作用域过滤(scope_to_staff),
    不同员工互不串记录。actor 取不到时不过滤(回退看全部,避免登录态异常致空)。
    """
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
    actor_id = _staff_user_id(staff) if scope_to_staff else None
    if actor_id:
        where.append("created_by=?")
        params.append(actor_id)
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


def approve_session(
    session_id: int,
    *,
    kol_pool_ids: list[Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """R1:人审锁定该会话里要推进合作的候选 KOL → 写 approved_kol_ids(R2 据此建项目草案)。

    只接受真实存在的 kol_pool_id(校验 vkpi_kol_pool 存在性,绝不写任意 id);去重保序;replace
    语义(本次选择即最终锁定集)。校验口径用「池中存在」而非「会话项含该 id」——因全网新发现
    new_creator 入池后会话项 kol_pool_id 仍为 NULL,若按会话项交集会把这些真候选全误杀。
    审计落 result_summary_json.approval(谁/何时/接受几个/跳过几个)。绝不写 vkpi_kol_pool /
    viltrox_fit_score / rule_v0,只读池做存在性校验 + 写本会话 approved_kol_ids + summary 两处。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")

    requested: list[int] = []
    seen: set[int] = set()
    for raw in _list(kol_pool_ids):
        parsed = _int_or_none(raw)
        if parsed and parsed not in seen:
            seen.add(parsed)
            requested.append(parsed)

    # 存在性校验:只接受真实在池的 kol_pool_id(只读 vkpi_kol_pool,绝不写)。
    valid_ids: set[int] = set()
    if requested:
        placeholders = ",".join("?" for _ in requested)
        pool_rows = conn.execute(
            f"SELECT id FROM vkpi_kol_pool WHERE id IN ({placeholders})",
            requested,
        ).fetchall()
        valid_ids = {int(dict(r)["id"]) for r in pool_rows if dict(r).get("id")}

    accepted = [kid for kid in requested if kid in valid_ids]
    skipped = [kid for kid in requested if kid not in valid_ids]

    # 审计合并进 result_summary_json.approval(沿用既有 jsonb 合并;不另加列)。
    summary = _loads(dict(row).get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    approved_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    summary["approval"] = {
        "approved_kol_ids": accepted,
        "approved_count": len(accepted),
        "skipped_not_in_pool": skipped,
        "approved_by": _staff_user_id(staff),
        "approved_at": approved_at,
        "viltrox_fit_score_untouched": True,
    }

    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET approved_kol_ids=?::jsonb,
            result_summary_json=?::jsonb,
            updated_at=NOW()
        WHERE id=?
        RETURNING *
        """,
        (_json_dumps(accepted), _json_dumps(summary), int(session_id)),
    ).fetchone()
    conn.commit()
    session = _row_to_session(updated)
    session["approved_count"] = len(accepted)
    session["skipped_not_in_pool"] = skipped
    return session


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

