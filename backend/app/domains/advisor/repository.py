"""Scoped persistence facade for Marketing Advisor conversations and memory."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.advisor.repository_support import (
    ALLOWED_ACTION_TYPES,
    ALLOWED_CONTEXT_TYPES,
    ALLOWED_MEMORY_KINDS,
    ALLOWED_SENSITIVITY,
    _CLAIM_TABLE,
    AdvisorConflict,
    AdvisorNotFound,
    AdvisorRepositoryError,
    AdvisorSchemaUnavailable,
    AdvisorValidationError,
    _as_utc_datetime,
    _bounded_json,
    _claim_token_hash,
    _json_dumps,
    _new_uid,
    _positive_limit,
    _request_sha256,
    _row_dict,
    _scope_params,
    _text,
    sanitize_action_drafts,
    sanitize_context_refs,
    sanitize_provenance,
)
from app.domains.advisor.scope import AdvisorScope


def _ensure_schema() -> None:
    """Use the facade's DB seam so tests and alternate runtimes can replace it."""

    if not table_exists("vkpi_advisor_threads"):
        raise AdvisorSchemaUnavailable(
            "migration 250_vkpi_marketing_advisor_memory.sql is not applied"
        )


def schema_ready() -> bool:
    try:
        return bool(table_exists("vkpi_advisor_threads"))
    except Exception:
        return False


def claim_schema_ready() -> bool:
    try:
        return bool(table_exists(_CLAIM_TABLE))
    except Exception:
        return False


def claim_turn_request(
    scope: AdvisorScope,
    thread_uid: str,
    client_request_id: str,
    *,
    request_sha256: str,
    lease_seconds: int = 180,
) -> dict[str, Any]:
    """Claim one exact request using short transactions and a durable CAS row.

    A lease may be reclaimed only while ``provider_attempted`` is false.  Once
    the pre-call marker is committed, an expired lease becomes
    ``outcome_unknown`` and is never replayed automatically.  This deliberately
    prefers a visible manual-reconciliation state over duplicate provider spend.
    """

    _ensure_schema()
    if not table_exists(_CLAIM_TABLE):
        raise AdvisorSchemaUnavailable(
            "migration 252_vkpi_advisor_turn_claims.sql is not applied"
        )
    safe_thread_uid = _text(thread_uid, 80)
    request_key = _text(client_request_id, 120)
    if not request_key:
        raise AdvisorValidationError("client_request_id is required for a durable claim")
    request_hash = _request_sha256(request_sha256)
    now = datetime.now(timezone.utc)
    lease = now + timedelta(seconds=max(30, min(int(lease_seconds or 180), 600)))
    token = secrets.token_urlsafe(32)
    token_hash = _claim_token_hash(token)
    conn = get_conn()
    try:
        if _thread_row(conn, scope, safe_thread_uid) is None:
            raise AdvisorNotFound("thread not found")
        inserted = conn.execute(
            "INSERT INTO vkpi_advisor_turn_claims "
            "(organization_id, staff_id, thread_uid, client_request_id, request_sha256, "
            "claim_token_sha256, state, provider_attempted, claimed_at, lease_expires_at, updated_at) "
            "VALUES (?,?,?,?,?,?,'claimed',FALSE,?,?,?) "
            "ON CONFLICT (organization_id, staff_id, thread_uid, client_request_id) DO NOTHING "
            "RETURNING state",
            (
                *_scope_params(scope),
                safe_thread_uid,
                request_key,
                request_hash,
                token_hash,
                now,
                lease,
                now,
            ),
        ).fetchone()
        if inserted is not None:
            conn.commit()
            return {
                "status": "acquired",
                "state": "claimed",
                "claim_token": token,
                "provider_attempted": False,
                "idempotent_replay": False,
            }

        row = conn.execute(
            "SELECT * FROM vkpi_advisor_turn_claims "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=? "
            "FOR UPDATE",
            (*_scope_params(scope), safe_thread_uid, request_key),
        ).fetchone()
        if row is None:
            raise AdvisorConflict("advisor turn claim disappeared")
        item = _row_dict(row)
        if str(item.get("request_sha256") or "") != request_hash:
            raise AdvisorConflict("client_request_id was already used for different content")

        replay = _idempotent_turn(conn, scope, safe_thread_uid, request_key)
        if replay is not None:
            assistant_uid = ""
            user_uid = ""
            for message in replay.get("messages") or []:
                if message.get("role") == "user" and not user_uid:
                    user_uid = str(message.get("message_uid") or "")
                if message.get("role") == "assistant":
                    assistant_uid = str(message.get("message_uid") or "")
            conn.execute(
                "UPDATE vkpi_advisor_turn_claims SET state='completed', completed_at=COALESCE(completed_at,?), "
                "result_user_message_uid=?, result_assistant_message_uid=?, updated_at=? "
                "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=?",
                (
                    now,
                    user_uid or None,
                    assistant_uid or None,
                    now,
                    *_scope_params(scope),
                    safe_thread_uid,
                    request_key,
                ),
            )
            conn.commit()
            return {
                "status": "replay",
                "state": "completed",
                "provider_attempted": bool(item.get("provider_attempted")),
                "idempotent_replay": True,
                "replay": replay,
            }

        state = str(item.get("state") or "")
        attempted = bool(item.get("provider_attempted"))
        expired = (_as_utc_datetime(item.get("lease_expires_at")) or now) <= now
        if state == "claimed" and not attempted and expired:
            updated = conn.execute(
                "UPDATE vkpi_advisor_turn_claims SET claim_token_sha256=?, claimed_at=?, "
                "lease_expires_at=?, failure_code='', updated_at=? "
                "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=? "
                "AND state='claimed' AND provider_attempted=FALSE RETURNING state",
                (
                    token_hash,
                    now,
                    lease,
                    now,
                    *_scope_params(scope),
                    safe_thread_uid,
                    request_key,
                ),
            ).fetchone()
            if updated is not None:
                conn.commit()
                return {
                    "status": "acquired",
                    "state": "claimed",
                    "claim_token": token,
                    "provider_attempted": False,
                    "idempotent_replay": False,
                    "lease_reclaimed_before_provider": True,
                }
        if attempted and state in {"claimed", "provider_started"} and expired:
            conn.execute(
                "UPDATE vkpi_advisor_turn_claims SET state='outcome_unknown', "
                "failure_code='provider_outcome_unknown_after_lease_expiry', updated_at=? "
                "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=?",
                (now, *_scope_params(scope), safe_thread_uid, request_key),
            )
            state = "outcome_unknown"
        conn.commit()
        return {
            "status": "blocked" if state in {"outcome_unknown", "failed_before_provider"} else "in_progress",
            "state": state,
            "provider_attempted": attempted,
            "idempotent_replay": False,
            "reason": (
                "provider_outcome_unknown_manual_reconciliation_required"
                if state == "outcome_unknown"
                else "request_failed_before_provider_new_request_id_required"
                if state == "failed_before_provider"
                else "request_in_progress"
            ),
        }
    except Exception:
        conn.rollback()
        raise


def mark_turn_provider_started(
    scope: AdvisorScope,
    thread_uid: str,
    client_request_id: str,
    claim_token: str,
    *,
    provider_binding: str,
    lease_seconds: int = 180,
) -> dict[str, Any]:
    """Commit the irreversible pre-call boundary before any HTTP request."""

    now = datetime.now(timezone.utc)
    lease = now + timedelta(seconds=max(30, min(int(lease_seconds or 180), 600)))
    conn = get_conn()
    try:
        row = conn.execute(
            "UPDATE vkpi_advisor_turn_claims SET state='provider_started', provider_attempted=TRUE, "
            "provider_binding=?, provider_started_at=?, lease_expires_at=?, updated_at=? "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=? "
            "AND claim_token_sha256=? AND state='claimed' AND provider_attempted=FALSE "
            "AND lease_expires_at>? RETURNING state, provider_started_at",
            (
                _text(provider_binding, 160),
                now,
                lease,
                now,
                *_scope_params(scope),
                _text(thread_uid, 80),
                _text(client_request_id, 120),
                _claim_token_hash(claim_token),
                now,
            ),
        ).fetchone()
        if row is None:
            raise AdvisorConflict("advisor turn claim is not eligible for provider start")
        conn.commit()
        return _row_dict(row)
    except Exception:
        conn.rollback()
        raise


def mark_turn_outcome_unknown(
    scope: AdvisorScope,
    thread_uid: str,
    client_request_id: str,
    claim_token: str,
    *,
    failure_code: str,
) -> None:
    """Persist an auditable no-replay state after an uncertain provider call."""

    conn = get_conn()
    try:
        row = conn.execute(
            "UPDATE vkpi_advisor_turn_claims SET state='outcome_unknown', failure_code=?, updated_at=NOW() "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=? "
            "AND claim_token_sha256=? AND provider_attempted=TRUE AND state='provider_started' "
            "RETURNING state",
            (
                _text(failure_code, 120) or "provider_outcome_unknown",
                *_scope_params(scope),
                _text(thread_uid, 80),
                _text(client_request_id, 120),
                _claim_token_hash(claim_token),
            ),
        ).fetchone()
        if row is None:
            raise AdvisorConflict("advisor turn claim outcome CAS failed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _thread_row(conn: Any, scope: AdvisorScope, thread_uid: str, *, include_deleted: bool = False) -> Any:
    deleted_clause = "" if include_deleted else "AND deleted_at IS NULL"
    return conn.execute(
        f"SELECT * FROM vkpi_advisor_threads "
        f"WHERE organization_id=? AND staff_id=? AND thread_uid=? {deleted_clause}",
        (*_scope_params(scope), _text(thread_uid, 80)),
    ).fetchone()


def create_thread(
    scope: AdvisorScope,
    *,
    title: str = "",
    context_refs: Any = None,
) -> dict[str, Any]:
    _ensure_schema()
    safe_refs = sanitize_context_refs(context_refs)
    thread_uid = _new_uid("advthr")
    conn = get_conn()
    row = conn.execute(
        "INSERT INTO vkpi_advisor_threads "
        "(thread_uid, organization_id, staff_id, title, context_refs_json) "
        "VALUES (?,?,?,?,?::jsonb) RETURNING *",
        (thread_uid, *_scope_params(scope), _text(title, 240), _json_dumps(safe_refs)),
    ).fetchone()
    conn.commit()
    return _row_dict(row)


def list_threads(scope: AdvisorScope, *, limit: int = 50) -> list[dict[str, Any]]:
    _ensure_schema()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_advisor_threads "
        "WHERE organization_id=? AND staff_id=? AND deleted_at IS NULL "
        "ORDER BY COALESCE(last_message_at, updated_at) DESC, id DESC LIMIT ?",
        (*_scope_params(scope), _positive_limit(limit)),
    ).fetchall()
    conn.commit()
    return [_row_dict(row) for row in rows]


def get_thread(scope: AdvisorScope, thread_uid: str) -> dict[str, Any]:
    _ensure_schema()
    conn = get_conn()
    row = _thread_row(conn, scope, thread_uid)
    conn.commit()
    if row is None:
        raise AdvisorNotFound("thread not found")
    return _row_dict(row)


def update_thread(
    scope: AdvisorScope,
    thread_uid: str,
    *,
    title: str | None = None,
    status: str | None = None,
    context_refs: Any = None,
    context_refs_present: bool = False,
) -> dict[str, Any]:
    _ensure_schema()
    assignments: list[str] = []
    params: list[Any] = []
    if title is not None:
        assignments.append("title=?")
        params.append(_text(title, 240))
    if status is not None:
        normalized_status = _text(status, 20).lower()
        if normalized_status not in {"active", "archived"}:
            raise AdvisorValidationError("thread status must be active or archived")
        assignments.append("status=?")
        params.append(normalized_status)
        assignments.append("archived_at=CASE WHEN ?='archived' THEN NOW() ELSE NULL END")
        params.append(normalized_status)
    if context_refs_present:
        assignments.append("context_refs_json=?::jsonb")
        params.append(_json_dumps(sanitize_context_refs(context_refs)))
    if not assignments:
        return get_thread(scope, thread_uid)
    assignments.append("updated_at=NOW()")
    conn = get_conn()
    row = conn.execute(
        f"UPDATE vkpi_advisor_threads SET {', '.join(assignments)} "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND deleted_at IS NULL "
        "RETURNING *",
        (*params, *_scope_params(scope), _text(thread_uid, 80)),
    ).fetchone()
    conn.commit()
    if row is None:
        raise AdvisorNotFound("thread not found")
    return _row_dict(row)


def delete_thread(scope: AdvisorScope, thread_uid: str) -> dict[str, Any]:
    _ensure_schema()
    conn = get_conn()
    row = conn.execute(
        "UPDATE vkpi_advisor_threads "
        "SET status='deleted', deleted_at=NOW(), updated_at=NOW() "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND deleted_at IS NULL "
        "RETURNING thread_uid, status, deleted_at",
        (*_scope_params(scope), _text(thread_uid, 80)),
    ).fetchone()
    conn.commit()
    if row is None:
        raise AdvisorNotFound("thread not found")
    return _row_dict(row)


def list_messages(
    scope: AdvisorScope,
    thread_uid: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_schema()
    conn = get_conn()
    if _thread_row(conn, scope, thread_uid) is None:
        conn.commit()
        raise AdvisorNotFound("thread not found")
    rows = conn.execute(
        "SELECT * FROM ("
        "SELECT * FROM vkpi_advisor_messages "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND deleted_at IS NULL "
        "ORDER BY id DESC LIMIT ?"
        ") AS latest_messages ORDER BY id ASC",
        (*_scope_params(scope), _text(thread_uid, 80), _positive_limit(limit, default=100, maximum=500)),
    ).fetchall()
    conn.commit()
    return [_row_dict(row) for row in rows]


def _idempotent_turn(
    conn: Any,
    scope: AdvisorScope,
    thread_uid: str,
    client_request_id: str,
) -> dict[str, Any] | None:
    if not client_request_id:
        return None
    rows = conn.execute(
        "SELECT * FROM vkpi_advisor_messages "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? "
        "AND client_request_id=? AND deleted_at IS NULL ORDER BY id ASC",
        (*_scope_params(scope), thread_uid, client_request_id),
    ).fetchall()
    if not rows:
        return None
    drafts = conn.execute(
        "SELECT * FROM vkpi_advisor_action_drafts "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? "
        "AND source_message_uid=? ORDER BY id ASC",
        (*_scope_params(scope), thread_uid, str(dict(rows[0]).get("message_uid") or "")),
    ).fetchall()
    return {
        "messages": [_row_dict(row) for row in rows],
        "draft_actions": [_row_dict(row) for row in drafts],
        "idempotent_replay": True,
    }


def get_idempotent_turn(
    scope: AdvisorScope,
    thread_uid: str,
    client_request_id: str,
) -> dict[str, Any] | None:
    """Return one completed owner-scoped replay without provider side effects."""

    _ensure_schema()
    request_key = _text(client_request_id, 120)
    if not request_key:
        return None
    safe_thread_uid = _text(thread_uid, 80)
    conn = get_conn()
    if _thread_row(conn, scope, safe_thread_uid) is None:
        conn.commit()
        raise AdvisorNotFound("thread not found")
    replay = _idempotent_turn(conn, scope, safe_thread_uid, request_key)
    conn.commit()
    return replay


def create_degraded_turn(
    scope: AdvisorScope,
    thread_uid: str,
    *,
    content_text: str,
    context_refs: Any,
    requested_actions: Any,
    client_request_id: str = "",
    provider_reason: str = "advisor_provider_not_connected",
    assistant_content: str | None = None,
    assistant_provenance: Any = None,
    assistant_metadata: Any = None,
    assistant_status: str = "degraded",
    provider_status: str = "unavailable",
    provider_called: bool = False,
    memory_used: bool = False,
    claim_token: str = "",
) -> dict[str, Any]:
    """Persist one idempotent user/assistant turn and draft-only actions.

    This repository function has no provider import or call. Provider execution
    happens before this boundary under the durable claim CAS; the explicit
    status fields merely persist its result. Every requested external,
    business-writing or cost-bearing action remains a non-executable draft.
    """

    _ensure_schema()
    content = _text(content_text, 20_000)
    if not content:
        raise AdvisorValidationError("message content is required")
    safe_refs = sanitize_context_refs(context_refs)
    safe_actions = sanitize_action_drafts(requested_actions)
    request_key = _text(client_request_id, 120)
    safe_thread_uid = _text(thread_uid, 80)
    safe_assistant_status = _text(assistant_status, 20).lower()
    if safe_assistant_status not in {"ready", "degraded", "failed"}:
        raise AdvisorValidationError("unsupported assistant status")
    safe_provider_status = _text(provider_status, 40).lower()
    if safe_provider_status not in {
        "ready",
        "unavailable",
        "blocked",
        "failed",
        "not_requested",
    }:
        raise AdvisorValidationError("unsupported provider status")
    conn = get_conn()
    try:
        if _thread_row(conn, scope, safe_thread_uid) is None:
            raise AdvisorNotFound("thread not found")
        replay = _idempotent_turn(conn, scope, safe_thread_uid, request_key)
        if replay is not None:
            conn.commit()
            return replay

        user_uid = _new_uid("advmsg")
        assistant_uid = _new_uid("advmsg")
        reply = _text(assistant_content, 20_000) or (
            "问题和上下文已安全保存。Marketing Advisor 的模型通道当前未连接，"
            "本次没有调用外部模型，也没有执行发送、业务写入或费用动作。"
        )
        safe_assistant_provenance = _bounded_json(assistant_provenance or {})
        safe_assistant_metadata = _bounded_json(assistant_metadata or {})
        user_row = conn.execute(
            "INSERT INTO vkpi_advisor_messages "
            "(message_uid, organization_id, staff_id, thread_uid, role, content_text, status, "
            "provider_status, context_refs_json, provenance_json, metadata_json, client_request_id) "
            "VALUES (?,?,?,?,? ,?,?,?,?::jsonb,?::jsonb,?::jsonb,?) RETURNING *",
            (
                user_uid,
                *_scope_params(scope),
                safe_thread_uid,
                "user",
                content,
                "ready",
                "not_requested",
                _json_dumps(safe_refs),
                _json_dumps({"capture": "explicit_user_input"}),
                _json_dumps({"provider_called": bool(provider_called)}),
                request_key,
            ),
        ).fetchone()
        assistant_row = conn.execute(
            "INSERT INTO vkpi_advisor_messages "
            "(message_uid, organization_id, staff_id, thread_uid, role, content_text, status, "
            "provider_status, provider_reason, context_refs_json, provenance_json, metadata_json, "
            "client_request_id) "
            "VALUES (?,?,?,?,? ,?,?,?,?,?::jsonb,?::jsonb,?::jsonb,?) RETURNING *",
            (
                assistant_uid,
                *_scope_params(scope),
                safe_thread_uid,
                "assistant",
                reply,
                safe_assistant_status,
                safe_provider_status,
                _text(provider_reason, 160),
                _json_dumps(safe_refs),
                _json_dumps(
                    {
                        "source_message_uid": user_uid,
                        **(
                            safe_assistant_provenance
                            if isinstance(safe_assistant_provenance, dict)
                            else {"bridge_provenance": safe_assistant_provenance}
                        ),
                    }
                ),
                _json_dumps(
                    {
                        "provider_called": bool(provider_called),
                        "action_mode": "draft_only",
                        "memory_used": bool(memory_used),
                        **(
                            safe_assistant_metadata
                            if isinstance(safe_assistant_metadata, dict)
                            else {"bridge_metadata": safe_assistant_metadata}
                        ),
                    }
                ),
                request_key,
            ),
        ).fetchone()
        draft_rows: list[dict[str, Any]] = []
        for action in safe_actions:
            draft_uid = _new_uid("advdraft")
            draft_row = conn.execute(
                "INSERT INTO vkpi_advisor_action_drafts "
                "(draft_uid, organization_id, staff_id, thread_uid, source_message_uid, action_type, "
                "target_type, target_id, estimated_cost_cents, writes_business_data, payload_json, "
                "provenance_json, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?::jsonb,?::jsonb,'draft') RETURNING *",
                (
                    draft_uid,
                    *_scope_params(scope),
                    safe_thread_uid,
                    user_uid,
                    action["action_type"],
                    action["target_type"],
                    action["target_id"],
                    action["estimated_cost_cents"],
                    action["writes_business_data"],
                    _json_dumps(action["payload"]),
                    _json_dumps(action["provenance"]),
                ),
            ).fetchone()
            draft_rows.append(_row_dict(draft_row))
        conn.execute(
            "UPDATE vkpi_advisor_threads SET last_message_at=NOW(), updated_at=NOW() "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=?",
            (*_scope_params(scope), safe_thread_uid),
        )
        if claim_token:
            expected_state = "provider_started" if provider_called else "claimed"
            completed_claim = conn.execute(
                "UPDATE vkpi_advisor_turn_claims SET state='completed', completed_at=NOW(), "
                "failure_code=?, result_user_message_uid=?, result_assistant_message_uid=?, updated_at=NOW() "
                "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND client_request_id=? "
                "AND claim_token_sha256=? AND state=? AND provider_attempted=? RETURNING state",
                (
                    "" if safe_provider_status == "ready" else _text(provider_reason, 120),
                    user_uid,
                    assistant_uid,
                    *_scope_params(scope),
                    safe_thread_uid,
                    request_key,
                    _claim_token_hash(claim_token),
                    expected_state,
                    bool(provider_called),
                ),
            ).fetchone()
            if completed_claim is None:
                raise AdvisorConflict("advisor turn claim completion CAS failed")
        conn.commit()
        return {
            "messages": [_row_dict(user_row), _row_dict(assistant_row)],
            "draft_actions": draft_rows,
            "idempotent_replay": False,
        }
    except Exception:
        conn.rollback()
        if request_key:
            replay = _idempotent_turn(conn, scope, safe_thread_uid, request_key)
            if replay is not None:
                conn.commit()
                return replay
        raise


# Keep the public repository API stable while the memory persistence lives in a
# focused module.  Callers continue importing ``app.domains.advisor.repository``.
from app.domains.advisor.repository_memory import (  # noqa: E402
    confirm_memory_candidate,
    create_memory_candidate,
    delete_memory_candidate,
    delete_memory_fact,
    get_memory,
    list_action_drafts,
    reject_memory_candidate,
    update_memory_fact,
    update_memory_settings,
)
