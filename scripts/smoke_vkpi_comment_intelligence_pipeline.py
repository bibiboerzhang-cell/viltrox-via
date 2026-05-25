#!/usr/bin/env python3
"""Smoke P2.1 comment intelligence pipeline.

Forces LLM gateway offline and verifies an existing post with comments can run
through sentiment analysis, pillar classification, and comment pillar linking
without external API spend.
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
    from app.domains.comments import collector as comments_collector
    from app.domains.comments import intelligence as comment_intelligence
    from app.services.vkpi import pillars, sentiment

    marker = f"ci_pipe_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    project_id = account_id = post_id = None
    comment_ids: list[int] = []
    try:
        comments_collector.ensure_vkpi_comments_schema()
        sentiment.ensure_vkpi_sentiment_schema()
        pillars.ensure_vkpi_pillar_schema()

        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P2.1 pipeline smoke", marker, "brand_monitor", True, "{}"),
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
                "Viltrox lens review with sample footage",
                "A practical lens review with test footage and handling notes.",
                12,
                2,
                '["viltrox", "lensreview"]',
                "{}",
            ),
        ).fetchone()["id"]

        for idx, text in enumerate(
            [
                "Great image quality from this lens, very impressed.",
                "Could you compare this against the Sony option?",
            ],
            start=1,
        ):
            comment_id = conn.execute(
                """
                INSERT INTO vkpi_comments (
                  account_id, post_id, post_table, external_post_id,
                  platform, external_comment_id, comment_text,
                  author_handle, likes_count, reply_count, raw_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    account_id,
                    post_id,
                    "industry_posts",
                    marker + "_video",
                    "youtube",
                    f"{marker}_comment_{idx}",
                    text,
                    f"viewer_{idx}",
                    idx - 1,
                    0,
                    "{}",
                ),
            ).fetchone()["id"]
            comment_ids.append(int(comment_id))
        conn.commit()

        result = comment_intelligence.process_post(
            post_id,
            collect_comments=False,
            analyze_sentiment=True,
            classify_pillar=True,
            force_reprocess=True,
            comment_limit=10,
        )
        if result.get("status") != "ok":
            raise AssertionError(f"pipeline did not return ok: {result}")
        sentiment_step = result.get("steps", {}).get("sentiment", {})
        if sentiment_step.get("by_status", {}).get("ok") != len(comment_ids):
            raise AssertionError(f"expected all comments analyzed: {result}")
        if result.get("steps", {}).get("pillar", {}).get("status") != "ok":
            raise AssertionError(f"pillar step failed: {result}")
        if result.get("steps", {}).get("comment_pillar_links", {}).get("updated") != len(comment_ids):
            raise AssertionError(f"comments not linked to primary pillar: {result}")

        linked = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_comments
            WHERE post_id = ? AND post_table = 'industry_posts'
              AND sentiment_id IS NOT NULL AND pillar_id IS NOT NULL
            """,
            (post_id,),
        ).fetchone()
        if int((linked or {}).get("n") or 0) != len(comment_ids):
            raise AssertionError("comment sentiment/pillar links missing")

        print("VKPI_COMMENT_INTELLIGENCE_PIPELINE_SMOKE_OK")
    finally:
        if post_id is not None:
            conn.execute("DELETE FROM vkpi_post_pillars WHERE post_id = ? AND post_table = ?", (post_id, "industry_posts"))
        for comment_id in comment_ids:
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
