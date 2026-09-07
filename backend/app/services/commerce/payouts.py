"""
services/commerce/payouts.py — Payout cycle + lifecycle management

Cycle lifecycle:
    upcoming -> active (when its start_date is reached)
    active   -> processing (admin clicks 'Process now' or cycle.process_date hits)
    processing -> processed (only when every payout is terminal)
    processing -> active/upcoming (blocked before dispatch; safe to retry)

Payout lifecycle:
    pending -> approved -> paid
    pending -> held (missing info / suspected fraud)
    approved -> paying -> paid/failed (confirmed terminal provider receipt)
    paying -> payment_unknown (unconfirmed outcome; never automatically retry)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.costs.common import normalize_currency

logger = get_logger(__name__)
_LEGACY_PLACEHOLDER_SQL = (
    "(substr(lower(trim(coalesce(paid_tx_id,''))),1,8)='pp_stub_' "
    "OR substr(lower(trim(coalesce(paid_tx_id,''))),1,10)='bank_stub_')"
)


class PayoutNotDispatched(RuntimeError):
    """Only a server adapter that has not issued external I/O may raise this."""


@dataclass(frozen=True)
class PayoutDispatchReceipt:
    """Server-adapter evidence of one final transfer, never a client payload.

    Accepted/queued batches are not terminal receipts. No live adapter is
    configured here; adding one requires provider-side idempotency/reconciliation.
    """

    payout_id: int
    method: str
    amount_cents: int
    currency: str
    status: str
    transaction_id: str


def _month_bounds(anchor: datetime) -> tuple[datetime, datetime]:
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(seconds=1)
    return start, end


def _shift_month(anchor: datetime, delta: int) -> datetime:
    year = anchor.year + ((anchor.month - 1 + delta) // 12)
    month = ((anchor.month - 1 + delta) % 12) + 1
    return anchor.replace(year=year, month=month, day=1)


def _cycle_id_for(date_value: datetime) -> str:
    return date_value.strftime("%b-%Y").lower()


def _cycle_label_for(date_value: datetime) -> str:
    return date_value.strftime("%b %Y")


def _ensure_seed_cycles(conn) -> None:
    existing = conn.execute("SELECT COUNT(*) AS n FROM payout_cycles").fetchone()
    if (existing["n"] or 0) > 0:
        return

    now = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    template = [
        (_shift_month(now, 1), "upcoming"),
        (now, "active"),
        (_shift_month(now, -1), "processed"),
        (_shift_month(now, -2), "processed"),
    ]
    for month_start, status in template:
        start, end = _month_bounds(month_start)
        process_date = end + timedelta(days=1, hours=9)
        conn.execute(
            """
            INSERT OR IGNORE INTO payout_cycles (
                id, label, start_date, end_date, status, process_date, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _cycle_id_for(month_start),
                _cycle_label_for(month_start),
                start.isoformat() + "Z",
                end.isoformat() + "Z",
                status,
                process_date.isoformat() + "Z",
                process_date.isoformat() + "Z" if status == "processed" else None,
            ),
        )
    conn.commit()


# =========================================================================
# Cycle queries
# =========================================================================

def list_cycles() -> dict:
    conn = get_conn()
    _ensure_seed_cycles(conn)
    rows = conn.execute(
        "SELECT * FROM payout_cycles ORDER BY start_date DESC LIMIT 12"
    ).fetchall()
    cycles = []
    for r in rows:
        c = dict(r)
        counts = _cycle_counts(r["id"])
        c.update(counts)
        cycles.append(c)
    return {"cycles": cycles}


def get_cycle_detail(cycle_id: str) -> dict | None:
    conn = get_conn()
    _ensure_seed_cycles(conn)
    row = conn.execute(
        "SELECT * FROM payout_cycles WHERE id = ?", (cycle_id,)
    ).fetchone()
    if not row:
        return None

    payouts = conn.execute(
        """
        SELECT p.*, u.creator_code AS user_handle, u.email AS user_email,
               (
                   SELECT ua.country
                   FROM user_addresses ua
                   WHERE ua.user_id = u.id
                   ORDER BY ua.is_default DESC, ua.id ASC
                   LIMIT 1
               ) AS user_country
        FROM payouts p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.cycle_id = ?
        ORDER BY p.amount_cents DESC
        """,
        (cycle_id,),
    ).fetchall()

    return {
        "cycle": {**dict(row), **_cycle_counts(cycle_id)},
        "payouts": [_project_payout(p) for p in payouts],
    }


def _cycle_counts(cycle_id: str) -> dict:
    conn = get_conn()
    r = conn.execute(
        f"""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='approved' THEN amount_cents ELSE 0 END) AS approved_cents,
          SUM(CASE WHEN status='pending'  THEN amount_cents ELSE 0 END) AS pending_cents,
          SUM(CASE WHEN status='held'     THEN amount_cents ELSE 0 END) AS held_cents,
          SUM(CASE WHEN status='paid' AND NOT {_LEGACY_PLACEHOLDER_SQL} THEN amount_cents ELSE 0 END) AS paid_cents,
          SUM(CASE WHEN status='paid' AND {_LEGACY_PLACEHOLDER_SQL} THEN amount_cents ELSE 0 END) AS unverified_cents,
          SUM(CASE WHEN status='paid' AND {_LEGACY_PLACEHOLDER_SQL} THEN 1 ELSE 0 END) AS blocked_legacy_receipt_count,
          COUNT(DISTINCT user_id) AS unique_creators
        FROM payouts WHERE cycle_id = ?
        """,
        (cycle_id,),
    ).fetchone()
    currency_summary = _cycle_currency_summary(conn, cycle_id)
    result = {
        "approved_cents": r["approved_cents"] or 0,
        "pending_cents":  r["pending_cents"]  or 0,
        "held_cents":     r["held_cents"]     or 0,
        "paid_cents":     r["paid_cents"]     or 0,
        "unverified_cents": r["unverified_cents"] or 0,
        "blocked_legacy_receipt_count": r["blocked_legacy_receipt_count"] or 0,
        "creator_count":  r["unique_creators"] or 0,
        **currency_summary,
    }
    if currency_summary["aggregation_status"] in {"mixed_currency", "unknown_currency"}:
        for key in ("approved_cents", "pending_cents", "held_cents", "paid_cents", "unverified_cents"):
            result[key] = None  # Never add amounts belonging to different units.
    return result


def _payout_currency(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("payout currency is missing or unsupported")
    currency = normalize_currency(value)
    if not currency:
        raise ValueError("payout currency is missing or unsupported")
    return "CNY" if currency == "RMB" else currency


def _cycle_currency_summary(conn: Any, cycle_id: str) -> dict:
    rows = conn.execute("SELECT DISTINCT currency FROM payouts WHERE cycle_id=?", (cycle_id,)).fetchall()
    try:
        currencies = {_payout_currency(row["currency"]) for row in rows}
    except ValueError:
        return {"currency": None, "aggregation_status": "unknown_currency"}
    status = "empty" if not currencies else "single_currency" if len(currencies) == 1 else "mixed_currency"
    return {"currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "aggregation_status": status}


# =========================================================================
# Accrual (called from scheduler daily)
# =========================================================================

def accrue_cycle(cycle_id: str) -> dict:
    """Compute pending payouts from orders in cycle's window."""
    conn = get_conn()
    try:
        # Serialize accrual with process_cycle's cycle CAS. A completed or
        # in-flight cycle cannot gain new payable rows after its final count.
        lock = " FOR UPDATE" if is_postgres_runtime() else ""
        cycle = conn.execute(
            "SELECT * FROM payout_cycles WHERE id = ?" + lock, (cycle_id,),
        ).fetchone()
        if not cycle or cycle["status"] not in {"active", "upcoming"}:
            raise ValueError("payout cycle is not open for accrual")
        return _accrue_open_cycle(conn, cycle_id, cycle)
    except BaseException:
        conn.rollback()
        raise


def _accrue_open_cycle(conn: Any, cycle_id: str, cycle: Any) -> dict:

    # Aggregate commission by user for orders in window
    # CompatRow serializes PostgreSQL JSON arrays for the existing TEXT ledger.
    order_ids_aggregate = "json_agg(id)" if is_postgres_runtime() else "json_group_array(id)"
    aggregates = conn.execute(
        f"""
        SELECT attribution_user_id AS user_id, upper(trim(currency)) AS currency,
               COUNT(*) AS orders,
               SUM(subtotal_cents) AS gmv,
               SUM(commission_cents) AS commission,
               {order_ids_aggregate} AS order_ids
        FROM orders
        WHERE placed_at BETWEEN ? AND ?
          AND status = 'paid'
          AND attribution_user_id IS NOT NULL
          AND commission_cents > 0
        GROUP BY attribution_user_id, upper(trim(currency))
        """,
        (cycle["start_date"], cycle["end_date"]),
    ).fetchall()

    aggregates = _validated_accruals(aggregates)
    created_count = 0
    for agg in aggregates:
        # upsert by (cycle_id, user_id)
        existing = conn.execute(
            "SELECT id, currency, status FROM payouts WHERE cycle_id = ? AND user_id = ?",
            (cycle_id, agg["user_id"]),
        ).fetchone()
        if existing:
            if existing["status"] != "pending" and _payout_currency(existing["currency"]) != agg["currency"]:
                raise ValueError("approved payout currency cannot change during accrual")
            conn.execute(
                """UPDATE payouts SET
                    amount_cents = ?, gmv_cents = ?, order_count = ?, order_ids_json = ?, currency = ?
                   WHERE id = ? AND status = 'pending'""",
                (
                    agg["commission"], agg["gmv"], agg["orders"], agg["order_ids"],
                    agg["currency"], existing["id"],
                ),
            )
        else:
            # Current viltrox-2.0 does not store payout profiles on users.
            # Fall back to the user's email as a PayPal target when available.
            user = conn.execute(
                "SELECT email FROM users WHERE id = ?",
                (agg["user_id"],),
            ).fetchone()
            method = "paypal"
            details_obj = {"paypal_email": user["email"]} if user and user["email"] else {}
            details = json.dumps(details_obj)

            # Hold reason: missing PayPal email
            hold_reason = None
            status = "pending"
            if method == "paypal" and not details_obj.get("paypal_email"):
                hold_reason = "Missing PayPal email"
                status = "held"

            conn.execute(
                """INSERT INTO payouts (
                    cycle_id, user_id, amount_cents, gmv_cents, order_count,
                    order_ids_json, method, method_details, status, hold_reason, currency
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cycle_id, agg["user_id"], agg["commission"], agg["gmv"], agg["orders"],
                    agg["order_ids"], method, details, status, hold_reason, agg["currency"],
                ),
            )
            created_count += 1

    conn.commit()
    return {"accrued_count": created_count, "cycle_id": cycle_id}


def _validated_accruals(rows: Any) -> list[dict]:
    validated, seen_users = [], set()
    for row in rows:
        item = dict(row)
        item["currency"] = _payout_currency(item["currency"])
        if item["user_id"] in seen_users:
            raise ValueError("mixed currencies cannot accrue into one creator payout")
        seen_users.add(item["user_id"])
        validated.append(item)
    return validated


# =========================================================================
# Approve / hold / release
# =========================================================================

def approve_all(cycle_id: str, admin_id: int) -> dict:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE payouts SET status = 'approved',
            approved_at = datetime('now'), approved_by = ?
           WHERE cycle_id = ? AND status = 'pending'""",
        (admin_id, cycle_id),
    )
    conn.commit()
    return {"approved_count": cur.rowcount, "cycle_id": cycle_id}


def approve_one(payout_id: int, admin_id: int) -> dict:
    conn = get_conn()
    changed = conn.execute(
        """UPDATE payouts SET status='approved',
            approved_at = datetime('now'), approved_by = ?
           WHERE id = ? AND status IN ('pending','held')""",
        (admin_id, payout_id),
    )
    _require_changed_payout(conn, changed)
    return {"ok": True, "payout_id": payout_id}


def hold_one(payout_id: int, reason: str, admin_id: int) -> dict:
    conn = get_conn()
    changed = conn.execute(
        "UPDATE payouts SET status='held', hold_reason=? "
        "WHERE id = ? AND status IN ('pending','approved','held')",
        (reason, payout_id),
    )
    _require_changed_payout(conn, changed)
    return {"ok": True, "payout_id": payout_id}


def release_one(payout_id: int, admin_id: int) -> dict:
    conn = get_conn()
    changed = conn.execute(
        "UPDATE payouts SET status='pending', hold_reason=NULL "
        "WHERE id = ? AND status='held'",
        (payout_id,),
    )
    _require_changed_payout(conn, changed)
    return {"ok": True, "payout_id": payout_id}


def adjust_one(
    payout_id: int, new_amount_cents: int, reason: str, admin_id: int
) -> dict:
    if type(new_amount_cents) is not int or new_amount_cents < 0:
        raise ValueError("payout amount must be non-negative integer cents")
    conn = get_conn()
    changed = conn.execute(
        "UPDATE payouts SET amount_cents=?, hold_reason=? "
        "WHERE id = ? AND status IN ('pending','held')",
        (new_amount_cents, f"ADJUSTED: {reason}", payout_id),
    )
    _require_changed_payout(conn, changed)
    return {"ok": True, "payout_id": payout_id, "new_amount_cents": new_amount_cents}


def _require_changed_payout(conn: Any, changed: Any) -> None:
    if changed.rowcount != 1:
        conn.rollback()
        raise ValueError("payout state does not allow this action")
    try:
        conn.commit()
    except BaseException:
        conn.rollback()  # Keep the earlier durable paying claim if settlement was not saved.
        raise


def user_history(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, c.label AS cycle_label
           FROM payouts p JOIN payout_cycles c ON p.cycle_id = c.id
           WHERE p.user_id = ? AND p.status = 'paid'
           ORDER BY p.paid_at DESC""",
        (user_id,),
    ).fetchall()
    return [_project_payout(r) for r in rows]


def _project_payout(row: Any) -> dict:
    result = dict(row)
    transaction = str(result.get("paid_tx_id") or "").strip().lower()
    if result.get("status") == "paid" and transaction.startswith(("pp_stub_", "bank_stub_")):
        result.update(status="payment_unknown", stored_status="paid", payment_confirmed=None,
                      payment_verification="legacy_placeholder_receipt")
    return result


# =========================================================================
# Process cycle (no live payment adapter is configured)
# =========================================================================

def process_cycle(cycle_id: str, admin_id: int) -> dict:
    """Claim once; only confirmed final server receipts can transition to paid."""
    conn = get_conn()
    cycle = conn.execute(
        "SELECT * FROM payout_cycles WHERE id = ?", (cycle_id,)
    ).fetchone()
    if not cycle:
        raise ValueError("cycle not found")
    if cycle["status"] not in ("active", "upcoming"):
        raise ValueError(f"cycle {cycle_id} is in status {cycle['status']}; cannot process")

    # 2026-07-18 竞态修:认领 cycle 用 CAS(带状态谓词+rowcount),两个并发
    # process 只有一个能把 cycle 从 active/upcoming 抢到 processing;输家 409。
    cur = conn.cursor()
    cur.execute(
        "UPDATE payout_cycles SET status='processing', processed_by=? "
        "WHERE id = ? AND status IN ('active','upcoming')",
        (admin_id, cycle_id),
    )
    conn.commit()
    if int(getattr(cur, "rowcount", 0) or 0) != 1:
        raise ValueError(f"cycle {cycle_id} already being processed by another request")

    approved = conn.execute(
        "SELECT * FROM payouts WHERE cycle_id = ? AND status = 'approved'",
        (cycle_id,),
    ).fetchall()

    outcomes = [_process_payout(conn, dict(p)) for p in approved]
    completion = _finish_cycle(conn, cycle_id, cycle["status"])
    has_called = any(item["status"] in {"paid", "failed"} for item in outcomes)
    unknown = completion["unknown_count"] > 0
    not_called = [item for item in outcomes if item["status"] == "not_called"]
    return {
        "status": "unknown" if unknown else "processed" if completion["cycle_complete"]
        else "blocked" if not_called else "partial",
        "processed_count": sum(item["status"] == "paid" for item in outcomes),
        "failed": [item for item in outcomes if item["status"] == "failed"],
        "not_called": not_called,
        "unknown": [item for item in outcomes if item["status"] == "payment_unknown"],
        "provider_calls_performed": True if has_called else None if unknown else False,
        "cycle_id": cycle_id,
        **completion,
    }


def _confirmed_receipt(receipt: Any, payout: dict) -> bool:
    if not isinstance(receipt, PayoutDispatchReceipt):
        return False
    if type(receipt.payout_id) is not int or type(receipt.amount_cents) is not int:
        return False
    try:
        expected_currency = _payout_currency(payout.get("currency"))
        observed_currency = _payout_currency(receipt.currency)
    except ValueError:
        return False
    expected = (payout["id"], payout["method"], payout["amount_cents"], expected_currency)
    observed = (receipt.payout_id, receipt.method, receipt.amount_cents, observed_currency)
    tx = receipt.transaction_id
    return (
        observed == expected and receipt.amount_cents > 0
        and receipt.method in {"paypal", "bank"} and receipt.status in {"paid", "failed"}
        and isinstance(tx, str) and 0 < len(tx) <= 200 and tx == tx.strip()
        and not any(char.isspace() for char in tx)
        and not tx.lower().startswith(("pp_stub_", "bank_stub_", "stub_"))
    )


def _dispatch_result(payout: dict) -> tuple[str, str | None, str]:
    try:
        payout = {**payout, "currency": _payout_currency(payout.get("currency"))}
    except ValueError:
        return "not_called", None, "payout_currency_invalid"
    if type(payout.get("amount_cents")) is not int or payout["amount_cents"] <= 0:
        return "not_called", None, "payout_amount_invalid"
    try:
        receipt = _dispatch_payout(dict(payout))  # Adapter mutation cannot rewrite the approved contract.
        if _confirmed_receipt(receipt, payout):
            return receipt.status, receipt.transaction_id, "provider_confirmed_final"
        return "payment_unknown", None, "invalid_provider_receipt"
    except PayoutNotDispatched:
        return "not_called", None, "payment_provider_not_configured"
    except Exception as exc:
        # No raw provider message/contact/payment data in logs or API receipts.
        logger.warning("payout dispatch outcome unknown | id=%s error_type=%s", payout["id"], type(exc).__name__)
        return "payment_unknown", None, "provider_outcome_unknown"


def _process_payout(conn: Any, payout: dict) -> dict:
    claim = conn.execute(
        "UPDATE payouts SET status='paying' WHERE id=? AND status='approved' RETURNING *",
        (payout["id"],),
    )
    claimed_row = claim.fetchone()
    conn.commit()  # A crash after this point must never grant an automatic replay.
    if claimed_row is None:
        return {"payout_id": payout["id"], "status": "not_claimed"}
    payout = dict(claimed_row)  # Bind the receipt to claim-time amount and approval.
    status, transaction_id, reason = _dispatch_result(payout)
    persisted_status = "approved" if status == "not_called" else status
    changed = conn.execute(
        "UPDATE payouts SET status=?, paid_tx_id=?, "
        "paid_at=CASE WHEN ?='paid' THEN datetime('now') ELSE NULL END, "
        "failed_at=CASE WHEN ?='failed' THEN datetime('now') ELSE NULL END, failed_reason=? "
        "WHERE id=? AND status='paying'",
        (persisted_status, transaction_id if status == "paid" else None, status, status,
         None if status == "paid" else reason, payout["id"]),
    )
    _require_changed_payout(conn, changed)
    return {"payout_id": payout["id"], "status": status, "reason": reason}


def _finish_cycle(conn: Any, cycle_id: str, original_status: str) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS unresolved, "
        "SUM(CASE WHEN status IN ('paying','payment_unknown') THEN 1 ELSE 0 END) AS unknown_count, "
        f"SUM(CASE WHEN status='paid' AND {_LEGACY_PLACEHOLDER_SQL} THEN 1 ELSE 0 END) AS legacy_count "
        f"FROM payouts WHERE cycle_id=? AND (status NOT IN ('paid','failed') OR "
        f"(status='paid' AND {_LEGACY_PLACEHOLDER_SQL}))",
        (cycle_id,),
    ).fetchone()
    legacy = int(row["legacy_count"] or 0)
    unresolved, unknown = int(row["unresolved"]), int(row["unknown_count"] or 0) + legacy
    status = "processed" if unresolved == 0 else "processing" if unknown else original_status
    changed = conn.execute(
        "UPDATE payout_cycles SET status=?, "
        "processed_at=CASE WHEN ?='processed' THEN datetime('now') ELSE NULL END "
        "WHERE id=? AND status='processing'",
        (status, status, cycle_id),
    )
    _require_changed_payout(conn, changed)
    return {"cycle_status": status, "cycle_complete": unresolved == 0,
            "unresolved_count": unresolved, "unknown_count": unknown,
            "blocked_legacy_receipt_count": legacy}


def _dispatch_payout(payout: dict) -> PayoutDispatchReceipt:
    """No PayPal/bank adapter exists: block before any external I/O."""
    raise PayoutNotDispatched("payment_provider_not_configured")


# =========================================================================
# Disputes
# =========================================================================

def list_disputes(status: str | None = "open") -> dict:
    conn = get_conn()
    where, params = [], []
    if status:
        where.append("d.status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT d.*, u.creator_code AS user_handle
            FROM payout_disputes d LEFT JOIN users u ON d.user_id = u.id
            {where_sql} ORDER BY d.created_at DESC""",
        params,
    ).fetchall()
    return {"disputes": [dict(r) for r in rows]}


def resolve_dispute(
    dispute_id: int, resolution: str, note: str, admin_id: int
) -> dict:
    conn = get_conn()
    new_status = "resolved_uphold" if resolution == "uphold" else "resolved_overturn"
    conn.execute(
        """UPDATE payout_disputes SET status=?, resolved_by=?, resolved_at=datetime('now'),
           resolution_note=? WHERE id = ?""",
        (new_status, admin_id, note, dispute_id),
    )
    # If overturn, release the held payout
    if resolution == "overturn":
        d = conn.execute(
            "SELECT payout_id FROM payout_disputes WHERE id = ?", (dispute_id,)
        ).fetchone()
        if d and d["payout_id"]:
            conn.execute(
                "UPDATE payouts SET status='pending', hold_reason=NULL WHERE id = ? AND status='held'",
                (d["payout_id"],),
            )
    conn.commit()
    return {"ok": True, "dispute_id": dispute_id, "status": new_status}
