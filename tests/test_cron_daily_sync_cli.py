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
        == "2026-05-22T05:04:05Z"  # N×24h - 2h 宽限(吃掉上一轮运行时长漂移,修 hot 层隔日空转)
    )


def test_queued_result_summary_reports_enqueue_truth() -> None:
    summary = cron_daily_sync.result_summary(
        {
            "job": "daily_incremental_sync",
            "status": "queued",
            "official": {
                "channels_requested": 18,
                "channels_enqueued": 17,
                "channels_failed_to_enqueue": 1,
            },
            "kol_pool_light": {
                "requested": 9,
                "enqueued": 8,
                "failed_to_enqueue": 1,
            },
            "ran_at": "2026-08-30T04:00:00Z",
        }
    )

    assert summary == {
        "status": "queued",
        "dry_run": False,
        "official_requested": 18,
        "official_enqueued": 17,
        "official_synced": None,
        "official_failed": 1,
        "kol_requested": 9,
        "kol_enqueued": 8,
        "kol_refreshed": None,
        "kol_partial": None,
        "kol_errors": 1,
        "started_at": None,
        "finished_at": "2026-08-30T04:00:00Z",
    }


def test_post_sync_maintenance_never_runs_for_dry_run_or_enqueue_receipt() -> None:
    assert cron_daily_sync.post_sync_maintenance_decision(
        {"status": "planned", "dry_run": True},
        requested_dry_run=True,
    ) == (False, "dry_run")
    assert cron_daily_sync.post_sync_maintenance_decision(
        {"status": "queued"},
        requested_dry_run=False,
    ) == (False, "job_not_completed:queued")
    assert cron_daily_sync.post_sync_maintenance_decision(
        {"status": "ok", "dry_run": False},
        requested_dry_run=False,
    ) == (True, "completed_sync")


def test_enqueue_failures_fail_the_systemd_oneshot() -> None:
    assert cron_daily_sync.result_exit_code(
        {
            "status": "queued",
            "official": {"channels_failed_to_enqueue": 1},
            "kol_pool_light": {"failed_to_enqueue": 0},
        }
    ) == 2
    assert cron_daily_sync.result_exit_code(
        {
            "status": "queued",
            "official": {"channels_failed_to_enqueue": 0},
            "kol_pool_light": {"failed_to_enqueue": 0},
        }
    ) == 0
