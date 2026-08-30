"""Transactional runtime for legacy Apify ledger and cap repair."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.platform.apify_budget_reconciliation_contract import (
    CapRepairExpectation,
    LegacyExpectation,
    ReconciliationDependencies,
    ReconciliationRejected,
    reject,
)


def _legacy_expectation(
    reservation_key: str,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Any,
    *,
    action: str,
    dependencies: ReconciliationDependencies,
) -> tuple[LegacyExpectation | None, dict[str, Any] | None]:
    key = str(reservation_key or "").strip()
    run_id = str(expected_run_id or "").strip()
    status = str(expected_terminal_status or "").strip().upper()
    actual = dependencies.money(expected_actual_cost_usd)
    ledger_id = dependencies.positive_int(expected_ledger_id) or 0
    if not key:
        return None, {action: False, "reason": "no_reservation"}
    invalid = (
        ledger_id <= 0
        or not run_id
        or not run_id.isascii()
        or not run_id.replace("-", "").isalnum()
        or status not in dependencies.terminal_run_states
        or actual is None
    )
    if invalid:
        return None, {action: False, "reason": "invalid_expected_evidence"}
    return (
        LegacyExpectation(
            key=key,
            run_id=run_id,
            status=status,
            actual=actual,
            ledger_id=ledger_id,
        ),
        None,
    )


def _cap_expectation(
    reservation_key: str,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Any,
    expected_settled_at: str,
    expected_provider_current_spend: Any,
    expected_monthly_current_spend: Any,
    dependencies: ReconciliationDependencies,
) -> tuple[CapRepairExpectation | None, dict[str, Any] | None]:
    base, early = _legacy_expectation(
        reservation_key,
        expected_ledger_id,
        expected_run_id,
        expected_terminal_status,
        expected_actual_cost_usd,
        action="repaired",
        dependencies=dependencies,
    )
    if early is not None or base is None:
        return None, early
    settled_at = dependencies.parse_time(expected_settled_at)
    spend = {
        dependencies.budget_scope: dependencies.money(
            expected_provider_current_spend
        ),
        "monthly_total": dependencies.money(expected_monthly_current_spend),
    }
    if (
        base.actual <= 0
        or settled_at is None
        or any(value is None for value in spend.values())
    ):
        return None, {"repaired": False, "reason": "invalid_expected_evidence"}
    return (
        CapRepairExpectation(
            base=base,
            settled_at=settled_at,
            spend={scope: value for scope, value in spend.items() if value is not None},
        ),
        None,
    )


def _exact_ledger(
    candidates: list[Any],
    run_id: str,
    dependencies: ReconciliationDependencies,
) -> tuple[dict[str, Any], dict[str, Any]]:
    exact: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in candidates:
        ledger = dict(raw)
        metadata = dependencies.json_object(ledger.get("metadata_json"))
        if metadata is None:
            reject("ledger_metadata_invalid")
        if str(metadata.get("apify_run_id") or "").strip() == run_id:
            exact.append((ledger, metadata))
    if len(exact) != 1:
        reject("ledger_run_not_unique")
    return exact[0]


def _validate_legacy_ledger(
    ledger: dict[str, Any],
    metadata: dict[str, Any],
    reservation: dict[str, Any],
    expectation: LegacyExpectation,
    dependencies: ReconciliationDependencies,
) -> None:
    actor = str(reservation.get("actor_id") or "").strip().replace("~", "/").lower()
    if int(ledger.get("id") or 0) != expectation.ledger_id:
        reject("ledger_id_mismatch")
    if (
        str(ledger.get("cron_task") or "").strip().lower()
        != dependencies.budget_scope
        or str(ledger.get("model_name") or "")
        .strip()
        .replace("~", "/")
        .lower()
        != actor
        or str(metadata.get("actor_id") or "")
        .strip()
        .replace("~", "/")
        .lower()
        != actor
        or str(metadata.get("operation") or "")
        != str(reservation.get("operation") or "")
        or metadata.get("unified_entry") is not True
        or str(metadata.get("scope") or "").strip().lower()
        != dependencies.budget_scope
        or metadata.get("budget_reservation_key") != ""
        or metadata.get("budget_reservation_settlement") != {}
    ):
        reject("ledger_not_legacy_budget_accounted")
    if str(metadata.get("run_status") or "").strip().upper() != expectation.status:
        reject("ledger_terminal_status_mismatch")


def _ledger_cost_evidence(
    metadata: dict[str, Any], dependencies: ReconciliationDependencies
) -> Decimal | None:
    if metadata.get("reconciled") is True:
        return dependencies.money(metadata.get("settled_usd"))
    if (
        metadata.get("pricing_basis") == "usage_settled"
        and metadata.get("estimated") is False
    ):
        return dependencies.money(metadata.get("usage_total_usd"))
    return None


def _validate_claim(claim: Any) -> dict[str, Any]:
    if not claim:
        reject("provider_claim_missing")
    claim_data = dict(claim)
    if str(claim_data.get("state") or "") not in {
        "completed",
        "failed",
        "blocked",
    }:
        reject("provider_claim_not_terminal")
    return claim_data


def _validate_reconcile_reservation(
    reservation: dict[str, Any],
    metadata: dict[str, Any],
    expectation: LegacyExpectation,
    dependencies: ReconciliationDependencies,
) -> None:
    audit = metadata.get(dependencies.reconciliation_audit_key)
    if str(reservation.get("state") or "") == "settled":
        if (
            isinstance(audit, dict)
            and int(audit.get("ledger_id") or 0) == expectation.ledger_id
            and str(audit.get("apify_run_id") or "") == expectation.run_id
            and str(audit.get("terminal_status") or "").upper()
            == expectation.status
            and dependencies.money(audit.get("actual_cost_usd"))
            == expectation.actual
            and dependencies.money(reservation.get("actual_cost_usd"))
            == expectation.actual
        ):
            reject("already_reconciled", ledger_id=expectation.ledger_id)
        reject("already_settled_by_other_path")
    if str(reservation.get("state") or "") != "provider_started":
        reject("reservation_not_provider_started")
    if (
        reservation.get("actual_cost_usd") is not None
        or reservation.get("settled_at") is not None
        or not str(reservation.get("provider_started_at") or "").strip()
    ):
        reject("reservation_state_inconsistent")
    if str(reservation.get("apify_run_id") or "").strip() != expectation.run_id:
        reject("reservation_run_id_mismatch")
    if audit is not None:
        reject("reservation_audit_conflict")


def _reconciliation_audit(
    reservation: dict[str, Any],
    claim: dict[str, Any],
    expectation: LegacyExpectation,
    now: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "ledger_id": expectation.ledger_id,
        "apify_run_id": expectation.run_id,
        "terminal_status": expectation.status,
        "actual_cost_usd": format(expectation.actual, "f"),
        "accounting_source": "legacy_unreserved_record_apify_run",
        "claim_snapshot": {
            "state": str(claim.get("state") or ""),
            "fence_token": int(claim.get("fence_token") or 0),
            "provider_run_id": str(claim.get("provider_run_id") or ""),
        },
        "ledger_inserted": False,
        "budget_caps_updated": False,
        "reconciled_at": now,
    }


def _run_reconciliation_transaction(
    conn: Any,
    lock: str,
    expectation: LegacyExpectation,
    dependencies: ReconciliationDependencies,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM vkpi_apify_budget_reservations WHERE reservation_key=?" + lock,
        (expectation.key,),
    ).fetchone()
    if not row:
        reject("missing_reservation")
    reservation = dict(row)
    reservation_meta = dependencies.json_object(reservation.get("metadata_json"))
    if reservation_meta is None:
        reject("reservation_metadata_invalid")
    _validate_reconcile_reservation(
        reservation, reservation_meta, expectation, dependencies
    )
    claim = _validate_claim(
        conn.execute(
            """
            SELECT state,lease_expires_at,provider_run_id,fence_token
            FROM vkpi_provider_execution_claims WHERE task_id=?
            """
            + lock,
            (str(reservation.get("task_id") or ""),),
        ).fetchone()
    )
    candidates = conn.execute(
        """
        SELECT id,cron_task,ai_provider,model_name,cost_usd,metadata_json
        FROM vkpi_ai_cost_ledger
        WHERE ai_provider='apify' AND metadata_json LIKE ?
        """
        + lock,
        (f"%{expectation.run_id}%",),
    ).fetchall()
    ledger, ledger_meta = _exact_ledger(
        candidates, expectation.run_id, dependencies
    )
    _validate_legacy_ledger(
        ledger, ledger_meta, reservation, expectation, dependencies
    )
    evidence = _ledger_cost_evidence(ledger_meta, dependencies)
    if (
        dependencies.money(ledger.get("cost_usd")) != expectation.actual
        or evidence != expectation.actual
    ):
        reject("ledger_actual_cost_mismatch")
    now = dependencies.iso(dependencies.utcnow())
    reservation_meta[dependencies.reconciliation_audit_key] = _reconciliation_audit(
        reservation, claim, expectation, now
    )
    updated = conn.execute(
        """
        UPDATE vkpi_apify_budget_reservations
        SET state='settled',actual_cost_usd=?,metadata_json=?,settled_at=?,updated_at=?
        WHERE reservation_key=? AND state='provider_started' AND apify_run_id=?
        """,
        (
            format(expectation.actual, "f"),
            dependencies.json_dumps(reservation_meta, ensure_ascii=False),
            now,
            now,
            expectation.key,
            expectation.run_id,
        ),
    )
    if int(getattr(updated, "rowcount", 0) or 0) != 1:
        raise RuntimeError("legacy reservation reconciliation lost its state fence")
    readback = conn.execute(
        "SELECT state,actual_cost_usd FROM vkpi_apify_budget_reservations WHERE reservation_key=?",
        (expectation.key,),
    ).fetchone()
    if (
        not readback
        or str(readback["state"]) != "settled"
        or dependencies.money(readback["actual_cost_usd"]) != expectation.actual
    ):
        raise RuntimeError("legacy reservation reconciliation readback mismatch")
    conn.commit()
    return {
        "settled": True,
        "ledger_id": expectation.ledger_id,
        "apify_run_id": expectation.run_id,
        "terminal_status": expectation.status,
        "actual_cost_usd": float(expectation.actual),
        "budget_caps_updated": False,
        "ledger_inserted": False,
    }


def reconcile_legacy_reservation(
    reservation_key: str,
    *,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Any,
    dependencies: ReconciliationDependencies,
) -> dict[str, Any]:
    expectation, early = _legacy_expectation(
        reservation_key,
        expected_ledger_id,
        expected_run_id,
        expected_terminal_status,
        expected_actual_cost_usd,
        action="settled",
        dependencies=dependencies,
    )
    if early is not None or expectation is None:
        return early or {"settled": False, "reason": "invalid_expected_evidence"}
    dependencies.ensure_schema()
    conn = dependencies.get_conn()
    lock = " FOR UPDATE" if dependencies.is_postgres_runtime() else ""
    try:
        return _run_reconciliation_transaction(
            conn, lock, expectation, dependencies
        )
    except ReconciliationRejected as exc:
        conn.rollback()
        return {"settled": False, "reason": exc.reason, **exc.extra}
    except Exception:
        conn.rollback()
        raise


def _validate_repair_audit(
    audit: Any,
    expectation: CapRepairExpectation,
    dependencies: ReconciliationDependencies,
) -> None:
    if audit is None:
        return
    audit_before = audit.get("cap_spend_before") if isinstance(audit, dict) else None
    if (
        isinstance(audit, dict)
        and int(audit.get("ledger_id") or 0) == expectation.base.ledger_id
        and str(audit.get("apify_run_id") or "") == expectation.base.run_id
        and str(audit.get("terminal_status") or "").upper()
        == expectation.base.status
        and dependencies.money(audit.get("actual_cost_usd"))
        == expectation.base.actual
        and dependencies.parse_time(audit.get("settled_at"))
        == expectation.settled_at
        and isinstance(audit_before, dict)
        and all(
            dependencies.money(audit_before.get(scope)) == value
            for scope, value in expectation.spend.items()
        )
    ):
        reject("already_repaired", ledger_id=expectation.base.ledger_id)
    reject("reservation_repair_audit_conflict")


def _validate_repair_reservation(
    reservation: dict[str, Any],
    metadata: dict[str, Any],
    expectation: CapRepairExpectation,
    dependencies: ReconciliationDependencies,
) -> None:
    _validate_repair_audit(
        metadata.get(dependencies.cap_repair_audit_key),
        expectation,
        dependencies,
    )
    if metadata.get(dependencies.reconciliation_audit_key) is not None:
        reject("reservation_reconciliation_audit_present")
    if metadata != {}:
        reject("reservation_metadata_not_empty")
    base = expectation.base
    if (
        str(reservation.get("state") or "") != "settled"
        or dependencies.money(reservation.get("actual_cost_usd")) != base.actual
        or dependencies.parse_time(reservation.get("settled_at"))
        != expectation.settled_at
        or not str(reservation.get("provider_started_at") or "").strip()
    ):
        reject("settled_reservation_evidence_mismatch")
    if str(reservation.get("apify_run_id") or "").strip() != base.run_id:
        reject("reservation_run_id_mismatch")


def _repair_audit(
    claim: dict[str, Any],
    expectation: CapRepairExpectation,
    before: dict[str, Decimal],
    after: dict[str, Decimal],
    now: str,
    dependencies: ReconciliationDependencies,
) -> dict[str, Any]:
    base = expectation.base
    return {
        "version": 1,
        "ledger_id": base.ledger_id,
        "apify_run_id": base.run_id,
        "terminal_status": base.status,
        "actual_cost_usd": format(base.actual, "f"),
        "settled_at": dependencies.iso(expectation.settled_at),
        "accounting_source": "legacy_unreserved_record_apify_run",
        "claim_snapshot": {
            "state": str(claim.get("state") or ""),
            "fence_token": int(claim.get("fence_token") or 0),
            "provider_run_id": str(claim.get("provider_run_id") or ""),
        },
        "cap_spend_before": {
            scope: format(value, "f") for scope, value in before.items()
        },
        "cap_spend_after": {
            scope: format(value, "f") for scope, value in after.items()
        },
        "ledger_modified": False,
        "reservation_state_or_actual_modified": False,
        "repaired_at": now,
    }


def _write_cap_repair(
    conn: Any,
    reservation: dict[str, Any],
    metadata: dict[str, Any],
    claim: dict[str, Any],
    expectation: CapRepairExpectation,
    before: dict[str, Decimal],
    after: dict[str, Decimal],
    dependencies: ReconciliationDependencies,
) -> None:
    base = expectation.base
    now = dependencies.iso(dependencies.utcnow())
    metadata[dependencies.cap_repair_audit_key] = _repair_audit(
        claim, expectation, before, after, now, dependencies
    )
    audit_update = conn.execute(
        """
        UPDATE vkpi_apify_budget_reservations SET metadata_json=?
        WHERE reservation_key=? AND state='settled' AND actual_cost_usd=? AND apify_run_id=?
          AND settled_at=? AND metadata_json='{}'
        """,
        (
            dependencies.json_dumps(metadata, ensure_ascii=False),
            base.key,
            format(base.actual, "f"),
            base.run_id,
            dependencies.iso(expectation.settled_at),
        ),
    )
    if int(getattr(audit_update, "rowcount", 0) or 0) != 1:
        raise RuntimeError("legacy double budget repair lost its reservation fence")
    for scope in (dependencies.budget_scope, "monthly_total"):
        updated = conn.execute(
            """
            UPDATE vkpi_provider_budget_caps
            SET current_spend=current_spend-?
            WHERE scope=? AND current_spend=?
            """,
            (format(base.actual, "f"), scope, format(before[scope], "f")),
        )
        if int(getattr(updated, "rowcount", 0) or 0) != 1:
            raise RuntimeError("legacy double budget repair lost its cap fence")


def _verify_cap_repair(
    conn: Any,
    expectation: CapRepairExpectation,
    after: dict[str, Decimal],
    dependencies: ReconciliationDependencies,
) -> None:
    readback_caps = {
        str(item["scope"]): dependencies.money(item["current_spend"])
        for item in conn.execute(
            """
            SELECT scope,current_spend FROM vkpi_provider_budget_caps
            WHERE scope IN ('provider:apify','monthly_total')
            """
        ).fetchall()
    }
    readback_reservation = conn.execute(
        """
        SELECT state,actual_cost_usd,metadata_json FROM vkpi_apify_budget_reservations
        WHERE reservation_key=?
        """,
        (expectation.base.key,),
    ).fetchone()
    if readback_caps != after or not readback_reservation:
        raise RuntimeError("legacy double budget repair readback mismatch")
    readback_meta = dependencies.json_object(readback_reservation["metadata_json"])
    if (
        str(readback_reservation["state"]) != "settled"
        or dependencies.money(readback_reservation["actual_cost_usd"])
        != expectation.base.actual
        or not isinstance(
            (readback_meta or {}).get(dependencies.cap_repair_audit_key), dict
        )
    ):
        raise RuntimeError("legacy double budget repair reservation readback mismatch")


def _run_cap_repair_transaction(
    conn: Any,
    lock: str,
    expectation: CapRepairExpectation,
    dependencies: ReconciliationDependencies,
) -> dict[str, Any]:
    base = expectation.base
    row = conn.execute(
        "SELECT * FROM vkpi_apify_budget_reservations WHERE reservation_key=?" + lock,
        (base.key,),
    ).fetchone()
    if not row:
        reject("missing_reservation")
    reservation = dict(row)
    metadata = dependencies.json_object(reservation.get("metadata_json"))
    if metadata is None:
        reject("reservation_metadata_invalid")
    _validate_repair_reservation(
        reservation, metadata, expectation, dependencies
    )
    claim = _validate_claim(
        conn.execute(
            """
            SELECT state,provider_run_id,fence_token
            FROM vkpi_provider_execution_claims WHERE task_id=?
            """
            + lock,
            (str(reservation.get("task_id") or ""),),
        ).fetchone()
    )
    candidates = conn.execute(
        """
        SELECT id,cron_task,ai_provider,model_name,cost_usd,metadata_json,occurred_at
        FROM vkpi_ai_cost_ledger
        WHERE ai_provider='apify' AND metadata_json LIKE ?
        """
        + lock,
        (f"%{base.run_id}%",),
    ).fetchall()
    ledger, ledger_meta = _exact_ledger(candidates, base.run_id, dependencies)
    _validate_legacy_ledger(
        ledger, ledger_meta, reservation, base, dependencies
    )
    ledger_occurred = dependencies.parse_time(ledger.get("occurred_at"))
    if ledger_occurred is None or ledger_occurred >= expectation.settled_at:
        reject("ledger_not_before_reservation_settlement")
    cost_evidence = _ledger_cost_evidence(ledger_meta, dependencies)
    if (
        dependencies.money(ledger.get("cost_usd")) != base.actual
        or cost_evidence != base.actual
    ):
        reject("ledger_actual_cost_mismatch")
    cap_rows = conn.execute(
        """
        SELECT scope,current_spend FROM vkpi_provider_budget_caps
        WHERE scope IN ('provider:apify','monthly_total')
        ORDER BY CASE scope WHEN 'provider:apify' THEN 0 ELSE 1 END
        """
        + lock
    ).fetchall()
    before = {
        str(item["scope"]): dependencies.money(item["current_spend"])
        for item in cap_rows
    }
    if before != expectation.spend:
        reject("budget_caps_current_spend_mismatch")
    if any(value is None or value < base.actual for value in before.values()):
        reject("budget_caps_not_safely_decrementable")
    safe_before = {
        scope: value for scope, value in before.items() if value is not None
    }
    after = {
        scope: value - base.actual for scope, value in safe_before.items()
    }
    _write_cap_repair(
        conn,
        reservation,
        metadata,
        claim,
        expectation,
        safe_before,
        after,
        dependencies,
    )
    _verify_cap_repair(conn, expectation, after, dependencies)
    conn.commit()
    return {
        "repaired": True,
        "ledger_id": base.ledger_id,
        "apify_run_id": base.run_id,
        "actual_cost_usd": float(base.actual),
        "cap_spend_before": {
            scope: float(value) for scope, value in safe_before.items()
        },
        "cap_spend_after": {
            scope: float(value) for scope, value in after.items()
        },
        "ledger_modified": False,
        "reservation_state_or_actual_modified": False,
    }


def repair_legacy_caps(
    reservation_key: str,
    *,
    expected_ledger_id: int,
    expected_run_id: str,
    expected_terminal_status: str,
    expected_actual_cost_usd: Any,
    expected_settled_at: str,
    expected_provider_current_spend: Any,
    expected_monthly_current_spend: Any,
    dependencies: ReconciliationDependencies,
) -> dict[str, Any]:
    expectation, early = _cap_expectation(
        reservation_key,
        expected_ledger_id,
        expected_run_id,
        expected_terminal_status,
        expected_actual_cost_usd,
        expected_settled_at,
        expected_provider_current_spend,
        expected_monthly_current_spend,
        dependencies,
    )
    if early is not None or expectation is None:
        return early or {"repaired": False, "reason": "invalid_expected_evidence"}
    dependencies.ensure_schema()
    conn = dependencies.get_conn()
    lock = " FOR UPDATE" if dependencies.is_postgres_runtime() else ""
    try:
        return _run_cap_repair_transaction(
            conn, lock, expectation, dependencies
        )
    except ReconciliationRejected as exc:
        conn.rollback()
        return {"repaired": False, "reason": exc.reason, **exc.extra}
    except Exception:
        conn.rollback()
        raise
