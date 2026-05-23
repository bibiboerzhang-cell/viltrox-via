from __future__ import annotations

import json
import uuid

from app.db.connection import get_conn
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema
from scripts import vkpi_channel_delta_dry_run


def _insert_staff() -> int:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO staff (role, permissions_json, mfa_enabled, active, invited_at)
        VALUES (?, ?, 0, 1, ?)
        """,
        ("staff", "{}", "2026-05-23T00:00:00Z"),
    )
    row = conn.execute("SELECT id FROM staff ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"])


def _insert_channel(staff_id: int, *, marker: str, platform: str, raw_payload: dict, with_post_metrics: bool = True) -> int:
    conn = get_conn()
    handle = f"{marker}-{platform}"
    conn.execute(
        """
        INSERT INTO vkpi_employee_channels
            (channel_uid, staff_id, platform, account_handle, account_display_name, auth_method,
             last_sync_status, created_at, updated_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            handle,
            staff_id,
            platform,
            handle,
            handle,
            "manual_api_key",
            "synced",
            "2026-05-23T00:00:00Z",
            "2026-05-23T00:00:00Z",
            "{}",
        ),
    )
    channel = conn.execute("SELECT id FROM vkpi_employee_channels WHERE channel_uid=?", (handle,)).fetchone()
    channel_id = int(channel["id"])
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
            10,
            1000,
            100,
            10,
            0,
            0,
            int((raw_payload.get("post_level_delta") or {}).get("new_posts") or 0),
            int((raw_payload.get("post_level_delta") or {}).get("views_delta") or 0),
            int((raw_payload.get("post_level_delta") or {}).get("likes_delta") or 0),
            11.0,
            json.dumps(raw_payload),
            "2026-05-23T01:00:00Z",
        ),
    )
    if with_post_metrics:
        conn.execute(
            """
            INSERT INTO vkpi_channel_post_metrics
                (channel_id, snapshot_date, platform, post_uid, post_url, title, posted_at,
                 views, likes, comments, shares, views_delta, likes_delta, comments_delta,
                 shares_delta, delta_method, raw_post_json, first_seen_at, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                "2026-05-23",
                platform,
                f"{handle}-post-1",
                "https://example.com/post-1",
                "Post 1",
                "2026-05-23T00:30:00Z",
                120,
                12,
                2,
                0,
                int((raw_payload.get("post_level_delta") or {}).get("views_delta") or 0),
                int((raw_payload.get("post_level_delta") or {}).get("likes_delta") or 0),
                int((raw_payload.get("post_level_delta") or {}).get("comments_delta") or 0),
                0,
                "post_metric_delta_v1",
                "{}",
                "2026-05-23T01:00:00Z",
                "2026-05-23T01:00:00Z",
            ),
        )
    conn.commit()
    return channel_id


def test_channel_delta_dry_run_reports_real_and_protected_delta_states(tmp_path) -> None:
    ensure_vkpi_channels_schema()
    marker = f"unit-delta-dry-run-{uuid.uuid4().hex}"
    conn = get_conn()
    staff_id = _insert_staff()
    channel_ids: list[int] = []
    try:
        channel_ids.append(
            _insert_channel(
                staff_id,
                marker=marker,
                platform="youtube",
                raw_payload={
                    "post_level_delta": {
                        "method": "post_metric_delta_v1",
                        "sample_count": 1,
                        "matched_posts": 1,
                        "new_posts": 0,
                        "first_seen_existing_posts": 0,
                        "views_delta": 45,
                        "likes_delta": 3,
                        "comments_delta": 1,
                    }
                },
            )
        )
        channel_ids.append(
            _insert_channel(
                staff_id,
                marker=marker,
                platform="instagram",
                raw_payload={
                    "cumulative_floor": {
                        "fields": {"total_views": {"provider_value": 90, "kept_value": 100}},
                    },
                    "post_level_delta": {
                        "method": "post_metric_delta_v1",
                        "sample_count": 1,
                        "matched_posts": 0,
                        "new_posts": 0,
                        "first_seen_existing_posts": 1,
                        "views_delta": 0,
                        "likes_delta": 0,
                        "comments_delta": 0,
                    },
                },
            )
        )
        channel_ids.append(
            _insert_channel(
                staff_id,
                marker=marker,
                platform="tiktok",
                raw_payload={},
                with_post_metrics=False,
            )
        )

        report = vkpi_channel_delta_dry_run.build_report()
        accounts = [account for account in report["accounts"] if str(account["handle"]).startswith(marker)]
        by_platform = {account["platform"]: account for account in accounts}

        assert report["provider_calls"] is False
        assert len(accounts) == 3
        assert by_platform["youtube"]["explanation"] == "real_post_level_delta"
        assert by_platform["youtube"]["latest_post_metrics"]["views_delta"] == 45
        assert by_platform["instagram"]["explanation"] == "first_seen_existing_posts_not_counted_as_growth"
        assert by_platform["instagram"]["baseline_protected"] is True
        assert by_platform["instagram"]["baseline_protected_fields"] == ["total_views"]
        assert by_platform["tiktok"]["explanation"] == "no_post_level_sample"
        assert report["totals"]["views_delta"] >= 45

        markdown = vkpi_channel_delta_dry_run.render_markdown({"accounts": accounts, "totals": report["totals"], **report})
        assert "Official Channel Post-Level Delta Dry Run" in markdown
        assert "real_post_level_delta" in markdown
    finally:
        for channel_id in channel_ids:
            conn.execute("DELETE FROM vkpi_channel_post_metrics WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM vkpi_channel_metrics WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM vkpi_employee_channels WHERE id=?", (channel_id,))
        conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        conn.commit()
