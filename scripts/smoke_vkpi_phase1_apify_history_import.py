#!/usr/bin/env python3
"""Smoke test for Phase 1 Apify/history JSON import.

This validates the no-cost import path:
1. Account-only rows create accounts but do not create fake zero snapshots.
2. Rows with real metrics/videos create snapshots and posts from supplied data.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi import industry_data  # noqa: E402


def _cleanup(marker: str) -> None:
    conn = get_conn()
    projects = conn.execute("SELECT id FROM vkpi_industry_projects WHERE name LIKE ?", (f"%{marker}%",)).fetchall()
    account_ids: list[int] = []
    for project in projects:
        account_ids.extend([int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE project_id=?", (int(project["id"]),)).fetchall()])
    account_ids.extend([int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE handle LIKE ? OR raw_platform_data LIKE ?", (f"%{marker}%", f"%{marker}%")).fetchall()])
    for account_id in set(account_ids):
        post_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_industry_posts WHERE account_id=?", (account_id,)).fetchall()]
        for post_id in post_ids:
            conn.execute("DELETE FROM vkpi_industry_post_metrics WHERE post_id=?", (post_id,))
            conn.execute("DELETE FROM vkpi_industry_post_tags WHERE post_id=?", (post_id,))
        conn.execute("DELETE FROM vkpi_industry_posts WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_account_snapshots WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM vkpi_industry_accounts WHERE id=?", (account_id,))
    conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (f"%{marker}%", f"%{marker}%"))
    conn.execute("DELETE FROM vkpi_industry_projects WHERE name LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%"))
    conn.commit()


def main() -> None:
    marker = f"smoke_apify_import_{secrets.token_hex(5)}"
    try:
        project = industry_data.create_project({"name": f"{marker} project"}).get("project") or {}
        project_id = int(project["id"])
        result = industry_data.import_historical_dataset(
            project_id,
            [
                {
                    "platform": "instagram",
                    "username": f"{marker}_account_only",
                    "profileUrl": f"https://www.instagram.com/{marker}_account_only/",
                    "fullName": "Account only creator",
                },
                {
                    "platform": "youtube",
                    "handle": f"{marker}_yt",
                    "channelUrl": f"https://www.youtube.com/@{marker}_yt",
                    "displayName": "Real metrics creator",
                    "followers": 45678,
                    "videoCount": 120,
                    "views": 987000,
                    "videos": [
                        {
                            "id": f"{marker}v1",
                            "title": "Viltrox AF 35mm F1.2 field test",
                            "description": "Autofocus review #viltrox",
                            "url": f"https://www.youtube.com/watch?v={marker}v1",
                            "publishedAt": "2026-05-08T08:00:00Z",
                            "views": 3200,
                            "likes": 420,
                            "comments": 38,
                            "thumbnailUrl": f"https://img.example/{marker}.jpg",
                        }
                    ],
                },
            ],
            source_type="apify_json",
            source_ref=f"dataset-{marker}",
        )
        assert result["imported"] == 2, result
        assert result["skipped_count"] == 0, result
        assert result["snapshots_written"] == 1, result
        assert result["posts_written"] == 1, result

        accounts = industry_data.list_accounts(project_id=project_id).get("accounts") or []
        assert len(accounts) == 2, accounts
        account_only = next(row for row in accounts if "account_only" in str(row.get("handle")))
        metrics_account = next(row for row in accounts if str(row.get("platform")) == "youtube")
        no_snapshot = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_account_snapshots WHERE account_id=?", (int(account_only["id"]),)).fetchone()["c"]
        assert int(no_snapshot) == 0, "account-only import must not create fake snapshot"
        snapshot = get_conn().execute("SELECT * FROM vkpi_industry_account_snapshots WHERE account_id=?", (int(metrics_account["id"]),)).fetchone()
        assert snapshot, "metrics row should create snapshot"
        assert int(snapshot["followers"] or 0) == 45678, dict(snapshot)
        assert int(snapshot["views_30d"] or 0) == 3200, dict(snapshot)
        post_count = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_posts WHERE account_id=?", (int(metrics_account["id"]),)).fetchone()["c"]
        assert int(post_count) == 1, post_count
        stdout_out("VKPI_PHASE1_APIFY_HISTORY_IMPORT_SMOKE_OK")
    finally:
        _cleanup(marker)


if __name__ == "__main__":
    main()
