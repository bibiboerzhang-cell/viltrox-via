"""Narrow repair for legacy Apify reservations already charged in the ledger."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.platform.apify_budget_contracts import APIFY_BUDGET_SCOPE, _iso, _parse_time, _utcnow
from app.platform.apify_budget_reconciliation_contract import (
    ReconciliationDependencies,
)
from app.platform.apify_budget_reconciliation_runtime import (
    reconcile_legacy_reservation,
    repair_legacy_caps,
)


_TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
_AUDIT_KEY = "legacy_ledger_reconciliation"
_CAP_REPAIR_AUDIT_KEY = "legacy_ledger_double_budget_repair"


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


def _runtime_dependencies(
    *,
    get_conn: Any,
    is_postgres_runtime: Any,
    ensure_schema: Any,
) -> ReconciliationDependencies:
    """Bind live seams per call so tests and runtime selection stay authoritative."""

    return ReconciliationDependencies(
        ensure_schema=ensure_schema,
        get_conn=get_conn,
        is_postgres_runtime=is_postgres_runtime,
        utcnow=_utcnow,
        parse_time=_parse_time,
        iso=_iso,
        money=_money,
        json_object=_json_object,
        positive_int=_positive_int,
        json_dumps=json.dumps,
        budget_scope=APIFY_BUDGET_SCOPE,
        terminal_run_states=frozenset(_TERMINAL_RUN_STATES),
        reconciliation_audit_key=_AUDIT_KEY,
        cap_repair_audit_key=_CAP_REPAIR_AUDIT_KEY,
    )


def reconcile_legacy_apify_reservation_from_ledger(
    reservation_key: str,
    *,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Decimal | float | str,
) -> dict[str, Any]:
    """Settle one legacy reservation from an already-accounted ledger row.

    This only repairs reservation state. It never inserts a ledger row or
    changes provider/monthly budget caps.
    """

    from app.db.connection import get_conn, is_postgres_runtime
    from app.platform.apify_budget import _ensure_reservation_schema

    return reconcile_legacy_reservation(
        reservation_key,
        expected_ledger_id=expected_ledger_id,
        expected_run_id=expected_run_id,
        expected_terminal_status=expected_terminal_status,
        expected_actual_cost_usd=expected_actual_cost_usd,
        dependencies=_runtime_dependencies(
            get_conn=get_conn,
            is_postgres_runtime=is_postgres_runtime,
            ensure_schema=_ensure_reservation_schema,
        ),
    )


def repair_legacy_apify_double_counted_caps(
    reservation_key: str,
    *,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Decimal | float | str,
    expected_settled_at: str,
    expected_provider_current_spend: Decimal | float | str,
    expected_monthly_current_spend: Decimal | float | str,
) -> dict[str, Any]:
    """Undo one proven duplicate cap increment without changing the ledger.

    Evidence, reservation and both cap rows are checked in one transaction.
    Reservation state/actual cost and the source ledger row stay immutable.
    """

    from app.db.connection import get_conn, is_postgres_runtime
    from app.platform.apify_budget import _ensure_reservation_schema

    return repair_legacy_caps(
        reservation_key,
        expected_ledger_id=expected_ledger_id,
        expected_run_id=expected_run_id,
        expected_terminal_status=expected_terminal_status,
        expected_actual_cost_usd=expected_actual_cost_usd,
        expected_settled_at=expected_settled_at,
        expected_provider_current_spend=expected_provider_current_spend,
        expected_monthly_current_spend=expected_monthly_current_spend,
        dependencies=_runtime_dependencies(
            get_conn=get_conn,
            is_postgres_runtime=is_postgres_runtime,
            ensure_schema=_ensure_reservation_schema,
        ),
    )


__all__ = [
    "reconcile_legacy_apify_reservation_from_ledger",
    "repair_legacy_apify_double_counted_caps",
]
