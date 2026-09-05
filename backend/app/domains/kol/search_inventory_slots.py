"""Durable daily maintenance slots and fail-closed connection recovery.

Only bookkeeping lives here; these helpers never dispatch work or authorize a
provider. Unknown writes retain their previously committed slot reservation.
"""
from __future__ import annotations

import secrets
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)
# Raising this hard ceiling also requires the reviewed slot-table migration.
MAX_DAILY_LIMIT = 5


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _row(value: Any) -> dict[str, Any]:
    return dict(value)


def _reserve_daily_job_slots(
    conn: Any,
    *,
    batch_date: str,
    requested: int,
    actual_jobs: int,
) -> dict[str, Any]:
    """Atomically reserve unique daily job slots across scheduler/manual runs.

    The table primary key is ``(batch_date, slot_no)``. Concurrent callers may
    wait on the same first free slot, but they cannot jointly create more than
    ``MAX_DAILY_LIMIT`` rows. Reservations are committed before any provider
    job is inserted, so a process crash fails closed (temporary underfill) and
    can never reopen spend capacity.
    """

    safe_requested = max(0, min(_int(requested), MAX_DAILY_LIMIT))
    existing_rows = conn.execute(
        "SELECT slot_no FROM vkpi_kol_search_inventory_daily_slots "
        "WHERE batch_date=? ORDER BY slot_no",
        (batch_date,),
    ).fetchall()
    occupied = {
        _int(_row(item).get("slot_no"))
        for item in existing_rows
        if _int(_row(item).get("slot_no")) > 0
    }
    # Deploying the ledger during an already-active day must account for source
    # jobs created before the table existed. Fill anonymous legacy slots first.
    legacy_target = min(MAX_DAILY_LIMIT, max(0, _int(actual_jobs)))
    for slot_no in range(1, MAX_DAILY_LIMIT + 1):
        if len(occupied) >= legacy_target:
            break
        if slot_no in occupied:
            continue
        inserted = conn.execute(
            """
            INSERT INTO vkpi_kol_search_inventory_daily_slots
                (batch_date, slot_no, reservation_token, job_id, updated_at)
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (batch_date, slot_no) DO NOTHING
            RETURNING slot_no
            """,
            (batch_date, slot_no, f"legacy:{batch_date}"),
        ).fetchone()
        if inserted:
            occupied.add(slot_no)
        else:
            occupied.add(slot_no)

    token = f"refresh:{batch_date}:{secrets.token_hex(12)}"
    reserved: list[int] = []
    used_before = len(occupied)
    for slot_no in range(1, MAX_DAILY_LIMIT + 1):
        if len(reserved) >= safe_requested:
            break
        if slot_no in occupied:
            continue
        inserted = conn.execute(
            """
            INSERT INTO vkpi_kol_search_inventory_daily_slots
                (batch_date, slot_no, reservation_token, job_id, updated_at)
            VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
            ON CONFLICT (batch_date, slot_no) DO NOTHING
            RETURNING slot_no
            """,
            (batch_date, slot_no, token),
        ).fetchone()
        if inserted:
            inserted_slot = _int(_row(inserted).get("slot_no"), slot_no)
            occupied.add(inserted_slot)
            reserved.append(inserted_slot)
        else:
            occupied.add(slot_no)
    conn.commit()
    return {
        "reservation_token": token,
        "reserved_slots": reserved,
        "used_before": used_before,
        "used_after_reservation": len(occupied),
        "hard_limit": MAX_DAILY_LIMIT,
    }


def _bind_daily_job_slot(
    conn: Any,
    *,
    batch_date: str,
    reservation_token: str,
    slot_no: int,
    job_id: int,
) -> None:
    conn.execute(
        """
        UPDATE vkpi_kol_search_inventory_daily_slots
        SET job_id=?, updated_at=CURRENT_TIMESTAMP
        WHERE batch_date=? AND slot_no=? AND reservation_token=?
        """,
        (int(job_id), batch_date, int(slot_no), reservation_token),
    )
    conn.commit()


def _release_daily_job_slots(
    conn: Any,
    *,
    batch_date: str,
    reservation_token: str,
    slot_numbers: list[int],
) -> int:
    released = 0
    for slot_no in slot_numbers:
        cursor = conn.execute(
            """
            DELETE FROM vkpi_kol_search_inventory_daily_slots
            WHERE batch_date=? AND slot_no=? AND reservation_token=? AND job_id IS NULL
            """,
            (batch_date, int(slot_no), reservation_token),
        )
        released += max(0, _int(getattr(cursor, "rowcount", 0)))
    conn.commit()
    return released


def recover_dispatch_connection(conn: Any, diagnostics: dict[str, Any], *, stage: str) -> bool:
    """Rollback before another candidate; never release or re-dispatch a job."""
    if stage in {"slot_binding", "slot_release"}:
        key = f"{stage}_failures"
        diagnostics[key] = diagnostics.get(key, 0) + 1
        diagnostics.update(reservation_outcome_unknown=True,
                           error_code="maintenance_reservation_write_failed")
    else:
        diagnostics.update(enqueue_outcome_unknown=True,
                           error_code="maintenance_enqueue_outcome_unknown")
    diagnostics["retry_safe"] = False
    try:
        conn.rollback()
        return True
    except Exception:
        diagnostics.update(dispatch_blocked=True,
                           error_code="maintenance_connection_recovery_failed")
        logger.warning("inventory_refresh.connection_recovery_failed stage=%s", stage, exc_info=True)
        return False


def dispatch_summary(*, queued: int, already_queued: int, failed: int,
                     scan_exhausted: bool, granted: int, released: int,
                     candidate_count: int, processed: int,
                     diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Keep accepted receipts distinct from degraded reservation bookkeeping."""
    details = dict(diagnostics)
    if details.get("dispatch_blocked"):
        details["unattempted"] = candidate_count - processed
    status = (
        "partial"
        if scan_exhausted or details.get("reservation_outcome_unknown") or (failed and (queued or already_queued))
        else "failed" if failed else "ok"
    )
    return {"status": status, "queued": queued, "already_queued": already_queued,
            "failed": failed, "reservation_slots_released": released,
            "reservation_slots_held": granted - released, **details}
