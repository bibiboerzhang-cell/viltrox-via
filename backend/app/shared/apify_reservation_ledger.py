"""Shared persistence boundary for durable Apify budget reservations.

Both the paid-provider boundary and the cost ledger participate in reservation
settlement.  Keeping the schema proof and exactly-once settlement here avoids a
cost-domain dependency on the platform adapter while preserving the platform's
public facade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


APIFY_BUDGET_SCOPE = "provider:apify"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_apify_reservation_schema() -> None:
    """Prove migration 254 is present without creating or changing schema."""

    from app.db.connection import get_conn, is_postgres_runtime, table_exists

    conn = get_conn()
    required = {
        "vkpi_provider_execution_claims": (
            "task_id", "job_type", "lease_owner", "fence_token", "state",
            "lease_expires_at", "provider_run_id", "created_at", "updated_at", "completed_at",
        ),
        "vkpi_apify_budget_reservations": (
            "reservation_key", "task_id", "actor_id", "operation", "payload_hash",
            "execution_fence_token", "estimate_source", "estimated_cost_usd",
            "actual_cost_usd", "state", "apify_run_id", "metadata_json", "reserved_at",
            "provider_started_at", "settled_at", "updated_at",
        ),
    }
    for table, columns in required.items():
        if not table_exists(table):
            raise RuntimeError(f"migration 254 schema missing: {table}")
        conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE 1=0")
    if is_postgres_runtime():
        constraints = conn.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conname IN (
              'vkpi_provider_execution_claims_pkey',
              'vkpi_apify_budget_reservations_pkey',
              'uq_vkpi_apify_reservation_request',
              'fk_vkpi_apify_reservation_task'
            )
            """
        ).fetchall()
        names = {str(dict(row).get("conname") or "") for row in constraints}
        expected = {
            "vkpi_provider_execution_claims_pkey",
            "vkpi_apify_budget_reservations_pkey",
            "uq_vkpi_apify_reservation_request",
            "fk_vkpi_apify_reservation_task",
        }
        index = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname='uq_vkpi_apify_reservation_run'"
        ).fetchone()
        if names != expected or not index:
            raise RuntimeError("migration 254 reservation uniqueness constraints are missing")


def settle_apify_reservation(reservation_key: str, actual_cost_usd: float) -> dict[str, Any]:
    """Settle one reservation and increment cumulative scopes exactly once."""

    from app.db.connection import get_conn, is_postgres_runtime

    key = str(reservation_key or "").strip()
    actual = max(0.0, float(actual_cost_usd or 0.0))
    if not key:
        return {"settled": False, "reason": "no_reservation"}
    ensure_apify_reservation_schema()
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
        data = dict(row)
        if str(data.get("state") or "") == "settled":
            conn.rollback()
            return {
                "settled": False,
                "reason": "already_settled",
                "actual_cost_usd": float(data.get("actual_cost_usd") or 0.0),
            }
        if str(data.get("state") or "") != "provider_started" or not str(data.get("apify_run_id") or ""):
            conn.rollback()
            return {"settled": False, "reason": "provider_outcome_not_confirmed"}
        for scope in (APIFY_BUDGET_SCOPE, "monthly_total"):
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend=COALESCE(current_spend,0)+? WHERE scope=?",
                (actual, scope),
            )
        now = _iso(_utcnow())
        conn.execute(
            """
            UPDATE vkpi_apify_budget_reservations
            SET state='settled',actual_cost_usd=?,settled_at=?,updated_at=?
            WHERE reservation_key=?
            """,
            (actual, now, now, key),
        )
        conn.commit()
        return {"settled": True, "actual_cost_usd": actual}
    except Exception:
        conn.rollback()
        raise


__all__ = [
    "APIFY_BUDGET_SCOPE",
    "ensure_apify_reservation_schema",
    "settle_apify_reservation",
]
