from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import app.main as main
from app.main_health import (
    begin_runtime_trust_probe,
    bounded_runtime_trust,
    build_runtime_trust,
    run_runtime_trust_stage,
)


def _healthy_worker() -> dict[str, object]:
    return {
        "worker_heartbeat": "2026-08-29T00:00:00Z",
        "worker_online": True,
        "worker_sha": "a" * 40,
        "worker_sha_source": "db_heartbeat",
        "worker_heartbeat_source": "db_heartbeat",
    }


def test_runtime_trust_records_completed_stage_timings() -> None:
    trust = build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=lambda: {"active": False, "valid": True},
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: "306_example.sql",
        worker_probe=_healthy_worker,
        redis_worker_probe=lambda: {"online": True, "expected_count": 1},
        scheduler_probe=lambda: "not_configured",
        worker_sha_fallback_probe=lambda: {},
        server_git_sha="a" * 40,
        postgres_runtime=True,
    )

    probe = trust["probe"]
    assert probe["status"] == "ok"
    assert set(probe["stages"]) == {
        "db_startup",
        "release_validation",
        "release_identity",
        "db_migration",
        "worker_heartbeat",
        "redis_worker",
        "scheduler",
    }
    assert all(stage["status"] == "completed" for stage in probe["stages"].values())
    assert all(stage["duration_ms"] >= 0 for stage in probe["stages"].values())


def test_runtime_trust_does_not_repeat_db_probes_after_migration_unavailable() -> None:
    unexpected_calls: list[str] = []

    def unexpected(name: str):
        def call():
            unexpected_calls.append(name)
            raise AssertionError(f"{name} must be skipped")

        return call

    trust = build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=lambda: {"active": False, "valid": True},
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: None,
        worker_probe=unexpected("worker"),
        redis_worker_probe=unexpected("redis_worker"),
        scheduler_probe=unexpected("scheduler"),
        worker_sha_fallback_probe=lambda: {},
        server_git_sha="a" * 40,
        postgres_runtime=True,
    )

    assert unexpected_calls == []
    assert trust["worker_online"] is None
    assert trust["db_migration_source"] == "unavailable"
    assert trust["probe"]["status"] == "degraded"
    assert trust["probe"]["stages"]["worker_heartbeat"]["status"] == "skipped"
    assert trust["probe"]["stages"]["redis_worker"]["status"] == "skipped"
    assert trust["probe"]["stages"]["scheduler"]["status"] == "skipped"


def test_bounded_runtime_trust_reuses_one_inflight_probe_for_thirty_callers() -> None:
    calls = 0

    def slow_probe() -> dict[str, object]:
        nonlocal calls
        calls += 1
        begin_runtime_trust_probe()
        run_runtime_trust_stage("db_migration", lambda: time.sleep(0.35), None)
        return {"probe": {"status": "degraded"}}

    async def run() -> tuple[list[dict[str, object]], float]:
        started = time.perf_counter()
        results = await asyncio.gather(
            *(
                bounded_runtime_trust(
                    slow_probe,
                    server_git_sha="a" * 40,
                    client_git_sha="a" * 40,
                    timeout_seconds=0.1,
                )
                for _ in range(30)
            )
        )
        elapsed = time.perf_counter() - started
        await asyncio.sleep(0.3)
        return results, elapsed

    results, elapsed = asyncio.run(run())
    assert calls == 1
    assert elapsed < 0.3
    assert all(result["probe"]["status"] == "timeout" for result in results)
    assert all(result["worker_online"] is None for result in results)
    assert all(result["sha_aligned"] is None for result in results)


def test_health_timeout_is_responsive_degraded_and_fail_closed() -> None:
    def slow_probe() -> dict[str, object]:
        begin_runtime_trust_probe()
        run_runtime_trust_stage("db_migration", lambda: time.sleep(0.25), None)
        return {"probe": {"status": "degraded"}}

    request = SimpleNamespace(query_params={}, headers={})
    started = time.perf_counter()
    with (
        patch.object(main, "_runtime_trust", side_effect=slow_probe),
        patch.object(main, "RUNTIME_TRUST_TIMEOUT_SECONDS", 0.1),
        patch.object(main, "IS_PRODUCTION", False),
    ):
        response = asyncio.run(main.health_check(request, deep=False))
    elapsed = time.perf_counter() - started
    time.sleep(0.2)

    assert elapsed < 0.3
    assert response["status"] == "degraded"
    assert response["trust"]["probe"]["status"] == "timeout"
    assert response["trust"]["db_migration_source"] == "probe_unavailable"
    assert response["trust"]["release_validation"]["valid"] is False
    assert response["trust"]["sha_aligned"] is None
