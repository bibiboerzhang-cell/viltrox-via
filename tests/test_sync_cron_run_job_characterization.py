"""Behavior and side-effect-order locks for sync.cron.run_job."""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.sync import cron, daily_batch, daily_batch_capacity  # noqa: E402


def _stable_runtime(monkeypatch: pytest.MonkeyPatch, events: list[Any]) -> None:
    async def inline(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        events.append(("to_thread", fn.__name__, args, kwargs))
        return fn(*args, **kwargs)

    monkeypatch.setattr(cron.asyncio, "to_thread", inline)
    monkeypatch.setattr(cron, "_stamp", lambda: events.append("stamp") or "STAMP")


def test_public_signature_aliases_and_unsupported_error_are_stable() -> None:
    assert str(inspect.signature(cron.run_job)) == (
        "(job_name: 'str', payload: 'dict[str, Any] | None' = None, *, "
        "queue: 'Any | None' = None) -> 'dict[str, Any]'"
    )
    assert cron.normalize_job_name(" weekly-report ") == "weekly_report"
    with pytest.raises(ValueError, match="unsupported V-KPI cron job"):
        asyncio.run(cron.run_job("not-a-job"))


def test_simple_job_dispatch_arguments_results_and_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains import alerts, lineage, reports
    from app.domains.staff import kpi_ledger

    events: list[Any] = []
    _stable_runtime(monkeypatch, events)

    def lineage_run(**kwargs: Any) -> dict[str, Any]:
        events.append(("lineage", kwargs))
        return {"run": 1}

    def rollup(ledger_date: Any) -> dict[str, Any]:
        events.append(("rollup", ledger_date))
        return {"date": ledger_date}

    def alert_run() -> dict[str, Any]:
        events.append("alerts")
        return {"count": 3}

    def weekly(**kwargs: Any) -> dict[str, Any]:
        events.append(("weekly", kwargs))
        return {"summary": "ok", "context": {"must": "not leak"}}

    monkeypatch.setattr(lineage, "generate_run", lineage_run)
    monkeypatch.setattr(kpi_ledger, "generate_daily_rollup", rollup)
    monkeypatch.setattr(alerts, "generate_alerts", alert_run)
    monkeypatch.setattr(reports, "generate_weekly_report", weekly)

    lineage_result = asyncio.run(
        cron.run_job("lineage", {"period_days": "9", "scope_type": "tenant"})
    )
    rollup_result = asyncio.run(cron.run_job("kpi", {"ledger_date": "2026-08-29"}))
    alert_result = asyncio.run(cron.run_job("alert"))
    weekly_payload = {"period_days": "5", "staff": {"id": 7}, "tenant_id": 33}
    weekly_result = asyncio.run(cron.run_job("report", weekly_payload))

    assert lineage_result == {
        "job": "lineage_snapshot",
        "status": "ok",
        "result": {"run": 1},
        "ran_at": "STAMP",
    }
    assert rollup_result["result"] == {"date": "2026-08-29"}
    assert alert_result["result"] == {"count": 3}
    assert weekly_result == {
        "job": "weekly_report",
        "status": "ok",
        "result": {"summary": "ok"},
        "ran_at": "STAMP",
    }
    assert events == [
        (
            "to_thread",
            "lineage_run",
            (),
            {
                "period_days": 9,
                "scope_type": "tenant",
                "trigger_source": "scheduler_lineage_snapshot",
                "metadata": {"source": "cron.run_now"},
            },
        ),
        (
            "lineage",
            {
                "period_days": 9,
                "scope_type": "tenant",
                "trigger_source": "scheduler_lineage_snapshot",
                "metadata": {"source": "cron.run_now"},
            },
        ),
        "stamp",
        ("to_thread", "rollup", ("2026-08-29",), {}),
        ("rollup", "2026-08-29"),
        "stamp",
        ("to_thread", "alert_run", (), {}),
        "alerts",
        "stamp",
        (
            "to_thread",
            "weekly",
            (),
            {"period_days": 5, "staff": {"id": 7}, "filters": weekly_payload},
        ),
        (
            "weekly",
            {"period_days": 5, "staff": {"id": 7}, "filters": weekly_payload},
        ),
        "stamp",
    ]


def test_analytics_monitor_keeps_filter_fallback_locks_and_staff_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains import analytics

    events: list[Any] = []
    _stable_runtime(monkeypatch, events)
    products = [
        {"product_sku": "A", "enabled": "1", "monitor_platforms_json": '["youtube","tiktok"]'},
        {"product_sku": "B", "enabled": "false", "monitor_platforms_json": '["youtube"]'},
        {"product_sku": "C", "enabled": "1", "monitor_platforms_json": "bad-json"},
    ]
    monkeypatch.setattr(
        analytics,
        "list_monitored_products",
        lambda **kwargs: events.append(("products", kwargs)) or {"products": products},
    )

    async def queue_jobs(jobs: list[dict[str, Any]], *, queue: Any) -> dict[str, Any]:
        events.append(("queue_provider", jobs, queue))
        return {
            "requested": len(jobs),
            "enqueued": len(jobs),
            "failed_to_enqueue": 0,
            "task_ids": ["t1", "t2", "t3"],
            "failed": [],
        }

    monkeypatch.setattr(cron, "_queue_provider_jobs", queue_jobs)
    queue = object()
    result = asyncio.run(
        cron.run_job(
            "product_monitor",
            {"max_videos": "14", "staff": {"id": 9, "tenant_id": 4}},
            queue=queue,
        )
    )

    jobs = events[1][1]
    assert [(job["payload"]["body"]["product_sku"], job["payload"]["body"]["platform"]) for job in jobs] == [
        ("A", "youtube"),
        ("A", "tiktok"),
        ("C", "youtube"),
    ]
    assert all(job["payload"]["staff"] == {"id": 9, "tenant_id": 4} for job in jobs)
    assert [job["lock_key"] for job in jobs] == [
        "vkpi_analytics_monitor:A:youtube",
        "vkpi_analytics_monitor:A:tiktok",
        "vkpi_analytics_monitor:C:youtube",
    ]
    assert all(job["timeout_seconds"] == 1200 for job in jobs)
    assert result == {
        "job": "analytics_monitor",
        "status": "queued",
        "runs": 3,
        "requested": 3,
        "enqueued": 3,
        "failed_to_enqueue": 0,
        "task_ids": ["t1", "t2", "t3"],
        "failed": [],
        "ran_at": "STAMP",
    }


def test_channel_and_baseline_dispatch_keep_limits_and_filter_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains import channels

    events: list[Any] = []
    _stable_runtime(monkeypatch, events)
    rows = [
        {"id": 1, "platform": "YouTube"},
        {"id": 2, "platform": "instagram"},
        {"id": 3, "platform": "x"},
    ]
    monkeypatch.setattr(
        channels,
        "list_channels",
        lambda **kwargs: events.append(("channels", kwargs)) or {"channels": rows},
    )

    async def queue_channels(
        queued_rows: list[dict[str, Any]],
        *,
        payload: dict[str, Any],
        staff: dict[str, Any] | None,
        queue: Any,
    ) -> dict[str, Any]:
        events.append(("queue_channels", queued_rows, payload, staff, queue))
        return {
            "channels_enqueued": len(queued_rows),
            "channels_requested": len(queued_rows),
            "channels_failed_to_enqueue": 0,
            "task_ids": [f"c{row['id']}" for row in queued_rows],
            "failed": [],
        }

    monkeypatch.setattr(cron, "_queue_channel_syncs", queue_channels)
    queue = object()
    channel_result = asyncio.run(
        cron.run_job("channel-sync", {"max_posts": 8, "staff": {"id": 7}}, queue=queue)
    )
    baseline_result = asyncio.run(
        cron.run_job(
            "full-baseline",
            {"platforms": "youtube, instagram", "staff": {"id": 7}},
            queue=queue,
        )
    )

    assert channel_result["channels_enqueued"] == 3
    first_queue = events[1]
    assert first_queue[2]["max_posts"] == 8
    baseline_queue = events[4]
    assert [(row["id"], row["_requested_max_posts"]) for row in baseline_queue[1]] == [
        (1, 1000),
        (2, 1000),
    ]
    assert baseline_result["platforms"] == ["instagram", "youtube"]
    assert baseline_result["limits"] == {"instagram": 1000, "youtube": 1000}


@pytest.mark.parametrize("selector", ["qualified", "legacy"])
def test_daily_incremental_guard_and_queue_side_effect_order(
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
) -> None:
    from app.domains import channels
    from app.domains.sync import daily_sync, refresh_tier

    events: list[Any] = []
    parent_checkpoints: list[dict[str, Any]] = []
    _stable_runtime(monkeypatch, events)

    runtime_proof = {
        "proof_available": True,
        "proof_source": "test-ledger+heartbeat",
        "proof_at": "2026-08-31T12:00:00+00:00",
        "requested_worker_count": 2,
        "fresh_worker_processes": 1,
        "fresh_consumer_count": 2,
        "effective_worker_count": 2,
        "minimum_ready_sequence": 2,
        "release_sha": "a" * 40,
        "waiting_tasks": 0,
        "processing_tasks": 0,
        "active_backlog_tasks": 0,
        "backlog_policy": "reject_nonempty",
        "stream_key": "vkpi:jobs",
        "group": "vkpi-workers",
    }

    async def prove_capacity(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(runtime_proof)

    monkeypatch.setattr(
        daily_batch_capacity, "runtime_capacity_proof", prove_capacity
    )
    monkeypatch.setattr(daily_sync, "check_daily_sync_guard", lambda payload: events.append("guard"))
    monkeypatch.setattr(daily_sync, "_bool", lambda value: bool(value))
    monkeypatch.setattr(daily_sync, "_kol_refresh_selector", lambda payload: events.append("selector") or selector)
    monkeypatch.setattr(daily_sync, "_platform_filter", lambda value: events.append(("platforms", value)) or ["youtube"])
    monkeypatch.setattr(daily_sync, "_tier_filter", lambda value: events.append(("tiers", value)) or ["A"])
    monkeypatch.setattr(
        channels,
        "list_channels",
        lambda **kwargs: events.append("channels") or {"channels": [{"id": 1}]},
    )
    monkeypatch.setattr(
        refresh_tier,
        "qualified_refresh_rows",
        lambda **kwargs: events.append(("qualified_rows", kwargs)) or [{"id": 31}],
    )
    monkeypatch.setattr(
        daily_sync,
        "_kol_light_rows",
        lambda **kwargs: events.append(("legacy_rows", kwargs)) or [{"id": 41}],
    )

    async def queue_daily(
        official_rows: list[dict[str, Any]],
        kol_rows: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        events.append(("daily_queue", official_rows, kol_rows, kwargs))
        return {
            "official": {
                "channels_enqueued": 1,
                "channels_requested": 1,
                "channels_failed_to_enqueue": 0,
                "task_ids": ["official-1"],
                "failed": [],
            },
            "kol_pool_light": {
                "requested": 1,
                "enqueued": 1,
                "failed_to_enqueue": 0,
                "task_ids": ["kol-31"],
                "failed": [],
            },
            "task_ids": ["official-1", "kol-31"],
            "task_links": [
                {"task_id": "official-1", "lane": "official", "channel_id": 1},
                {"task_id": "kol-31", "lane": "kol_pool_light", "kol_pool_id": 31},
            ],
            "scheduler": "round_robin_v1",
        }

    monkeypatch.setattr(daily_batch, "queue_batch", queue_daily)
    monkeypatch.setattr(daily_batch, "new_batch_id", lambda: "batch-1")
    monkeypatch.setattr(daily_batch, "insert_parent", lambda *_args, **_kwargs: events.append("parent_insert"))
    monkeypatch.setattr(
        daily_batch,
        "checkpoint_parent",
        lambda _batch_id, summary: (
            events.append("parent_checkpoint"), parent_checkpoints.append(summary)
        ),
    )
    monkeypatch.setattr(daily_batch, "finish_parent", lambda *_args, **_kwargs: events.append("parent_finish"))

    async def reconcile(queue: Any) -> dict[str, int]:
        events.append(("reconcile", queue))
        return {"checked": 0, "reconciled": 0, "pending": 0, "failed": 0}

    monkeypatch.setattr(daily_batch, "reconcile_recent_parents", reconcile)
    real_observer = daily_batch.observe

    async def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("observe")
        return await real_observer(*args, **kwargs)

    monkeypatch.setattr(daily_batch, "observe", observe)
    payload = {
        "allow_qualified_kol_refresh": selector == "qualified",
        "allow_legacy_kol_full_refresh": selector == "legacy",
        "kol_limit": 25,
        "kol_offset": 2,
        "kol_platforms": "youtube",
        "kol_tiers": "A",
        "staff": {"id": 7},
        "_batch_receipt_callback": lambda receipt: events.append(("batch_receipt", receipt)),
    }

    class Queue:
        backend_name = "redis-stream"

        async def get_status(self, _task_id: str) -> dict[str, str]:
            return {"status": "queued"}

    queue = Queue()
    result = asyncio.run(cron.run_job("vkpi-daily-incremental", payload, queue=queue))

    row_event = "qualified_rows" if selector == "qualified" else "legacy_rows"
    labels = [item[0] if isinstance(item, tuple) else item for item in events]
    assert labels == [
        "guard",
        "reconcile",
        "guard",
        "channels",
        "selector",
        "platforms",
        *(["tiers"] if selector == "qualified" else []),
        row_event,
        "parent_insert",
        "daily_queue",
        "parent_checkpoint",
        "batch_receipt",
        "observe",
        "stamp",
        "parent_finish",
    ]
    receipt = next(item[1] for item in events if isinstance(item, tuple) and item[0] == "batch_receipt")
    assert receipt["batch_id"] == "batch-1"
    assert receipt["task_ids"] == ["official-1", "kol-31"]
    assert receipt["parent_persisted"] is True
    assert receipt["phase"] == "children_enqueued"
    assert receipt["enqueue_failures"] == 0
    assert receipt["official"]["channels_enqueued"] == 1
    assert receipt["kol_pool_light"]["enqueued"] == 1
    admission = result.pop("admission")
    assert admission["admitted"] is True
    assert admission["admission_reason"] == "within_verified_capacity"
    assert admission["worker_count"] == 2
    assert admission["runtime_proof"] == runtime_proof
    assert parent_checkpoints[-1]["admission"] == admission
    assert result == {
        "job": "daily_incremental_sync",
        "status": "queued",
        "batch_id": "batch-1",
        "task_ids": ["official-1", "kol-31"],
        "batch": {
            "batch_id": "batch-1",
            "parent_persisted": True,
            "identity_scope": "durable_parent_and_task_links",
            "scheduler": "round_robin_v1",
            "requested": 2,
            "enqueued": 2,
            "task_ids": ["official-1", "kol-31"],
            "task_links": [
                {"task_id": "official-1", "lane": "official", "channel_id": 1},
                {"task_id": "kol-31", "lane": "kol_pool_light", "kol_pool_id": 31},
            ],
        },
        "completion_scope": "enqueue_only",
        "provider_completion": "unknown",
        "completion": {
            "complete": False,
            "completion_scope": "enqueue_only",
            "provider_completion": "unknown",
            "wait_seconds": 0.0,
            "poll_seconds": 10.0,
            "sla_expired": False,
            "tasks_total": 2,
            "tasks_terminal": 0,
            "tasks_succeeded": 0,
            "tasks_partial": 0,
            "tasks_failed": 0,
            "tasks_skipped_known": 0,
            "tasks_pending": 2,
            "status_counts": {"unobserved": 2},
            "task_statuses": {"official-1": "unobserved", "kol-31": "unobserved"},
            "lookup_errors": {},
        },
        "official": {
            "channels_enqueued": 1,
            "channels_requested": 1,
            "channels_failed_to_enqueue": 0,
            "task_ids": ["official-1"],
            "failed": [],
        },
        "kol_pool_light": {
            "requested": 1,
            "enqueued": 1,
            "failed_to_enqueue": 0,
            "task_ids": ["kol-31"],
            "failed": [],
        },
        "enqueue_failures": 0,
        "ran_at": "STAMP",
    }


def test_daily_batch_round_robin_fairness_and_full_receipt_for_18_plus_91(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    calls: list[tuple[str, dict[str, Any], dict[str, Any] | None, int | None]] = []

    async def enqueue_task(
        queue: Any,
        task_type: str,
        params: dict[str, Any],
        *,
        staff: dict[str, Any] | None,
        priority: int | None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert queue is durable_queue
        calls.append((task_type, dict(params), staff, priority))
        target_id = params.get("channel_id") or params.get("kol_pool_id")
        return {"task_id": f"task-{task_type}-{target_id}", "status": "queued"}

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", enqueue_task)

    class Queue:
        backend_name = "redis-stream"

    durable_queue = Queue()
    official_rows = [{"id": index} for index in range(1, 19)]
    kol_rows = [{"id": index} for index in range(1001, 1092)]
    result = asyncio.run(
        daily_batch.queue_batch(
            official_rows,
            kol_rows,
            payload={"official_max_posts": 50, "kol_max_posts": 2},
            staff={"id": 7},
            queue=durable_queue,
            batch_id="batch-fair",
        )
    )

    lanes = [params["orchestration_lane"] for _task_type, params, _staff, _priority in calls]
    assert lanes[:36] == ["official", "kol_pool_light"] * 18
    assert lanes[36:] == ["kol_pool_light"] * 73
    assert all(params["orchestration_batch_id"] == "batch-fair" for _, params, _, _ in calls)
    assert all(priority == 5 for _, _, _, priority in calls)
    assert len(result["task_ids"]) == 109
    assert result["official"]["channels_enqueued"] == 18
    assert result["kol_pool_light"]["enqueued"] == 91
    assert result["scheduler"] == "round_robin_v1"


def test_daily_batch_completion_poll_is_injectable_and_requires_all_terminal() -> None:
    calls: dict[str, int] = {"official-1": 0, "kol-1": 0}
    sleeps: list[float] = []

    class Queue:
        async def get_status(self, task_id: str) -> dict[str, str]:
            calls[task_id] += 1
            return {"status": "queued" if calls[task_id] == 1 else "done"}

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    completion = asyncio.run(
        daily_batch.observe(
            Queue(),
            ["official-1", "kol-1"],
            wait_seconds=30,
            poll_seconds=2,
            sleep=fake_sleep,
            monotonic=lambda: 0.0,
        )
    )

    assert sleeps == [2]
    assert completion["complete"] is True
    assert completion["completion_scope"] == "provider_terminal"
    assert completion["provider_completion"] == "completed"
    assert completion["tasks_succeeded"] == 2
    assert daily_batch.result_status(completion, enqueue_failures=0) == "completed"


def test_daily_batch_completion_sla_keeps_success_plus_pending_queued_without_real_wait() -> None:
    clock = [0.0]

    class Queue:
        async def get_status(self, task_id: str) -> dict[str, str]:
            return {"status": "done" if task_id == "official-1" else "queued"}

    async def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    completion = asyncio.run(
        daily_batch.observe(
            Queue(),
            ["official-1", "kol-1"],
            wait_seconds=2,
            poll_seconds=1,
            sleep=fake_sleep,
            monotonic=lambda: clock[0],
        )
    )

    assert clock[0] == 2
    assert completion["complete"] is False
    assert completion["completion_scope"] == "bounded_observation"
    assert completion["provider_completion"] == "unknown"
    assert completion["sla_expired"] is True
    assert completion["tasks_terminal"] == 1
    assert completion["tasks_pending"] == 1
    assert daily_batch.result_status(completion, enqueue_failures=0) == "queued"


def test_daily_batch_malformed_target_is_an_enqueue_failure_not_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.domains.tasks.enqueue as task_enqueue

    async def must_not_enqueue(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("invalid target reached enqueue")

    monkeypatch.setattr(task_enqueue, "enqueue_vkpi_task", must_not_enqueue)

    class Queue:
        backend_name = "redis-stream"

    queued = asyncio.run(
        daily_batch.queue_batch(
            [{"id": 0}],
            [],
            payload={},
            staff=None,
            queue=Queue(),
            batch_id="batch-invalid",
        )
    )
    completion = asyncio.run(
        daily_batch.observe(Queue(), queued["task_ids"], wait_seconds=0, poll_seconds=1)
    )

    assert queued["official"]["channels_requested"] == 1
    assert queued["official"]["channels_enqueued"] == 0
    assert queued["official"]["channels_failed_to_enqueue"] == 1
    assert queued["official"]["failed"] == [
        {"channel_id": 0, "error": "ValueError: positive target id required"}
    ]
    assert daily_batch.result_status(completion, enqueue_failures=1) == "partial"


def test_daily_incremental_dry_run_plans_without_queue_or_guard_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.sync import daily_sync

    events: list[Any] = []
    _stable_runtime(monkeypatch, events)

    def plan(payload: dict[str, Any]) -> dict[str, Any]:
        events.append(("plan", dict(payload)))
        return {
            "job": "daily_incremental_sync",
            "status": "ok",
            "dry_run": True,
            "run_id": "dry-run-1",
            "official": {"dry_run": True, "requested": 18},
            "kol_pool_light": {"dry_run": True, "requested": 7},
            "health": {"blocked_next_run": False},
        }

    monkeypatch.setattr(daily_sync, "_bool", lambda value: bool(value))
    monkeypatch.setattr(daily_sync, "run_daily_incremental", plan)
    monkeypatch.setattr(
        daily_sync,
        "check_daily_sync_guard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must use the read-only planning path")
        ),
    )
    monkeypatch.setattr(
        cron,
        "_queue_channel_syncs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run enqueued official crawl work")
        ),
    )
    monkeypatch.setattr(
        daily_batch,
        "queue_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run enqueued daily batch work")
        ),
    )

    result = asyncio.run(
        cron.run_job(
            "daily_incremental_sync",
            {
                "dry_run": True,
                "allow_qualified_kol_refresh": True,
                "kol_refresh_selector": "qualified",
            },
            queue="MUST_NOT_BE_USED",
        )
    )

    assert result == {
        "job": "daily_incremental_sync",
        "status": "planned",
        "dry_run": True,
        "run_id": "dry-run-1",
        "official": {"dry_run": True, "requested": 18},
        "kol_pool_light": {"dry_run": True, "requested": 7},
        "health": {"blocked_next_run": False},
        "ran_at": "STAMP",
    }
    assert events == [
        (
            "plan",
            {
                "dry_run": True,
                "allow_qualified_kol_refresh": True,
                "kol_refresh_selector": "qualified",
            },
        ),
        "stamp",
    ]


def test_digest_and_morning_sync_preserve_capabilities_locks_and_phase_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains import analytics, channels
    from app.domains import industry as industry_domain
    from app.domains.industry import access as industry_access

    events: list[Any] = []
    _stable_runtime(monkeypatch, events)
    monkeypatch.setattr(channels, "list_channels", lambda **kwargs: events.append("channels") or {"channels": [{"id": 1}]})
    monkeypatch.setattr(
        industry_domain,
        "list_accounts",
        lambda **kwargs: events.append(("industry_rows", kwargs))
        or {"accounts": [{"id": 11, "project_id": 21, "crawl_enabled": True}, {"id": 0, "project_id": 2, "crawl_enabled": True}]},
    )
    monkeypatch.setattr(
        industry_access,
        "issue_server_refresh_capability",
        lambda **kwargs: events.append(("capability", kwargs)) or "CAP",
    )
    monkeypatch.setattr(
        industry_access,
        "build_refresh_payload",
        lambda account_id, **kwargs: events.append(("refresh_payload", account_id, kwargs)) or {"account_id": account_id, **kwargs},
    )
    monkeypatch.setattr(
        analytics,
        "list_monitored_products",
        lambda **kwargs: events.append(("products", kwargs))
        or {"products": [{"product_sku": "SKU", "enabled": "1", "monitor_platforms_json": '["youtube"]'}]},
    )
    monkeypatch.setattr(
        analytics,
        "generate_daily_staff_outreach_digest",
        lambda **kwargs: events.append(("digest", kwargs)) or {"generated": 4},
    )

    async def queue_channels(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append(("channel_queue", args, kwargs))
        return {"channels_enqueued": 1, "channels_failed_to_enqueue": 0, "task_ids": ["c1"]}

    provider_calls = 0

    async def queue_provider(jobs: list[dict[str, Any]], *, queue: Any) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        events.append((f"provider_queue_{provider_calls}", jobs, queue))
        return {"enqueued": len(jobs), "failed_to_enqueue": 0, "task_ids": [f"p{provider_calls}"]}

    monkeypatch.setattr(cron, "_queue_channel_syncs", queue_channels)
    monkeypatch.setattr(cron, "_queue_provider_jobs", queue_provider)
    digest_result = asyncio.run(cron.run_job("outreach_digest_only", {"limit": 8, "staff": {"id": 7}}))
    morning_result = asyncio.run(
        cron.run_job(
            "daily-morning-sync",
            {"limit": 9, "max_videos": 10, "period_days": 2, "staff": {"id": 7}},
            queue="QUEUE",
        )
    )

    assert digest_result == {
        "job": "daily_outreach_digest_only",
        "status": "ok",
        "digest": {"generated": 4},
        "ran_at": "STAMP",
    }
    assert [item[0] if isinstance(item, tuple) else item for item in events].index("channel_queue") < [item[0] if isinstance(item, tuple) else item for item in events].index("provider_queue_1")
    assert [item[0] if isinstance(item, tuple) else item for item in events].index("provider_queue_1") < [item[0] if isinstance(item, tuple) else item for item in events].index("provider_queue_2")
    industry_job = next(item for item in events if isinstance(item, tuple) and item[0] == "provider_queue_1")[1][0]
    assert industry_job == {
        "job_type": "industry_account_refresh",
        "payload": {"account_id": 11, "server_capability": "CAP"},
        "lock_key": "industry_account_refresh:11",
        "timeout_seconds": 1200,
    }
    assert morning_result["channels_enqueued"] == 1
    assert morning_result["industry_accounts_enqueued"] == 1
    assert morning_result["monitor_runs"] == 1
    assert morning_result["digest"] == {"generated": 4}


def test_manual_audit_order_and_failure_reraise_are_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    monkeypatch.setattr(cron, "_stamp", lambda: "STAMP")
    monkeypatch.setattr(
        cron,
        "_log_cron_audit",
        lambda **kwargs: events.append(("audit", kwargs["action_type"])),
    )

    async def success(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append(("run", args, kwargs))
        return {"job": "alerts", "status": "ok"}

    monkeypatch.setattr(cron, "run_job", success)
    result = asyncio.run(
        cron.run_manual_job(
            "alerts",
            {"confirm": "RUN alerts"},
            staff={"id": 7},
            queue="QUEUE",
        )
    )
    assert result == {"job": "alerts", "status": "ok"}
    assert [event[:2] for event in events] == [
        ("audit", "cron_run_requested"),
        ("run", ("alerts", {"confirm": "RUN alerts", "staff": {"id": 7}})),
        ("audit", "cron_run_completed"),
    ]

    events.clear()

    async def failure(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        events.append("run_failed")
        raise RuntimeError("locked")

    monkeypatch.setattr(cron, "run_job", failure)
    with pytest.raises(RuntimeError, match="locked"):
        asyncio.run(
            cron.run_manual_job(
                "alerts",
                {"confirm": "RUN alerts"},
                staff={"id": 7},
                queue="QUEUE",
            )
        )
    assert events == [
        ("audit", "cron_run_requested"),
        "run_failed",
        ("audit", "cron_run_failed"),
    ]


def test_provider_queue_partial_failure_is_reported_without_hidden_retry() -> None:
    class Queue:
        backend_name = "redis"

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any], str | None, int]] = []

        async def enqueue(
            self,
            job_type: str,
            payload: dict[str, Any],
            *,
            lock_key: str | None,
            timeout_seconds: int,
        ) -> str:
            self.calls.append((job_type, payload, lock_key, timeout_seconds))
            if job_type == "bad":
                raise RuntimeError("budget locked")
            return f"task-{job_type}"

    queue = Queue()
    jobs = [
        {"job_type": "one", "payload": {"tenant_id": 7}, "lock_key": "lock:one", "timeout_seconds": 30},
        {"job_type": "bad", "payload": {"account_id": 9}, "lock_key": "lock:bad", "timeout_seconds": 40},
        {"job_type": "two", "payload": {}, "lock_key": "lock:two", "timeout_seconds": 50},
    ]

    result = asyncio.run(cron._queue_provider_jobs(jobs, queue=queue))

    assert [call[0] for call in queue.calls] == ["one", "bad", "two"]
    assert len(queue.calls) == 3
    assert result == {
        "requested": 3,
        "enqueued": 2,
        "failed_to_enqueue": 1,
        "task_ids": ["task-one", "task-two"],
        "failed": [
            {
                "job_type": "bad",
                "target_id": 9,
                "error": "RuntimeError: budget locked",
            }
        ],
    }
