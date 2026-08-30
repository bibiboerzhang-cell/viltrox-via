from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.main_health import build_runtime_trust


ROOT = Path(__file__).resolve().parents[1]


def test_production_runtime_wires_web_scheduler_and_both_workers() -> None:
    main = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    main_health = (ROOT / "backend/app/main_health.py").read_text(encoding="utf-8")
    main_fence = (ROOT / "backend/app/main_release_validation.py").read_text(
        encoding="utf-8"
    )
    scheduler = (
        ROOT / "backend/app/services/scheduler/fleet_guard.py"
    ).read_text(encoding="utf-8")
    apify = (ROOT / "backend/app/workers/apify_jobs_worker.py").read_text(
        encoding="utf-8"
    )
    redis_worker = (ROOT / "backend/app/workers/worker_main.py").read_text(
        encoding="utf-8"
    )
    assert 'Path("/run/vkpi-release-validation.fence")' in (
        ROOT / "backend/app/core/release_validation.py"
    ).read_text(encoding="utf-8")
    assert "ReleaseValidationFenceMiddleware" in main
    assert "release_validation_request_allowed" in main_fence
    assert "skip_non_migration_writes=main_release_validation.release_validation_active()" in main
    assert "release_validation_probe=main_release_validation.safe_status" in main
    assert '"release_validation": run_runtime_trust_stage(' in main_health
    assert "release_validation_probe," in main_health
    assert "release_validation_active()" in scheduler
    assert "release_validation_active()" in apify
    assert "release_validation_active()" in redis_worker


def _build_runtime_trust(
    release_probe: Callable[[], object],
) -> dict[str, object]:
    return build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=release_probe,
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: "306_contract.sql",
        worker_probe=lambda: {
            "worker_heartbeat": "2026-08-29T00:00:00Z",
            "worker_online": True,
            "worker_sha": "a" * 40,
            "worker_sha_source": "db_heartbeat",
            "worker_heartbeat_source": "db_heartbeat",
        },
        redis_worker_probe=lambda: {"online": True, "expected_count": 1},
        scheduler_probe=lambda: "not_configured",
        worker_sha_fallback_probe=lambda: {},
        server_git_sha="a" * 40,
        postgres_runtime=True,
    )


def test_runtime_trust_projects_release_validation_and_fails_closed() -> None:
    release_status = {
        "active": False,
        "valid": True,
        "source": "contract_test",
    }

    trusted = _build_runtime_trust(lambda: release_status)
    assert trusted["release_validation"] == release_status
    assert trusted["probe"]["stages"]["release_validation"]["status"] == "completed"

    def broken_status() -> object:
        raise RuntimeError("status unavailable")

    untrusted = _build_runtime_trust(broken_status)
    assert untrusted["release_validation"] == {
        "active": True,
        "valid": False,
        "source": "status_error",
    }
    assert untrusted["probe"]["stages"]["release_validation"]["status"] == "error"
    assert "release_validation_untrusted" in untrusted["probe"]["failure_reasons"]
