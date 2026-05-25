"""DB helpers for market IntelligenceCard selection."""
from __future__ import annotations

from app.db.connection import get_conn


def latest_reviewed_market_run_id() -> int | None:
    try:
        row = get_conn().execute(
            """
            SELECT id
            FROM vkpi_competitor_signal_runs
            WHERE signal_count > 0
              AND source_summary_json LIKE ?
            ORDER BY committed_at DESC, id DESC
            LIMIT 1
            """,
            ("%market_signal_promotion_review_package_v0%",),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        return int(row["id"])
    except Exception:
        return None


__all__ = ["latest_reviewed_market_run_id"]
