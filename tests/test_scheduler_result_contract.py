"""Hermetic scheduler completion, explicit-record and paid-override regressions."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.core import config
from app.db import connection
from app.domains.kol import url_deep_crawl_maintenance_fence as maintenance
from app.domains.ops import scheduler_registry
from app.services.scheduler import fleet_guard, jobs_registry_gated, jobs_tasks, jobs_tasks_intel
from app.services.scheduler_execution_policy import local_scheduler_force_enable
from app.services.scheduler_result_contract import normalize_scheduler_result, scheduler_dispatch_result


@pytest.fixture()
def receipts(monkeypatch):
    records, fires = [], []
    monkeypatch.setattr(scheduler_registry, "record_run", lambda key, **kw: records.append((key, kw)))
    monkeypatch.setattr(fleet_guard, "release_validation_active", lambda: False)
    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *a: fleet_guard.ScheduledFireClaim(True, 1, "task", "now", False))
    monkeypatch.setattr(fleet_guard, "finish_scheduled_fire", lambda claim, **kw: fires.append(kw))
    monkeypatch.setattr(fleet_guard, "scheduled_fire_heartbeat", lambda claim: nullcontext())
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", nullcontext)
    return records, fires


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("result,expected", [
    (None, "completed"), ("legacy-done", "completed"), ({"status": "ok"}, "completed"),
    ({"status": "empty"}, "completed"), ({"scanned": 1}, "completed"),
    (False, "failed"), ({"ok": False}, "failed"), ({"status": "failed"}, "failed"),
    ({"status": "error", "error": "bad_payload"}, "failed"),
    ({"status": "partial"}, "failed"), ({"status": "provider_unknown"}, "failed"),
    ({"status": "module_missing"}, "failed"), ({"status": "unrecognized"}, "failed"),
    ({"status": "queued"}, "blocked:awaiting_completion"),
    ({"status": "in_progress"}, "blocked:awaiting_completion"),
    ({"status": "already_queued"}, "blocked:awaiting_completion"),
    ({"status": "disabled"}, "blocked:disabled"),
    ({"status": "blocked", "reason": "readiness_closed"}, "blocked:readiness_closed"),
    ({"status": "budget_exhausted"}, "blocked:budget_exhausted"),
])
def test_both_wrappers_share_structured_outcome(result, expected, asynchronous, receipts):
    records, fires = receipts

    def sync_callback():
        return result

    async def async_callback():
        return result

    callback = async_callback if asynchronous else sync_callback
    wrapped = fleet_guard.guard_scheduled_callable(
        "task", jobs_tasks_intel.with_scheduler_run_record("task", callback), owner_id="test"
    )
    actual = asyncio.run(wrapped()) if asynchronous else wrapped()
    assert actual == result
    assert len(records) == 1
    assert records[0][1]["ok"] is (expected == "completed")
    assert len(fires) == 1 and fires[0]["status"] == expected
    if expected.startswith("blocked:"):
        assert records[0][1]["status"] == "blocked"


@pytest.mark.parametrize("explicit_ok,returned,expected", [
    (True, {"status": "failed", "error": "late_failure"}, "failed"),
    (True, {"status": "queued"}, "blocked:awaiting_completion"),
    (False, None, "failed"), (False, {"status": "ok"}, "failed"),
])
def test_explicit_record_is_deferred_and_cannot_override_failure(explicit_ok, returned, expected, receipts):
    records, fires = receipts

    async def callback():
        jobs_tasks._record_scheduler_run("task", ok=explicit_ok, error="" if explicit_ok else "early_failure")
        assert records == []  # no premature last_success_at write
        return returned

    wrapped = fleet_guard.guard_scheduled_callable(
        "task", jobs_tasks_intel.with_scheduler_run_record("task", callback), owner_id="test"
    )
    asyncio.run(wrapped())
    assert len(records) == 1 and records[0][1]["ok"] is False
    assert fires[0]["status"] == expected


def test_explicit_success_followed_by_exception_never_records_success(receipts):
    records, fires = receipts

    async def callback():
        jobs_tasks._record_scheduler_run("task", ok=True)
        raise RuntimeError("late_failure")

    wrapped = fleet_guard.guard_scheduled_callable(
        "task", jobs_tasks_intel.with_scheduler_run_record("task", callback), owner_id="test"
    )
    with pytest.raises(RuntimeError, match="late_failure"):
        asyncio.run(wrapped())
    assert len(records) == 1 and records[0][1]["ok"] is False
    assert fires[0]["status"] == "failed"


def test_explicit_pending_receipt_survives_legacy_none_return(receipts):
    records, fires = receipts

    async def callback():
        jobs_tasks._record_scheduler_run(
            "task", ok=False, status="blocked", error="status=queued; awaiting_downstream_completion"
        )

    wrapped = fleet_guard.guard_scheduled_callable(
        "task", jobs_tasks_intel.with_scheduler_run_record("task", callback), owner_id="test"
    )
    asyncio.run(wrapped())
    assert len(records) == 1 and records[0][1]["status"] == "blocked"
    assert fires[0]["status"] == "blocked:awaiting_completion"


@pytest.mark.parametrize("status", ["failed", "partial", "disabled", "queued"])
def test_gated_job_does_not_record_structured_failure_as_success(status, monkeypatch, receipts):
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: True)
    monkeypatch.setattr("importlib.import_module", lambda module: SimpleNamespace(run=lambda: {"status": status}))
    callback = jobs_registry_gated.gated_daily_job("task", "fake.local", "run")
    result = asyncio.run(callback())
    assert result == {"status": status}
    assert receipts[0][0][1]["ok"] is False


@pytest.mark.parametrize("status", ["failed", "blocked", "partial", "queued", "disabled"])
def test_registry_itself_never_advances_success_for_non_success_status(status, monkeypatch):
    statements = []
    fake = SimpleNamespace(execute=lambda sql, params: statements.append((sql, params)), commit=lambda: None)
    monkeypatch.setattr(scheduler_registry, "table_exists", lambda name: True)
    monkeypatch.setattr(scheduler_registry, "get_conn", lambda: fake)
    monkeypatch.setattr(scheduler_registry, "_has_last_status_column", lambda conn: True)
    scheduler_registry.record_run("task", ok=True, status=status)
    assert len(statements) == 1
    assert "last_success_at" not in statements[0][0]
    assert "last_run_at" in statements[0][0]


@pytest.mark.parametrize("environment,mode,production,allowed", [
    ("test", "0", False, True), ("local", "0", False, True),
    ("development", "0", False, True), ("production", "0", False, False),
    ("prod", "0", False, False), ("stage", "0", False, False),
    ("staging", "0", False, False), ("unknown", "0", False, False),
    ("test", "1", False, False), ("test", "0", True, False),
])
def test_force_enable_only_allowed_in_explicit_nonproduction(environment, mode, production, allowed, monkeypatch):
    monkeypatch.setenv("OPS_SCHEDULER_FORCE_ENABLE", "1")
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("V2_PRODUCTION_MODE", mode)
    monkeypatch.setattr(config, "IS_PRODUCTION", production)
    assert local_scheduler_force_enable() is allowed
    monkeypatch.setattr(connection, "table_exists", lambda table: False)
    assert jobs_tasks._scheduler_task_enabled("disabled_task") is allowed
    monkeypatch.setattr("app.core.release_validation.release_validation_active", lambda: False)
    fake = SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchone=lambda: {"enabled": False}))
    reason = maintenance._maintenance_refresh_execution_block_reason(
        {}, get_connection=lambda: fake, batch_block_reason=lambda payload: ""
    )
    assert reason == ("" if allowed else "maintenance_refresh_task_disabled")


@pytest.mark.parametrize("callback_name", ["job_bh_daily_snapshot", "job_via_daily_learning"])
def test_daily_provider_dispatch_returns_queued_and_propagates_failure(callback_name, monkeypatch):
    async def enqueue(*args, **kwargs):
        return "job-1"

    monkeypatch.setattr(jobs_tasks, "_enqueue_provider_job", enqueue)
    callback = getattr(jobs_tasks, callback_name)
    assert asyncio.run(callback()) == {"status": "queued", "job_id": "job-1"}

    async def fail(*args, **kwargs):
        raise RuntimeError("queue_unavailable")

    monkeypatch.setattr(jobs_tasks, "_enqueue_provider_job", fail)
    with pytest.raises(RuntimeError, match="queue_unavailable"):
        asyncio.run(callback())


def test_dispatch_adapter_separates_partial_queued_empty_and_does_not_mutate():
    source = {"status": "ok", "queued": 2, "already_queued": 0, "failed": 0}
    adapted = scheduler_dispatch_result(source)
    assert source["status"] == "ok"
    assert adapted["status"] == "queued" and adapted["enqueue_status"] == "ok"
    assert normalize_scheduler_result(adapted).ok is False
    assert scheduler_dispatch_result({**source, "failed": 1})["status"] == "partial"
    assert scheduler_dispatch_result({"status": "empty", "queued": 0})["status"] == "empty"


@pytest.mark.parametrize("name", ["job_provider_health_check", "job_vkpi_goaffpro_metrics_sync", "job_vkpi_fit_snapshot"])
def test_core_outer_callbacks_preserve_failure_receipt_and_raise(name, monkeypatch, receipts):
    from app.services.system import provider_health

    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: True)
    failure = {"ok": False, "status": "failed", "error": "provider_failed"}

    async def fake_result(*args, **kwargs):
        return failure

    monkeypatch.setattr(provider_health, "run_provider_health_check", fake_result)
    monkeypatch.setattr(asyncio, "to_thread", fake_result)
    callback = getattr(jobs_tasks, name)
    assert asyncio.run(callback()) is failure

    async def explode(*args, **kwargs):
        raise RuntimeError("provider_unavailable")

    monkeypatch.setattr(provider_health, "run_provider_health_check", explode)
    monkeypatch.setattr(asyncio, "to_thread", explode)
    wrapped = jobs_tasks_intel.with_scheduler_run_record("task", callback)
    with pytest.raises(RuntimeError, match="provider_unavailable"):
        asyncio.run(wrapped())
    assert len(receipts[0]) == 1 and receipts[0][0][1]["ok"] is False


def test_official_visual_callback_exposes_queue_and_queue_failure(monkeypatch):
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: True)

    async def enqueue(*args, **kwargs):
        return "visual-job"

    monkeypatch.setattr(jobs_tasks, "_enqueue_provider_job", enqueue)
    assert asyncio.run(jobs_tasks_intel.job_vkpi_official_visual_scan()) == {"status": "queued", "job_id": "visual-job"}

    async def explode(*args, **kwargs):
        raise RuntimeError("queue_unavailable")

    monkeypatch.setattr(jobs_tasks, "_enqueue_provider_job", explode)
    with pytest.raises(RuntimeError, match="queue_unavailable"):
        asyncio.run(jobs_tasks_intel.job_vkpi_official_visual_scan())


def test_brief_daily_callback_preserves_failed_quality_check(monkeypatch):
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: True)

    async def fake_result(*args, **kwargs):
        return {"passed": False, "items": 0}

    monkeypatch.setattr(asyncio, "to_thread", fake_result)
    result = asyncio.run(jobs_tasks_intel.job_vkpi_brief_agent())
    assert result["status"] == "failed"


def test_goaffpro_partial_failure_is_not_a_success(monkeypatch):
    async def fake_result(*args, **kwargs):
        return {"ok": True, "synced": 2, "errors": 1}

    monkeypatch.setattr(asyncio, "to_thread", fake_result)
    result = asyncio.run(jobs_tasks.job_vkpi_goaffpro_metrics_sync())
    assert result["status"] == "partial"
    assert normalize_scheduler_result(result).ok is False


@pytest.mark.parametrize("counts,expected", [
    ({"ok": 2, "failed": 1, "blocked": 0}, "partial"),
    ({"ok": 1, "failed": 0, "blocked": 1}, "partial"),
    ({"ok": 0, "failed": 0, "blocked": 2}, "blocked"),
    ({"ok": 2, "failed": 0, "blocked": 0}, "ok"),
    ({"ok": 0, "failed": 0, "blocked": 0, "skipped": 2}, "empty"),
])
def test_official_daily_batch_does_not_hide_partial_failures(counts, expected, monkeypatch, receipts):
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: True)

    async def fake_result(*args, **kwargs):
        return counts

    monkeypatch.setattr(asyncio, "to_thread", fake_result)
    result = asyncio.run(jobs_tasks_intel.job_vkpi_official_daily_report())
    assert result["status"] == expected
    assert receipts[0][0][1]["ok"] is (expected in {"ok", "empty"})
