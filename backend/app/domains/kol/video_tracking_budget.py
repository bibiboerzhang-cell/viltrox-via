"""Monthly budget scope for automatic tracked-video metric refreshes.

The scheduler enqueue pass (``video_metric_schedule``) is the only automatic
fan-out of paid provider work for metric tracking, so this scope is enforced
there: when the month's attributed spend reaches the cap, no new refresh jobs
are queued until the scope resets.  Manual, interactive refreshes stay under the
global ``provider:apify`` cap and are not throttled here.

Spend is not a separate counter.  It is derived from ``vkpi_ai_cost_ledger``
rows whose metadata names the metric-refresh operation (set through
``metadata_cost_attribution`` by the refresh worker), so reconciliation of Apify
costs is reflected automatically and replays cannot double count.  The
``current_spend`` column on the scope row is a mirror written for dashboards.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger


logger = get_logger(__name__)

BUDGET_SCOPE = "metric_tracking"
COST_TAG = "metric_tracking"
CAP_ENV = "VKPI_METRIC_TRACKING_MONTHLY_CAP_USD"
DEFAULT_MONTHLY_CAP_USD = 30.0
LEDGER_OPERATION = "kol_video_metric_refresh"
FALLBACK_ACTION = "pause_tracking_enqueue"
_WARNING_AT = 0.80
_HARD_STOP_AT = 1.00


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default  # NaN guard


def configured_monthly_cap_usd() -> float:
    """Cap from ``VKPI_METRIC_TRACKING_MONTHLY_CAP_USD`` (default 30, never < 0)."""

    raw = os.environ.get(CAP_ENV, "").strip()
    cap = _float(raw, DEFAULT_MONTHLY_CAP_USD) if raw else DEFAULT_MONTHLY_CAP_USD
    return max(0.0, cap)


def month_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(now: datetime | None = None) -> datetime:
    start = month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def _operation_marker() -> str:
    # record_cost serializes metadata with ``json.dumps`` default separators.
    return json.dumps({"operation": LEDGER_OPERATION})[1:-1]


def _is_sqlite(conn: Any) -> bool:
    return callable(getattr(conn, "executescript", None))


def month_spend_usd(conn: Any, *, now: datetime | None = None) -> float:
    """Sum this month's Apify ledger rows attributed to the metric refresh job."""

    start = month_start(now)
    marker = _operation_marker()
    if _is_sqlite(conn):
        sql = """
            SELECT COALESCE(SUM(cost_usd), 0) AS spend
            FROM vkpi_ai_cost_ledger
            WHERE ai_provider='apify'
              AND occurred_at >= ?
              AND instr(COALESCE(metadata_json, ''), ?) > 0
        """
        params: tuple[Any, ...] = (start.isoformat(), marker)
    else:
        sql = """
            SELECT COALESCE(SUM(cost_usd), 0) AS spend
            FROM vkpi_ai_cost_ledger
            WHERE ai_provider='apify'
              AND occurred_at >= ?
              AND POSITION(? IN COALESCE(metadata_json, '')) > 0
        """
        params = (start, marker)
    row = conn.execute(sql, params).fetchone()
    return round(_float(dict(row).get("spend") if row else 0.0), 6)


def load_scope(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?",
        (BUDGET_SCOPE,),
    ).fetchone()
    return dict(row) if row else None


def ensure_budget_scope(
    conn: Any,
    *,
    cap_usd: float | None = None,
    now: datetime | None = None,
    seeded_by: str = "enroll_metric_tracking",
) -> dict[str, Any]:
    """Idempotently seed the ``metric_tracking`` cap row; re-runs only update the cap.

    Caller owns the transaction.  Existing ``current_spend`` and metadata are
    preserved; only ``cap_usd``/``reset_at``/``fallback_action`` are refreshed so
    operators can change the env cap and re-run safely.
    """

    cap = configured_monthly_cap_usd() if cap_usd is None else max(0.0, _float(cap_usd))
    reset_at = next_month_start(now)
    existing = load_scope(conn)
    metadata = {
        "seeded_by": seeded_by,
        "tier": "feature",
        "provider": "apify",
        "cost_tag": COST_TAG,
        "ledger_operation": LEDGER_OPERATION,
        "cap_env": CAP_ENV,
        "note": "automatic tracked-video metric refresh fan-out; spend derived from cost ledger",
    }
    if existing is None:
        conn.execute(
            """
            INSERT INTO vkpi_provider_budget_caps (
                scope, cap_usd, current_spend, warning_at, hard_stop_at,
                reset_at, fallback_action, metadata_json
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                BUDGET_SCOPE,
                cap,
                _WARNING_AT,
                _HARD_STOP_AT,
                reset_at,
                FALLBACK_ACTION,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        action = "inserted"
    else:
        conn.execute(
            """
            UPDATE vkpi_provider_budget_caps
            SET cap_usd=?, reset_at=?, fallback_action=?
            WHERE scope=?
            """,
            (cap, reset_at, FALLBACK_ACTION, BUDGET_SCOPE),
        )
        action = "updated" if _float(existing.get("cap_usd")) != cap else "unchanged"
    return {"scope": BUDGET_SCOPE, "cap_usd": cap, "reset_at": reset_at.isoformat(), "action": action}


def budget_gate(conn: Any, *, now: datetime | None = None, sync_spend: bool = True) -> dict[str, Any]:
    """Decide whether the automatic enqueue pass may queue more paid refreshes.

    Fail-closed: a missing scope row blocks the pass (seed it with
    ``scripts/ops/enroll_metric_tracking.py``).  A zero cap disables the cap.
    """

    scope = load_scope(conn)
    if scope is None:
        return {
            "allowed": False,
            "reason": "budget_scope_not_configured",
            "scope": BUDGET_SCOPE,
            "cap_usd": None,
            "spend_usd": None,
        }
    spend = month_spend_usd(conn, now=now)
    cap = _float(scope.get("cap_usd"))
    hard_stop = max(0.0, min(1.0, _float(scope.get("hard_stop_at"), _HARD_STOP_AT)))
    warning = max(0.0, min(1.0, _float(scope.get("warning_at"), _WARNING_AT)))
    if sync_spend and abs(_float(scope.get("current_spend")) - spend) > 1e-6:
        conn.execute(
            "UPDATE vkpi_provider_budget_caps SET current_spend=? WHERE scope=?",
            (spend, BUDGET_SCOPE),
        )
    allowed = cap <= 0 or spend < cap * hard_stop
    decision = {
        "allowed": allowed,
        "reason": "within_cap" if allowed else "hard_stop_or_projected_cap:metric_tracking",
        "scope": BUDGET_SCOPE,
        "cap_usd": round(cap, 6),
        "spend_usd": spend,
        "warning": bool(cap > 0 and spend >= cap * warning),
        "month_start": month_start(now).isoformat(),
    }
    if not allowed:
        logger.warning(
            "metric tracking enqueue blocked by budget | scope=%s spend=%.4f cap=%.4f",
            BUDGET_SCOPE, spend, cap,
        )
    return decision


__all__ = [
    "BUDGET_SCOPE",
    "CAP_ENV",
    "COST_TAG",
    "DEFAULT_MONTHLY_CAP_USD",
    "LEDGER_OPERATION",
    "budget_gate",
    "configured_monthly_cap_usd",
    "ensure_budget_scope",
    "load_scope",
    "month_spend_usd",
    "month_start",
    "next_month_start",
]
