from __future__ import annotations

import pytest

from app.services.scheduler import jobs


def test_scheduler_task_allowlist_is_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VKPI_SCHEDULER_TASK_ALLOWLIST", raising=False)

    assert jobs.scheduler_task_allowlist() is None


def test_scheduler_task_allowlist_normalizes_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "VKPI_SCHEDULER_TASK_ALLOWLIST",
        " runtime_metrics_snapshot, scheduler_fire_stale_recovery, runtime_metrics_snapshot ",
    )

    assert jobs.scheduler_task_allowlist() == frozenset(
        {"runtime_metrics_snapshot", "scheduler_fire_stale_recovery"}
    )


@pytest.mark.parametrize("value", ["", " , ", "runtime_metrics_snapshot,*", "bad/task"])
def test_scheduler_task_allowlist_rejects_empty_or_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("VKPI_SCHEDULER_TASK_ALLOWLIST", value)

    with pytest.raises(RuntimeError, match="VKPI_SCHEDULER_TASK_ALLOWLIST"):
        jobs.scheduler_task_allowlist()


def test_scheduler_filters_unlisted_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_SCHEDULER_TASK_ALLOWLIST", "runtime_metrics_snapshot")
    scheduler = jobs.FleetSafeAsyncIOScheduler()

    assert scheduler.add_job(lambda: None, id="cache_cleanup") is None
    registered = scheduler.add_job(lambda: None, id="runtime_metrics_snapshot")

    assert registered is not None
    assert registered.id == "runtime_metrics_snapshot"


def test_scheduler_allowlist_rejects_unknown_registered_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_SCHEDULER_TASK_ALLOWLIST", "missing_task")
    scheduler = jobs.FleetSafeAsyncIOScheduler()

    with pytest.raises(RuntimeError, match="unavailable tasks: missing_task"):
        jobs.enforce_scheduler_task_allowlist(scheduler)
