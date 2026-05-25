#!/usr/bin/env python3
"""Smoke P2.11 alert drilldown source rows."""
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
    from app.domains.comments import collector as comments_collector
    from app.domains import alerts
    import app.domains.comments.sentiment as sentiment

    marker = f"ad_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    project_id = account_id = post_id = comment_id = sentiment_id = None
    alert_key = ""
    try:
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()

        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P2.11 alert drilldown", marker, "brand_monitor", True, "{}"),
        ).fetchone()["id"]
        account_id = conn.execute(
            """
            INSERT INTO vkpi_industry_accounts (
              account_uid, project_id, platform, platform_user_id, handle,
              display_name, profile_url, crawl_enabled, is_active, raw_platform_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                marker + "_account",
                project_id,
                "instagram",
                marker + "_ig",
                "viltrox.cine",
                "Viltrox Cine",
                "https://www.instagram.com/viltrox.cine/",
                True,
                True,
                "{}",
            ),
        ).fetchone()["id"]
        post_id = conn.execute(
            """
            INSERT INTO vkpi_industry_posts (
              post_uid, account_id, platform, platform_post_id, post_url,
              title, caption, likes, comments, hashtags_json, raw_platform_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                marker + "_post",
                account_id,
                "instagram",
                marker + "_post_id",
                "https://www.instagram.com/p/" + marker[:8],
                "Alert drilldown post",
                "Source comment drilldown.",
                7,
                2,
                "[]",
                "{}",
            ),
        ).fetchone()["id"]
        comment_id = conn.execute(
            """
            INSERT INTO vkpi_comments (
              account_id, post_id, post_table, external_post_id,
              platform, external_comment_id, comment_text,
              author_handle, raw_data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                account_id,
                post_id,
                "industry_posts",
                marker + "_post_id",
                "instagram",
                marker + "_comment",
                "This lens update is hostile and unacceptable.",
                "field_tester",
                "{}",
            ),
        ).fetchone()["id"]
        sentiment_id = conn.execute(
            """
            INSERT INTO vkpi_sentiment_results (
              comment_id, sentiment, sentiment_confidence,
              emotion, emotion_confidence,
              brand_attitude, brand_attitude_confidence,
              llm_provider, llm_model, prompt_version,
              language_detected, input_tokens, output_tokens, cost_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                comment_id,
                "negative",
                0.97,
                "anger",
                0.88,
                "hostile",
                0.94,
                "rule_v0",
                "smoke",
                marker,
                "en",
                0,
                0,
                0,
            ),
        ).fetchone()["id"]
        conn.commit()

        result = alerts.generate_comment_intelligence_alerts(days=7)
        if result.get("count", 0) < 1:
            raise AssertionError(f"expected alert generation: {result}")
        alert_key = f"comment-intelligence-industry_posts-{post_id}"
        row = conn.execute("SELECT id FROM vkpi_alerts WHERE alert_key = ?", (alert_key,)).fetchone()
        if not row:
            raise AssertionError("alert row missing")

        detail = alerts.get_alert_detail(int(row["id"]), staff={"id": 0, "role": "admin", "is_owner": True})
        if not detail.get("post") or int(detail["post"].get("id") or 0) != int(post_id):
            raise AssertionError(f"post source row missing: {detail}")
        if not detail.get("account") or str(detail["account"].get("handle")) != "viltrox.cine":
            raise AssertionError(f"account source row missing: {detail}")
        comments = detail.get("comments") or []
        if len(comments) != 1:
            raise AssertionError(f"expected one flagged comment: {detail}")
        comment = comments[0]
        if comment.get("sentiment") != "negative" or comment.get("brand_attitude") != "hostile":
            raise AssertionError(f"sentiment source row missing: {comment}")
        summary = detail.get("source_summary") or {}
        if int(summary.get("comment_count") or 0) != 1:
            raise AssertionError(f"summary comment count wrong: {summary}")

        print("VKPI_ALERT_DRILLDOWN_SMOKE_OK")
    finally:
        if alert_key:
            conn.execute("DELETE FROM vkpi_alerts WHERE alert_key = ?", (alert_key,))
        conn.execute("DELETE FROM vkpi_alerts WHERE alert_key LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%"))
        if sentiment_id is not None:
            conn.execute("DELETE FROM vkpi_sentiment_results WHERE id = ?", (sentiment_id,))
        if comment_id is not None:
            conn.execute("DELETE FROM vkpi_comments WHERE id = ?", (comment_id,))
        if post_id is not None:
            conn.execute("DELETE FROM vkpi_industry_posts WHERE id = ?", (post_id,))
        if account_id is not None:
            conn.execute("DELETE FROM vkpi_industry_accounts WHERE id = ?", (account_id,))
        if project_id is not None:
            conn.execute("DELETE FROM vkpi_industry_projects WHERE id = ?", (project_id,))
        conn.commit()


if __name__ == "__main__":
    main()
