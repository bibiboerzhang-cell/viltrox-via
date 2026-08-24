"""Serialized persistence for lazy budget-window rollover."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.costs.budget_windows import project_budget_window

logger = get_logger(__name__)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def roll_budget_window(
    conn: Any,
    row: dict[str, Any],
    *,
    postgres: bool,
    release_fenced: bool,
    commit: bool,
    strict: bool = False,
) -> dict[str, Any]:
    """Roll one expired window under the same row lock used by cost writes.

    The first pure projection avoids locking ordinary in-window reads.  When a
    rollover is due, PostgreSQL re-reads ``FOR UPDATE`` and projects again from
    the locked row.  Callers that subsequently add cost use ``commit=False`` so
    reset and increment remain one transaction; this prevents a concurrent
    reset from erasing newly recorded spend.
    """

    projected, roll_required, _zero_spend = project_budget_window(row)
    if not roll_required or release_fenced:
        return row
    scope = str(row.get("scope") or "")
    if not scope:
        return row
    try:
        lock_suffix = " FOR UPDATE" if postgres else ""
        fresh_row = conn.execute(
            "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?" + lock_suffix,
            (scope,),
        ).fetchone()
        if fresh_row is None:
            if commit:
                conn.commit()
            return row
        fresh = _row_dict(fresh_row)
        projected, roll_required, zero_spend = project_budget_window(fresh)
        if not roll_required:
            if commit:
                conn.commit()
            return fresh
        next_reset = str(projected.get("reset_at") or "")
        if zero_spend:
            cursor = conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend=0, reset_at=? WHERE scope=?",
                (next_reset, scope),
            )
        else:
            cursor = conn.execute(
                "UPDATE vkpi_provider_budget_caps SET reset_at=? WHERE scope=?",
                (next_reset, scope),
            )
        if getattr(cursor, "rowcount", 1) == 0:
            raise RuntimeError("budget_window_roll_target_missing")
        if commit:
            conn.commit()
        return projected
    except Exception:
        if strict:
            raise
        if commit:
            try:
                conn.rollback()
            except Exception:
                logger.debug("budget window rollback skipped scope=%s", scope, exc_info=True)
        logger.warning("budget window roll failed scope=%s", scope, exc_info=True)
        return row


__all__ = ["roll_budget_window"]
