#!/usr/bin/env python3
"""Smoke P2.10 configurable comment alert thresholds."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"

from _smoke_seed import cleanup_admin, seed_admin  # noqa: E402


def main() -> None:
    from app.db.connection import get_conn
    from app.domains.comments import collector as comments_collector
    import importlib

    platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")
    from app.services.vkpi import alerts, sentiment

    marker = f"ci_threshold_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    user_id = staff_id = project_id = account_id = post_id = comment_id = sentiment_id = None
    alert_key = ""
    original = platform_crawl_settings.comment_alert_settings().get("settings") or {}
    try:
        user_id, staff_id = seed_admin(conn, marker=marker, vkpi_permission="admin", is_owner=True)
        staff = {"id": staff_id, "role": "admin", "is_owner": 1}
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()

        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P2.10 threshold smoke", marker, "brand_monitor", True, "{}"),
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
                "Viltrox threshold smoke",
                "A smoke post.",
                1,
                1,
                "[]",
                "{}",
            ),
        ).fetchone()["id"]
        alert_key = f"comment-intelligence-industry_posts-{post_id}"
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
                "offline",
                "rule_v0",
                "smoke",
                "en",
                0,
                0,
                0,
            ),
        ).fetchone()["id"]
        conn.commit()

        platform_crawl_settings.update_comment_alert_settings(
            {
                "enabled": True,
                "window_days": 7,
                "min_negative": 5,
                "min_critical": 5,
                "min_hostile": 2,
            },
            staff=staff,
        )
        blocked = alerts.generate_comment_intelligence_alerts()
        row = conn.execute("SELECT id FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        assert row is None, f"threshold should block single hostile alert: {blocked}"

        platform_crawl_settings.update_comment_alert_settings(
            {
                "enabled": True,
                "window_days": 7,
                "min_negative": 5,
                "min_critical": 5,
                "min_hostile": 1,
            },
            staff=staff,
        )
        generated = alerts.generate_comment_intelligence_alerts()
        row = conn.execute("SELECT metadata_json FROM vkpi_alerts WHERE alert_key=?", (alert_key,)).fetchone()
        assert row is not None, f"threshold should allow single hostile alert: {generated}"
        metadata = str(row["metadata_json"] or "")
        assert '"min_hostile": 1' in metadata, metadata

        print("VKPI_COMMENT_ALERT_THRESHOLDS_SMOKE_OK")
    finally:
        try:
            if original:
                platform_crawl_settings.update_comment_alert_settings(original, staff={"id": staff_id or 0, "role": "admin", "is_owner": 1})
        except Exception:
            pass
        try:
            if alert_key:
                conn.execute("DELETE FROM vkpi_alerts WHERE alert_key=?", (alert_key,))
            if sentiment_id:
                conn.execute("DELETE FROM vkpi_sentiment_results WHERE id=?", (sentiment_id,))
            if comment_id:
                conn.execute("DELETE FROM vkpi_comments WHERE id=?", (comment_id,))
            if post_id:
                conn.execute("DELETE FROM vkpi_industry_posts WHERE id=?", (post_id,))
            if account_id:
                conn.execute("DELETE FROM vkpi_industry_accounts WHERE id=?", (account_id,))
            if project_id:
                conn.execute("DELETE FROM vkpi_industry_projects WHERE id=?", (project_id,))
            conn.commit()
        finally:
            cleanup_admin(conn, user_id=user_id, staff_id=staff_id)


if __name__ == "__main__":
    main()
