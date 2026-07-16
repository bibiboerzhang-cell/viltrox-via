from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
import app.domains.channels.post_metrics as post_metrics_schema
import app.domains.channels.schema as channels_schema
from app.services.vkpi.channel_post_metrics import ensure_channel_post_metrics_schema, normalize_channel_posts, record_channel_post_metrics
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema


@pytest.fixture(scope="module", autouse=True)
def _channel_post_metrics_test_db(tmp_path_factory: pytest.TempPathFactory):
    db_path = (tmp_path_factory.mktemp("channel-post-metrics") / "channels.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db
    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_channels_ready = channels_schema._SCHEMA_READY
    old_post_metrics_ready = post_metrics_schema._SCHEMA_READY
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    channels_schema._SCHEMA_READY = False
    post_metrics_schema._SCHEMA_READY = False
    conn = get_conn()
    try:
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        conn.execute(
            """
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT NOT NULL DEFAULT 'readonly',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                invited_at TEXT
            )
            """
        )
        ensure_vkpi_channels_schema()
        ensure_channel_post_metrics_schema()
        conn.commit()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        channels_schema._SCHEMA_READY = old_channels_ready
        post_metrics_schema._SCHEMA_READY = old_post_metrics_ready


def test_normalize_channel_posts_reads_instagram_and_tiktok_metrics():
    instagram = normalize_channel_posts(
        "instagram",
        {
            "raw_sample": {
                "posts": [
                    {
                        "shortCode": "ABC123",
                        "caption": "Viltrox sample",
                        "url": "https://instagram.com/p/ABC123/",
                        "timestamp": "2026-05-22T01:00:00Z",
                        "videoViewCount": 120,
                        "likesCount": 8,
                        "commentsCount": 2,
                    }
                ]
            }
        },
    )
    tiktok = normalize_channel_posts(
        "tiktok",
        {
            "raw_sample": {
                "items": [
                    {
                        "id": "7642560794273664277",
                        "text": "AF 35mm F1.2 LAB",
                        "webVideoUrl": "https://www.tiktok.com/@viltrox.global/video/7642560794273664277",
                        "createTimeISO": "2026-05-22T03:59:17.000Z",
                        "playCount": 435,
                        "diggCount": 25,
                        "commentCount": 1,
                    }
                ]
            }
        },
    )

    assert instagram[0]["post_uid"] == "ABC123"
    assert instagram[0]["canonical_post_uid"] == "instagram:ABC123"
    assert instagram[0]["provider_post_id"] == "ABC123"
    assert instagram[0]["views"] == 120
    assert instagram[0]["likes"] == 8
    assert tiktok[0]["post_uid"] == "7642560794273664277"
    assert tiktok[0]["canonical_post_uid"] == "tiktok:7642560794273664277"
    assert tiktok[0]["provider_post_id"] == "7642560794273664277"
    assert tiktok[0]["views"] == 435
    assert tiktok[0]["comments"] == 1


def test_record_channel_post_metrics_uses_seen_post_delta_not_sample_sum():
    ensure_vkpi_channels_schema()
    ensure_channel_post_metrics_schema()
    conn = get_conn()
    marker = f"unit-post-delta-{uuid.uuid4().hex}"
    staff_id = 0
    channel_id = 0
    try:
        conn.execute(
            """
            INSERT INTO staff (role, permissions_json, mfa_enabled, active, invited_at)
            VALUES (?, ?, 0, 1, ?)
            """,
            ("admin", "{}", "2026-05-22T00:00:00Z"),
        )
        staff = conn.execute("SELECT id FROM staff ORDER BY id DESC LIMIT 1").fetchone()
        staff_id = int(staff["id"])
        conn.execute(
            """
            INSERT INTO vkpi_employee_channels
                (channel_uid, staff_id, platform, account_handle, auth_method, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (marker, staff_id, "tiktok", marker, "manual_api_key", "2026-05-22T00:00:00Z", "2026-05-22T00:00:00Z", "{}"),
        )
        channel = conn.execute("SELECT id FROM vkpi_employee_channels WHERE channel_uid=?", (marker,)).fetchone()
        channel_id = int(channel["id"])
        conn.commit()

        old_post = {
            "raw_sample": {
                "items": [
                    {
                        "id": "post-old",
                        "webVideoUrl": "https://www.tiktok.com/@v/video/post-old",
                        "createTimeISO": "2026-05-20T01:00:00Z",
                        "playCount": 100,
                        "diggCount": 10,
                        "commentCount": 1,
                    }
                ]
            }
        }
        first = record_channel_post_metrics(
            channel_id=channel_id,
            platform="tiktok",
            snapshot_date="2026-05-22",
            captured_at="2026-05-22T02:00:00Z",
            raw_payload=old_post,
            previous_captured_at="2026-05-21T02:00:00Z",
        )
        assert first["first_seen_existing_posts"] == 1
        assert first["views_delta"] == 0

        updated = {
            "raw_sample": {
                "items": [
                    {
                        "id": "post-old",
                        "webVideoUrl": "https://www.tiktok.com/@v/video/post-old",
                        "createTimeISO": "2026-05-20T01:00:00Z",
                        "playCount": 145,
                        "diggCount": 12,
                        "commentCount": 2,
                    }
                ]
            }
        }
        second = record_channel_post_metrics(
            channel_id=channel_id,
            platform="tiktok",
            snapshot_date="2026-05-23",
            captured_at="2026-05-23T02:00:00Z",
            raw_payload=updated,
            previous_captured_at="2026-05-22T02:00:00Z",
        )
        assert second["matched_posts"] == 1
        assert second["views_delta"] == 45
        assert second["likes_delta"] == 2
        assert second["comments_delta"] == 1
    finally:
        if channel_id:
            conn.execute("DELETE FROM vkpi_channel_post_metrics WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM vkpi_employee_channels WHERE id=?", (channel_id,))
        if staff_id:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        conn.commit()
