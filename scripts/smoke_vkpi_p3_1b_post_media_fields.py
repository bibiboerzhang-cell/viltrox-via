#!/usr/bin/env python3
"""Smoke test for P3.1B post media persistence.

This is an offline/no-cost test. It feeds Apify-shaped payloads into the
history import and collector seams, then verifies video fields survive into
vkpi_industry_posts for the frontend video player.
"""
from __future__ import annotations

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
from app.services.vkpi import industry_data, industry_snapshot_collector  # noqa: E402


def _cleanup(marker: str) -> None:
    conn = get_conn()
    project_rows = conn.execute("SELECT id FROM vkpi_industry_projects WHERE name LIKE ?", (f"%{marker}%",)).fetchall()
    account_ids: list[int] = []
    for row in project_rows:
        account_ids.extend(
            [
                int(item["id"])
                for item in conn.execute(
                    "SELECT id FROM vkpi_industry_accounts WHERE project_id=?",
                    (int(row["id"]),),
                ).fetchall()
            ]
        )
    account_ids.extend(
        [
            int(item["id"])
            for item in conn.execute(
                "SELECT id FROM vkpi_industry_accounts WHERE handle LIKE ? OR raw_platform_data LIKE ?",
                (f"%{marker}%", f"%{marker}%"),
            ).fetchall()
        ]
    )
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


def _post_for(account_id: int, post_id: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM vkpi_industry_posts WHERE account_id=? AND platform_post_id=?",
        (int(account_id), str(post_id)),
    ).fetchone()
    assert row, f"post not found: {post_id}"
    return dict(row)


def main() -> None:
    marker = f"smoke_p31b_{secrets.token_hex(5)}"
    try:
        project = industry_data.create_project({"name": f"{marker} media project"}).get("project") or {}
        project_id = int(project["id"])

        imported = industry_data.import_historical_dataset(
            project_id,
            [
                {
                    "platform": "instagram",
                    "username": f"{marker}_ig",
                    "profileUrl": f"https://www.instagram.com/{marker}_ig/",
                    "fullName": "P3.1B Instagram media fixture",
                    "followersCount": 12345,
                    "mediaCount": 9,
                    "latestPosts": [
                        {
                            "id": f"{marker}_ig_1",
                            "url": f"https://www.instagram.com/reel/{marker}/",
                            "videoUrl": f"https://scontent.cdninstagram.com/v/t50.2886-16/{marker}.mp4",
                            "displayUrl": f"https://scontent.cdninstagram.com/v/t51.2885-15/{marker}.jpg",
                            "caption": "Video fixture #viltrox",
                            "takenAt": "2026-05-10T12:00:00Z",
                            "duration": 17,
                            "likesCount": 200,
                            "commentsCount": 11,
                            "videoViewCount": 3000,
                        }
                    ],
                }
            ],
            source_type="apify_media_fixture",
            source_ref=f"dataset-{marker}",
        )
        assert imported["posts_written"] == 1, imported
        ig_account = imported["accounts"][0]
        ig_post = _post_for(int(ig_account["id"]), f"{marker}_ig_1")
        assert ig_post["video_url"].endswith(f"{marker}.mp4"), ig_post
        assert ig_post["media_type"] == "video", ig_post
        assert int(ig_post["duration_seconds"] or 0) == 17, ig_post
        assert ig_post["video_source"] == "apify_cdn", ig_post
        assert ig_post["thumbnail_url"].endswith(f"{marker}.jpg"), ig_post

        tiktok_account = industry_data.add_account(
            project_id,
            {
                "platform": "tiktok",
                "handle": f"{marker}_tk",
                "profile_url": f"https://www.tiktok.com/@{marker}_tk",
                "crawl_enabled": False,
            },
        ).get("account") or {}
        collected = industry_snapshot_collector.collect_account_snapshot(
            int(tiktok_account["id"]),
            raw_data={
                "source": "smoke_fixture",
                "snapshot_date": "2026-05-10",
                "profile": {"items": [{"username": f"{marker}_tk", "statistics": {"followers": 1200, "posts": 2}}]},
                "videos": [
                    {
                        "id": f"{marker}_tk_1",
                        "webVideoUrl": f"https://www.tiktok.com/@{marker}_tk/video/1",
                        "downloadAddr": f"https://v16-webapp.tiktokcdn-us.com/{marker}.mp4",
                        "caption": "TikTok fixture #viltrox",
                        "timestamp": "2026-05-10T13:00:00Z",
                        "duration": "9",
                        "playCount": 99,
                        "diggCount": 7,
                        "commentCount": 3,
                    }
                ],
            },
            force_local=True,
        )
        assert collected["posts_written"] == 1, collected
        tk_post = _post_for(int(tiktok_account["id"]), f"{marker}_tk_1")
        assert tk_post["video_url"].endswith(f"{marker}.mp4"), tk_post
        assert tk_post["media_type"] == "video", tk_post
        assert int(tk_post["duration_seconds"] or 0) == 9, tk_post

        print("VKPI_P3_1B_POST_MEDIA_FIELDS_SMOKE_OK")
    finally:
        _cleanup(marker)


if __name__ == "__main__":
    main()
