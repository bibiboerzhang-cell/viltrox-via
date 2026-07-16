from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
import app.domains.channels.schema as channels_schema
from app.domains.access import scope
from app.domains import channels
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema


@pytest.fixture(scope="module", autouse=True)
def _channel_baseline_test_db(tmp_path_factory: pytest.TempPathFactory):
    db_path = (tmp_path_factory.mktemp("channel-baseline") / "channels.db").resolve()
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
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
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
        ensure_vkpi_channels_schema()
        conn.commit()
        yield db_path
    finally:
        channels._clear_channel_read_cache()
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        channels_schema._SCHEMA_READY = old_channels_ready


def test_official_matrix_surfaces_cumulative_floor_as_baseline_protected() -> None:
    ensure_vkpi_channels_schema()
    conn = get_conn()
    marker = f"unit-baseline-protected-{uuid.uuid4().hex}"
    staff_id = 0
    channel_id = 0
    try:
        conn.execute(
            """
            INSERT INTO staff (role, permissions_json, mfa_enabled, active, invited_at)
            VALUES (?, ?, 0, 1, ?)
            """,
            ("staff", "{}", "2026-05-23T00:00:00Z"),
        )
        staff = conn.execute("SELECT id FROM staff ORDER BY id DESC LIMIT 1").fetchone()
        staff_id = int(staff["id"])
        conn.execute(
            """
            INSERT INTO vkpi_employee_channels
                (channel_uid, staff_id, platform, account_handle, account_display_name, auth_method,
                 last_sync_status, created_at, updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                marker,
                staff_id,
                "instagram",
                marker,
                "Baseline Protected Test",
                "manual_api_key",
                "synced",
                "2026-05-23T00:00:00Z",
                "2026-05-23T00:00:00Z",
                json.dumps({"official_account": True}),
            ),
        )
        channel = conn.execute("SELECT id FROM vkpi_employee_channels WHERE channel_uid=?", (marker,)).fetchone()
        channel_id = int(channel["id"])
        raw_payload = {
            "cumulative_floor": {
                "reason": "unit narrower sample",
                "fields": {
                    "posts_count": {"provider_value": 40, "kept_value": 50},
                    "total_views": {"provider_value": 900, "kept_value": 1000},
                },
            },
            "raw_sample": {"posts": []},
        }
        conn.execute(
            """
            INSERT INTO vkpi_channel_metrics
                (channel_id, snapshot_date, followers, posts_count, total_views, total_likes,
                 total_comments, total_shares, followers_delta, posts_delta, views_delta_24h,
                 likes_delta_24h, engagement_rate, raw_payload_json, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                "2026-05-23",
                100,
                50,
                1000,
                80,
                12,
                3,
                0,
                0,
                0,
                0,
                9.5,
                json.dumps(raw_payload),
                "2026-05-23T01:00:00Z",
            ),
        )
        conn.commit()
        channels._clear_channel_read_cache()

        result = channels.official_account_matrix(staff={"id": staff_id, "role": "staff"}, limit=20)

        assert result["account_count"] >= 1
        platform = next(item for item in result["platforms"] if item["platform"] == "instagram")
        account = next(item for item in platform["accounts"] if item["id"] == channel_id)
        assert platform["baseline_protected"] is True
        assert platform["baseline_protected_accounts"] >= 1
        assert {"posts_count", "total_views"}.issubset(set(platform["baseline_protected_fields"]))
        assert account["baseline_protected"] is True
        assert account["baseline_protected_fields"] == ["posts_count", "total_views"]
        assert account["baseline_protected_reason"] == "unit narrower sample"
        assert account["baseline_protected_detail"] == {
            "posts_count": {"provider_value": 40, "kept_value": 50},
            "total_views": {"provider_value": 900, "kept_value": 1000},
        }
        assert account["last_sync_at"] == "2026-05-23T01:00:00Z"
        assert account["posts_delta"] == 0
        assert account["views_delta"] == 0
    finally:
        if channel_id:
            conn.execute("DELETE FROM vkpi_channel_metrics WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM vkpi_employee_channels WHERE id=?", (channel_id,))
        if staff_id:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        conn.commit()
        channels._clear_channel_read_cache()


def test_official_matrix_shows_company_accounts_to_employee_read_only() -> None:
    ensure_vkpi_channels_schema()
    conn = get_conn()
    marker = f"unit-channel-scope-{uuid.uuid4().hex}"
    staff_ids: list[int] = []
    channel_ids: list[int] = []
    try:
        for suffix in ("owner", "employee"):
            conn.execute(
                """
                INSERT INTO staff (role, permissions_json, mfa_enabled, active, invited_at)
                VALUES (?, ?, 0, 1, ?)
                """,
                ("employee", '{"vkpi":"write"}', "2026-05-23T00:00:00Z"),
            )
            staff_id = int(conn.execute("SELECT id FROM staff ORDER BY id DESC LIMIT 1").fetchone()["id"])
            staff_ids.append(staff_id)
        for suffix, staff_id, metadata in (
            ("official", staff_ids[0], {"official_account": True}),
            ("personal", staff_ids[0], {}),
        ):
            conn.execute(
                """
                INSERT INTO vkpi_employee_channels
                    (channel_uid, staff_id, platform, account_handle, account_display_name, auth_method,
                     last_sync_status, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{marker}-{suffix}",
                    staff_id,
                    "youtube",
                    f"{marker}-{suffix}",
                    f"Scope {suffix}",
                    "manual_api_key",
                    "synced",
                    "2026-05-23T00:00:00Z",
                    "2026-05-23T00:00:00Z",
                    json.dumps(metadata),
                ),
            )
            channel_id = int(conn.execute("SELECT id FROM vkpi_employee_channels WHERE channel_uid=?", (f"{marker}-{suffix}",)).fetchone()["id"])
            channel_ids.append(channel_id)
        conn.commit()
        channels._clear_channel_read_cache()

        employee = {"id": staff_ids[1], "role": "employee", "permissions": {"vkpi": "write"}}
        employee_result = channels.official_account_matrix(staff=employee, limit=20)
        employee_account_ids = {
            account["id"]
            for platform in employee_result["platforms"]
            for account in platform["accounts"]
        }
        assert channel_ids[0] in employee_account_ids
        assert channel_ids[1] not in employee_account_ids
        assert channels.get_channel(channel_ids[0], staff=employee)["channel"]["id"] == channel_ids[0]
        with pytest.raises(scope.ScopeDenied):
            channels.get_channel(channel_ids[0], staff=employee, write=True)
        with pytest.raises(scope.ScopeDenied):
            channels.sync_now(channel_ids[0], staff=employee)

        admin_result = channels.official_account_matrix(staff={"id": staff_ids[0], "role": "admin"}, limit=20)
        admin_account_ids = {
            account["id"]
            for platform in admin_result["platforms"]
            for account in platform["accounts"]
        }
        assert channel_ids[0] in admin_account_ids
        assert channel_ids[1] not in admin_account_ids
        assert channels.get_channel(channel_ids[1], staff={"id": staff_ids[0], "role": "admin"})["channel"]["id"] == channel_ids[1]
        assert channels.get_channel(channel_ids[1], staff={"id": staff_ids[0], "role": "admin"}, write=True)["channel"]["id"] == channel_ids[1]
    finally:
        for channel_id in channel_ids:
            conn.execute("DELETE FROM vkpi_channel_metrics WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM vkpi_employee_channels WHERE id=?", (channel_id,))
        for staff_id in staff_ids:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        conn.commit()
        channels._clear_channel_read_cache()
