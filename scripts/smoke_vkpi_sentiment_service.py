#!/usr/bin/env python3
"""Smoke P1.4 sentiment service persistence path.

This smoke is offline by construction: it forces the LLM gateway rule fallback,
then verifies that analyze_comment() still writes a neutral sentiment result and
links vkpi_comments.sentiment_id. It must not consume provider quota.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"


def main() -> None:
    from app.db.connection import get_conn
    from app.services.vkpi.comments_collector import ensure_vkpi_comments_schema
    from app.services.vkpi import sentiment

    marker = f"sentiment_smoke_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    comment_id = None
    try:
        ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()
        row = conn.execute(
            """
            INSERT INTO vkpi_comments (
              platform, external_comment_id, comment_text, language_detected,
              external_post_id, post_table, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, NOW())
            RETURNING id
            """,
            (
                "youtube",
                marker,
                "Love the colors from this Viltrox lens test.",
                "en",
                marker + "_post",
                "industry_posts",
            ),
        ).fetchone()
        comment_id = int(row["id"])
        conn.commit()

        result = sentiment.analyze_comment(comment_id, force_reanalyze=True)
        if result.get("status") != "ok":
            raise AssertionError(f"unexpected status: {result}")
        if result.get("sentiment") != "neutral":
            raise AssertionError(f"offline fallback should be neutral: {result}")
        if result.get("llm_provider") != "rule_v0":
            raise AssertionError(f"expected rule_v0 provider: {result}")

        linked = conn.execute(
            "SELECT sentiment_id FROM vkpi_comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
        if not linked or not linked["sentiment_id"]:
            raise AssertionError("vkpi_comments.sentiment_id was not linked")

        stored = conn.execute(
            "SELECT sentiment, emotion, brand_attitude FROM vkpi_sentiment_results WHERE comment_id = ?",
            (comment_id,),
        ).fetchone()
        if not stored or stored["sentiment"] != "neutral":
            raise AssertionError(f"sentiment result not stored: {stored}")

        stats = sentiment.stats(days=1)
        if not isinstance(stats.get("by_sentiment"), list):
            raise AssertionError(f"stats shape invalid: {stats}")

        print("VKPI_SENTIMENT_SERVICE_SMOKE_OK")
    finally:
        if comment_id is not None:
            conn.execute("DELETE FROM vkpi_sentiment_results WHERE comment_id = ?", (comment_id,))
            conn.execute("DELETE FROM vkpi_comments WHERE id = ?", (comment_id,))
            conn.commit()


if __name__ == "__main__":
    main()
