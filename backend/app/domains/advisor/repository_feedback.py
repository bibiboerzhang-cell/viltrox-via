"""Trusted, owner-scoped feedback for persisted Marketing Advisor messages.

Feedback is evidence, not training.  A correction can be proposed as a
personal-memory candidate, but it remains ``pending`` until the owner confirms
it through the existing memory API.  This module never creates facts, invokes a
model, starts a training job, or changes weights.
"""
from __future__ import annotations

from typing import Any

from app.domains.advisor.repository_support import (
    AdvisorConflict,
    AdvisorNotFound,
    AdvisorSchemaUnavailable,
    AdvisorValidationError,
    _bounded_json,
    _json_dumps,
    _new_uid,
    _row_dict,
    _scope_params,
    _sha256,
    _text,
    sanitize_context_refs,
    sanitize_provenance,
)
from app.domains.advisor.scope import AdvisorScope


_FEEDBACK_TABLE = "vkpi_advisor_message_feedback"


def _facade():
    # Preserve repository.get_conn/table_exists as the public dependency seam.
    from app.domains.advisor import repository

    return repository


def _ensure_feedback_schema() -> None:
    _facade()._ensure_schema()
    if not _facade().table_exists(_FEEDBACK_TABLE):
        raise AdvisorSchemaUnavailable(
            "migration 268_vkpi_advisor_trusted_feedback.sql is not applied"
        )


def _get_conn() -> Any:
    return _facade().get_conn()


def _feedback_event(
    conn: Any,
    scope: AdvisorScope,
    *,
    feedback: dict[str, Any],
    event_type: str,
    client_request_id: str,
    request_sha256: str,
    before: Any = None,
) -> None:
    conn.execute(
        "INSERT INTO vkpi_advisor_message_feedback_events "
        "(event_uid, feedback_uid, organization_id, staff_id, thread_uid, message_uid, "
        "actor_staff_id, event_type, client_request_id, request_sha256, before_sha256, "
        "after_sha256, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb)",
        (
            _new_uid("advfbevt"),
            feedback["feedback_uid"],
            *_scope_params(scope),
            feedback["thread_uid"],
            feedback["message_uid"],
            scope.staff_id,
            event_type,
            client_request_id,
            request_sha256,
            _sha256(before) if before is not None else "",
            _sha256(feedback),
            _json_dumps(
                {
                    "candidate_uid": feedback.get("candidate_uid") or "",
                    "activation": "requires_explicit_memory_confirmation",
                    "training_triggered": False,
                    "weights_changed": False,
                }
            ),
        ),
    )


def _candidate_for_feedback(
    conn: Any,
    scope: AdvisorScope,
    *,
    message_uid: str,
    rating: str,
    correction: str,
    provenance: dict[str, str],
    feedback_uid: str,
    existing_candidate_uid: str = "",
) -> dict[str, Any]:
    # Import lazily to avoid a repository facade cycle and reuse the existing
    # owner settings + memory audit primitives inside this transaction.
    from app.domains.advisor.repository_memory import _ensure_memory_settings, _memory_event

    settings = _row_dict(_ensure_memory_settings(conn, scope))
    if settings.get("state") != "active":
        raise AdvisorConflict("personal memory is paused")

    value = {
        "text": correction,
        "rating": rating,
        "feedback_uid": feedback_uid,
        "source": "advisor_message_feedback",
    }
    candidate_provenance = dict(provenance)
    if not any(candidate_provenance.values()):
        candidate_provenance["source_ref"] = f"advisor-message-feedback:{message_uid}"

    existing = None
    if existing_candidate_uid:
        existing = conn.execute(
            "SELECT * FROM vkpi_advisor_memory_candidates "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=? AND deleted_at IS NULL "
            "FOR UPDATE",
            (*_scope_params(scope), _text(existing_candidate_uid, 80)),
        ).fetchone()
    existing_item = _row_dict(existing) if existing is not None else {}
    if existing_item.get("status") == "pending":
        before = existing_item
        row = conn.execute(
            "UPDATE vkpi_advisor_memory_candidates "
            "SET summary=?, value_json=?::jsonb, provenance_json=?::jsonb "
            "WHERE organization_id=? AND staff_id=? AND candidate_uid=? "
            "AND status='pending' AND deleted_at IS NULL RETURNING *",
            (
                correction,
                _json_dumps(_bounded_json(value)),
                _json_dumps(candidate_provenance),
                *_scope_params(scope),
                existing_item["candidate_uid"],
            ),
        ).fetchone()
        result = _row_dict(row)
        _memory_event(
            conn,
            scope,
            event_type="edited",
            subject_type="candidate",
            subject_uid=result["candidate_uid"],
            before=before,
            after=result,
            detail={"source": "advisor_feedback", "activation": "pending"},
        )
        return result

    candidate_uid = _new_uid("advmemc")
    row = conn.execute(
        "INSERT INTO vkpi_advisor_memory_candidates "
        "(candidate_uid, organization_id, staff_id, source_message_uid, memory_kind, memory_key, "
        "summary, value_json, provenance_json, sensitivity, status) "
        "VALUES (?,?,?,?,?,?,?,?::jsonb,?::jsonb,'normal','pending') RETURNING *",
        (
            candidate_uid,
            *_scope_params(scope),
            message_uid,
            "semantic",
            _text(f"advisor-feedback:{message_uid}", 160),
            correction,
            _json_dumps(_bounded_json(value)),
            _json_dumps(candidate_provenance),
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
        detail={
            "source": "advisor_feedback",
            "activation": "requires_explicit_confirmation",
        },
    )
    return result


def submit_message_feedback(
    scope: AdvisorScope,
    thread_uid: str,
    message_uid: str,
    *,
    rating: str,
    correction_text: str = "",
    propose_memory: bool = False,
    context_refs: Any = None,
    provenance: Any = None,
    client_request_id: str = "",
) -> dict[str, Any]:
    """Create or revise one owner's feedback for an assistant message.

    ``client_request_id`` is payload-bound. An exact replay returns the prior
    result without another audit event or memory candidate. Reusing that id for
    another payload fails closed.
    """

    _ensure_feedback_schema()
    safe_thread_uid = _text(thread_uid, 80)
    safe_message_uid = _text(message_uid, 80)
    safe_rating = _text(rating, 20).lower()
    if safe_rating not in {"helpful", "unhelpful"}:
        raise AdvisorValidationError("rating must be helpful or unhelpful")
    safe_correction = _text(correction_text, 4000)
    if propose_memory and not safe_correction:
        raise AdvisorValidationError("correction_text is required when propose_memory is true")
    if propose_memory and safe_rating != "unhelpful":
        raise AdvisorValidationError("propose_memory is only valid for corrected unhelpful feedback")
    safe_provenance = sanitize_provenance(provenance)
    if not any(safe_provenance.values()):
        safe_provenance["source_ref"] = f"advisor-message-feedback:{safe_message_uid}"
    request_id = _text(client_request_id, 120)
    if not request_id:
        raise AdvisorValidationError("client_request_id is required for feedback idempotency")
    conn = _get_conn()
    try:
        thread = _facade()._thread_row(conn, scope, safe_thread_uid)
        if thread is None:
            raise AdvisorNotFound("thread not found")
        message = conn.execute(
            "SELECT * FROM vkpi_advisor_messages "
            "WHERE organization_id=? AND staff_id=? AND thread_uid=? AND message_uid=? "
            "AND deleted_at IS NULL FOR UPDATE",
            (*_scope_params(scope), safe_thread_uid, safe_message_uid),
        ).fetchone()
        if message is None:
            raise AdvisorNotFound("message not found")
        message_item = _row_dict(message)
        if str(message_item.get("role") or "") != "assistant":
            raise AdvisorValidationError("feedback is only accepted for assistant messages")

        # Feedback evidence must be bound to the assistant message's original
        # turn, never to whatever the UI picker happens to contain later.
        # ``context_refs`` stays accepted for API compatibility but is ignored.
        del context_refs
        safe_refs = sanitize_context_refs(message_item.get("context_refs_json"))
        payload = {
            "thread_uid": safe_thread_uid,
            "message_uid": safe_message_uid,
            "rating": safe_rating,
            "correction_text": safe_correction,
            "propose_memory": bool(propose_memory),
            "context_refs": safe_refs,
            "provenance": safe_provenance,
        }
        payload_sha256 = _sha256(payload)

        if request_id:
            replay_event = conn.execute(
                "SELECT * FROM vkpi_advisor_message_feedback_events "
                "WHERE organization_id=? AND staff_id=? AND client_request_id=? FOR UPDATE",
                (*_scope_params(scope), request_id),
            ).fetchone()
            if replay_event is not None:
                event = _row_dict(replay_event)
                if event.get("request_sha256") != payload_sha256:
                    raise AdvisorConflict("client_request_id was already used for a different feedback payload")
                replay_row = conn.execute(
                    "SELECT * FROM vkpi_advisor_message_feedback "
                    "WHERE organization_id=? AND staff_id=? AND feedback_uid=?",
                    (*_scope_params(scope), event["feedback_uid"]),
                ).fetchone()
                if replay_row is None:
                    raise AdvisorConflict("idempotent feedback receipt has no current feedback row")
                replay = _row_dict(replay_row)
                candidate = None
                if replay.get("candidate_uid"):
                    candidate_row = conn.execute(
                        "SELECT * FROM vkpi_advisor_memory_candidates "
                        "WHERE organization_id=? AND staff_id=? AND candidate_uid=? AND deleted_at IS NULL",
                        (*_scope_params(scope), replay["candidate_uid"]),
                    ).fetchone()
                    candidate = _row_dict(candidate_row) if candidate_row is not None else None
                conn.commit()
                return {
                    "feedback": replay,
                    "candidate": candidate,
                    "idempotent_replay": True,
                }

        before_row = conn.execute(
            "SELECT * FROM vkpi_advisor_message_feedback "
            "WHERE organization_id=? AND staff_id=? AND message_uid=? FOR UPDATE",
            (*_scope_params(scope), safe_message_uid),
        ).fetchone()
        before = _row_dict(before_row) if before_row is not None else None
        feedback_uid = str((before or {}).get("feedback_uid") or _new_uid("advfb"))
        candidate = None
        candidate_uid = str((before or {}).get("candidate_uid") or "")
        if propose_memory:
            candidate = _candidate_for_feedback(
                conn,
                scope,
                message_uid=safe_message_uid,
                rating=safe_rating,
                correction=safe_correction,
                provenance=safe_provenance,
                feedback_uid=feedback_uid,
                existing_candidate_uid=candidate_uid,
            )
            candidate_uid = str(candidate["candidate_uid"])
        elif candidate_uid:
            existing_candidate = conn.execute(
                "SELECT * FROM vkpi_advisor_memory_candidates "
                "WHERE organization_id=? AND staff_id=? AND candidate_uid=? "
                "AND deleted_at IS NULL FOR UPDATE",
                (*_scope_params(scope), candidate_uid),
            ).fetchone()
            existing_item = _row_dict(existing_candidate) if existing_candidate is not None else {}
            if existing_item.get("status") == "pending":
                from app.domains.advisor.repository_memory import _memory_event

                rejected_row = conn.execute(
                    "UPDATE vkpi_advisor_memory_candidates "
                    "SET status='rejected', reviewed_at=NOW() "
                    "WHERE organization_id=? AND staff_id=? AND candidate_uid=? "
                    "AND status='pending' AND deleted_at IS NULL RETURNING *",
                    (*_scope_params(scope), candidate_uid),
                ).fetchone()
                candidate = _row_dict(rejected_row)
                _memory_event(
                    conn,
                    scope,
                    event_type="rejected",
                    subject_type="candidate",
                    subject_uid=candidate_uid,
                    before=existing_item,
                    after=candidate,
                    detail={"source": "advisor_feedback_withdrawal"},
                )
                candidate_uid = ""

        if before is None:
            row = conn.execute(
                "INSERT INTO vkpi_advisor_message_feedback "
                "(feedback_uid, organization_id, staff_id, thread_uid, message_uid, rating, "
                "correction_text, propose_memory, context_refs_json, provenance_json, candidate_uid, "
                "last_client_request_id, payload_sha256) "
                "VALUES (?,?,?,?,?,?,?,?,?::jsonb,?::jsonb,?,?,?) RETURNING *",
                (
                    feedback_uid,
                    *_scope_params(scope),
                    safe_thread_uid,
                    safe_message_uid,
                    safe_rating,
                    safe_correction,
                    bool(propose_memory),
                    _json_dumps(safe_refs),
                    _json_dumps(safe_provenance),
                    candidate_uid or None,
                    request_id,
                    payload_sha256,
                ),
            ).fetchone()
            event_type = "created"
        else:
            row = conn.execute(
                "UPDATE vkpi_advisor_message_feedback SET rating=?, correction_text=?, "
                "propose_memory=?, context_refs_json=?::jsonb, provenance_json=?::jsonb, "
                "candidate_uid=?, last_client_request_id=?, payload_sha256=?, updated_at=NOW() "
                "WHERE organization_id=? AND staff_id=? AND message_uid=? RETURNING *",
                (
                    safe_rating,
                    safe_correction,
                    bool(propose_memory),
                    _json_dumps(safe_refs),
                    _json_dumps(safe_provenance),
                    candidate_uid or None,
                    request_id,
                    payload_sha256,
                    *_scope_params(scope),
                    safe_message_uid,
                ),
            ).fetchone()
            event_type = "updated"
        result = _row_dict(row)
        _feedback_event(
            conn,
            scope,
            feedback=result,
            event_type=event_type,
            client_request_id=request_id,
            request_sha256=payload_sha256,
            before=before,
        )
        conn.commit()
        return {
            "feedback": result,
            "candidate": candidate,
            "idempotent_replay": False,
        }
    except Exception:
        conn.rollback()
        raise


def attach_feedback_to_messages(
    conn: Any,
    scope: AdvisorScope,
    thread_uid: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach current feedback in one bounded query; never an N+1 loop."""

    if not messages or not _facade().table_exists(_FEEDBACK_TABLE):
        return messages
    rows = conn.execute(
        "SELECT * FROM vkpi_advisor_message_feedback "
        "WHERE organization_id=? AND staff_id=? AND thread_uid=? ORDER BY id ASC",
        (*_scope_params(scope), _text(thread_uid, 80)),
    ).fetchall()
    by_message = {
        item["message_uid"]: item
        for item in (_row_dict(row) for row in rows)
    }
    for message in messages:
        if message.get("message_uid") in by_message:
            message["feedback"] = by_message[message["message_uid"]]
    return messages
