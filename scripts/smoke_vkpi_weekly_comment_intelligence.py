#!/usr/bin/env python3
"""Smoke P2.7 weekly reports include grounded comment intelligence context."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"


def main() -> None:
    from app.db.connection import get_conn
    from app.services.vkpi import (
        comment_intelligence,
        comments_collector,
        pillars,
        sentiment,
        weekly_report_generator,
    )

    marker = f"weekly_ci_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    user_id = staff_id = project_id = account_id = post_id = comment_id = None
    run_ids: list[int] = []
    try:
        weekly_report_generator.ensure_vkpi_weekly_reports_schema()
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()
        pillars.ensure_vkpi_pillar_schema()
        comment_intelligence.ensure_vkpi_comment_intelligence_schema()

        user_id = conn.execute(
            """
            INSERT INTO users (email, password_hash, name, status, role, email_verified)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                marker + "@viltrox.test",
                "v2:00:00",
                "Weekly CI Smoke",
                "active",
                "admin",
                1,
            ),
        ).fetchone()["id"]
        staff_id = conn.execute(
            """
            INSERT INTO staff (user_id, role, permissions_json, active, is_owner)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (user_id, "marketing_analyst", '{"vkpi":"admin"}', 1, 0),
        ).fetchone()["id"]
        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P2.7 weekly CI smoke", marker, "brand_monitor", True, "{}"),
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
                "Viltrox weekly CI smoke",
                "Sample lens footage looks good.",
                8,
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
                "The lens color and footage look great.",
                "viewer",
                "{}",
            ),
        ).fetchone()["id"]
        conn.commit()

        run = comment_intelligence.process_post(
            post_id,
            collect_comments=False,
            analyze_sentiment=True,
            classify_pillar=True,
            force_reprocess=True,
            staff={"id": staff_id},
        )
        run_ids.append(int(run["run_id"]))
        if run.get("status") != "ok":
            raise AssertionError(f"pipeline failed: {run}")

        period_end = date.today()
        report = weekly_report_generator.generate_for_template(
            staff_id=staff_id,
            template_key="layer1_universal",
            period_start=period_end - timedelta(days=7),
            period_end=period_end,
        )
        if report.get("status") != "ok":
            raise AssertionError(f"weekly report failed: {report}")
        stored = conn.execute(
            """
            SELECT body_md
            FROM vkpi_weekly_reports
            WHERE staff_id = ? AND template_key = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (staff_id, "layer1_universal"),
        ).fetchone()
        body = str((stored or {}).get("body_md") or "")
        if "comment_intelligence_summary" not in body:
            raise AssertionError(f"weekly report missing section key: {body[:1000]}")
        if "Comment intelligence summary" not in body:
            raise AssertionError(f"weekly report missing grounded CI summary: {body[:1000]}")
        if "Pipeline runs:" not in body or "Comment coverage:" not in body:
            raise AssertionError(f"weekly report missing CI metrics: {body[:1000]}")

        print("VKPI_WEEKLY_COMMENT_INTELLIGENCE_SMOKE_OK")
    finally:
        if staff_id is not None:
            conn.execute("DELETE FROM vkpi_weekly_reports WHERE staff_id = ?", (staff_id,))
        for run_id in run_ids:
            conn.execute("DELETE FROM vkpi_comment_intelligence_runs WHERE id = ?", (run_id,))
        if post_id is not None:
            conn.execute("DELETE FROM vkpi_post_pillars WHERE post_id = ? AND post_table = ?", (post_id, "industry_posts"))
        if comment_id is not None:
            conn.execute("DELETE FROM vkpi_sentiment_results WHERE comment_id = ?", (comment_id,))
            conn.execute("DELETE FROM vkpi_comments WHERE id = ?", (comment_id,))
        if post_id is not None:
            conn.execute("DELETE FROM vkpi_industry_posts WHERE id = ?", (post_id,))
        if account_id is not None:
            conn.execute("DELETE FROM vkpi_industry_accounts WHERE id = ?", (account_id,))
        if project_id is not None:
            conn.execute("DELETE FROM vkpi_industry_projects WHERE id = ?", (project_id,))
        if staff_id is not None:
            conn.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
        if user_id is not None:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


if __name__ == "__main__":
    main()
