"""Compatibility facade for market IntelligenceCard builders."""
from __future__ import annotations

from app.db.connection import get_conn
from app.domains.market.intelligence_cards import (
    build_market_intelligence_cards,
    build_market_intelligence_cards_from_files,
    latest_external_signal_smoke_report,
    latest_usable_market_llm_report,
    render_market_intelligence_cards_markdown,
    write_market_intelligence_cards,
)


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


__all__ = [
    "build_market_intelligence_cards",
    "build_market_intelligence_cards_from_files",
    "latest_external_signal_smoke_report",
    "latest_reviewed_market_run_id",
    "latest_usable_market_llm_report",
    "render_market_intelligence_cards_markdown",
    "write_market_intelligence_cards",
]
