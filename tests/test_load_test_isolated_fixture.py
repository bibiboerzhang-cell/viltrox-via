from __future__ import annotations

import ast
import asyncio
import json
import socket
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts.ops import load_test_isolated_fixture as isolated


def small_config() -> isolated.IsolatedFixtureConfig:
    return isolated.IsolatedFixtureConfig(
        tiers=(1, 5),
        trials=2,
        duration_seconds=0.08,
        seed=20260713,
        database_slots=4,
        aggregate_compute_slots=2,
        request_timeout_ms=500.0,
        event_loop_sample_ms=2.0,
    )


def test_default_target_is_the_required_six_tiers_and_config_is_frozen() -> None:
    config = isolated.IsolatedFixtureConfig().validated()
    assert config.tiers == (1, 5, 10, 20, 40, 80)
    assert config.trials == 3
    with pytest.raises(FrozenInstanceError):
        config.trials = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tiers": (1, 81)}, "tiers"),
        ({"tiers": (5, 5)}, "tiers"),
        ({"trials": 6}, "trials"),
        ({"duration_seconds": 11.0}, "duration_seconds"),
        ({"database_slots": 0}, "database_slots"),
        ({"aggregate_compute_slots": 129}, "aggregate_compute_slots"),
    ],
)
def test_code_owned_hard_bounds_fail_before_execution(
    changes: dict[str, object], message: str
) -> None:
    values = {
        "tiers": (1, 5),
        "trials": 1,
        "duration_seconds": 0.05,
        "seed": 1,
        "database_slots": 4,
        "aggregate_compute_slots": 2,
        "request_timeout_ms": 500.0,
        "event_loop_sample_ms": 2.0,
        **changes,
    }
    with pytest.raises(ValueError, match=message):
        isolated.IsolatedFixtureConfig(**values).validated()


def test_identity_digests_are_unique_per_vu_and_never_emitted() -> None:
    identities = {
        isolated.synthetic_identity_digest(20260713, trial, 80, vu)
        for trial in range(3)
        for vu in range(80)
    }
    assert len(identities) == 240
    assert all(len(value) == 64 for value in identities)


def test_isolated_run_uses_no_connect_calls_and_records_zero_external_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network connect attempted by isolated fixture")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = asyncio.run(isolated.build_report(small_config()))

    assert report["evidence_class"] == "isolated_fixture"
    assert report["status"] == "completed_isolated_fixture"
    assert report["safety"] == {
        "transport": "direct_in_process_coroutine_call",
        "network_stack_used": False,
        "socket_listeners_opened": False,
        "http_requests_issued": 0,
        "database_connections_opened": 0,
        "redis_connections_opened": 0,
        "worker_jobs_enqueued": 0,
        "browser_used": False,
        "provider_calls": 0,
        "business_rows_read": 0,
        "business_rows_written": 0,
    }
    assert report["claims"]["production_performance_evidence"] is False
    assert report["claims"]["real_user_capacity_evidence"] is False
    assert report["claims"]["maximum_real_users"] is None
    for tier in report["tier_results"]:
        assert tier["trial_count"] == 2
        for trial in tier["trials"]:
            assert trial["total_requests"] > 0
            assert trial["identities"]["requested"] == trial["tier_vu"]
            assert trial["identities"]["unique"] == trial["tier_vu"]
            assert trial["identities"]["collision_count"] == 0
            assert trial["identities"]["identity_values_persisted"] is False
            assert set(trial["external_io"].values()) == {0}


def test_report_signature_detects_metric_tampering() -> None:
    report = asyncio.run(isolated.build_report(small_config()))
    artifact = isolated.sign_frozen_report(report, seed=small_config().seed)
    assert isolated.verify_signed_report(artifact.report) is True
    assert artifact.report["attestation"]["independent_producer"] is False
    assert artifact.report["attestation"]["production_trust"] is False

    tampered = json.loads(json.dumps(artifact.report))
    tampered["tier_results"][0]["representative"]["requests_per_second"] += 1
    assert isolated.verify_signed_report(tampered) is False


def test_frozen_artifact_is_exclusive_owner_read_only_and_reverifiable(
    tmp_path: Path,
) -> None:
    report = asyncio.run(isolated.build_report(small_config()))
    artifact = isolated.sign_frozen_report(report, seed=small_config().seed)
    output = tmp_path / "isolated.json"
    assert isolated.write_frozen_artifact(artifact, output) == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert isolated.verify_signed_report(loaded) is True
    with pytest.raises(FileExistsError):
        isolated.write_frozen_artifact(artifact, output)


def test_module_has_no_network_client_or_live_service_anchor() -> None:
    source_path = Path(isolated.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(
        {"aiohttp", "httpx", "requests", "urllib", "psycopg", "redis"}
    )
    for forbidden in (
        "http://",
        "https://",
        "localhost:5173",
        "127.0.0.1:8102",
        "54329",
        "6379",
        "/api/",
    ):
        assert forbidden not in source


def test_cli_tier_parser_keeps_vu_explicit_and_rejects_oversized_tier() -> None:
    assert isolated.parse_tiers("1,5,10,20,40,80") == (1, 5, 10, 20, 40, 80)
    with pytest.raises(Exception, match="tiers"):
        isolated.parse_tiers("1,81")
