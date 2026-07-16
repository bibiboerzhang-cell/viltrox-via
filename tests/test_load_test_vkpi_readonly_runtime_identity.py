from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import scripts.load_test_vkpi_readonly as load_test_module


RELEASE_SHA = "a" * 40
MIGRATION = "246_vkpi_worker_runtime_identity.sql"
WORKER_BOOT_SHA256 = "b" * 64
RUN_NONCE = "capacity-runtime-identity-0001"
NOW = datetime(2026, 7, 13, 22, 0, tzinfo=timezone.utc)


def _args(*extra: str):
    return load_test_module.build_parser().parse_args(
        [
            "--profiles",
            "health",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
            "--execution-run-nonce",
            RUN_NONCE,
            "--expected-runtime-release-sha",
            RELEASE_SHA,
            "--expected-migration-version",
            MIGRATION,
            "--expected-worker-release-sha",
            RELEASE_SHA,
            "--expected-worker-boot-nonce-sha256",
            WORKER_BOOT_SHA256,
            "--worker-not-before",
            "2026-07-13T21:55:00Z",
            "--max-worker-heartbeat-age-seconds",
            "180",
            *extra,
        ]
    )


def _health_payload() -> dict:
    return {
        "status": "ok",
        "build": {
            "git_sha": RELEASE_SHA,
            "client_matches_server": True,
        },
        "trust": {
            "db_startup": {
                "backend": "postgres",
                "state": "completed",
                "schema_migrations": "completed",
            },
            "sha_aligned": True,
            "server_git_sha": RELEASE_SHA,
            "client_git_sha": RELEASE_SHA,
            "db_migration_max": MIGRATION,
            "db_migration_source": "schema_migrations",
            "worker_sha": RELEASE_SHA,
            "worker_sha_source": "db_heartbeat",
            "worker_heartbeat_source": "db_heartbeat",
            "worker_online": True,
            "worker_name": "apify-worker-1",
            "worker_pid": 4321,
            "worker_boot_nonce_sha256": WORKER_BOOT_SHA256,
            "worker_started_at": "2026-07-13T21:56:00Z",
            "worker_heartbeat": "2026-07-13T21:59:00Z",
        },
    }


def test_execution_plan_binds_complete_target_runtime_identity() -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())
    identity = plan["target_runtime_identity"]

    assert identity["complete"] is True
    assert identity["health_path"] == "/health"
    assert identity["release"]["expected_server_git_sha"] == RELEASE_SHA
    assert identity["release"]["expected_client_git_sha"] == RELEASE_SHA
    assert identity["migration"]["expected_applied_version"] == MIGRATION
    assert identity["migration"]["required_source"] == "schema_migrations"
    assert identity["worker"]["expected_release_sha"] == RELEASE_SHA
    assert identity["worker"]["expected_boot_nonce_sha256"] == WORKER_BOOT_SHA256
    assert identity["worker"]["not_before"] == "2026-07-13T21:55:00Z"
    assert identity["preflight"] == {
        "requests": 1,
        "must_pass_before_pressure": True,
        "raw_health_payload_persisted": False,
    }

    for flag, value in (
        ("--expected-runtime-release-sha", "c" * 40),
        ("--expected-migration-version", "247_next.sql"),
        ("--expected-worker-release-sha", "d" * 40),
        ("--expected-worker-boot-nonce-sha256", "e" * 64),
        ("--worker-not-before", "2026-07-13T21:56:00Z"),
        ("--max-worker-heartbeat-age-seconds", "60"),
    ):
        changed = load_test_module.build_capacity_execution_plan(_args(flag, value))
        assert changed.plan_sha256 != plan.plan_sha256


def test_incomplete_runtime_identity_plan_is_not_approval_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = load_test_module.build_parser().parse_args(
        [
            "--profiles",
            "health",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
            "--execution-run-nonce",
            RUN_NONCE,
        ]
    )
    plan = load_test_module.build_capacity_execution_plan(args)
    state = load_test_module.current_capacity_worktree_state()
    monkeypatch.setattr(
        load_test_module,
        "current_capacity_worktree_state",
        lambda: dict(state),
    )

    checks, failures = load_test_module._capacity_execution_runtime_binding_status(plan)

    assert plan["target_runtime_identity"]["complete"] is False
    assert checks["target_runtime_identity_plan_binding_valid"] is False
    assert "capacity_execution_target_runtime_identity_plan_binding" in failures


def test_matching_health_payload_passes_without_persisting_raw_health() -> None:
    contract = load_test_module.build_capacity_execution_plan(_args())[
        "target_runtime_identity"
    ]
    payload = _health_payload()

    report = load_test_module.validate_target_runtime_identity_payload(
        payload,
        contract,
        now=NOW,
    )

    assert report["pass"] is True
    assert report["failure_reasons"] == []
    assert report["raw_health_payload_persisted"] is False
    assert report["observed"]["server_release_sha"] == RELEASE_SHA[:12]
    assert RELEASE_SHA not in str(report)
    assert payload not in report.values()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda payload: payload["build"].update({"git_sha": "c" * 40}),
            "runtime_server_release_sha_mismatch",
        ),
        (
            lambda payload: payload["trust"].update(
                {"client_git_sha": "c" * 40, "sha_aligned": False}
            ),
            "runtime_client_release_sha_mismatch",
        ),
        (
            lambda payload: payload["trust"].update(
                {"db_migration_max": "245_old.sql"}
            ),
            "runtime_migration_version_mismatch",
        ),
        (
            lambda payload: payload["trust"].update(
                {"db_migration_source": "code_manifest_fallback"}
            ),
            "runtime_migration_source_untrusted",
        ),
        (
            lambda payload: payload["trust"].update({"worker_sha": "c" * 40}),
            "runtime_worker_release_sha_mismatch",
        ),
        (
            lambda payload: payload["trust"].update(
                {"worker_sha_source": "assumed_same_repo"}
            ),
            "runtime_worker_release_source_untrusted",
        ),
        (
            lambda payload: payload["trust"].update(
                {"worker_heartbeat_source": "apify_jobs_activity"}
            ),
            "runtime_worker_heartbeat_source_untrusted",
        ),
        (
            lambda payload: payload["trust"].update(
                {"worker_boot_nonce_sha256": "c" * 64}
            ),
            "runtime_worker_boot_nonce_mismatch",
        ),
        (
            lambda payload: payload["trust"].update(
                {"worker_started_at": "2026-07-13T21:54:00Z"}
            ),
            "runtime_worker_started_before_approved_restart",
        ),
        (
            lambda payload: payload["trust"].update(
                {"worker_heartbeat": "2026-07-13T21:56:00Z"}
            ),
            "runtime_worker_heartbeat_stale",
        ),
    ],
)
def test_runtime_identity_mismatch_fails_closed(mutation, reason: str) -> None:
    contract = load_test_module.build_capacity_execution_plan(_args())[
        "target_runtime_identity"
    ]
    payload = _health_payload()
    mutation(payload)

    report = load_test_module.validate_target_runtime_identity_payload(
        payload,
        contract,
        now=NOW,
    )

    assert report["pass"] is False
    assert reason in report["failure_reasons"]


def test_verify_runtime_identity_uses_one_injected_probe_and_redacts_payload() -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())
    calls: list[tuple[object, str, int]] = []

    async def fake_probe(context, *, backend_base: str, max_response_bytes: int):
        calls.append((context, backend_base, max_response_bytes))
        return {"ok": True, "payload": _health_payload(), "request_count": 1}

    context = load_test_module.RequestContext(object(), "secret-token", 0)
    report = asyncio.run(
        load_test_module.verify_target_runtime_identity(
            context,
            backend_base="http://127.0.0.1:8102",
            max_response_bytes=1024 * 1024,
            execution_plan=plan,
            probe_fn=fake_probe,
            now=NOW,
        )
    )

    assert report["pass"] is True
    assert report["request_count"] == 1
    assert report["token_persisted"] is False
    assert report["raw_health_payload_persisted"] is False
    assert len(calls) == 1
    assert "secret-token" not in str(report)
    assert RELEASE_SHA not in str(report)


def test_runtime_health_probe_is_fixed_get_only_and_never_sends_staff_token() -> None:
    encoded = json.dumps(_health_payload()).encode("utf-8")
    captured: dict = {}

    class FakeContent:
        def __init__(self) -> None:
            self._remaining = encoded

        async def read(self, size: int) -> bytes:
            chunk = self._remaining[:size]
            self._remaining = self._remaining[size:]
            return chunk

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class FakeSession:
        def get(self, url: str, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    context = load_test_module.RequestContext(FakeSession(), "secret-token", 0)
    result = asyncio.run(
        load_test_module.probe_target_runtime_health(
            context,
            backend_base="http://127.0.0.1:8102",
            max_response_bytes=1024 * 1024,
        )
    )

    assert result["ok"] is True
    assert result["request_count"] == 1
    assert captured["url"] == "http://127.0.0.1:8102/health"
    assert captured["allow_redirects"] is False
    assert "Authorization" not in captured["headers"]


@pytest.mark.parametrize(
    "encoded",
    [
        b'{"status":"ok","status":"ok"}',
        b'{"status":"ok","trust":{"worker_pid":NaN}}',
        b"[]",
    ],
)
def test_runtime_identity_json_rejects_ambiguous_or_non_object_payloads(
    encoded: bytes,
) -> None:
    with pytest.raises(ValueError):
        load_test_module._strict_identity_json_loads(encoded)


@pytest.mark.parametrize("request_count", [0, 2, True, "not-a-count"])
def test_verify_runtime_identity_rejects_non_single_probe_count(request_count) -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())

    async def fake_probe(_context, **_kwargs):
        return {
            "ok": True,
            "payload": _health_payload(),
            "request_count": request_count,
        }

    report = asyncio.run(
        load_test_module.verify_target_runtime_identity(
            load_test_module.RequestContext(object(), None, 0),
            backend_base="http://127.0.0.1:8102",
            max_response_bytes=1024 * 1024,
            execution_plan=plan,
            probe_fn=fake_probe,
            now=NOW,
        )
    )

    assert report["pass"] is False
    assert report["failure_reasons"] == [
        "runtime_health_probe_request_count_invalid"
    ]


def test_execute_blocks_pressure_when_injected_runtime_probe_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args("--execute-live", "--no-raw-samples", "--session-count", "1")
    plan = load_test_module.build_capacity_execution_plan(args)
    approval = {
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": load_test_module.hashlib.sha256(
            RUN_NONCE.encode("utf-8")
        ).hexdigest(),
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(load_test_module, "is_verified_capacity_execution_approval", lambda _v: True)
    monkeypatch.setattr(load_test_module, "is_consumed_capacity_execution_approval", lambda _v: True)
    monkeypatch.setattr(load_test_module, "redeem_consumed_capacity_execution_approval", lambda _v: True)
    monkeypatch.setattr(
        load_test_module,
        "resolve_token_pool",
        lambda _path=None: (
            [],
            {
                "sources": ["test_no_auth"],
                "token_count": 0,
                "token_emitted": False,
                "token_persisted": False,
            },
        ),
    )
    monkeypatch.setattr(load_test_module.aiohttp, "TCPConnector", lambda **_kwargs: object())
    monkeypatch.setattr(load_test_module.aiohttp, "ClientSession", lambda **_kwargs: FakeSession())

    async def fake_probe(_context, **_kwargs):
        payload = deepcopy(_health_payload())
        payload["trust"]["worker_sha"] = "c" * 40
        return {"ok": True, "payload": payload, "request_count": 1}

    async def forbidden_pressure(*_args, **_kwargs):
        raise AssertionError("pressure must not start after runtime identity mismatch")

    monkeypatch.setattr(load_test_module, "_execute_v3_with_contexts", forbidden_pressure)

    report = asyncio.run(
        load_test_module.execute(
            args,
            execution_plan=plan,
            operator_approval=approval,
            runtime_identity_probe_fn=fake_probe,
        )
    )

    assert report["runtime_identity_preflight"]["pass"] is False
    assert "runtime_worker_release_sha_mismatch" in report[
        "runtime_identity_preflight"
    ]["failure_reasons"]
    assert report["network_requests_issued"] == 1
    assert report["pressure_observed"] is False
    assert report["pressure_completed"] is False
    assert report["safety"]["pressure_started"] is False
