#!/usr/bin/env python3
"""Smoke P2.2 comment intelligence run ledger + retry path."""
from __future__ import annotations
from stdout_utils import out as stdout_out

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
    import app.domains.comments.sentiment as sentiment
    from app.domains.content import pillars

    marker = f"ci_runs_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    project_id = account_id = post_id = None
    comment_id = None
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
            (marker + "_project", "P2.2 run ledger smoke", marker, "brand_monitor", True, "{}"),
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
                "Viltrox run ledger test",
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
                "This looks useful for portrait work.",
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
        if result.get("status") != "ok":
            raise AssertionError(f"unexpected first run: {result}")
        run_ids.append(int(result["run_id"]))

        detail = comment_intelligence.get_run(result["run_id"])
        if not detail or detail.get("status") != "ok":
            raise AssertionError(f"run detail missing: {detail}")
        if not detail.get("params") or not detail.get("steps"):
            raise AssertionError(f"run params/steps missing: {detail}")

        listed = comment_intelligence.list_runs(post_id=post_id, limit=10)
        if listed.get("count", 0) < 1:
            raise AssertionError(f"run list missing result: {listed}")

        retry = comment_intelligence.retry_run(result["run_id"])
        if retry.get("status") != "ok":
            raise AssertionError(f"retry failed: {retry}")
        run_ids.append(int(retry["run_id"]))

        retry_detail = comment_intelligence.get_run(retry["run_id"])
        if int(retry_detail.get("retry_of_run_id") or 0) != int(result["run_id"]):
            raise AssertionError(f"retry link missing: {retry_detail}")

        stdout_out("VKPI_COMMENT_INTELLIGENCE_RUNS_SMOKE_OK")
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
