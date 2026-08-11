"""Action-layer coordination for the tiny plan-linked execution allowlist."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.actions import validators
from app.domains.agents import step_execution

logger = get_logger(__name__)

_ACTION_TABLE = "vkpi_action_inbox"
_EXECUTION_LEDGER = "vkpi_action_execution_ledger"


def _now() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _record_rejection(
    action: dict[str, Any],
    action_id: int,
    staff: dict[str, Any] | None,
    reason: str,
    *,
    release_claim: bool,
) -> tuple[int | None, bool]:
    """Persist rejection and optionally reverse an unexecuted CAS claim."""
    conn = None
    try:
        if not table_exists(_EXECUTION_LEDGER):
            raise RuntimeError("action execution ledger is unavailable")
        conn = get_conn()
        row = conn.execute(
            f"""
            INSERT INTO {_EXECUTION_LEDGER}
              (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
               endpoint, cost_cents, error, detail_json, created_at)
            VALUES (?,?,?,?,'executed','skipped',?,0,?,{_json_param()},{_now()})
            RETURNING id
            """,
            (
                int(action_id),
                str(action.get("category") or ""),
                str(action.get("dedupe_key") or ""),
                int(scope.actor_staff_id(staff)) or None,
                str(action.get("suggested_endpoint") or ""),
                str(reason or "")[:2000],
                _dumps({"handler_started": False, "plan_contract_rejected": True}),
            ),
        ).fetchone()
        ledger_id = int(dict(row)["id"]) if row else None
        if release_claim:
            cursor = conn.execute(
                f"UPDATE {_ACTION_TABLE} SET status='approved', updated_at={_now()} "
                "WHERE id=? AND status='executing'",
                (int(action_id),),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("execution claim lost")
        conn.commit()
        return ledger_id, True
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            logger.debug("orchestrated_steps.rejection_rollback_failed", exc_info=True)
        logger.warning(
            "orchestrated_steps.rejection_persist_failed",
            extra={"action_id": action_id, "reason": reason, "release_claim": release_claim},
            exc_info=True,
        )
        return None, False


def prepare(
    action: dict[str, Any],
    action_id: int,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preflight the unlocked plan before the Action CAS claim."""
    try:
        contract = step_execution.resolve_action_contract(action, staff, lock_plan=False)
        return {
            "ok": True,
            "contract": contract,
            "action": step_execution.canonical_action(action, contract),
        }
    except step_execution.StepExecutionRejected as exc:
        reason = exc.reason
    except Exception:
        logger.warning(
            "orchestrated_steps.prepare_failed",
            extra={"action_id": action_id},
            exc_info=True,
        )
        reason = "plan_contract_unavailable"
    ledger_id, _ = _record_rejection(action, action_id, staff, reason, release_claim=False)
    return {"ok": False, "reason": reason, "ledger_id": ledger_id}


def lock_claimed(
    action: dict[str, Any],
    action_id: int,
    staff: dict[str, Any] | None,
    expected_contract: dict[str, Any],
) -> dict[str, Any]:
    """Lock/revalidate the plan after claim; keep the transaction open on success."""
    try:
        conn = get_conn()
        contract = step_execution.resolve_action_contract(
            action,
            staff,
            conn=conn,
            lock_plan=True,
        )
        if contract.get("fingerprint") != expected_contract.get("fingerprint"):
            raise step_execution.StepExecutionRejected("plan_contract_changed")
        canonical = step_execution.canonical_action(action, contract)
        validation = validators.validate_action(canonical)
        if not validation.get("ok"):
            raise step_execution.StepExecutionRejected(
                str(validation.get("reason") or "locked_validation_failed")
            )
        step_execution.mark_plan_executing(conn, contract)
        canonical["_transaction_conn"] = conn
        return {"ok": True, "contract": contract, "action": canonical}
    except step_execution.StepExecutionRejected as exc:
        reason = exc.reason
    except Exception:
        logger.warning(
            "orchestrated_steps.plan_lock_failed",
            extra={"action_id": action_id},
            exc_info=True,
        )
        reason = "plan_contract_unavailable"
    try:
        get_conn().rollback()
    except Exception:
        logger.debug("orchestrated_steps.plan_lock_rollback_failed", exc_info=True)
    ledger_id, released = _record_rejection(
        action,
        action_id,
        staff,
        reason,
        release_claim=True,
    )
    return {
        "ok": False,
        "reason": reason if released else "execution_finalize_failed",
        "outcome": "skipped" if released else "failed",
        "ledger_id": ledger_id,
        "manual_reconciliation_required": not released,
    }


def rollback_handler_transaction(action: dict[str, Any]) -> None:
    """Discard any partial local state write before recording a failed receipt."""
    conn = action.get("_transaction_conn")
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        logger.warning("orchestrated_steps.handler_rollback_failed", exc_info=True)


__all__ = ["lock_claimed", "prepare", "rollback_handler_transaction"]
