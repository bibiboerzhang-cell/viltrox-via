"""/health additive fields (A1 W1): trust.heartbeat_age_seconds + probe.external_ping_hint.

Both are additive; the existing trust/probe keys and stage names are untouched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.main_health import (
    EXTERNAL_PING_HINT,
    _RUNTIME_TRUST_COORDINATOR,
    _runtime_trust_failure_payload,
    build_runtime_trust,
    compute_heartbeat_age,
    external_ping_hint,
)

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


def _row(lane: str | None, age: float | None, **extra) -> dict:
    row = {"worker_name": f"apify-worker-{lane or 'x'}", "heartbeat_age_seconds": age, "online": True}
    if lane is not None:
        row["lane"] = lane
    row.update(extra)
    return row


def test_oldest_critical_heartbeat_is_max_over_roles_of_freshest_row() -> None:
    trust = {
        "worker_heartbeat": "2026-09-02T11:59:57Z",
        "worker_fleet": {
            "workers": [
                _row("interactive", 3.0),
                _row("batch", 9000.0),  # a lane row that died long ago must not dominate
                _row("batch", 12.5),
                _row("batch", 400.0),
            ]
        },
        "redis_worker_fleet": {"workers": [{"worker_name": "redis-worker-1", "heartbeat_age_seconds": 20.0}]},
    }
    result = compute_heartbeat_age(trust, now=NOW)
    assert result["heartbeat_age_roles"] == {
        "apify:batch": 12.5,
        "apify:interactive": 3.0,
        "redis-worker": 20.0,
    }
    assert result["heartbeat_age_seconds"] == 20.0


def test_rows_without_lane_fall_into_the_all_role_and_bad_values_are_ignored() -> None:
    trust = {
        "worker_fleet": {
            "workers": [
                _row(None, 7.0),
                _row(None, True),
                _row(None, None),
                _row(None, float("nan")),
                "not-a-row",
            ]
        },
        "redis_worker_fleet": {"workers": [{"heartbeat_age_seconds": None}]},
    }
    result = compute_heartbeat_age(trust, now=NOW)
    assert result["heartbeat_age_roles"] == {"apify:all": 7.0}
    assert result["heartbeat_age_seconds"] == 7.0


def test_legacy_probe_without_fleet_uses_primary_worker_heartbeat_iso() -> None:
    trust = {"worker_heartbeat": (NOW - timedelta(seconds=95)).isoformat().replace("+00:00", "Z")}
    result = compute_heartbeat_age(trust, now=NOW)
    assert result == {"heartbeat_age_seconds": 95.0, "heartbeat_age_roles": {"apify:primary": 95.0}}


def test_no_heartbeat_evidence_is_none_not_zero() -> None:
    assert compute_heartbeat_age({}, now=NOW) == {"heartbeat_age_seconds": None, "heartbeat_age_roles": {}}
    assert compute_heartbeat_age({"worker_heartbeat": "garbage"}, now=NOW)["heartbeat_age_seconds"] is None
    assert compute_heartbeat_age({"worker_fleet": {"workers": []}}, now=NOW)["heartbeat_age_seconds"] is None


def test_build_runtime_trust_adds_fields_without_changing_existing_contract() -> None:
    def worker_probe() -> dict[str, object]:
        return {
            "worker_heartbeat": "2026-09-02T11:59:50Z",
            "worker_online": True,
            "worker_sha": "a" * 40,
            "worker_sha_source": "db_heartbeat",
            "worker_heartbeat_source": "db_heartbeat",
            "worker_fleet": {"workers": [_row("interactive", 4.0), _row("batch", 31.0)]},
        }

    trust = build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=lambda: {"active": False, "valid": True},
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: "306_example.sql",
        worker_probe=worker_probe,
        redis_worker_probe=lambda: {
            "online": True,
            "expected_count": 1,
            "workers": [{"worker_name": "redis-worker-1", "heartbeat_age_seconds": 9.0}],
        },
        scheduler_probe=lambda: "not_configured",
        worker_sha_fallback_probe=lambda: {},
        server_git_sha="a" * 40,
        postgres_runtime=True,
    )

    assert trust["heartbeat_age_seconds"] == 31.0
    assert trust["heartbeat_age_roles"] == {"apify:batch": 31.0, "apify:interactive": 4.0, "redis-worker": 9.0}
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
    assert probe["external_ping_hint"] == {
        "path": "/health",
        "method": "GET",
        "auth": "none",
        "expect_http_status": 200,
        "expect_json": {"status": "ok"},
        "note": EXTERNAL_PING_HINT["note"],
    }
    # Existing keys are still there with their historical meaning.
    for key in ("db_startup", "db_migration_max", "worker_online", "redis_worker_fleet", "scheduler_status", "sha_aligned"):
        assert key in trust


def test_migration_unavailable_branch_still_reports_none_age() -> None:
    trust = build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=lambda: {"active": False, "valid": True},
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: None,
        worker_probe=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
        redis_worker_probe=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
        scheduler_probe=lambda: "not_configured",
        worker_sha_fallback_probe=lambda: {},
        server_git_sha="a" * 40,
        postgres_runtime=True,
    )
    assert trust["heartbeat_age_seconds"] is None
    assert trust["heartbeat_age_roles"] == {}
    assert trust["probe"]["status"] == "degraded"


def test_forward_compatible_migration_superset_does_not_degrade_rollback_health() -> None:
    trust = build_runtime_trust(
        db_startup_probe=lambda: {"state": "completed"},
        release_validation_probe=lambda: {"active": False, "valid": True},
        client_git_sha_probe=lambda: "a" * 40,
        db_migration_probe=lambda: {
            "max": "311_future_forward_compatible.sql",
            "set_complete": True,
            "set_exact": False,
            "applied_count": 311,
            "expected_count": 310,
            "missing_count": 0,
            "unexpected_count": 1,
            "set_sha256": "b" * 64,
        },
        worker_probe=lambda: {
            "worker_heartbeat": "2026-09-02T11:59:57Z",
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

    assert trust["db_migration_complete"] is True
    assert trust["db_migration_exact"] is False
    assert trust["db_migration_unexpected_count"] == 1
    assert trust["probe"]["status"] == "ok"


def test_failure_payload_and_timeout_snapshot_carry_the_new_fields() -> None:
    snapshot = _RUNTIME_TRUST_COORDINATOR.snapshot("timeout", timeout_seconds=0.5)
    assert snapshot["external_ping_hint"]["path"] == "/health"
    assert snapshot["in_flight"] is True
    payload = _runtime_trust_failure_payload(server_git_sha="a" * 40, client_git_sha="b" * 40, probe=snapshot)
    assert payload["heartbeat_age_seconds"] is None
    assert payload["heartbeat_age_roles"] == {}
    assert payload["probe"]["external_ping_hint"]["auth"] == "none"
    assert payload["worker_sha_source"] == "probe_unavailable"


def test_external_ping_hint_returns_an_isolated_copy() -> None:
    hint = external_ping_hint()
    hint["expect_json"]["status"] = "mutated"
    hint["path"] = "/mutated"
    assert EXTERNAL_PING_HINT["expect_json"] == {"status": "ok"}
    assert EXTERNAL_PING_HINT["path"] == "/health"
    assert external_ping_hint()["path"] == "/health"
