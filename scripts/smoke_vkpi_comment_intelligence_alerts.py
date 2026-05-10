#!/usr/bin/env python3
"""Smoke P2.8 comment intelligence alerts."""
from __future__ import annotations

import json
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
    from app.services.vkpi import alerts, comments_collector, sentiment

    marker = f"ci_alert_{uuid.uuid4().hex[:10]}"
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
            (marker + "_project", "P2.8 alert smoke", marker, "brand_monitor", True, "{}"),
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
                "youtube",
                marker + "_channel",
                "viltrox",
                "Viltrox",
                "https://www.youtube.com/@Viltrox",
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
                "youtube",
                marker + "_video",
                "https://www.youtube.com/watch?v=" + marker[:11],
                "Viltrox alert smoke",
                "A smoke post.",
                1,
                1,
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
                marker + "_video",
                "youtube",
                marker + "_comment",
                "This is hostile and damaging.",
                "viewer",
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
                0.96,
                "anger",
                0.9,
                "hostile",
                0.95,
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
            raise AssertionError(f"expected comment intelligence alert: {result}")
        alert_key = f"comment-intelligence-industry_posts-{post_id}"
        row = conn.execute("SELECT * FROM vkpi_alerts WHERE alert_key = ?", (alert_key,)).fetchone()
        if not row:
            raise AssertionError(f"alert not persisted with key {alert_key}: {result}")
        data = dict(row)
        if data.get("severity") != "danger":
            raise AssertionError(f"expected danger severity, got {data}")
        if data.get("rule_key") != "comment_intelligence.negative_or_hostile":
            raise AssertionError(f"wrong rule key: {data}")
        metadata = json.loads(data.get("metadata_json") or "{}")
        if int(metadata.get("hostile_count") or 0) < 1:
            raise AssertionError(f"metadata missing hostile count: {metadata}")

        aggregate = alerts.generate_alerts()
        ci = aggregate.get("comment_intelligence") or {}
        if ci.get("count", 0) < 1:
            raise AssertionError(f"aggregate alerts missing comment intelligence: {aggregate}")

        print("VKPI_COMMENT_INTELLIGENCE_ALERTS_SMOKE_OK")
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
