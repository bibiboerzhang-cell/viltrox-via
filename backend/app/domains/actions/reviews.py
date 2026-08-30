"""Human verification for completed Action Inbox executions.

Execution and verification are different facts.  Executors record what they
attempted and observed; a manager must attach evidence before the result counts
as verified agent evidence.  The action checklist, audit ledger, and event
provenance are committed atomically.  No provider, LLM, or business adapter is
called from this module.
"""
from __future__ import annotations

import json
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.platform import event_ledger, review_contract

_ACTIONS = "vkpi_action_inbox"
_EXECUTIONS = "vkpi_action_execution_ledger"
_TOOL_RUNS = "vkpi_agent_tool_run"
_EVENTS = "vkpi_event_ledger"
_ENDPOINT = "manual:verify-result"

logger = get_logger(__name__)


@dataclass(frozen=True)
class _VerificationRequest:
    decision: str
    reason: str
    evidence: list[dict[str, Any]]
    correlation_id: str
    execution_ledger_id: int
    detail_sha256: str
    candidate_sha256: str
    actor_staff_id: int
    organization_id: int


@dataclass(frozen=True)
class _ExecutionCandidate:
    receipt: dict[str, Any]
    detail_sha256: str
    candidate_sha256: str
    tool_run_ids: list[int]
    execution_effect: str


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _safe_detail(value: Any) -> tuple[dict[str, Any], str]:
    snapshot = review_contract.redact_review_snapshot(_loads(value))
    safe = snapshot if isinstance(snapshot, dict) else {}
    return safe, review_contract.review_snapshot_sha256(safe)


def _candidate_envelope(
    action_id: int,
    action: dict[str, Any],
    receipt: dict[str, Any],
    *,
    detail: dict[str, Any],
    detail_sha256: str,
    tool_run_ids: list[int],
) -> tuple[dict[str, Any], str, str]:
    """Bind everything visible to the reviewer into one canonical receipt."""
    verification_plan = [
        str(item) for item in _list(action.get("verification_plan_json"))
        if str(item).strip()
    ][:20]
    envelope = {
        "action_id": int(action_id),
        "execution_ledger_id": int(receipt["id"]),
        "execution_created_at": str(receipt.get("created_at") or ""),
        "endpoint": str(receipt.get("endpoint") or ""),
        "outcome": str(receipt.get("outcome") or ""),
        "detail_json": detail,
        "detail_sha256": detail_sha256,
        "tool_run_ids": [int(value) for value in tool_run_ids],
        "verification_plan": verification_plan,
    }
    canonical = review_contract.canonical_review_json(envelope)
    return envelope, canonical, review_contract.review_snapshot_sha256(envelope)


def get_action_review_candidate(action_id: int) -> dict[str, Any]:
    """Return the exact redacted execution receipt a manager may review."""
    if not all(table_exists(name) for name in (_ACTIONS, _EXECUTIONS)):
        return {"ok": False, "reason": "verification_ledger_unavailable"}
    conn = get_conn()
    action = conn.execute(
        f"SELECT id,status,verification_plan_json,result_checklist_json FROM {_ACTIONS} WHERE id=?",
        (int(action_id),),
    ).fetchone()
    if action is None:
        return {"ok": False, "reason": "action_not_found"}
    action_row = dict(action)
    if str(action_row.get("status") or "") != "executed":
        return {"ok": False, "reason": "action_not_awaiting_result_verification"}
    if isinstance(_loads(action_row.get("result_checklist_json")).get("human_verification"), dict):
        return {"ok": False, "reason": "action_result_already_verified"}
    receipt = conn.execute(
        f"""
        SELECT id, endpoint, outcome, detail_json, created_at
        FROM {_EXECUTIONS}
        WHERE action_id=? AND mode='executed' AND outcome='success' AND endpoint<>?
        ORDER BY id DESC LIMIT 1
        """,
        (int(action_id), _ENDPOINT),
    ).fetchone()
    if receipt is None:
        return {"ok": False, "reason": "successful_execution_receipt_required"}
    receipt_row = dict(receipt)
    detail, detail_sha256 = _safe_detail(receipt_row.get("detail_json"))
    if not detail:
        return {"ok": False, "reason": "execution_receipt_not_reviewable"}
    tool_run_ids: list[int] = []
    if table_exists(_TOOL_RUNS):
        rows = conn.execute(
            f"SELECT id,inputs_json FROM {_TOOL_RUNS} WHERE output_ref=? AND status='executed' ORDER BY id",
            (f"action:{int(action_id)}",),
        ).fetchall()
        tool_run_ids = [
            int(dict(row)["id"])
            for row in rows
            if int(_loads(dict(row).get("inputs_json")).get("execution_ledger_id") or 0)
            == int(receipt_row["id"])
        ]
    if len(tool_run_ids) > 1:
        return {"ok": False, "reason": "ambiguous_agent_tool_run_receipts"}
    envelope, candidate_canonical, candidate_sha256 = _candidate_envelope(
        action_id,
        action_row,
        receipt_row,
        detail=detail,
        detail_sha256=detail_sha256,
        tool_run_ids=tool_run_ids,
    )
    return {
        "ok": True,
        **envelope,
        "detail_json": detail,
        "detail_json_canonical": review_contract.canonical_review_json(detail),
        "detail_sha256": detail_sha256,
        "candidate_canonical_json": candidate_canonical,
        "candidate_sha256": candidate_sha256,
    }


def _normalize_verification_request(
    staff: dict[str, Any] | None,
    *,
    decision: str,
    reason: str,
    evidence: list[dict[str, Any]],
    correlation_id: str,
    expected_execution_ledger_id: int,
    expected_detail_sha256: str,
    expected_candidate_sha256: str,
) -> tuple[_VerificationRequest | None, dict[str, Any] | None]:
    normalized_decision = str(decision or "").strip().lower()
    normalized_reason = review_contract.normalize_review_text(reason, max_length=500)
    correlation = review_contract.normalize_correlation(correlation_id)
    evidence_rows = review_contract.normalize_evidence(evidence)
    try:
        expected_ledger_id = int(expected_execution_ledger_id)
    except (TypeError, ValueError):
        expected_ledger_id = 0
    expected_hash = str(expected_detail_sha256 or "").strip().lower()
    expected_candidate_hash = str(expected_candidate_sha256 or "").strip().lower()
    reviewer = review_contract.reviewer_context(staff)
    if normalized_decision not in {"accepted", "rejected"}:
        return None, {"ok": False, "reason": "invalid_verification_decision"}
    if normalized_reason is None:
        return None, {"ok": False, "reason": "verification_reason_required"}
    if evidence_rows is None:
        return None, {"ok": False, "reason": "verification_evidence_required"}
    if correlation is None:
        return None, {"ok": False, "reason": "verification_correlation_required"}
    if expected_ledger_id <= 0 or any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in (expected_hash, expected_candidate_hash)
    ):
        return None, {"ok": False, "reason": "verification_candidate_required"}
    if reviewer is None:
        return None, {"ok": False, "reason": "verification_scope_unavailable"}
    actor_id, organization_id = reviewer
    return (
        _VerificationRequest(
            decision=normalized_decision,
            reason=normalized_reason,
            evidence=evidence_rows,
            correlation_id=correlation,
            execution_ledger_id=expected_ledger_id,
            detail_sha256=expected_hash,
            candidate_sha256=expected_candidate_hash,
            actor_staff_id=actor_id,
            organization_id=organization_id,
        ),
        None,
    )


def _rollback_result(conn: Any, result: dict[str, Any]) -> dict[str, Any]:
    conn.rollback()
    return result


def _load_action_for_verification(
    conn: Any,
    action_id: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    lock_clause = " FOR UPDATE" if is_postgres_runtime() else ""
    row = conn.execute(
        f"""
        SELECT id, category, dedupe_key, suggested_endpoint, status,
               result_checklist_json, verification_plan_json
        FROM {_ACTIONS}
        WHERE id = ?{lock_clause}
        """,
        (int(action_id),),
    ).fetchone()
    if row is None:
        return None, _rollback_result(conn, {"ok": False, "reason": "action_not_found"})
    return dict(row), None


def _audit_matches_request(detail: dict[str, Any], request: _VerificationRequest) -> bool:
    return (
        str(detail.get("decision") or "") == request.decision
        and str(detail.get("reason") or "") == request.reason
        and detail.get("evidence") == request.evidence
        and int(detail.get("actor_staff_id") or 0) == request.actor_staff_id
        and int(detail.get("execution_ledger_id") or 0) == request.execution_ledger_id
        and str(detail.get("execution_detail_sha256") or "") == request.detail_sha256
        and str(detail.get("execution_candidate_sha256") or "") == request.candidate_sha256
    )


def _idempotent_tool_run_ids(detail: dict[str, Any]) -> list[int]:
    return [
        int(value)
        for value in detail.get("tool_run_ids", [])
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]


def _load_prior_verification(
    conn: Any,
    action_id: int,
    request: _VerificationRequest,
) -> tuple[list[Any], dict[str, Any] | None]:
    audits = conn.execute(
        f"""
        SELECT id, detail_json
        FROM {_EXECUTIONS}
        WHERE action_id = ? AND endpoint = ?
        ORDER BY id DESC
        """,
        (int(action_id), _ENDPOINT),
    ).fetchall()
    for audit in audits:
        audit_row = dict(audit)
        detail = _loads(audit_row.get("detail_json"))
        if str(detail.get("correlation_id") or "") != request.correlation_id:
            continue
        conn.rollback()
        if not _audit_matches_request(detail, request):
            return audits, {"ok": False, "reason": "verification_correlation_conflict"}
        return audits, {
            "ok": True,
            "action_id": int(action_id),
            "decision": request.decision,
            "ledger_id": int(audit_row["id"]),
            "tool_run_ids": _idempotent_tool_run_ids(detail),
            "correlation_id": request.correlation_id,
            "idempotent": True,
        }
    return audits, None


def _validate_action_state(
    conn: Any,
    action: dict[str, Any],
    audits: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if str(action.get("status") or "") != "executed":
        return None, _rollback_result(
            conn,
            {
                "ok": False,
                "reason": "action_not_awaiting_result_verification",
                "status": str(action.get("status") or ""),
            },
        )
    checklist = _loads(action.get("result_checklist_json"))
    if isinstance(checklist.get("human_verification"), dict) or audits:
        return None, _rollback_result(
            conn,
            {"ok": False, "reason": "action_result_already_verified"},
        )
    return checklist, None


def _matched_tool_runs(conn: Any, action_id: int, receipt_id: int) -> list[dict[str, Any]]:
    tool_rows = conn.execute(
        f"SELECT id, inputs_json FROM {_TOOL_RUNS} "
        "WHERE output_ref = ? AND status = 'executed' ORDER BY id",
        (f"action:{int(action_id)}",),
    ).fetchall()
    return [
        dict(tool_row)
        for tool_row in tool_rows
        if int(_loads(dict(tool_row).get("inputs_json")).get("execution_ledger_id") or 0)
        == receipt_id
    ]


def _load_execution_candidate(
    conn: Any,
    action_id: int,
    action: dict[str, Any],
    request: _VerificationRequest,
) -> tuple[_ExecutionCandidate | None, dict[str, Any] | None]:
    success_row = conn.execute(
        f"""
        SELECT id, endpoint, outcome, detail_json, created_at
        FROM {_EXECUTIONS}
        WHERE id = ? AND action_id = ?
          AND mode = 'executed'
          AND outcome = 'success'
          AND endpoint <> ?
        """,
        (request.execution_ledger_id, int(action_id), _ENDPOINT),
    ).fetchone()
    if success_row is None:
        return None, _rollback_result(
            conn,
            {"ok": False, "reason": "successful_execution_receipt_required"},
        )
    success_receipt = dict(success_row)
    visible_detail, current_hash = _safe_detail(success_receipt.get("detail_json"))
    if not hmac.compare_digest(current_hash, request.detail_sha256):
        return None, _rollback_result(
            conn,
            {"ok": False, "reason": "verification_candidate_changed"},
        )

    tool_run_ids: list[int] = []
    execution_effect = "none"
    if table_exists(_TOOL_RUNS):
        matched = _matched_tool_runs(conn, action_id, int(success_receipt["id"]))
        if len(matched) > 1:
            return None, _rollback_result(
                conn,
                {"ok": False, "reason": "ambiguous_agent_tool_run_receipts"},
            )
        tool_run_ids = [int(tool_row["id"]) for tool_row in matched]
        if matched:
            execution_effect = str(
                _loads(matched[0].get("inputs_json")).get("execution_effect") or "none"
            )

    _envelope, _candidate_json, current_candidate_hash = _candidate_envelope(
        action_id,
        action,
        success_receipt,
        detail=visible_detail,
        detail_sha256=current_hash,
        tool_run_ids=tool_run_ids,
    )
    if not hmac.compare_digest(current_candidate_hash, request.candidate_sha256):
        return None, _rollback_result(
            conn,
            {"ok": False, "reason": "verification_candidate_changed"},
        )
    return (
        _ExecutionCandidate(
            receipt=success_receipt,
            detail_sha256=current_hash,
            candidate_sha256=current_candidate_hash,
            tool_run_ids=tool_run_ids,
            execution_effect=execution_effect,
        ),
        None,
    )


def _insert_review_events(
    conn: Any,
    action_id: int,
    request: _VerificationRequest,
    candidate: _ExecutionCandidate,
    audit_detail: dict[str, Any],
) -> None:
    trace_id = event_ledger.new_trace_id("action", action_id)
    event_ledger.insert_required(
        conn,
        "action_result_accepted" if request.decision == "accepted" else "action_result_rejected",
        entity_type="action",
        entity_id=action_id,
        actor_type="staff",
        actor_id=request.actor_staff_id,
        source="action_inbox.human_verification",
        payload=audit_detail,
        trace_id=trace_id,
        provenance={
            "kind": "human_review",
            "evidence_count": len(request.evidence),
            "execution_ledger_id": int(candidate.receipt["id"]),
            "execution_effect": candidate.execution_effect,
            "evidence_verification": "staff_attestation_bound_to_execution_ledger",
        },
        organization_id=request.organization_id,
    )
    if table_exists(_TOOL_RUNS):
        for tool_run_id in candidate.tool_run_ids:
            event_ledger.insert_required(
                conn,
                "agent_tool_run_accepted"
                if request.decision == "accepted"
                else "agent_tool_run_rejected",
                entity_type="agent_tool_run",
                entity_id=tool_run_id,
                actor_type="staff",
                actor_id=request.actor_staff_id,
                source="action_inbox.human_verification",
                payload={
                    "action_id": int(action_id),
                    "decision": request.decision,
                    "correlation_id": request.correlation_id,
                    "evidence_count": len(request.evidence),
                },
                trace_id=trace_id,
                provenance={
                    "kind": "human_review",
                    "action_id": int(action_id),
                    "execution_ledger_id": int(candidate.receipt["id"]),
                    "execution_effect": candidate.execution_effect,
                    "evidence_verification": "staff_attestation_bound_to_execution_ledger",
                },
                organization_id=request.organization_id,
            )


def _persist_verification(
    conn: Any,
    action_id: int,
    action: dict[str, Any],
    checklist: dict[str, Any],
    request: _VerificationRequest,
    candidate: _ExecutionCandidate,
) -> dict[str, Any]:
    verified_at = datetime.now(timezone.utc).isoformat()
    verification = {
        "decision": request.decision,
        "reason": request.reason,
        "correlation_id": request.correlation_id,
        "evidence_count": len(request.evidence),
        "actor_staff_id": request.actor_staff_id,
        "verified_at": verified_at,
    }
    merged = {**checklist, "human_verification": verification}
    cursor = conn.execute(
        f"""
        UPDATE {_ACTIONS}
        SET result_checklist_json = {_json_param()}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'executed'
        """,
        (_dumps(merged), int(action_id)),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        return _rollback_result(
            conn,
            {"ok": False, "reason": "action_verification_state_changed"},
        )

    audit_detail = {
        "kind": "human_result_verification",
        "decision": request.decision,
        "reason": request.reason,
        "evidence": request.evidence,
        "correlation_id": request.correlation_id,
        "actor_staff_id": request.actor_staff_id,
        "verified_at": verified_at,
        "execution_ledger_id": int(candidate.receipt["id"]),
        "execution_detail_sha256": candidate.detail_sha256,
        "execution_candidate_sha256": candidate.candidate_sha256,
        "tool_run_ids": candidate.tool_run_ids,
        "execution_effect": candidate.execution_effect,
    }
    ledger = conn.execute(
        f"""
        INSERT INTO {_EXECUTIONS}
          (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
           endpoint, cost_cents, error, detail_json, created_at)
        VALUES (?,?,?,?,'executed',?,?,0,?,{_json_param()},CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (
            int(action_id),
            str(action.get("category") or ""),
            str(action.get("dedupe_key") or ""),
            request.actor_staff_id,
            "success" if request.decision == "accepted" else "failed",
            _ENDPOINT,
            "" if request.decision == "accepted" else request.reason,
            _dumps(audit_detail),
        ),
    ).fetchone()
    if ledger is None:
        raise RuntimeError("verification ledger insert returned no id")
    ledger_id = int(dict(ledger)["id"])

    _insert_review_events(conn, action_id, request, candidate, audit_detail)
    conn.commit()
    return {
        "ok": True,
        "action_id": int(action_id),
        "decision": request.decision,
        "ledger_id": ledger_id,
        "tool_run_ids": candidate.tool_run_ids,
        "correlation_id": request.correlation_id,
        "idempotent": False,
    }


def verify_action_result(
    action_id: int,
    staff: dict[str, Any] | None,
    *,
    decision: str,
    reason: str,
    evidence: list[dict[str, Any]],
    correlation_id: str,
    expected_execution_ledger_id: int,
    expected_detail_sha256: str,
    expected_candidate_sha256: str,
) -> dict[str, Any]:
    """Accept or reject one completed action result with reviewer evidence."""
    request, input_error = _normalize_verification_request(
        staff,
        decision=decision,
        reason=reason,
        evidence=evidence,
        correlation_id=correlation_id,
        expected_execution_ledger_id=expected_execution_ledger_id,
        expected_detail_sha256=expected_detail_sha256,
        expected_candidate_sha256=expected_candidate_sha256,
    )
    if input_error is not None:
        return input_error
    if request is None:
        raise AssertionError("validated verification request is required")
    if not all(table_exists(name) for name in (_ACTIONS, _EXECUTIONS, _EVENTS)):
        return {"ok": False, "reason": "verification_ledger_unavailable"}

    conn = get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
        action, action_error = _load_action_for_verification(conn, action_id)
        if action_error is not None:
            return action_error
        if action is None:
            raise AssertionError("locked action is required")

        audits, prior_result = _load_prior_verification(conn, action_id, request)
        if prior_result is not None:
            return prior_result
        checklist, state_error = _validate_action_state(conn, action, audits)
        if state_error is not None:
            return state_error
        if checklist is None:
            raise AssertionError("validated action checklist is required")

        candidate, candidate_error = _load_execution_candidate(conn, action_id, action, request)
        if candidate_error is not None:
            return candidate_error
        if candidate is None:
            raise AssertionError("validated execution candidate is required")
        return _persist_verification(conn, action_id, action, checklist, request, candidate)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_result_review.rollback_failed", exc_info=True)
        logger.warning("action_result_review.failed", extra={"action_id": action_id}, exc_info=True)
        return {"ok": False, "reason": "action_result_verification_failed"}
