"""Owner-scoped personal-memory persistence for Marketing Advisor."""
from __future__ import annotations

from typing import Any

from app.domains.advisor.repository_support import (
    ALLOWED_MEMORY_KINDS,
    ALLOWED_SENSITIVITY,
    AdvisorConflict,
    AdvisorNotFound,
    AdvisorValidationError,
    _bounded_json,
    _json_dumps,
    _new_uid,
    _positive_limit,
    _retention_cutoff,
    _retention_days,
    _retention_policy,
    _row_dict,
    _scope_params,
    _sha256,
    _text,
    sanitize_provenance,
)
from app.domains.advisor.scope import AdvisorScope


def _facade():
    # Resolve lazily to preserve repository.get_conn/table_exists as the public
    # dependency seam without creating an import-time cycle.
    from app.domains.advisor import repository

    return repository


def _ensure_schema() -> None:
    _facade()._ensure_schema()


def _get_conn() -> Any:
    return _facade().get_conn()


def _ensure_memory_settings(conn: Any, scope: AdvisorScope) -> Any:
    conn.execute(
        "INSERT INTO vkpi_advisor_memory_settings "
        "(organization_id, staff_id, updated_by_staff_id) VALUES (?,?,?) "
        "ON CONFLICT (organization_id, staff_id) DO NOTHING",
        (*_scope_params(scope), scope.staff_id),
    )
    return conn.execute(
        "SELECT * FROM vkpi_advisor_memory_settings "
        "WHERE organization_id=? AND staff_id=?",
        _scope_params(scope),
    ).fetchone()


def _memory_event(
    conn: Any,
    scope: AdvisorScope,
    *,
    event_type: str,
    subject_type: str,
    subject_uid: str,
    before: Any = None,
    after: Any = None,
    detail: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO vkpi_advisor_memory_events "
        "(organization_id, staff_id, actor_staff_id, event_type, subject_type, subject_uid, "
        "before_sha256, after_sha256, detail_json) VALUES (?,?,?,?,?,?,?,?,?::jsonb)",
        (
            *_scope_params(scope),
            scope.staff_id,
            event_type,
            subject_type,
            _text(subject_uid, 160),
            _sha256(before) if before is not None else "",
            _sha256(after) if after is not None else "",
            _json_dumps(_bounded_json(detail or {})),
        ),
    )


def get_memory(scope: AdvisorScope, *, limit: int = 100) -> dict[str, Any]:
    _ensure_schema()
    conn = _get_conn()
    settings = conn.execute(
        "SELECT * FROM vkpi_advisor_memory_settings "
        "WHERE organization_id=? AND staff_id=?",
        _scope_params(scope),
    ).fetchone()
    settings_item = _row_dict(settings) if settings is not None else {
        "organization_id": scope.organization_id,
        "staff_id": scope.staff_id,
        "state": "active",
        "retention_days": 180,
        "persisted": False,
    }
    retention_days = _retention_days(settings_item.get("retention_days"))
    retention_cutoff = _retention_cutoff(retention_days)
    bounded_limit = _positive_limit(limit, default=100, maximum=500)
    candidates = conn.execute(
        "SELECT * FROM vkpi_advisor_memory_candidates "
        "WHERE organization_id=? AND staff_id=? AND deleted_at IS NULL "
        "AND created_at>=? "
        "ORDER BY id DESC LIMIT ?",
        (*_scope_params(scope), retention_cutoff, bounded_limit),
    ).fetchall()
    facts = conn.execute(
        "SELECT * FROM vkpi_advisor_memory_facts "
        "WHERE organization_id=? AND staff_id=? AND deleted_at IS NULL "
        "AND updated_at>=? "
        "ORDER BY updated_at DESC, id DESC LIMIT ?",
        (*_scope_params(scope), retention_cutoff, bounded_limit),
    ).fetchall()
    conn.commit()
    return {
        "settings": settings_item,
        "candidates": [_row_dict(row) for row in candidates],
        "facts": [_row_dict(row) for row in facts],
        "retention_policy": _retention_policy(retention_days, retention_cutoff),
    }


def update_memory_settings(
    scope: AdvisorScope,
    *,
    state: str,
    retention_days: int | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    safe_state = _text(state, 20).lower()
    if safe_state not in {"active", "paused"}:
        raise AdvisorValidationError("memory state must be active or paused")
    conn = _get_conn()
    try:
        before = _row_dict(_ensure_memory_settings(conn, scope))
        retention = int(before.get("retention_days") or 180) if retention_days is None else int(retention_days)
        if retention < 1 or retention > 3650:
            raise AdvisorValidationError("retention_days must be between 1 and 3650")
        row = conn.execute(
            "UPDATE vkpi_advisor_memory_settings "
            "SET state=?, retention_days=?, updated_by_staff_id=?, updated_at=NOW() "
            "WHERE organization_id=? AND staff_id=? RETURNING *",
            (safe_state, retention, scope.staff_id, *_scope_params(scope)),
        ).fetchone()
        after = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="paused" if safe_state == "paused" else "resumed",
            subject_type="settings",
            subject_uid=f"{scope.organization_id}:{scope.staff_id}",
            before=before,
            after=after,
        )
        conn.commit()
        return after
    except Exception:
        conn.rollback()
        raise


def create_memory_candidate(
    scope: AdvisorScope,
    *,
    memory_kind: str,
    memory_key: str,
    summary: str,
    value: Any,
    provenance: Any,
    sensitivity: str = "normal",
    source_message_uid: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    kind = _text(memory_kind, 40).lower()
    if kind not in ALLOWED_MEMORY_KINDS:
        raise AdvisorValidationError("unsupported memory_kind")
    key = _text(memory_key, 160)
    if not key:
        raise AdvisorValidationError("memory_key is required")
    safe_sensitivity = _text(sensitivity, 20).lower() or "normal"
    if safe_sensitivity not in ALLOWED_SENSITIVITY:
        raise AdvisorValidationError("unsupported sensitivity")
    safe_source_uid = _text(source_message_uid, 80) or None
    safe_value = _bounded_json(value or {})
    safe_provenance = sanitize_provenance(provenance)
    if not any(safe_provenance.values()):
        safe_provenance["source_ref"] = "explicit:user-memory-candidate"
    conn = _get_conn()
    try:
        settings = _row_dict(_ensure_memory_settings(conn, scope))
        if settings.get("state") == "paused":
            raise AdvisorConflict("personal memory is paused")
        if safe_source_uid:
            source = conn.execute(
                "SELECT 1 FROM vkpi_advisor_messages "
                "WHERE organization_id=? AND staff_id=? AND message_uid=? AND deleted_at IS NULL",
                (*_scope_params(scope), safe_source_uid),
            ).fetchone()
            if source is None:
                raise AdvisorNotFound("source message not found")
        candidate_uid = _new_uid("advmemc")
        row = conn.execute(
            "INSERT INTO vkpi_advisor_memory_candidates "
            "(candidate_uid, organization_id, staff_id, source_message_uid, memory_kind, memory_key, "
            "summary, value_json, provenance_json, sensitivity, status) "
            "VALUES (?,?,?,?,?,?,?,?::jsonb,?::jsonb,?,'pending') RETURNING *",
            (
                candidate_uid,
                *_scope_params(scope),
                safe_source_uid,
                kind,
                key,
                _text(summary, 2000),
                _json_dumps(safe_value),
                _json_dumps(safe_provenance),
                safe_sensitivity,
            ),
        ).fetchone()
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="candidate_created",
            subject_type="candidate",
            subject_uid=candidate_uid,
            after=result,
            detail={"activation": "requires_explicit_confirmation"},
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def confirm_memory_candidate(scope: AdvisorScope, candidate_uid: str) -> dict[str, Any]:
    _ensure_schema()
    safe_uid = _text(candidate_uid, 80)
    conn = _get_conn()
    try:
        _ensure_memory_settings(conn, scope)
        # Locking one owner settings row serializes confirmations for the same
        # personal memory namespace and protects the active memory_key unique index.
        settings_row = conn.execute(
            "SELECT state, retention_days FROM vkpi_advisor_memory_settings "
            "WHERE organization_id=? AND staff_id=? FOR UPDATE",
            _scope_params(scope),
        ).fetchone()
        settings = _row_dict(settings_row)
        if settings.get("state") != "active":
            raise AdvisorConflict("personal memory is paused")
        retention_cutoff = _retention_cutoff(
            _retention_days(settings.get("retention_days"))
        )
        raw_candidate = conn.execute(
            "SELECT * FROM vkpi_advisor_memory_candidates "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=? AND deleted_at IS NULL "
            "AND created_at>=? "
            "FOR UPDATE",
            (*_scope_params(scope), safe_uid, retention_cutoff),
        ).fetchone()
        if raw_candidate is None:
            raise AdvisorNotFound("memory candidate not found or outside retention window")
        candidate = _row_dict(raw_candidate)
        if candidate.get("status") == "confirmed" and candidate.get("confirmed_fact_uid"):
            existing = conn.execute(
                "SELECT * FROM vkpi_advisor_memory_facts "
                "WHERE organization_id=? AND staff_id=? AND fact_uid=? AND deleted_at IS NULL",
                (*_scope_params(scope), candidate["confirmed_fact_uid"]),
            ).fetchone()
            if existing is None:
                raise AdvisorConflict("confirmed memory fact was deleted")
            conn.commit()
            return _row_dict(existing)
        if candidate.get("status") != "pending":
            raise AdvisorConflict("memory candidate is not pending")
        raw_existing = conn.execute(
            "SELECT * FROM vkpi_advisor_memory_facts "
            "WHERE organization_id=? AND staff_id=? AND memory_key=? AND deleted_at IS NULL "
            "FOR UPDATE",
            (*_scope_params(scope), candidate["memory_key"]),
        ).fetchone()
        if raw_existing is None:
            fact_uid = _new_uid("advmemf")
            fact_row = conn.execute(
                "INSERT INTO vkpi_advisor_memory_facts "
                "(fact_uid, organization_id, staff_id, source_candidate_uid, memory_kind, memory_key, "
                "summary, value_json, provenance_json, sensitivity, status) "
                "VALUES (?,?,?,?,?,?,?,?::jsonb,?::jsonb,?,'active') RETURNING *",
                (
                    fact_uid,
                    *_scope_params(scope),
                    safe_uid,
                    candidate["memory_kind"],
                    candidate["memory_key"],
                    candidate["summary"],
                    _json_dumps(candidate["value_json"]),
                    _json_dumps(candidate["provenance_json"]),
                    candidate["sensitivity"],
                ),
            ).fetchone()
        else:
            existing = _row_dict(raw_existing)
            fact_uid = str(existing["fact_uid"])
            fact_row = conn.execute(
                "UPDATE vkpi_advisor_memory_facts SET source_candidate_uid=?, memory_kind=?, summary=?, "
                "value_json=?::jsonb, provenance_json=?::jsonb, sensitivity=?, status='active', "
                "version=version+1, updated_at=NOW() "
                "WHERE organization_id=? AND staff_id=? AND fact_uid=? RETURNING *",
                (
                    safe_uid,
                    candidate["memory_kind"],
                    candidate["summary"],
                    _json_dumps(candidate["value_json"]),
                    _json_dumps(candidate["provenance_json"]),
                    candidate["sensitivity"],
                    *_scope_params(scope),
                    fact_uid,
                ),
            ).fetchone()
        fact = _row_dict(fact_row)
        conn.execute(
            "UPDATE vkpi_advisor_memory_candidates "
            "SET status='confirmed', confirmed_fact_uid=?, reviewed_at=NOW() "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=?",
            (fact_uid, *_scope_params(scope), safe_uid),
        )
        _memory_event(
            conn,
            scope,
            event_type="confirmed",
            subject_type="fact",
            subject_uid=fact_uid,
            after=fact,
            detail={"candidate_uid": safe_uid},
        )
        conn.commit()
        return fact
    except Exception:
        conn.rollback()
        raise


def reject_memory_candidate(scope: AdvisorScope, candidate_uid: str) -> dict[str, Any]:
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "UPDATE vkpi_advisor_memory_candidates "
            "SET status='rejected', reviewed_at=NOW() "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=? "
            "AND status='pending' AND deleted_at IS NULL RETURNING *",
            (*_scope_params(scope), _text(candidate_uid, 80)),
        ).fetchone()
        if row is None:
            raise AdvisorNotFound("pending memory candidate not found")
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="rejected",
            subject_type="candidate",
            subject_uid=str(result["candidate_uid"]),
            before={"status": "pending"},
            after=result,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def delete_memory_candidate(scope: AdvisorScope, candidate_uid: str) -> dict[str, Any]:
    _ensure_schema()
    conn = _get_conn()
    try:
        row = conn.execute(
            "UPDATE vkpi_advisor_memory_candidates "
            "SET status='deleted', deleted_at=NOW() "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=? "
            "AND status <> 'confirmed' AND deleted_at IS NULL "
            "RETURNING candidate_uid, status, deleted_at",
            (*_scope_params(scope), _text(candidate_uid, 80)),
        ).fetchone()
        if row is None:
            raise AdvisorNotFound("deletable memory candidate not found")
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="deleted",
            subject_type="candidate",
            subject_uid=str(result["candidate_uid"]),
            after=result,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def update_memory_fact(
    scope: AdvisorScope,
    fact_uid: str,
    *,
    summary: str | None = None,
    value: Any = None,
    value_present: bool = False,
    status: str | None = None,
) -> dict[str, Any]:
    _ensure_schema()
    assignments: list[str] = []
    params: list[Any] = []
    if summary is not None:
        assignments.append("summary=?")
        params.append(_text(summary, 2000))
    if value_present:
        assignments.append("value_json=?::jsonb")
        params.append(_json_dumps(_bounded_json(value or {})))
    event_type = "edited"
    if status is not None:
        safe_status = _text(status, 20).lower()
        if safe_status not in {"active", "paused"}:
            raise AdvisorValidationError("memory fact status must be active or paused")
        assignments.append("status=?")
        params.append(safe_status)
        event_type = "paused" if safe_status == "paused" else "resumed"
    if not assignments:
        raise AdvisorValidationError("at least one memory fact field is required")
    assignments.extend(["version=version+1", "updated_at=NOW()"])
    conn = _get_conn()
    try:
        before_row = conn.execute(
            "SELECT * FROM vkpi_advisor_memory_facts "
            "WHERE organization_id=? AND staff_id=? AND fact_uid=? AND deleted_at IS NULL",
            (*_scope_params(scope), _text(fact_uid, 80)),
        ).fetchone()
        if before_row is None:
            raise AdvisorNotFound("memory fact not found")
        before = _row_dict(before_row)
        row = conn.execute(
            f"UPDATE vkpi_advisor_memory_facts SET {', '.join(assignments)} "
            "WHERE organization_id=? AND staff_id=? AND fact_uid=? AND deleted_at IS NULL "
            "RETURNING *",
            (*params, *_scope_params(scope), _text(fact_uid, 80)),
        ).fetchone()
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type=event_type,
            subject_type="fact",
            subject_uid=str(result["fact_uid"]),
            before=before,
            after=result,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def delete_memory_fact(scope: AdvisorScope, fact_uid: str) -> dict[str, Any]:
    _ensure_schema()
    conn = _get_conn()
    try:
        before_row = conn.execute(
            "SELECT * FROM vkpi_advisor_memory_facts "
            "WHERE organization_id=? AND staff_id=? AND fact_uid=? AND deleted_at IS NULL",
            (*_scope_params(scope), _text(fact_uid, 80)),
        ).fetchone()
        if before_row is None:
            raise AdvisorNotFound("memory fact not found")
        before = _row_dict(before_row)
        row = conn.execute(
            "UPDATE vkpi_advisor_memory_facts "
            "SET status='deleted', deleted_at=NOW(), updated_at=NOW(), version=version+1 "
            "WHERE organization_id=? AND staff_id=? AND fact_uid=? AND deleted_at IS NULL "
            "RETURNING fact_uid, status, version, deleted_at",
            (*_scope_params(scope), _text(fact_uid, 80)),
        ).fetchone()
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="deleted",
            subject_type="fact",
            subject_uid=str(result["fact_uid"]),
            before=before,
            after=result,
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def list_action_drafts(
    scope: AdvisorScope,
    *,
    thread_uid: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_schema()
    params: list[Any] = [*_scope_params(scope)]
    thread_clause = ""
    if thread_uid:
        thread_clause = "AND thread_uid=?"
        params.append(_text(thread_uid, 80))
    params.append(_positive_limit(limit, default=100, maximum=500))
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_advisor_action_drafts "
        f"WHERE organization_id=? AND staff_id=? {thread_clause} "
        "ORDER BY id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    conn.commit()
    return [_row_dict(row) for row in rows]
