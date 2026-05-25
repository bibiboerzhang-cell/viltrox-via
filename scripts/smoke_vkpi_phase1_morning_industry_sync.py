#!/usr/bin/env python3
"""Smoke test for the 08:00 morning industry account sync seam.

It seeds one crawl-enabled account while all platform settings remain disabled.
The morning sync must skip it as not_configured and must not create fake
snapshots or fake posts.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")
os.environ["YOUTUBE_API_KEY"] = ""
os.environ["GOOGLE_YOUTUBE_API_KEY"] = ""

from app.db.connection import get_conn  # noqa: E402
from app.domains.sync import cron  # noqa: E402
from app.services.vkpi import industry_data, industry_snapshot_collector  # noqa: E402


def _cleanup(marker: str) -> None:
    conn = get_conn()
    projects = conn.execute("SELECT id FROM vkpi_industry_projects WHERE name LIKE ?", (f"%{marker}%",)).fetchall()
    account_ids = []
    for project in projects:
        account_ids.extend([int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE project_id=?", (int(project["id"]),)).fetchall()])
    account_ids.extend([int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE handle LIKE ?", (f"%{marker}%",)).fetchall()])
    for account_id in set(account_ids):
        post_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_posts WHERE account_id=?", (account_id,)).fetchall()]
        for post_id in post_ids:
            conn.execute("DELETE FROM vkpi_industry_post_metrics WHERE post_id=?", (post_id,))
            conn.execute("DELETE FROM vkpi_industry_post_tags WHERE post_id=?", (post_id,))
        conn.execute("DELETE FROM vkpi_industry_posts WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_account_snapshots WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_accounts WHERE id=?", (account_id,))
    conn.execute("DELETE FROM vkpi_industry_projects WHERE name LIKE ?", (f"%{marker}%",))
    conn.commit()


def main() -> None:
    marker = f"smoke_morning_industry_{secrets.token_hex(5)}"
    try:
        project = industry_data.create_project({"name": f"{marker} project"}).get("project") or {}
        account = industry_data.add_account(
            int(project["id"]),
            {
                "platform": "youtube",
                "handle": f"{marker}_channel",
                "profile_url": f"https://www.youtube.com/@{marker}_channel",
                "crawl_enabled": True,
            },
        ).get("account") or {}
        direct = industry_snapshot_collector.sync_enabled_accounts(limit=20)
        assert direct.get("candidate_accounts", 0) >= 1, direct
        assert direct.get("synced") == 0, direct
        assert direct.get("skipped", 0) >= 1, direct

        result = asyncio.run(cron.run_job("morning_sync", {"limit": 100, "max_videos": 1, "period_days": 1, "industry_account_limit": 20}))
        assert result.get("status") == "ok", result
        assert result.get("industry_accounts_synced") == 0, result
        assert result.get("industry_accounts_skipped", 0) >= 1, result
        snapshot_count = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_account_snapshots WHERE account_id=?", (int(account["id"]),)).fetchone()["c"]
        post_count = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_posts WHERE account_id=?", (int(account["id"]),)).fetchone()["c"]
        assert int(snapshot_count) == 0, "not_configured morning sync must not create fake snapshot"
        assert int(post_count) == 0, "not_configured morning sync must not create fake posts"
        print("VKPI_PHASE1_MORNING_INDUSTRY_SYNC_SMOKE_OK")
    finally:
        _cleanup(marker)


if __name__ == "__main__":
    main()
