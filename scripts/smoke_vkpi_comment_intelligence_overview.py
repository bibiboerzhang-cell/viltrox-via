#!/usr/bin/env python3
"""Smoke P2.3 comment intelligence overview."""
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
    from app.domains.comments import intelligence as comment_intelligence
    from app.services.vkpi import pillars, sentiment

    marker = f"ci_overview_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    project_id = account_id = post_id = comment_id = None
    run_ids: list[int] = []
    try:
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()
        pillars.ensure_vkpi_pillar_schema()
        comment_intelligence.ensure_vkpi_comment_intelligence_schema()

        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P2.3 overview smoke", marker, "brand_monitor", True, "{}"),
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
                "Viltrox overview smoke",
                "A lens review smoke post.",
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
                "The sample footage looks good.",
                "viewer",
                "{}",
            ),
        ).fetchone()["id"]
        conn.commit()

        result = comment_intelligence.process_post(
            post_id,
            collect_comments=False,
            analyze_sentiment=True,
            classify_pillar=True,
            force_reprocess=True,
        )
        run_ids.append(int(result["run_id"]))
        if result.get("status") != "ok":
            raise AssertionError(f"unexpected run result: {result}")

        overview = comment_intelligence.overview(days=1, recent_limit=10)
        runs = overview.get("runs") or {}
        coverage = overview.get("coverage") or {}
        if runs.get("total", 0) < 1:
            raise AssertionError(f"overview did not count runs: {overview}")
        if "ok" not in (runs.get("by_status") or {}):
            raise AssertionError(f"overview missing ok run status: {overview}")
        if coverage.get("comments_total", 0) < 1:
            raise AssertionError(f"overview did not count comments: {overview}")
        if coverage.get("comments_with_sentiment", 0) < 1:
            raise AssertionError(f"overview did not count sentiment coverage: {overview}")
        if coverage.get("comments_with_pillar", 0) < 1:
            raise AssertionError(f"overview did not count comment pillar coverage: {overview}")
        if coverage.get("posts_with_primary_pillar", 0) < 1:
            raise AssertionError(f"overview did not count post pillar coverage: {overview}")
        distributions = overview.get("distributions") or {}
        if not distributions.get("sentiment"):
            raise AssertionError(f"overview missing sentiment distribution: {overview}")
        if not distributions.get("brand_attitude"):
            raise AssertionError(f"overview missing brand_attitude distribution: {overview}")
        if not distributions.get("pillars"):
            raise AssertionError(f"overview missing pillar distribution: {overview}")

        print("VKPI_COMMENT_INTELLIGENCE_OVERVIEW_SMOKE_OK")
    finally:
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
        conn.commit()


if __name__ == "__main__":
    main()
