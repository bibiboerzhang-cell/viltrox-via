from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains import channels
from app.domains.channels import official
from app.domains.channels import official_summary
import app.domains.channels.schema as channels_schema
from app.domains.dashboard import recent_content as dashboard_recent_content


@pytest.fixture(scope="module", autouse=True)
def _compact_summary_test_db(tmp_path_factory: pytest.TempPathFactory):
    db_path = (tmp_path_factory.mktemp("dashboard-official-summary") / "channels.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db
    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_channels_ready = channels_schema._SCHEMA_READY
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    channels_schema._SCHEMA_READY = False
    conn = get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                name TEXT,
                avatar_url TEXT DEFAULT ''
            );
            CREATE TABLE staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT NOT NULL DEFAULT 'readonly',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                mfa_enabled INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                invited_at TEXT
            );
            """
        )
        channels_schema.ensure_vkpi_channels_schema()
        conn.execute(
            """
            INSERT INTO staff (role, permissions_json, mfa_enabled, active, invited_at)
            VALUES ('admin', '{}', 0, 1, '2026-08-24T00:00:00Z')
            """
        )
        conn.commit()
        yield db_path
    finally:
        channels._clear_channel_read_cache()
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        channels_schema._SCHEMA_READY = old_channels_ready


@pytest.fixture(autouse=True)
def _seed_compact_summary_rows():
    conn = get_conn()
    staff_id = int(conn.execute("SELECT id FROM staff ORDER BY id LIMIT 1").fetchone()["id"])
    rows = (
        (
            "summary-official-instagram",
            "Instagram",
            {"official_account": True},
            12,
            1200,
        ),
        (
            "summary-official-youtube",
            "youtube",
            {"binding_source": "official_account_list_2026_05_16"},
            8,
            800,
        ),
        (
            "summary-personal-false-key",
            "youtube",
            {"official_account": False},
            999,
            999999,
        ),
    )
    for channel_uid, platform, metadata, posts, views in rows:
        conn.execute(
            """
            INSERT INTO vkpi_employee_channels
                (channel_uid, staff_id, platform, account_handle, account_display_name,
                 auth_method, last_sync_status, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'manual_api_key', 'synced', ?, ?, ?)
            """,
            (
                channel_uid,
                staff_id,
                platform,
                channel_uid,
                channel_uid,
                "2026-08-24T00:00:00Z",
                "2026-08-24T00:00:00Z",
                json.dumps(metadata),
            ),
        )
        channel_id = int(
            conn.execute(
                "SELECT id FROM vkpi_employee_channels WHERE channel_uid=?",
                (channel_uid,),
            ).fetchone()["id"]
        )
        if channel_uid == "summary-official-instagram":
            conn.execute(
                """
                INSERT INTO vkpi_channel_metrics
                    (channel_id, snapshot_date, followers, posts_count, total_views,
                     total_likes, total_comments, total_shares, followers_delta,
                     posts_delta, views_delta_24h, likes_delta_24h, engagement_rate,
                     raw_payload_json, captured_at)
                VALUES (?, '2026-08-23', 100, 999, 999999, 10, 2, 1,
                        0, 0, 0, 0, 1.0, '{}', '2026-08-23T01:00:00Z')
                """,
                (channel_id,),
            )
        conn.execute(
            """
            INSERT INTO vkpi_channel_metrics
                (channel_id, snapshot_date, followers, posts_count, total_views,
                 total_likes, total_comments, total_shares, followers_delta,
                 posts_delta, views_delta_24h, likes_delta_24h, engagement_rate,
                 raw_payload_json, captured_at)
            VALUES (?, '2026-08-24', 100, ?, ?, 10, 2, 1, 0, 0, 0, 0, 1.0, ?, ?)
            """,
            (
                channel_id,
                posts,
                views,
                json.dumps({"raw_sample": {}}),
                "2026-08-24T01:00:00Z",
            ),
        )
    conn.commit()
    channels._clear_channel_read_cache()
    try:
        yield
    finally:
        conn.execute("DELETE FROM vkpi_channel_metrics")
        conn.execute("DELETE FROM vkpi_employee_channels")
        conn.commit()
        channels._clear_channel_read_cache()


def _project_full_matrix(matrix: dict[str, object]) -> dict[str, int]:
    platforms = matrix.get("platforms") if isinstance(matrix.get("platforms"), list) else []
    return {
        "account_count": int(matrix.get("account_count") or 0),
        "post_count": int(matrix.get("post_count") or 0),
        "total_views": int(matrix.get("total_views") or 0),
        "platform_count": len(platforms),
    }


def test_compact_official_summary_matches_full_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(official, "_staff_managed_summary", lambda: [])

    full = channels.official_account_matrix(limit=20)
    compact = channels.official_account_matrix_summary(limit=20)

    assert compact == _project_full_matrix(full)
    assert compact == {
        "account_count": 2,
        "post_count": 20,
        "total_views": 2000,
        "platform_count": 2,
    }


def test_compact_summary_preserves_zero_post_sample_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = get_conn()
    staff_id = int(conn.execute("SELECT id FROM staff ORDER BY id LIMIT 1").fetchone()["id"])
    conn.execute(
        """
        INSERT INTO vkpi_employee_channels
            (channel_uid, staff_id, platform, account_handle, account_display_name,
             auth_method, last_sync_status, created_at, updated_at, metadata_json)
        VALUES ('summary-zero-posts', ?, 'youtube', 'summary-zero-posts',
                'summary-zero-posts', 'manual_api_key', 'synced', ?, ?, ?)
        """,
        (
            staff_id,
            "2026-08-24T00:00:00Z",
            "2026-08-24T00:00:00Z",
            json.dumps({"official_list_20260516": True}),
        ),
    )
    channel_id = int(
        conn.execute(
            "SELECT id FROM vkpi_employee_channels WHERE channel_uid='summary-zero-posts'"
        ).fetchone()["id"]
    )
    raw_payload = {
        "raw_sample": {
            "videos": [
                {"id": "video-one", "snippet": {"title": "One"}},
                {"id": "video-two", "snippet": {"title": "Two"}},
            ]
        }
    }
    conn.execute(
        """
        INSERT INTO vkpi_channel_metrics
            (channel_id, snapshot_date, followers, posts_count, total_views,
             total_likes, total_comments, total_shares, followers_delta,
             posts_delta, views_delta_24h, likes_delta_24h, engagement_rate,
             raw_payload_json, captured_at)
        VALUES (?, '2026-08-24', 10, 0, 77, 0, 0, 0, 0, 0, 0, 0, 0, ?, ?)
        """,
        (channel_id, json.dumps(raw_payload), "2026-08-24T02:00:00Z"),
    )
    conn.commit()
    channels._clear_channel_read_cache()
    monkeypatch.setattr(official, "_staff_managed_summary", lambda: [])

    full = channels.official_account_matrix(limit=1)
    compact = channels.official_account_matrix_summary(limit=1)

    assert compact == _project_full_matrix(full)
    assert compact["post_count"] == 21
    assert compact["total_views"] == 2077


def test_compact_metric_path_skips_full_media_and_staff_work(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("compact metric path entered full matrix work")

    from app.domains.channels import posts as channel_posts

    monkeypatch.setattr(official, "official_account_matrix", forbidden)
    monkeypatch.setattr(official, "_extract_posts", forbidden)
    monkeypatch.setattr(official, "_attach_cached_item_videos", forbidden)
    monkeypatch.setattr(official, "_attach_post_identity", forbidden)
    monkeypatch.setattr(official, "_attach_media_contract", forbidden)
    monkeypatch.setattr(official, "_staff_managed_summary", forbidden)
    monkeypatch.setattr(channel_posts, "_posts_from_package", forbidden)

    summary_rows = official_summary._latest_channel_summary_rows()
    official_rows = [row for row in summary_rows if official_summary._is_official_channel_row(row)]
    assert all(row["metric_raw_payload_json"] is None for row in official_rows)
    assert official_summary.official_account_matrix_summary(limit=20) == {
        "account_count": 2,
        "post_count": 20,
        "total_views": 2000,
        "platform_count": 2,
    }


def test_dashboard_summary_uses_compact_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("dashboard summary called the full official matrix")

    monkeypatch.setattr(dashboard_recent_content.channels, "official_account_matrix", forbidden)
    monkeypatch.setattr(
        dashboard_recent_content.channels,
        "official_account_matrix_summary",
        lambda *, limit: {
            "account_count": 18,
            "post_count": 120,
            "total_views": 5000,
            "platform_count": 4,
        },
    )

    assert dashboard_recent_content._dashboard_official_matrix_summary(limit=20) == {
        "account_count": 18,
        "post_count": 120,
        "total_views": 5000,
        "platform_count": 4,
        "source": "official-channel-matrix",
    }
