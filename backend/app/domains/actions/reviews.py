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
        return {"ok": False, "reason": "invalid_verification_decision"}
    if normalized_reason is None:
        return {"ok": False, "reason": "verification_reason_required"}
    if evidence_rows is None:
        return {"ok": False, "reason": "verification_evidence_required"}
    if correlation is None:
        return {"ok": False, "reason": "verification_correlation_required"}
    if expected_ledger_id <= 0 or any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in (expected_hash, expected_candidate_hash)
    ):
        return {"ok": False, "reason": "verification_candidate_required"}
    if reviewer is None:
        return {"ok": False, "reason": "verification_scope_unavailable"}
    actor_id, organization_id = reviewer
    if not all(table_exists(name) for name in (_ACTIONS, _EXECUTIONS, _EVENTS)):
        return {"ok": False, "reason": "verification_ledger_unavailable"}

    conn = get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
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
            conn.rollback()
            return {"ok": False, "reason": "action_not_found"}
        action = dict(row)

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
            if str(detail.get("correlation_id") or "") != correlation:
                continue
            conn.rollback()
            same = (
                str(detail.get("decision") or "") == normalized_decision
                and str(detail.get("reason") or "") == normalized_reason
                and detail.get("evidence") == evidence_rows
                and int(detail.get("actor_staff_id") or 0) == actor_id
                and int(detail.get("execution_ledger_id") or 0) == expected_ledger_id
                and str(detail.get("execution_detail_sha256") or "") == expected_hash
                and str(detail.get("execution_candidate_sha256") or "")
                == expected_candidate_hash
            )
            if not same:
                return {"ok": False, "reason": "verification_correlation_conflict"}
            return {
                "ok": True,
                "action_id": int(action_id),
                "decision": normalized_decision,
                "ledger_id": int(audit_row["id"]),
                "tool_run_ids": [
                    int(value) for value in detail.get("tool_run_ids", [])
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0
                ],
                "correlation_id": correlation,
                "idempotent": True,
            }

        if str(action.get("status") or "") != "executed":
            conn.rollback()
            return {
                "ok": False,
                "reason": "action_not_awaiting_result_verification",
                "status": str(action.get("status") or ""),
            }
        checklist = _loads(action.get("result_checklist_json"))
        if isinstance(checklist.get("human_verification"), dict) or audits:
            conn.rollback()
            return {"ok": False, "reason": "action_result_already_verified"}

        success_row = conn.execute(
            f"""
            SELECT id, endpoint, outcome, detail_json, created_at
            FROM {_EXECUTIONS}
            WHERE id = ? AND action_id = ?
              AND mode = 'executed'
              AND outcome = 'success'
              AND endpoint <> ?
            """,
            (expected_ledger_id, int(action_id), _ENDPOINT),
        ).fetchone()
        if success_row is None:
            conn.rollback()
            return {"ok": False, "reason": "successful_execution_receipt_required"}
        success_receipt = dict(success_row)
        visible_detail, current_hash = _safe_detail(success_receipt.get("detail_json"))
        if not hmac.compare_digest(current_hash, expected_hash):
            conn.rollback()
            return {"ok": False, "reason": "verification_candidate_changed"}

        tool_run_ids: list[int] = []
        execution_effect = "none"
        if table_exists(_TOOL_RUNS):
            tool_rows = conn.execute(
                f"SELECT id, inputs_json FROM {_TOOL_RUNS} "
                "WHERE output_ref = ? AND status = 'executed' ORDER BY id",
                (f"action:{int(action_id)}",),
            ).fetchall()
            matched = [
                dict(tool_row) for tool_row in tool_rows
                if int(_loads(dict(tool_row).get("inputs_json")).get("execution_ledger_id") or 0)
                == int(dict(success_row)["id"])
            ]
            if len(matched) > 1:
                conn.rollback()
                return {"ok": False, "reason": "ambiguous_agent_tool_run_receipts"}
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
        if not hmac.compare_digest(current_candidate_hash, expected_candidate_hash):
            conn.rollback()
            return {"ok": False, "reason": "verification_candidate_changed"}

        verified_at = datetime.now(timezone.utc).isoformat()
        verification = {
            "decision": normalized_decision,
            "reason": normalized_reason,
            "correlation_id": correlation,
            "evidence_count": len(evidence_rows),
            "actor_staff_id": actor_id,
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
            conn.rollback()
            return {"ok": False, "reason": "action_verification_state_changed"}

        audit_detail = {
            "kind": "human_result_verification",
            "decision": normalized_decision,
            "reason": normalized_reason,
            "evidence": evidence_rows,
            "correlation_id": correlation,
            "actor_staff_id": actor_id,
            "verified_at": verified_at,
            "execution_ledger_id": int(dict(success_row)["id"]),
            "execution_detail_sha256": current_hash,
            "execution_candidate_sha256": current_candidate_hash,
            "tool_run_ids": tool_run_ids,
            "execution_effect": execution_effect,
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
                int(action_id), str(action.get("category") or ""),
                str(action.get("dedupe_key") or ""), actor_id,
                "success" if normalized_decision == "accepted" else "failed",
                _ENDPOINT,
                "" if normalized_decision == "accepted" else normalized_reason,
                _dumps(audit_detail),
            ),
        ).fetchone()
        if ledger is None:
            raise RuntimeError("verification ledger insert returned no id")
        ledger_id = int(dict(ledger)["id"])

        trace_id = event_ledger.new_trace_id("action", action_id)
        event_ledger.insert_required(
            conn,
            "action_result_accepted" if normalized_decision == "accepted" else "action_result_rejected",
            entity_type="action",
            entity_id=action_id,
            actor_type="staff",
            actor_id=actor_id,
            source="action_inbox.human_verification",
            payload=audit_detail,
            trace_id=trace_id,
            provenance={
                "kind": "human_review",
                "evidence_count": len(evidence_rows),
                "execution_ledger_id": int(dict(success_row)["id"]),
                "execution_effect": execution_effect,
                "evidence_verification": "staff_attestation_bound_to_execution_ledger",
            },
            organization_id=organization_id,
        )

        if table_exists(_TOOL_RUNS):
            for tool_run_id in tool_run_ids:
                event_ledger.insert_required(
                    conn,
                    "agent_tool_run_accepted"
                    if normalized_decision == "accepted" else "agent_tool_run_rejected",
                    entity_type="agent_tool_run",
                    entity_id=tool_run_id,
                    actor_type="staff",
                    actor_id=actor_id,
                    source="action_inbox.human_verification",
                    payload={
                        "action_id": int(action_id),
                        "decision": normalized_decision,
                        "correlation_id": correlation,
                        "evidence_count": len(evidence_rows),
                    },
                    trace_id=trace_id,
                    provenance={
                        "kind": "human_review",
                        "action_id": int(action_id),
                        "execution_ledger_id": int(dict(success_row)["id"]),
                        "execution_effect": execution_effect,
                        "evidence_verification": "staff_attestation_bound_to_execution_ledger",
                    },
                    organization_id=organization_id,
                )

        conn.commit()
        return {
            "ok": True,
            "action_id": int(action_id),
            "decision": normalized_decision,
            "ledger_id": ledger_id,
            "tool_run_ids": tool_run_ids,
            "correlation_id": correlation,
            "idempotent": False,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_result_review.rollback_failed", exc_info=True)
        logger.warning("action_result_review.failed", extra={"action_id": action_id}, exc_info=True)
        return {"ok": False, "reason": "action_result_verification_failed"}
