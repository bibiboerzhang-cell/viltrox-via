"""Atomic correction of an existing run's observed Apify charge.

The public ActorRun schema exposes optional usageTotalUsd, not a documented
final-charge flag. Observations remain provisional even after they stabilize:
https://docs.apify.com/api/client/js/reference/interface/ActorRun
No provider I/O, Actor restart, budget rollover or schema mutation lives here.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domains.costs.budget_guard_persistence import cost_decimal, money_db_param

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
SCOPES = ("provider:apify", "monthly_total")
STABLE_SECONDS = 300
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_BUSY_SQLSTATES = frozenset({"55P03", "40P01"})


def _busy_sqlstate(error: BaseException) -> str | None:
    """Recognize only lock contention, including preserved wrapper causes.

    PostgresCompatCursor rolls back and re-raises the original psycopg error.
    Other callers may add a cause wrapper; never inspect exception text or
    turn an unrelated database SQLSTATE into a retryable accounting result.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        diag = getattr(current, "diag", None)
        state = (getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
                 or getattr(diag, "sqlstate", None))
        if state is not None:
            return state if isinstance(state, str) and state in _BUSY_SQLSTATES else None
        current = current.__cause__ or current.__context__
    return None


def observed_charge(run: Any) -> Decimal | None:
    if not isinstance(run, dict) or run.get("status") not in TERMINAL:
        return None
    value = run.get("usageTotalUsd")
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        amount = cost_decimal(value)
    except ValueError:
        return None
    # A reported zero can be real or a delayed charge. Do not release a paid
    # reservation or overwrite a known positive charge on this evidence alone.
    return amount if amount > 0 else None


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("metadata_invalid")
    return parsed


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _same_budget_window(row: dict, incurred: datetime, now: datetime) -> bool:
    """Only the original, still-current UTC month may receive its delta."""
    if (incurred.year, incurred.month) != (now.year, now.month):
        return False
    next_month = (datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
                  if now.month == 12 else datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc))
    try:
        return _time(row.get("reset_at")) == next_month
    except (TypeError, ValueError):
        return False


def _insert_initial_ledger(
    conn: Any, reservation: dict, initial_entry: dict, *,
    run_id: str, key: str, postgres: bool,
) -> tuple[dict, dict]:
    """Called only after the unique reservation and matching ledger rows lock."""
    actual = reservation.get("actual_cost_usd")
    baseline = cost_decimal(actual) if actual is not None else Decimal("0")
    stamp = (reservation.get("settled_at") if actual is not None else None)
    stamp = stamp or reservation.get("provider_started_at") or reservation.get("reserved_at")
    incurred = _iso(_time(stamp))
    meta = {**initial_entry, "scope": "provider:apify", "apify_run_id": run_id,
            "budget_reservation_key": key, "cost_budget_accounted": actual is not None,
            "estimated": actual is None, "unified_entry": True}
    created = conn.execute(
        "INSERT INTO vkpi_ai_cost_ledger (cron_task,ai_provider,model_name,cost_usd,"
        "tokens_in,tokens_out,metadata_json,occurred_at) VALUES (?, 'apify', ?, ?,0,0,?,?) RETURNING id",
        ("provider:apify", str(initial_entry.get("actor_id") or ""),
         money_db_param(baseline, postgres=postgres), json.dumps(meta, ensure_ascii=False), incurred),
    ).fetchone()
    if created is None:
        raise ValueError("ledger_insert_unconfirmed")
    return ({"id": int(created["id"]), "cost_usd": baseline,
             "metadata_json": json.dumps(meta), "occurred_at": incurred}, meta)


def _accounting_baseline(
    reservation: dict, ledger: dict, meta: dict, amount: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    old_cost = cost_decimal(ledger["cost_usd"])
    actual = reservation.get("actual_cost_usd")
    unaccounted = (actual is None and reservation.get("state") != "settled"
                   and meta.get("cost_budget_accounted") is False)
    if not unaccounted and (actual is None or cost_decimal(actual) != old_cost):
        raise ValueError("reservation_ledger_cost_drift_requires_review")
    budget_before = Decimal(0) if unaccounted else old_cost
    if amount < budget_before:
        raise ValueError("cost_decrease_requires_review")
    return old_cost, budget_before, amount - budget_before


def _lock_scope_rows(
    conn: Any, lock: str, *, budget_before: Decimal, delta: Decimal,
    incurred: datetime, now: datetime,
) -> dict[str, dict]:
    scope_rows = {}
    for scope in SCOPES:
        row = conn.execute(
            "SELECT scope,current_spend,reset_at FROM vkpi_provider_budget_caps WHERE scope=?" + lock,
            (scope,),
        ).fetchone()
        if row is None:
            raise ValueError("budget_scope_missing")
        scope_rows[scope] = dict(row)
        if delta and cost_decimal(scope_rows[scope]["current_spend"]) < budget_before:
            raise ValueError("budget_baseline_missing_requires_review")
        if delta and not _same_budget_window(scope_rows[scope], incurred, now):
            raise ValueError("budget_window_requires_review")
    return scope_rows


def _verify_readback(
    conn: Any, *, ledger_id: int, key: str, amount: Decimal,
    scope_rows: dict[str, dict], delta: Decimal,
) -> None:
    ledger_readback = conn.execute("SELECT cost_usd FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone()
    reservation_readback = conn.execute(
        "SELECT actual_cost_usd,state FROM vkpi_apify_budget_reservations WHERE reservation_key=?", (key,),
    ).fetchone()
    if (not ledger_readback or not reservation_readback
            or cost_decimal(ledger_readback["cost_usd"]) != amount
            or cost_decimal(reservation_readback["actual_cost_usd"]) != amount
            or reservation_readback["state"] != "settled"):
        raise ValueError("ledger_reservation_readback_failed")
    for scope, row in scope_rows.items():
        fresh = conn.execute("SELECT current_spend,reset_at FROM vkpi_provider_budget_caps WHERE scope=?", (scope,)).fetchone()
        if (not fresh or cost_decimal(fresh["current_spend"]) != cost_decimal(row["current_spend"]) + delta
                or fresh["reset_at"] != row["reset_at"]):
            raise ValueError("budget_delta_readback_failed")


def reconcile_run_observation(
    conn: Any, run: dict, *, postgres: bool, now: datetime,
    expected_ledger_id: int | None = None,
    initial_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update detail, reservation and two original cumulative scopes together.

Uses the existing reservation's unique run identity and PostgreSQL NOWAIT row
locks. Contention returns a reconciliation-only retry receipt instead of
waiting with locks held in the older provider-start path's opposite order.
SQLite test/compatibility execution acquires the writer
lock before reading. Ambiguous legacy accounting and cross-window adjustments
remain manual-review work; they never partially update one of the three books.
    """
    run_id = str(run.get("id") or "")
    amount = observed_charge(run)
    if not _RUN_ID.fullmatch(run_id) or amount is None:
        return {"reconciled": False, "reason": "charge_observation_unconfirmed"}
    now = _time(now)
    lock = " FOR UPDATE NOWAIT" if postgres else ""
    try:
        if not postgres:
            conn.execute("BEGIN IMMEDIATE")
        reservations = conn.execute(
            "SELECT * FROM vkpi_apify_budget_reservations WHERE apify_run_id=?" + lock,
            (run_id,),
        ).fetchall()
        if len(reservations) != 1:
            raise ValueError("reservation_run_not_unique")
        reservation = dict(reservations[0])
        key = str(reservation.get("reservation_key") or "")
        if (run.get("_vkpi_budget_reservation_key") is not None
                and run["_vkpi_budget_reservation_key"] != key):
            raise ValueError("run_reservation_identity_mismatch")
        if reservation.get("state") not in {"settled", "provider_started", "unknown"}:
            raise ValueError("reservation_state_unconfirmed")
        # The bound parameter is only a prefilter; exact parsed identity below
        # rejects substring matches and supports old compact JSON formatting.
        candidates = conn.execute(
            "SELECT id,cost_usd,metadata_json,occurred_at FROM vkpi_ai_cost_ledger "
            "WHERE ai_provider='apify' AND metadata_json LIKE ?" + lock,
            (f"%{run_id}%",),
        ).fetchall()
        rows = [(dict(row), _json(row["metadata_json"])) for row in candidates]
        rows = [(row, meta) for row, meta in rows if meta.get("apify_run_id") == run_id]
        inserted = False
        if not rows and initial_entry is not None and expected_ledger_id is None:
            # The unique reservation row is locked before checking/inserting
            # the first ledger row, so concurrent canonical recorders serialize.
            rows = [_insert_initial_ledger(
                conn, reservation, initial_entry, run_id=run_id, key=key, postgres=postgres,
            )]
            inserted = True
        if len(rows) != 1:
            raise ValueError("ledger_run_not_unique")
        ledger, meta = rows[0]
        ledger_id = int(ledger["id"])
        if expected_ledger_id is not None and ledger_id != expected_ledger_id:
            raise ValueError("ledger_identity_mismatch")
        if not key or meta.get("budget_reservation_key") != key:
            raise ValueError("ledger_reservation_identity_mismatch")
        old_cost, budget_before, delta = _accounting_baseline(reservation, ledger, meta, amount)
        incurred = _time(ledger["occurred_at"])
        scope_rows = _lock_scope_rows(
            conn, lock, budget_before=budget_before, delta=delta, incurred=incurred, now=now,
        )
        observation = _json(meta.get("charge_observation"))
        same_observation = (old_cost == amount and observation.get("amount_usd") == format(amount, "f"))
        stable_since = _time(observation["stable_since"]) if same_observation else now
        if stable_since > now:
            raise ValueError("observation_time_invalid")
        observed_state = "stable_observation" if (now - stable_since).total_seconds() >= STABLE_SECONDS else "provisional"
        observation = {"amount_usd": format(amount, "f"), "observed_at": _iso(now),
                       "stable_since": _iso(stable_since), "state": observed_state,
                       "provider_final": False, "stable_window_seconds": STABLE_SECONDS}
        meta.update(usage_total_usd=float(amount), estimated=False, pricing_basis="usage_observed",
                    cost_budget_accounted=True, cost_state="provisional", provider_cost_final=False,
                    charge_observation=observation, reconciled=False)
        if delta:
            meta["last_reconcile_delta_usd"] = format(delta, "f")
        reservation_meta = _json(reservation.get("metadata_json"))
        reservation_meta.update(charge_observation=observation, provider_cost_final=False,
                                cost_ledger_id=ledger_id)
        for scope in SCOPES:
            cursor = conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend=COALESCE(current_spend,0)+? WHERE scope=?",
                (money_db_param(delta, postgres=postgres), scope),
            )
            if cursor.rowcount != 1:
                raise ValueError("budget_update_unconfirmed")
        cursor = conn.execute(
            "UPDATE vkpi_apify_budget_reservations SET actual_cost_usd=?,state='settled',"
            "settled_at=COALESCE(settled_at,?),updated_at=?,metadata_json=? WHERE reservation_key=?",
            (money_db_param(amount, postgres=postgres), _iso(now), _iso(now),
             json.dumps(reservation_meta, ensure_ascii=False), key),
        )
        if cursor.rowcount != 1:
            raise ValueError("reservation_update_unconfirmed")
        cursor = conn.execute(
            "UPDATE vkpi_ai_cost_ledger SET cost_usd=?,metadata_json=? WHERE id=?",
            (money_db_param(amount, postgres=postgres), json.dumps(meta, ensure_ascii=False), ledger_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("ledger_update_unconfirmed")
        _verify_readback(conn, ledger_id=ledger_id, key=key, amount=amount, scope_rows=scope_rows, delta=delta)
        conn.commit()
        return {"reconciled": True, "recorded": True, "ledger_id": ledger_id,
                "apify_run_id": run_id, "cost_usd": float(amount), "delta_usd": float(delta),
                "persisted_cost_usd": format(amount, "f"), "cost_micro_usd": int(amount * 1_000_000),
                "scope": "provider:apify", "ai_provider": "apify",
                "model_name": str(meta.get("actor_id") or ""), "occurred_at": _iso(incurred),
                "tokens_in": 0, "tokens_out": 0,
                "cost_state": "provisional", "provider_cost_final": False,
                "observation_state": observed_state, "accounting_verified": True,
                "scopes_updated": list(SCOPES), "budget_reservation_key": key,
                "ledger_inserted": inserted}
    except Exception as exc:
        conn.rollback()
        state = _busy_sqlstate(exc)
        if state is not None:
            return {"reconciled": False, "recorded": False, "reason": "reconciliation_busy",
                    "apify_run_id": run_id, "sqlstate": state,
                    "next_action": "retry_existing_run_reconciliation",
                    "automatic_provider_retry_allowed": False}
        if isinstance(exc, ValueError):
            return {"reconciled": False, "recorded": False, "reason": str(exc)}
        raise
