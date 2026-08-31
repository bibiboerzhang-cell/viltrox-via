from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import cron_daily_sync


ROOT = Path(__file__).resolve().parents[1]


def test_emit_event_binds_valid_systemd_invocation_id(monkeypatch) -> None:
    lines: list[str] = []
    invocation_id = "a" * 32
    monkeypatch.setenv("INVOCATION_ID", invocation_id)
    monkeypatch.setattr(cron_daily_sync, "out", lambda value, **_kwargs: lines.append(value))

    cron_daily_sync.emit_event("cron_daily_sync_started", dry_run=False)

    assert json.loads(lines[-1])["invocation_id"] == invocation_id
    monkeypatch.setenv("INVOCATION_ID", "not-systemd")
    cron_daily_sync.emit_event("cron_daily_sync_failed")
    assert json.loads(lines[-1])["invocation_id"] == ""


def test_default_completion_sla_fits_hard_capped_daily_capacity_and_primary_systemd_budget() -> None:
    requested = 18 + cron_daily_sync.DEFAULT_DAILY_KOL_LIMIT
    workers = cron_daily_sync.DEFAULT_DAILY_WORKER_COUNT
    child_timeout = cron_daily_sync.DEFAULT_DAILY_CHILD_TIMEOUT_SECONDS
    worst_case_child_seconds = ((requested + workers - 1) // workers) * child_timeout
    assert cron_daily_sync.DEFAULT_DAILY_KOL_LIMIT == 90
    assert worst_case_child_seconds == 16_200
    assert cron_daily_sync.DEFAULT_COMPLETION_WAIT_SECONDS == 17_100.0
    assert cron_daily_sync.DEFAULT_COMPLETION_WAIT_SECONDS >= worst_case_child_seconds + 900
    assert cron_daily_sync.DEFAULT_COMPLETION_WAIT_SECONDS + 4_500 <= 6 * 60 * 60
    unit = (ROOT / "scripts/ops/systemd/vkpi-sync-daily.service").read_text(encoding="utf-8")
    assert "TimeoutStartSec=6h" in unit
    assert "OnFailure=vkpi-sync-daily-alert@%n.service" in unit
    assert "RestartPreventExitStatus=75 76" in unit
    assert "--kol-limit 90" in unit
    assert "--worker-count 2" in unit
    assert "--child-timeout-seconds 300" in unit
    assert "\nRestart=" not in unit
    assert "the next timer is the retry boundary" in unit


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
        "batch_id": None,
        "task_ids": [],
        "completion_scope": None,
        "provider_completion": None,
        "completion_sla_expired": None,
        "tasks_total": 0,
        "tasks_terminal": None,
        "tasks_succeeded": None,
        "tasks_partial": None,
        "tasks_failed": None,
        "tasks_skipped_known": None,
        "tasks_pending": None,
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
    assert cron_daily_sync.post_sync_maintenance_decision(
        {
            "status": "completed",
            "provider_completion": "completed",
            "completion": {"completion_scope": "provider_terminal"},
        },
        requested_dry_run=False,
    ) == (True, "completed_sync")
    assert cron_daily_sync.post_sync_maintenance_decision(
        {
            "status": "completed",
            "provider_completion": "unknown",
            "completion": {"completion_scope": "bounded_observation", "sla_expired": True},
        },
        requested_dry_run=False,
    ) == (False, "provider_not_completed:unknown")


def test_queued_or_partial_receipt_never_passes_the_systemd_oneshot() -> None:
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
    ) == 75
    assert cron_daily_sync.result_exit_code(
        {
            "status": "partial",
            "provider_completion": "partial",
            "completion": {"sla_expired": True, "tasks_pending": 4},
        }
    ) == 2
    assert cron_daily_sync.result_exit_code(
        {
            "status": "completed",
            "provider_completion": "completed",
            "completion": {"sla_expired": False, "tasks_pending": 0},
        }
    ) == 0


def test_completed_result_summary_exposes_auditable_batch_and_terminal_counts() -> None:
    summary = cron_daily_sync.result_summary(
        {
            "status": "completed",
            "batch_id": "daily-1",
            "task_ids": ["official-1", "kol-1"],
            "completion_scope": "provider_terminal",
            "provider_completion": "completed",
            "completion": {
                "sla_expired": False,
                "tasks_terminal": 2,
                "tasks_succeeded": 2,
                "tasks_partial": 0,
                "tasks_failed": 0,
                "tasks_pending": 0,
            },
            "official": {
                "channels_requested": 1,
                "channels_enqueued": 1,
                "channels_failed_to_enqueue": 0,
                "failed": [],
            },
            "kol_pool_light": {"requested": 1, "enqueued": 1},
            "ran_at": "2026-08-31T04:03:00Z",
        }
    )

    assert summary["batch_id"] == "daily-1"
    assert summary["task_ids"] == ["official-1", "kol-1"]
    assert summary["completion_scope"] == "provider_terminal"
    assert summary["provider_completion"] == "completed"
    assert summary["official_failed"] == 0
    assert summary["tasks_total"] == 2
    assert summary["tasks_terminal"] == 2
    assert summary["tasks_pending"] == 0
    assert summary["completion_sla_expired"] is False


def test_no_work_is_a_success_but_all_enqueue_failures_are_not() -> None:
    no_work = {
        "status": "completed",
        "provider_completion": "not_run",
        "completion_scope": "no_work",
        "completion": {
            "complete": True,
            "provider_completion": "not_run",
            "completion_scope": "no_work",
            "tasks_total": 0,
            "tasks_pending": 0,
        },
        "enqueue_failures": 0,
    }
    all_enqueue_failed = {
        **no_work,
        "status": "partial",
        "enqueue_failures": 3,
        "official": {"channels_failed_to_enqueue": 3},
    }

    assert cron_daily_sync.result_exit_code(no_work) == 0
    assert cron_daily_sync.result_exit_code(
        {key: value for key, value in no_work.items() if key != "enqueue_failures"}
    ) == 2
    assert cron_daily_sync.result_exit_code(all_enqueue_failed) == 2
    assert cron_daily_sync.post_sync_maintenance_decision(
        no_work,
        requested_dry_run=False,
    ) == (False, "provider_not_completed:not_run")


def test_immediate_batch_receipt_is_emitted_before_terminal_observation(monkeypatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        cron_daily_sync,
        "emit_event",
        lambda event, **payload: events.append((event, payload)),
    )

    cron_daily_sync.emit_batch_queued_receipt(
        {"batch_id": "daily-1", "task_ids": ["task-1"], "parent_persisted": True}
    )

    assert events == [
        (
            "cron_daily_sync_enqueued",
            {"summary": {"batch_id": "daily-1", "task_ids": ["task-1"], "parent_persisted": True}},
        )
    ]


def test_candidate_cli_emits_nonempty_terminal_batch_receipt_before_maintenance(
    monkeypatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    maintenance: list[str] = []
    task_ids = [f"official-{index}" for index in range(18)]

    async def run_job(_name: str, _payload: dict[str, object]) -> dict[str, object]:
        return {
            "status": "completed",
            "batch_id": "daily-20260831",
            "task_ids": task_ids,
            "completion_scope": "provider_terminal",
            "provider_completion": "completed",
            "completion": {
                "complete": True,
                "completion_scope": "provider_terminal",
                "provider_completion": "completed",
                "tasks_total": 18,
                "tasks_terminal": 18,
                "tasks_succeeded": 18,
                "tasks_partial": 0,
                "tasks_failed": 0,
                "tasks_pending": 0,
                "sla_expired": False,
            },
            "official": {
                "channels_requested": 18,
                "channels_enqueued": 18,
                "channels_failed_to_enqueue": 0,
            },
            "kol_pool_light": {"skipped": True},
        }

    async def close_db() -> None:
        return None

    monkeypatch.setattr(cron_daily_sync, "run_job", run_job)
    monkeypatch.setattr(cron_daily_sync, "close_db_runtime", close_db)
    monkeypatch.setattr(
        cron_daily_sync,
        "emit_event",
        lambda event, **payload: events.append((event, payload)),
    )
    monkeypatch.setattr(
        cron_daily_sync,
        "run_post_sync_maintenance",
        lambda *, batch_id: maintenance.append(batch_id),
    )
    monkeypatch.setattr("sys.argv", ["cron_daily_sync.py"])

    assert asyncio.run(cron_daily_sync.main()) == 0
    finished = next(payload["summary"] for event, payload in events if event == "cron_daily_sync_finished")
    assert finished["batch_id"] == "daily-20260831"
    assert finished["task_ids"] == task_ids
    assert finished["completion_scope"] == "provider_terminal"
    assert finished["provider_completion"] == "completed"
    assert finished["tasks_terminal"] == 18
    assert finished["tasks_pending"] == 0
    assert finished["official_requested"] == 18
    assert finished["official_enqueued"] == 18
    assert maintenance == ["daily-20260831"]
