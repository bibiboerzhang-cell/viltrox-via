#!/usr/bin/env python3
"""Smoke test for Phase 1 industry snapshot collection.

The test verifies two critical rules:
1. Disabled/unconfigured providers do not create fake zero snapshots.
2. Real raw platform data can be converted into the full 22+ KPI snapshot
   shape and persisted with posts.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.services.vkpi import industry_data, industry_snapshot_collector  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _raw_fixture(marker: str) -> dict[str, Any]:
    return {
        "source": "smoke_fixture",
        "snapshot_date": "2026-05-09",
        "youtube_kpi_status": "fixture",
        "youtube_kpi_source_ref": f"fixture-{marker}",
        "profile": {
            "items": [
                {
                    "id": f"UC{marker[:10]}",
                    "snippet": {
                        "title": f"{marker} Viltrox Test Channel",
                        "description": "Camera lenses, autofocus tests and creator workflow.",
                    },
                    "statistics": {
                        "subscriberCount": "12345",
                        "videoCount": "88",
                        "viewCount": "987654",
                    },
                }
            ]
        },
        "videos": [
            {
                "id": f"{marker}a",
                "snippet": {
                    "publishedAt": "2026-05-07T10:00:00Z",
                    "title": "Viltrox AF 35mm F1.2 review #viltrox #lens",
                    "description": "Sharpness and autofocus test #camera",
                    "thumbnails": {"high": {"url": f"https://img.example/{marker}a.jpg"}},
                },
                "statistics": {"viewCount": "1500", "likeCount": "180", "commentCount": "24"},
                "contentDetails": {"duration": "PT8M12S"},
            },
            {
                "id": f"{marker}b",
                "snippet": {
                    "publishedAt": "2026-05-08T14:00:00Z",
                    "title": "Sigma vs Viltrox portrait lens",
                    "description": "35mm samples #portrait",
                    "thumbnails": {"medium": {"url": f"https://img.example/{marker}b.jpg"}},
                },
                "statistics": {"viewCount": "900", "likeCount": "90", "commentCount": "10"},
                "contentDetails": {"duration": "PT5M30S"},
            },
        ],
    }


def _cleanup(marker: str) -> None:
    conn = get_conn()
    project_rows = conn.execute("SELECT id FROM vkpi_industry_projects WHERE name LIKE ?", (f"%{marker}%",)).fetchall()
    account_ids: list[int] = []
    for row in project_rows:
        account_ids.extend([int(item["id"]) for item in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE project_id=?", (int(row["id"]),)).fetchall()])
    account_ids.extend([int(item["id"]) for item in conn.execute("SELECT id FROM vkpi_industry_accounts WHERE handle LIKE ?", (f"%{marker}%",)).fetchall()])
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
    marker = f"smoke_phase1_{secrets.token_hex(5)}"
    try:
        project = industry_data.create_project({"name": f"{marker} industry project"}).get("project") or {}
        project_id = int(project["id"])
        account = industry_data.add_account(
            project_id,
            {
                "platform": "youtube",
                "handle": f"{marker}_channel",
                "profile_url": f"https://www.youtube.com/@{marker}_channel",
                "crawl_enabled": False,
            },
        ).get("account") or {}
        account_id = int(account["id"])

        blocked = industry_data.refresh_account(account_id)
        assert blocked.get("sync_status") in {"disabled", "not_configured"}, blocked
        assert blocked.get("provider_status") in {"disabled", "not_configured", "budget_disabled"}, blocked
        no_snapshot_count = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_account_snapshots WHERE account_id=?", (account_id,)).fetchone()["c"]
        assert int(no_snapshot_count) == 0, "disabled refresh must not write fake snapshot"

        kpis = industry_snapshot_collector.calculate_kpis(_raw_fixture(marker))
        missing = [field for field in industry_snapshot_collector.SNAPSHOT_FIELDS if field not in kpis]
        assert not missing, missing
        assert kpis["followers"] == 12345, kpis
        assert kpis["posts"] == 88, kpis
        assert kpis["views"] == 987654, kpis
        assert kpis["views_30d"] == 2400, kpis
        assert kpis["likes"] == 270, kpis
        assert kpis["comments"] == 34, kpis
        assert kpis["engagement_total_30d"] == 304, kpis
        assert kpis["avg_video_duration_seconds"] == 411.0, kpis
        assert kpis["reach_total_30d"] is None, "unknown fields must stay null"

        collected = industry_snapshot_collector.collect_account_snapshot(account_id, raw_data=_raw_fixture(marker), force_local=True)
        assert collected.get("sync_status") == "synced", collected
        assert collected.get("posts_written") == 2, collected
        snapshot = collected.get("snapshot") or {}
        assert int(snapshot.get("followers") or 0) == 12345, snapshot
        assert str(snapshot.get("youtube_kpi_status") or "") == "fixture", snapshot
        post_count = get_conn().execute("SELECT COUNT(*) AS c FROM vkpi_industry_posts WHERE account_id=?", (account_id,)).fetchone()["c"]
        assert int(post_count) == 2, post_count
        print("VKPI_PHASE1_SNAPSHOT_COLLECTOR_SMOKE_OK")
    finally:
        _cleanup(marker)


if __name__ == "__main__":
    main()
