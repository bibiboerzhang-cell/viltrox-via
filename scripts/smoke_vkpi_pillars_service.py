#!/usr/bin/env python3
"""Smoke P1.5 pillar service persistence path.

Forces LLM gateway offline and verifies classify_post() still creates a
deterministic primary pillar row via rule fallback. No provider quota is used.
"""
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
    from app.domains.content import pillars

    marker = f"pillar_smoke_{uuid.uuid4().hex[:10]}"
    conn = get_conn()
    project_id = account_id = post_id = None
    try:
        pillars.ensure_vkpi_pillar_schema()
        project_id = conn.execute(
            """
            INSERT INTO vkpi_industry_projects (project_uid, name, description, project_type, is_active, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (marker + "_project", "P1.5 pillar smoke", marker, "brand_monitor", True, "{}"),
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
              title, caption, hashtags_json, raw_platform_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                marker + "_post",
                account_id,
                "youtube",
                marker + "_video",
                "https://www.youtube.com/watch?v=" + marker[:11],
                "Viltrox lens review and sample footage",
                "A hands-on lens review with test footage and shooting tips.",
                '["viltrox", "lensreview"]',
                "{}",
            ),
        ).fetchone()["id"]
        conn.commit()

        result = pillars.classify_post(post_id, force_reclassify=True)
        if result.get("status") != "ok":
            raise AssertionError(f"unexpected status: {result}")
        if result.get("primary_pillar") != "other":
            raise AssertionError(f"offline fallback should classify as other: {result}")
        if result.get("llm_provider") != "rule_v0":
            raise AssertionError(f"expected rule_v0 provider: {result}")

        stored = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM vkpi_post_pillars
            WHERE post_id = ? AND post_table = ? AND prompt_version = ?
            """,
            (post_id, "industry_posts", pillars.PROMPT_VERSION),
        ).fetchone()
        if not stored or int(stored["n"] or 0) < 1:
            raise AssertionError("pillar classification was not persisted")

        listed = pillars.list_pillars()
        if len(listed.get("pillars") or []) < 17:
            raise AssertionError("default pillar seeds missing")

        stdout_out("VKPI_PILLARS_SERVICE_SMOKE_OK")
    finally:
        if post_id is not None:
            conn.execute("DELETE FROM vkpi_post_pillars WHERE post_id = ? AND post_table = ?", (post_id, "industry_posts"))
            conn.execute("DELETE FROM vkpi_industry_posts WHERE id = ?", (post_id,))
        if account_id is not None:
            conn.execute("DELETE FROM vkpi_industry_accounts WHERE id = ?", (account_id,))
        if project_id is not None:
            conn.execute("DELETE FROM vkpi_industry_projects WHERE id = ?", (project_id,))
        conn.commit()


if __name__ == "__main__":
    main()
