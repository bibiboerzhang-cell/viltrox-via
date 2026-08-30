from __future__ import annotations

import asyncio
import json

import pytest

from app.domains.market import ai_today, competitor_radar, signal_refresh
from app.services.scheduler import jobs_tasks_intel


@pytest.mark.parametrize(
    ("task_key", "job", "module", "function_name"),
    [
        ("vkpi_ai_today_hot", jobs_tasks_intel.job_vkpi_ai_today_hot, ai_today, "generate_ai_today_hot"),
        (
            "vkpi_competitor_radar",
            jobs_tasks_intel.job_vkpi_competitor_radar,
            competitor_radar,
            "generate_competitor_radar",
        ),
        (
            "vkpi_market_signal_refresh",
            jobs_tasks_intel.job_vkpi_market_signal_refresh,
            signal_refresh,
            "refresh_external_signals",
        ),
    ],
)
def test_intel_jobs_record_success(monkeypatch, task_key, job, module, function_name) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    monkeypatch.setattr(module, function_name, lambda: {"status": "ok"})

    asyncio.run(job())

    assert recorded == [(task_key, True, "")]


def test_intel_job_records_non_ok_result_without_advancing_success(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    monkeypatch.setattr(
        ai_today,
        "generate_ai_today_hot",
        lambda: {
            "status": "invalid",
            "result_status": "invalid",
            "reason": "invalid_result_contract",
            "generated_at": "2026-07-16T12:00:00Z",
            "provenance": {
                "provider": "none",
                "status": "all_providers_failed",
                "attempts": [
                    {
                        "provider": "google",
                        "model": "gemini-2.5-pro",
                        "status": "transient_error",
                    },
                    {
                        "provider": "anthropic",
                        "model": "claude-sonnet-5",
                        "status": "transient_error",
                    },
                ],
            },
        },
    )

    with pytest.raises(RuntimeError, match="non-ok result"):
        asyncio.run(jobs_tasks_intel.job_vkpi_ai_today_hot())

    assert len(recorded) == 1
    task_key, ok, raw_error = recorded[0]
    assert task_key == "vkpi_ai_today_hot"
    assert ok is False
    assert json.loads(raw_error) == {
        "kind": "ai_today_attempt_v1",
        "status": "invalid",
        "reason": "invalid_result_contract",
        "provider": "anthropic",
        "provider_status": "transient_error",
        "generation_status": "all_providers_failed",
        "model": "claude-sonnet-5",
        "providers_attempted": ["google", "anthropic"],
        "generated_at": "2026-07-16T12:00:00Z",
    }


def test_ai_today_exception_is_recorded_and_propagated(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )

    def fail() -> dict[str, str]:
        raise RuntimeError("discovery unavailable")

    monkeypatch.setattr(ai_today, "generate_ai_today_hot", fail)

    with pytest.raises(RuntimeError, match="discovery unavailable"):
        asyncio.run(jobs_tasks_intel.job_vkpi_ai_today_hot())

    assert recorded == [("vkpi_ai_today_hot", False, "discovery unavailable")]


def test_intel_job_records_exception_text(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )

    def fail() -> dict[str, str]:
        raise RuntimeError("signal refresh failed")

    monkeypatch.setattr(signal_refresh, "refresh_external_signals", fail)

    asyncio.run(jobs_tasks_intel.job_vkpi_market_signal_refresh())

    assert recorded == [("vkpi_market_signal_refresh", False, "signal refresh failed")]
