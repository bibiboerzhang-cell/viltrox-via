from __future__ import annotations

from datetime import datetime, timezone

from scripts import cron_daily_sync


def test_compute_kol_stale_before_prefers_explicit_timestamp() -> None:
    assert (
        cron_daily_sync.compute_kol_stale_before(
            "2026-05-23T00:00:00Z",
            1,
            now=datetime(2026, 5, 24, tzinfo=timezone.utc),
        )
        == "2026-05-23T00:00:00Z"
    )


def test_compute_kol_stale_before_blank_means_catchup_mode() -> None:
    assert cron_daily_sync.compute_kol_stale_before("", 0, now=datetime(2026, 5, 24, tzinfo=timezone.utc)) == ""


def test_compute_kol_stale_before_from_days() -> None:
    assert (
        cron_daily_sync.compute_kol_stale_before(
            "",
            2,
            now=datetime(2026, 5, 24, 3, 4, 5, tzinfo=timezone.utc),
        )
        == "2026-05-22T03:04:05Z"
    )
