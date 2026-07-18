"""
services/commerce/payouts.py — Payout cycle + lifecycle management

Cycle lifecycle:
    upcoming -> active (when its start_date is reached)
    active   -> processing (admin clicks 'Process now' or cycle.process_date hits)
    processing -> processed (when all payouts are paid or failed)

Payout lifecycle:
    pending -> approved -> paid
    pending -> held (missing info / suspected fraud)
    approved -> failed (PayPal/bank send error)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


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
        "payouts": [dict(p) for p in payouts],
    }


def _cycle_counts(cycle_id: str) -> dict:
    conn = get_conn()
    r = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status='approved' THEN amount_cents ELSE 0 END) AS approved_cents,
          SUM(CASE WHEN status='pending'  THEN amount_cents ELSE 0 END) AS pending_cents,
          SUM(CASE WHEN status='held'     THEN amount_cents ELSE 0 END) AS held_cents,
          SUM(CASE WHEN status='paid'     THEN amount_cents ELSE 0 END) AS paid_cents,
          COUNT(DISTINCT user_id) AS unique_creators
        FROM payouts WHERE cycle_id = ?
        """,
        (cycle_id,),
    ).fetchone()
    return {
        "approved_cents": r["approved_cents"] or 0,
        "pending_cents":  r["pending_cents"]  or 0,
        "held_cents":     r["held_cents"]     or 0,
        "paid_cents":     r["paid_cents"]     or 0,
        "creator_count":  r["unique_creators"] or 0,
    }


# =========================================================================
# Accrual (called from scheduler daily)
# =========================================================================

def accrue_cycle(cycle_id: str) -> dict:
    """Compute pending payouts from orders in cycle's window."""
    conn = get_conn()
    cycle = conn.execute(
        "SELECT * FROM payout_cycles WHERE id = ?", (cycle_id,)
    ).fetchone()
    if not cycle:
        raise ValueError(f"cycle {cycle_id} not found")

    # Aggregate commission by user for orders in window
    aggregates = conn.execute(
        """
        SELECT attribution_user_id AS user_id,
               COUNT(*) AS orders,
               SUM(subtotal_cents) AS gmv,
               SUM(commission_cents) AS commission,
               json_group_array(id) AS order_ids
        FROM orders
        WHERE placed_at BETWEEN ? AND ?
          AND status = 'paid'
          AND attribution_user_id IS NOT NULL
          AND commission_cents > 0
        GROUP BY attribution_user_id
        """,
        (cycle["start_date"], cycle["end_date"]),
    ).fetchall()

    created_count = 0
    for agg in aggregates:
        # upsert by (cycle_id, user_id)
        existing = conn.execute(
            "SELECT id FROM payouts WHERE cycle_id = ? AND user_id = ?",
            (cycle_id, agg["user_id"]),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE payouts SET
                    amount_cents = ?, gmv_cents = ?, order_count = ?, order_ids_json = ?
                   WHERE id = ? AND status = 'pending'""",
                (
                    agg["commission"], agg["gmv"], agg["orders"], agg["order_ids"],
                    existing["id"],
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
                    order_ids_json, method, method_details, status, hold_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    cycle_id, agg["user_id"], agg["commission"], agg["gmv"], agg["orders"],
                    agg["order_ids"], method, details, status, hold_reason,
                ),
            )
            created_count += 1

    conn.commit()
    return {"accrued_count": created_count, "cycle_id": cycle_id}


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
    conn.execute(
        """UPDATE payouts SET status='approved',
            approved_at = datetime('now'), approved_by = ?
           WHERE id = ? AND status IN ('pending','held')""",
        (admin_id, payout_id),
    )
    conn.commit()
    return {"ok": True, "payout_id": payout_id}


def hold_one(payout_id: int, reason: str, admin_id: int) -> dict:
    conn = get_conn()
    conn.execute(
        "UPDATE payouts SET status='held', hold_reason=? WHERE id = ?",
        (reason, payout_id),
    )
    conn.commit()
    return {"ok": True, "payout_id": payout_id}


def release_one(payout_id: int, admin_id: int) -> dict:
    conn = get_conn()
    conn.execute(
        "UPDATE payouts SET status='pending', hold_reason=NULL WHERE id = ?",
        (payout_id,),
    )
    conn.commit()
    return {"ok": True, "payout_id": payout_id}


def adjust_one(
    payout_id: int, new_amount_cents: int, reason: str, admin_id: int
) -> dict:
    conn = get_conn()
    conn.execute(
        "UPDATE payouts SET amount_cents=?, hold_reason=? WHERE id = ?",
        (new_amount_cents, f"ADJUSTED: {reason}", payout_id),
    )
    conn.commit()
    return {"ok": True, "payout_id": payout_id, "new_amount_cents": new_amount_cents}


def user_history(user_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, c.label AS cycle_label
           FROM payouts p JOIN payout_cycles c ON p.cycle_id = c.id
           WHERE p.user_id = ? AND p.status = 'paid'
           ORDER BY p.paid_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# =========================================================================
# Process cycle (calls PayPal/bank — stubs here, hook your real senders)
# =========================================================================

def process_cycle(cycle_id: str, admin_id: int) -> dict:
    """Transition all approved payouts → paid by dispatching to payment providers."""
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

    processed_count = 0
    failed: list[dict] = []
    for p in approved:
        try:
            # 2026-07-18 竞态修:每笔打款前先 CAS 抢单——只有把该 payout 从
            # approved 抢到 paying 的那一次(rowcount==1)才真发款,杜绝双打款。
            claim = conn.cursor()
            claim.execute(
                "UPDATE payouts SET status='paying' WHERE id = ? AND status = 'approved'",
                (p["id"],),
            )
            conn.commit()
            if int(getattr(claim, "rowcount", 0) or 0) != 1:
                continue
            tx_id = _dispatch_payout(dict(p))
            conn.execute(
                "UPDATE payouts SET status='paid', paid_at=datetime('now'), paid_tx_id=? "
                "WHERE id = ?",
                (tx_id, p["id"]),
            )
            processed_count += 1
        except Exception as e:
            logger.exception("payout %s failed", p["id"])
            conn.execute(
                "UPDATE payouts SET status='failed', failed_at=datetime('now'), failed_reason=? "
                "WHERE id = ?",
                (str(e), p["id"]),
            )
            failed.append({"payout_id": p["id"], "error": str(e)})
    conn.commit()

    # Mark processed when all approved are resolved
    still_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM payouts WHERE cycle_id = ? AND status IN ('approved',)",
        (cycle_id,),
    ).fetchone()
    if still_pending["n"] == 0:
        conn.execute(
            "UPDATE payout_cycles SET status='processed', processed_at=datetime('now') "
            "WHERE id = ?",
            (cycle_id,),
        )
        conn.commit()

    return {
        "processed_count": processed_count,
        "failed": failed,
        "cycle_id": cycle_id,
    }


def _dispatch_payout(payout: dict) -> str:
    """
    Actually send the money. Replace with real PayPal Payouts API / bank transfer.
    Returns: provider transaction id
    """
    method = payout.get("method")
    if method == "paypal":
        # TODO: real PayPal Payouts SDK call
        # return paypal_client.create_payout(email, amount)
        return f"pp_stub_{payout['id']}_{datetime.utcnow().timestamp():.0f}"
    if method == "bank":
        # TODO: Stripe transfer or bank API
        return f"bank_stub_{payout['id']}"
    raise ValueError(f"Unsupported payout method: {method}")


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
                "UPDATE payouts SET status='pending', hold_reason=NULL WHERE id = ?",
                (d["payout_id"],),
            )
    conn.commit()
    return {"ok": True, "dispute_id": dispute_id, "status": new_status}
