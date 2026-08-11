"""Fail-closed, hash-bound Action approval and execution claim.

Approval is a durable fact, not merely ``status='approved'``.  The Action row,
manager identity, canonical execution contract, and required ledger event are
written atomically.  Claim re-locks and re-hashes that contract before any
handler may start.
"""
from __future__ import annotations

import hmac
import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.platform import event_ledger, review_contract

logger = get_logger(__name__)

_ACTION_TABLE = "vkpi_action_inbox"
_EXECUTION_LEDGER = "vkpi_action_execution_ledger"
_EVENT_TABLE = "vkpi_event_ledger"
_EVENT_SOURCE = "action_inbox.required_approval"
_POST_APPROVAL_STATUSES = frozenset({"approved", "executing", "executed", "failed"})
_APPROVABLE_STATUSES = frozenset({"suggested", "snoozed"})
APPROVAL_CONTRACT_COLUMNS = (
    "id,dedupe_key,category,title,detail,priority,entity_type,entity_id,"
    "suggested_endpoint,estimated_cost_cents,writes_business_data,uses_llm,"
    "requires_approval,owner_staff_id,reason,payload_json,touches_v6_fit,"
    "expected_gain,risk_level,evidence_refs_json,verification_plan_json,"
    "affected_tables_json,approval_reason,status,approved_by_staff_id,approved_at,"
    "approval_snapshot_sha256"
)
# Compatibility for the concurrently developed bridge; new callers should use
# the public name so the exact verification projection remains one contract.
_CONTRACT_COLUMNS = APPROVAL_CONTRACT_COLUMNS


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _snapshot(row: dict[str, Any], approval_reason: str) -> dict[str, Any]:
    """Canonical server-owned execution and review contract."""
    return {
        "schema": "vkpi_action_approval_snapshot/v1",
        "action_id": int(row.get("id") or 0),
        "dedupe_key": str(row.get("dedupe_key") or ""),
        "category": str(row.get("category") or ""),
        "title": str(row.get("title") or ""),
        "detail": str(row.get("detail") or ""),
        "priority": str(row.get("priority") or ""),
        "entity_type": str(row.get("entity_type") or ""),
        "entity_id": str(row.get("entity_id") or ""),
        "suggested_endpoint": str(row.get("suggested_endpoint") or ""),
        "estimated_cost_cents": int(row.get("estimated_cost_cents") or 0),
        "writes_business_data": bool(row.get("writes_business_data")),
        "uses_llm": bool(row.get("uses_llm")),
        "requires_approval": bool(row.get("requires_approval")),
        "owner_staff_id": int(row["owner_staff_id"]) if row.get("owner_staff_id") else None,
        "reason": str(row.get("reason") or ""),
        "payload_json": _loads(row.get("payload_json"), {}),
        "touches_v6_fit": bool(row.get("touches_v6_fit")),
        "expected_gain": str(row.get("expected_gain") or ""),
        "risk_level": str(row.get("risk_level") or ""),
        "evidence_refs_json": _loads(row.get("evidence_refs_json"), []),
        "verification_plan_json": _loads(row.get("verification_plan_json"), []),
        "affected_tables_json": _loads(row.get("affected_tables_json"), []),
        "approval_reason": str(approval_reason or ""),
    }


def _hash(row: dict[str, Any], approval_reason: str) -> str:
    return review_contract.review_snapshot_sha256(_snapshot(row, approval_reason))


def _begin_and_lock(conn: Any, action_id: int) -> dict[str, Any] | None:
    postgres = is_postgres_runtime()
    if not postgres and not bool(getattr(conn, "in_transaction", False)):
        conn.execute("BEGIN IMMEDIATE")
    lock = " FOR UPDATE" if postgres else ""
    row = conn.execute(
        f"SELECT {_CONTRACT_COLUMNS} FROM {_ACTION_TABLE} WHERE id=?{lock}",
        (int(action_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def _event_matches(conn: Any, action_id: int, actor_id: int, snapshot_hash: str) -> bool:
    row = conn.execute(
        f"SELECT actor_id,provenance_json FROM {_EVENT_TABLE} "
        "WHERE organization_id=1 AND event_type='action_approved' "
        "AND entity_type='action' AND entity_id=? AND source=?",
        (str(int(action_id)), _EVENT_SOURCE),
    ).fetchone()
    if row is None:
        return False
    item = dict(row)
    provenance = _loads(item.get("provenance_json"), {})
    return bool(
        str(item.get("actor_id") or "") == str(int(actor_id))
        and hmac.compare_digest(
            str(provenance.get("approval_snapshot_sha256") or ""), snapshot_hash,
        )
    )


def verified_approval_snapshot(conn: Any, row: dict[str, Any]) -> bool:
    """Pure-with-DB-read verification for callers already holding the Action lock."""
    try:
        action_id = int(row.get("id") or 0)
        approved_by = int(row.get("approved_by_staff_id") or 0)
        stored_hash = str(row.get("approval_snapshot_sha256") or "")
        current_hash = _hash(row, str(row.get("approval_reason") or ""))
    except (TypeError, ValueError):
        return False
    return bool(
        action_id > 0
        and approved_by > 0
        and row.get("approved_at") is not None
        and len(stored_hash) == 64
        and hmac.compare_digest(stored_hash, current_hash)
        and _event_matches(conn, action_id, approved_by, stored_hash)
    )


def approve_action(
    action_id: int,
    staff: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    reviewer = review_contract.reviewer_context(staff)
    actor_id = int(reviewer[0]) if reviewer is not None else 0
    if actor_id <= 0 or not scope.can_view_all(staff):
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
    if not table_exists(_ACTION_TABLE) or not table_exists(_EVENT_TABLE):
        return {"ok": False, "reason": "approval_evidence_unavailable", "action_id": int(action_id)}
    reason_raw = str(reason or "")
    normalized_reason = (
        review_contract.normalize_review_text(reason_raw, max_length=2000)
        if reason_raw.strip() else ""
    )
    if normalized_reason is None:
        return {"ok": False, "reason": "approval_reason_invalid", "action_id": int(action_id)}
    conn = get_conn()
    try:
        current = _begin_and_lock(conn, action_id)
        if current is None:
            conn.rollback()
            return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
        current_status = str(current.get("status") or "")
        legacy_unsealed = bool(
            current_status == "approved"
            and not current.get("approved_by_staff_id")
            and current.get("approved_at") is None
            and not str(current.get("approval_snapshot_sha256") or "")
        )
        if current_status in _POST_APPROVAL_STATUSES and not legacy_unsealed:
            stored_actor = int(current.get("approved_by_staff_id") or 0)
            stored_hash = str(current.get("approval_snapshot_sha256") or "")
            current_hash = _hash(current, str(current.get("approval_reason") or ""))
            exact = bool(
                stored_actor == actor_id
                and hmac.compare_digest(stored_hash, current_hash)
                and str(current.get("approval_reason") or "") == normalized_reason
                and _event_matches(conn, action_id, actor_id, stored_hash)
            )
            conn.rollback()
            if exact:
                return {
                    "ok": True, "status": current_status, "action_id": int(action_id),
                    "idempotent": True, "approval_snapshot_sha256": stored_hash,
                }
            return {"ok": False, "reason": "approval_replay_conflict", "action_id": int(action_id)}
        if current_status not in _APPROVABLE_STATUSES and not legacy_unsealed:
            conn.rollback()
            return {
                "ok": False, "reason": "illegal_state_transition", "action_id": int(action_id),
                "from_status": current_status, "to_status": "approved",
            }

        snapshot_hash = _hash(current, normalized_reason)
        now = "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"
        updated = conn.execute(
            f"UPDATE {_ACTION_TABLE} SET status='approved',approval_reason=?,"
            f"approved_by_staff_id=?,approved_at={now},approval_snapshot_sha256=?,updated_at={now} "
            "WHERE id=? AND (status IN ('suggested','snoozed') OR "
            "(status='approved' AND approved_by_staff_id IS NULL AND approved_at IS NULL "
            "AND COALESCE(approval_snapshot_sha256,'')='')) RETURNING approved_at",
            (normalized_reason, actor_id, snapshot_hash, int(action_id)),
        ).fetchone()
        if updated is None:
            raise RuntimeError("approval_state_changed")
        event_ledger.insert_required(
            conn,
            "action_approved",
            entity_type="action",
            entity_id=int(action_id),
            actor_type="staff",
            actor_id=str(actor_id),
            source=_EVENT_SOURCE,
            payload={"approval_reason": normalized_reason},
            trace_id=event_ledger.new_trace_id("action-approval", action_id),
            provenance={
                "evidence_verification": "server_bound_manager_approval",
                "approval_snapshot_sha256": snapshot_hash,
                "approved_by_staff_id": actor_id,
            },
            organization_id=1,
        )
        conn.commit()
        return {
            "ok": True, "status": "approved", "action_id": int(action_id),
            "idempotent": False, "approved_by_staff_id": actor_id,
            "upgraded_legacy_approval": legacy_unsealed,
            "approved_at": str(dict(updated).get("approved_at") or ""),
            "approval_snapshot_sha256": snapshot_hash,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_approval.rollback_failed", exc_info=True)
        logger.warning("action_approval.persist_failed", extra={"action_id": action_id}, exc_info=True)
        return {"ok": False, "reason": "approval_persist_failed", "action_id": int(action_id)}


def claim_action_execution(
    action_id: int,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Claim only an approved row whose immutable contract and event re-verify."""
    reviewer = review_contract.reviewer_context(staff)
    actor_id = int(reviewer[0]) if reviewer is not None else 0
    if actor_id <= 0:
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
    if not table_exists(_ACTION_TABLE) or not table_exists(_EVENT_TABLE):
        return {"ok": False, "reason": "approval_evidence_unavailable", "action_id": int(action_id)}
    conn = get_conn()
    try:
        current = _begin_and_lock(conn, action_id)
        if current is None or (
            not scope.can_view_all(staff)
            and int(current.get("owner_staff_id") or 0) != actor_id
        ):
            conn.rollback()
            return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
        status = str(current.get("status") or "")
        if status != "approved":
            conn.rollback()
            return {
                "ok": False, "reason": "execution_already_claimed", "action_id": int(action_id),
                "status": status,
            }
        if not verified_approval_snapshot(conn, current):
            conn.rollback()
            return {
                "ok": False, "reason": "approval_snapshot_mismatch", "action_id": int(action_id),
                "status": status,
            }
        now = "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"
        updated = conn.execute(
            f"UPDATE {_ACTION_TABLE} SET status='executing',updated_at={now} "
            "WHERE id=? AND status='approved' RETURNING id,status",
            (int(action_id),),
        ).fetchone()
        if updated is None:
            raise RuntimeError("execution_claim_changed")
        conn.commit()
        return {"ok": True, "status": "executing", "action_id": int(action_id)}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_claim.rollback_failed", exc_info=True)
        logger.warning("action_claim.failed", extra={"action_id": action_id}, exc_info=True)
        return {"ok": False, "reason": "execution_claim_failed", "action_id": int(action_id)}


def mark_done_action(
    action_id: int,
    staff: dict[str, Any] | None,
    *,
    note: str,
) -> dict[str, Any]:
    """Atomically verify approval, record manual completion, and append its ledger."""
    reviewer = review_contract.reviewer_context(staff)
    actor_id = int(reviewer[0]) if reviewer is not None else 0
    if actor_id <= 0:
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
    if not all(table_exists(name) for name in (_ACTION_TABLE, _EVENT_TABLE, _EXECUTION_LEDGER)):
        return {"ok": False, "reason": "approval_evidence_unavailable", "action_id": int(action_id)}
    note_raw = str(note or "")
    normalized_note = (
        review_contract.normalize_review_text(note_raw, max_length=2000)
        if note_raw.strip() else ""
    )
    if normalized_note is None:
        return {"ok": False, "reason": "manual_note_invalid", "action_id": int(action_id)}
    conn = get_conn()
    try:
        current = _begin_and_lock(conn, action_id)
        if current is None or (
            not scope.can_view_all(staff)
            and int(current.get("owner_staff_id") or 0) != actor_id
        ):
            conn.rollback()
            return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
        status = str(current.get("status") or "")
        if status != "approved":
            conn.rollback()
            return {
                "ok": False, "reason": "illegal_state_transition", "action_id": int(action_id),
                "from_status": status, "to_status": "executed",
            }
        if not verified_approval_snapshot(conn, current):
            conn.rollback()
            return {"ok": False, "reason": "approval_snapshot_mismatch", "action_id": int(action_id)}
        if (
            str(current.get("category") or "") == "orchestrated_step"
            or bool(current.get("writes_business_data"))
            or bool(current.get("uses_llm"))
            or bool(str(current.get("suggested_endpoint") or "").strip())
        ):
            conn.rollback()
            return {"ok": False, "reason": "manual_execution_not_allowed", "action_id": int(action_id)}

        now = "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"
        updated = conn.execute(
            f"UPDATE {_ACTION_TABLE} SET status='executed',updated_at={now} "
            "WHERE id=? AND status='approved' RETURNING id",
            (int(action_id),),
        ).fetchone()
        if updated is None:
            raise RuntimeError("manual_execution_state_changed")
        json_param = "?::jsonb" if is_postgres_runtime() else "?"
        ledger = conn.execute(
            f"INSERT INTO {_EXECUTION_LEDGER} "
            "(action_id,category,dedupe_key,actor_staff_id,mode,outcome,endpoint,"
            f"cost_cents,error,detail_json,created_at) VALUES (?,?,?,?,'executed','success',"
            f"'manual:mark-done',0,'',{json_param},{now}) RETURNING id",
            (
                int(action_id), str(current.get("category") or ""),
                str(current.get("dedupe_key") or ""), actor_id,
                json.dumps(
                    {
                        "kind": "manual_execution",
                        "note": normalized_note,
                        "approval_snapshot_sha256": str(
                            current.get("approval_snapshot_sha256") or ""
                        ),
                    },
                    ensure_ascii=False,
                ),
            ),
        ).fetchone()
        if ledger is None:
            raise RuntimeError("manual_execution_ledger_missing")
        ledger_id = int(dict(ledger)["id"])
        conn.commit()
        return {
            "ok": True, "status": "executed", "action_id": int(action_id),
            "ledger_id": ledger_id,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_mark_done.rollback_failed", exc_info=True)
        logger.warning("action_mark_done.failed", extra={"action_id": action_id}, exc_info=True)
        return {"ok": False, "reason": "mark_done_persist_failed", "action_id": int(action_id)}


__all__ = [
    "APPROVAL_CONTRACT_COLUMNS",
    "approve_action", "claim_action_execution", "mark_done_action",
    "verified_approval_snapshot",
]
