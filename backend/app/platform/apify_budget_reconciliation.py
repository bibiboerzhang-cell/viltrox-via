"""Narrow repair for legacy Apify reservations already charged in the ledger."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.platform.apify_budget_contracts import APIFY_BUDGET_SCOPE, _iso, _utcnow


_TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
_AUDIT_KEY = "legacy_ledger_reconciliation"


def _money(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.000001"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _json_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def reconcile_legacy_apify_reservation_from_ledger(
    reservation_key: str,
    *,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Decimal | float | str,
) -> dict[str, Any]:
    """Settle exactly one legacy reservation from already-accounted evidence.

    The eligible ledger shape is the canonical old ``record_apify_run`` path:
    an unreserved unified row whose commit already incremented
    ``provider:apify`` and ``monthly_total``.  This function only repairs the
    reservation state.  It never writes a ledger row or changes budget caps.
    """

    from app.db.connection import get_conn, is_postgres_runtime
    from app.platform.apify_budget import _ensure_reservation_schema

    key = str(reservation_key or "").strip()
    run_id = str(expected_run_id or "").strip()
    status = str(expected_terminal_status or "").strip().upper()
    actual = _money(expected_actual_cost_usd)
    parsed_ledger_id = _positive_int(expected_ledger_id)
    ledger_id = parsed_ledger_id or 0
    if not key:
        return {"settled": False, "reason": "no_reservation"}
    if (
        ledger_id <= 0
        or not run_id
        or not run_id.isascii()
        or not run_id.replace("-", "").isalnum()
        or status not in _TERMINAL_RUN_STATES
        or actual is None
    ):
        return {"settled": False, "reason": "invalid_expected_evidence"}

    _ensure_reservation_schema()
    conn = get_conn()
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    try:
        row = conn.execute(
            "SELECT * FROM vkpi_apify_budget_reservations WHERE reservation_key=?" + lock,
            (key,),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"settled": False, "reason": "missing_reservation"}
        reservation = dict(row)
        reservation_meta = _json_object(reservation.get("metadata_json"))
        if reservation_meta is None:
            conn.rollback()
            return {"settled": False, "reason": "reservation_metadata_invalid"}

        audit = reservation_meta.get(_AUDIT_KEY)
        if str(reservation.get("state") or "") == "settled":
            if (
                isinstance(audit, dict)
                and int(audit.get("ledger_id") or 0) == ledger_id
                and str(audit.get("apify_run_id") or "") == run_id
                and str(audit.get("terminal_status") or "").upper() == status
                and _money(audit.get("actual_cost_usd")) == actual
                and _money(reservation.get("actual_cost_usd")) == actual
            ):
                conn.rollback()
                return {"settled": False, "reason": "already_reconciled", "ledger_id": ledger_id}
            conn.rollback()
            return {"settled": False, "reason": "already_settled_by_other_path"}
        if str(reservation.get("state") or "") != "provider_started":
            conn.rollback()
            return {"settled": False, "reason": "reservation_not_provider_started"}
        if (
            reservation.get("actual_cost_usd") is not None
            or reservation.get("settled_at") is not None
            or not str(reservation.get("provider_started_at") or "").strip()
        ):
            conn.rollback()
            return {"settled": False, "reason": "reservation_state_inconsistent"}
        if str(reservation.get("apify_run_id") or "").strip() != run_id:
            conn.rollback()
            return {"settled": False, "reason": "reservation_run_id_mismatch"}
        if audit is not None:
            conn.rollback()
            return {"settled": False, "reason": "reservation_audit_conflict"}

        claim = conn.execute(
            """
            SELECT state,lease_expires_at,provider_run_id,fence_token
            FROM vkpi_provider_execution_claims WHERE task_id=?
            """ + lock,
            (str(reservation.get("task_id") or ""),),
        ).fetchone()
        if not claim:
            conn.rollback()
            return {"settled": False, "reason": "provider_claim_missing"}
        claim_data = dict(claim)
        if str(claim_data.get("state") or "") not in {"completed", "failed", "blocked"}:
            conn.rollback()
            return {"settled": False, "reason": "provider_claim_not_terminal"}

        # LIKE only narrows candidates; parsed JSON below provides exact match.
        candidates = conn.execute(
            """
            SELECT id,cron_task,ai_provider,model_name,cost_usd,metadata_json
            FROM vkpi_ai_cost_ledger
            WHERE ai_provider='apify' AND metadata_json LIKE ?
            """ + lock,
            (f"%{run_id}%",),
        ).fetchall()
        exact: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw in candidates:
            ledger = dict(raw)
            metadata = _json_object(ledger.get("metadata_json"))
            if metadata is None:
                conn.rollback()
                return {"settled": False, "reason": "ledger_metadata_invalid"}
            if str(metadata.get("apify_run_id") or "").strip() == run_id:
                exact.append((ledger, metadata))
        if len(exact) != 1:
            conn.rollback()
            return {"settled": False, "reason": "ledger_run_not_unique"}

        ledger, metadata = exact[0]
        actor = str(reservation.get("actor_id") or "").strip().replace("~", "/").lower()
        if int(ledger.get("id") or 0) != ledger_id:
            conn.rollback()
            return {"settled": False, "reason": "ledger_id_mismatch"}
        if (
            str(ledger.get("cron_task") or "").strip().lower() != APIFY_BUDGET_SCOPE
            or str(ledger.get("model_name") or "").strip().replace("~", "/").lower() != actor
            or str(metadata.get("actor_id") or "").strip().replace("~", "/").lower() != actor
            or str(metadata.get("operation") or "") != str(reservation.get("operation") or "")
            or metadata.get("unified_entry") is not True
            or str(metadata.get("scope") or "").strip().lower() != APIFY_BUDGET_SCOPE
            or metadata.get("budget_reservation_key") != ""
            or metadata.get("budget_reservation_settlement") != {}
        ):
            conn.rollback()
            return {"settled": False, "reason": "ledger_not_legacy_budget_accounted"}
        if str(metadata.get("run_status") or "").strip().upper() != status:
            conn.rollback()
            return {"settled": False, "reason": "ledger_terminal_status_mismatch"}

        evidence = (
            _money(metadata.get("settled_usd"))
            if metadata.get("reconciled") is True
            else _money(metadata.get("usage_total_usd"))
            if metadata.get("pricing_basis") == "usage_settled" and metadata.get("estimated") is False
            else None
        )
        if _money(ledger.get("cost_usd")) != actual or evidence != actual:
            conn.rollback()
            return {"settled": False, "reason": "ledger_actual_cost_mismatch"}

        now = _iso(_utcnow())
        reservation_meta[_AUDIT_KEY] = {
            "version": 1,
            "ledger_id": ledger_id,
            "apify_run_id": run_id,
            "terminal_status": status,
            "actual_cost_usd": format(actual, "f"),
            "accounting_source": "legacy_unreserved_record_apify_run",
            "claim_snapshot": {
                "state": str(claim_data.get("state") or ""),
                "fence_token": int(claim_data.get("fence_token") or 0),
                "provider_run_id": str(claim_data.get("provider_run_id") or ""),
            },
            "ledger_inserted": False,
            "budget_caps_updated": False,
            "reconciled_at": now,
        }
        updated = conn.execute(
            """
            UPDATE vkpi_apify_budget_reservations
            SET state='settled',actual_cost_usd=?,metadata_json=?,settled_at=?,updated_at=?
            WHERE reservation_key=? AND state='provider_started' AND apify_run_id=?
            """,
            (format(actual, "f"), json.dumps(reservation_meta, ensure_ascii=False), now, now, key, run_id),
        )
        if int(getattr(updated, "rowcount", 0) or 0) != 1:
            raise RuntimeError("legacy reservation reconciliation lost its state fence")
        readback = conn.execute(
            "SELECT state,actual_cost_usd FROM vkpi_apify_budget_reservations WHERE reservation_key=?",
            (key,),
        ).fetchone()
        if not readback or str(readback["state"]) != "settled" or _money(readback["actual_cost_usd"]) != actual:
            raise RuntimeError("legacy reservation reconciliation readback mismatch")
        conn.commit()
        return {
            "settled": True,
            "ledger_id": ledger_id,
            "apify_run_id": run_id,
            "terminal_status": status,
            "actual_cost_usd": float(actual),
            "budget_caps_updated": False,
            "ledger_inserted": False,
        }
    except Exception:
        conn.rollback()
        raise


__all__ = ["reconcile_legacy_apify_reservation_from_ledger"]
