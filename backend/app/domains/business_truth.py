"""Fail-closed guards for human-written real-business evidence.

Provider webhooks have their own cryptographic authentication.  Human repair or
backfill routes are different: they must be explicitly enabled by an owner and
carry a small, auditable authorization bundle.  Keeping this policy in one
module prevents a UI label such as "Shopify" from silently turning a manual row
into provider-confirmed GMV.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn


REAL_BUSINESS_MANUAL_WRITES_FLAG = "real_business_manual_writes"


class BusinessTruthWriteBlocked(ValueError):
    """Raised when a human business-truth write is not authorized."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def manual_writes_enabled() -> bool:
    """Return the persisted gate state; missing table/row fails closed."""

    try:
        row = get_conn().execute(
            "SELECT enabled FROM vkpi_feature_flags WHERE flag_key=?",
            (REAL_BUSINESS_MANUAL_WRITES_FLAG,),
        ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    try:
        value = row["enabled"]
    except Exception:
        value = row[0]
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _actor_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("staff_id", "id"):
        try:
            value = int(staff.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return None


def require_authorization_evidence(
    body: dict[str, Any] | None,
    *,
    staff: dict[str, Any] | None,
    action: str,
) -> dict[str, Any]:
    """Validate the global gate and a non-secret human authorization bundle.

    Required request shape::

        {"authorization_evidence": {
            "authorization_ref": "ticket/change/request id",
            "reason": "why this manual repair is needed",
            "confirmed_by_human": true
        }}

    The authenticated actor is stamped server-side.  Callers cannot promote a
    manual row to provider-confirmed truth by choosing a source/confidence label.
    """

    if not manual_writes_enabled():
        raise BusinessTruthWriteBlocked(
            "feature_disabled",
            f"{REAL_BUSINESS_MANUAL_WRITES_FLAG} is disabled",
        )
    actor_id = _actor_id(staff)
    if not actor_id:
        raise BusinessTruthWriteBlocked("actor_missing", "authenticated staff actor required")
    role = str((staff or {}).get("role") or "").strip().lower()
    is_owner = int((staff or {}).get("is_owner") or 0) == 1
    if not is_owner and role != "admin":
        raise BusinessTruthWriteBlocked(
            "owner_or_admin_required",
            "owner or admin role required for real-business truth writes",
        )
    raw = (body or {}).get("authorization_evidence")
    if not isinstance(raw, dict):
        raise BusinessTruthWriteBlocked(
            "authorization_evidence_required",
            "authorization_evidence object required",
        )
    authorization_ref = str(raw.get("authorization_ref") or raw.get("reference") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    confirmed = raw.get("confirmed_by_human") is True
    if not authorization_ref or not reason or not confirmed:
        raise BusinessTruthWriteBlocked(
            "authorization_evidence_incomplete",
            "authorization_ref, reason and confirmed_by_human=true are required",
        )
    return {
        "action": str(action or "manual_business_write"),
        "authorization_ref": authorization_ref[:200],
        "reason": reason[:500],
        "confirmed_by_human": True,
        "actor_staff_id": actor_id,
        "actor_role": role[:80],
        "evidence_class": "human_authorized_manual_entry",
    }


def verified_shopify_attribution_sql(alias: str = "") -> str:
    """Canonical SQL predicate for money/closure metrics.

    A request body can choose labels, but it cannot manufacture the provider
    order snapshot that is written by the signed Shopify adapter.  Keep this
    predicate on every GMV, ROI, KPI, and outcome aggregation; raw/detail views
    may still display reference rows with their pending confidence.
    """

    prefix = f"{str(alias).strip()}." if str(alias).strip() else ""
    return (
        f"{prefix}source_platform='shopify' "
        f"AND {prefix}confidence IN ('confirmed','refund') "
        f"AND {prefix}shopify_order_snapshot_id IS NOT NULL "
        "AND EXISTS ("
        "SELECT 1 FROM vkpi_shopify_order_snapshots truth_shopify_order "
        f"WHERE truth_shopify_order.id={prefix}shopify_order_snapshot_id "
        "AND truth_shopify_order.provider_auth_mode='shopify-hmac' "
        "AND truth_shopify_order.provider_verified_at IS NOT NULL "
        "AND NULLIF(TRIM(COALESCE(truth_shopify_order.raw_payload_hash,'')),'') IS NOT NULL "
        "AND LOWER(COALESCE(truth_shopify_order.financial_status,'')) "
        "IN ('paid','partially_paid','partially_refunded') "
        "AND truth_shopify_order.cancelled_at IS NULL"
        ")"
    )


def approved_actual_cost_sql(alias: str = "") -> str:
    """Canonical SQL predicate for costs allowed into ROI/closure metrics."""

    prefix = f"{str(alias).strip()}." if str(alias).strip() else ""
    return f"{prefix}status='actual' AND {prefix}approved_at IS NOT NULL"


def current_kpi_ledger_sql(alias: str = "") -> str:
    """Canonical predicate for KPI rows that may drive current UI/exports.

    Migration 255 deliberately keeps superseded ledger rows for audit while
    setting ``confidence='stale'``.  Business readers must exclude those rows;
    diagnostic and audit readers may still query the table without this guard.
    """

    prefix = f"{str(alias).strip()}." if str(alias).strip() else ""
    return f"LOWER(TRIM(COALESCE({prefix}confidence,''))) <> 'stale'"


__all__ = [
    "BusinessTruthWriteBlocked",
    "REAL_BUSINESS_MANUAL_WRITES_FLAG",
    "manual_writes_enabled",
    "approved_actual_cost_sql",
    "current_kpi_ledger_sql",
    "require_authorization_evidence",
    "verified_shopify_attribution_sql",
]
