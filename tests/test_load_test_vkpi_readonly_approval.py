from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature

import scripts.load_test_vkpi_readonly as load_test_module
import scripts.ops.load_test_approval as approval_module
import scripts.ops.load_test_runner as runner_module


RUN_NONCE = "capacity-approved-run-0001"
NOW = datetime(2026, 7, 13, 22, 0, tzinfo=timezone.utc)
PUBLIC_BYTES = bytes.fromhex("11" * 32)
SIGNATURE_BYTES = bytes.fromhex("22" * 64)
CLEAN_STATUS_SHA256 = hashlib.sha256(b"").hexdigest()


def _args(*extra: str):
    return load_test_module.build_parser().parse_args(
        [
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:60,5:60",
            "--session-count",
            "5",
            "--trials",
            "3",
            "--journey-profile",
            "staff-readonly-v1",
            "--execution-run-nonce",
            RUN_NONCE,
            "--telemetry-run-nonce",
            "capacity-telemetry-run-0001",
            "--expected-runtime-release-sha",
            "a" * 40,
            "--expected-migration-version",
            "246_vkpi_worker_runtime_identity.sql",
            "--expected-worker-release-sha",
            "a" * 40,
            "--expected-worker-boot-nonce-sha256",
            "b" * 64,
            "--worker-not-before",
            "2026-07-13T21:55:00Z",
            "--max-worker-heartbeat-age-seconds",
            "180",
            *extra,
        ]
    )


def _approval_payload(
    plan,
    *,
    nonce: str = RUN_NONCE,
    key_id: str = "capacity-operator-test-v1",
    issued_at: datetime = NOW - timedelta(minutes=5),
    expires_at: datetime = NOW + timedelta(minutes=30),
) -> dict:
    return {
        "schema_version": load_test_module.CAPACITY_EXECUTION_APPROVAL_SCHEMA,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "approval_scope": load_test_module.CAPACITY_EXECUTION_APPROVAL_SCOPE,
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "signature_base64": base64.b64encode(SIGNATURE_BYTES).decode("ascii"),
    }


def _write_private_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


def _install_public_only_verifier(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict,
    *,
    key_id: str = "capacity-operator-test-v1",
) -> None:
    signed_fields = (
        "schema_version",
        "algorithm",
        "key_id",
        "approval_scope",
        "plan_sha256",
        "run_nonce_sha256",
        "issued_at",
        "expires_at",
    )
    expected_message = approval_module._canonical_execution_json_bytes(
        {field: payload.get(field) for field in signed_fields}
    )

    class FakePublicKey:
        def verify(self, signature: bytes, message: bytes) -> None:
            if signature != SIGNATURE_BYTES or message != expected_message:
                raise InvalidSignature

    class FakeEd25519PublicKey:
        @classmethod
        def from_public_bytes(cls, value: bytes) -> FakePublicKey:
            if value != PUBLIC_BYTES:
                raise ValueError("unexpected public key")
            return FakePublicKey()

    monkeypatch.setattr(approval_module, "Ed25519PublicKey", FakeEd25519PublicKey)
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_CAPACITY_OPERATOR_ED25519_PUBLIC_KEYS",
        {key_id: base64.b64encode(PUBLIC_BYTES).decode("ascii")},
    )


def _install_clean_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "worktree_clean": True,
        "worktree_status_sha256": CLEAN_STATUS_SHA256,
    }
    monkeypatch.setattr(
        approval_module,
        "current_capacity_worktree_state",
        lambda: dict(state),
    )
    monkeypatch.setattr(
        runner_module,
        "current_capacity_worktree_state",
        lambda: dict(state),
    )


def test_execution_plan_is_immutable_and_binds_head_targets_identity_and_tiers() -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())
    original_hash = plan.plan_sha256
    worktree_state = load_test_module.current_capacity_worktree_state()

    assert plan["code"]["git_head"] == load_test_module.current_capacity_code_head()
    assert plan["code"]["worktree_clean"] is worktree_state["worktree_clean"]
    assert plan["code"]["worktree_status_sha256"] == worktree_state[
        "worktree_status_sha256"
    ]
    assert plan["code"]["runtime_source_bundle_sha256"] == (
        load_test_module.current_capacity_runner_source_bundle_sha256()
    )
    assert plan["code"]["runtime_source_files"] == list(
        load_test_module.CAPACITY_RUNNER_SOURCE_FILES
    )
    assert plan["targets"]["backend"]["port"] == 8102
    assert plan["targets"]["postgresql_telemetry"]["port"] == 54329
    assert plan["identity_preflight"]["unique_principal_per_session_required"] is True
    assert plan["approval_consumption"]["single_use_required"] is True
    assert plan["approval_consumption"]["atomic_create_required"] is True
    assert plan["approval_consumption"]["ledger_dir_path_sha256"] == (
        load_test_module.capacity_path_binding_sha256(
            load_test_module.DEFAULT_CAPACITY_EXECUTION_NONCE_LEDGER_DIR
        )
    )
    assert plan["workload"]["tiers"] == [
        {
            "duration_seconds": 60.0,
            "human_users": None,
            "load_model": "closed_loop",
            "virtual_users": 1,
        },
        {
            "duration_seconds": 60.0,
            "human_users": None,
            "load_model": "closed_loop",
            "virtual_users": 5,
        },
    ]
    copied_targets = plan["targets"]
    copied_targets["backend"]["port"] = 9999
    assert plan["targets"]["backend"]["port"] == 8102
    assert plan.plan_sha256 == original_hash
    with pytest.raises(TypeError):
        plan["targets"] = copied_targets  # type: ignore[index]
    with pytest.raises(AttributeError):
        plan._canonical = b"{}"  # type: ignore[misc]

    changed = load_test_module.build_capacity_execution_plan(
        _args("--backend-base", "http://127.0.0.1:8111")
    )
    assert changed.plan_sha256 != original_hash


@pytest.mark.parametrize(
    ("field_name", "mutated_value"),
    [
        ("frontend_base", "http://127.0.0.1:5174"),
        ("backend_base", "http://127.0.0.1:8103"),
        ("profiles", "health,light_db"),
        ("phases", "1,2"),
        ("trials", 2),
        ("requests_per_phase", 21),
        ("waves_per_phase", 4),
        ("timeout_seconds", 14.0),
        ("session_count", 2),
        ("token_file", Path("/not/read/mutated-token.json")),
        ("execution_nonce_ledger_dir", Path("/tmp/mutated-capacity-ledger")),
        ("calibration_as_of", "2026-07-13T23:00:00Z"),
    ],
)
def test_each_bound_execution_arg_mutation_is_rejected_before_freeze(
    field_name: str,
    mutated_value,
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
    setattr(args, field_name, mutated_value)

    with pytest.raises(ValueError):
        load_test_module.freeze_capacity_execution_args(args, plan)


def test_execution_args_are_immutable_after_exact_plan_freeze() -> None:
    args = _args()
    plan = load_test_module.build_capacity_execution_plan(args)
    frozen = load_test_module.freeze_capacity_execution_args(args, plan)

    assert frozen.frontend_base == args.frontend_base
    assert frozen.session_count == args.session_count
    with pytest.raises(AttributeError, match="frozen"):
        frozen.session_count = 999
    with pytest.raises(AttributeError, match="frozen"):
        frozen.token_file = Path("/not/read/rebound-token.json")


def test_cli_arg_mutation_after_plan_blocks_before_approval_writer_or_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def build_then_mutate(args):
        plan = runner_module.build_capacity_execution_plan(args)
        args.session_count = 2
        args.token_file = Path("/not/read/rebound-token.json")
        return plan

    def forbidden(*_args, **_kwargs):
        raise AssertionError("mutated args must block before privileged execution resources")

    monkeypatch.setattr(load_test_module, "build_capacity_execution_plan", build_then_mutate)
    monkeypatch.setattr(load_test_module, "verify_capacity_execution_approval", forbidden)
    monkeypatch.setattr(load_test_module, "RawSampleWriter", forbidden)
    monkeypatch.setattr(load_test_module, "resolve_token_pool", forbidden)
    output = tmp_path / "mutated-args-blocked.json"
    raw_output = tmp_path / "must-not-exist.samples.ndjson"

    exit_code = load_test_module.main(
        [
            "--execute-live",
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
            "--output",
            str(output),
            "--raw-output",
            str(raw_output),
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert summary["status"] == "blocked"
    assert report["operator_preflight"]["failure_reasons"] == [
        "capacity_execution_args_changed_after_plan"
    ]
    assert raw_output.exists() is False


def test_allowlisted_public_only_operator_approval_verifies_without_persisting_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_clean_worktree(monkeypatch)
    plan = load_test_module.build_capacity_execution_plan(_args())
    payload = _approval_payload(plan)
    _install_public_only_verifier(monkeypatch, payload)
    path = tmp_path / "capacity-approval.json"
    _write_private_json(path, payload)

    approval = load_test_module.verify_capacity_execution_approval(
        path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=NOW,
    )
    public = load_test_module.public_capacity_execution_approval(approval)

    assert load_test_module.is_verified_capacity_execution_approval(approval) is True
    assert public["trusted"] is True
    assert public["key_id"] == "capacity-operator-test-v1"
    assert public["plan_sha256"] == plan.plan_sha256
    assert public["signature_persisted"] is False
    encoded = json.dumps(public)
    assert payload["signature_base64"] not in encoded
    assert RUN_NONCE not in encoded


def test_dirty_worktree_plan_cannot_become_live_execution_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirty_status = b" M backend/private/path.py\x00"
    dirty_state = {
        "worktree_clean": False,
        "worktree_status_sha256": hashlib.sha256(dirty_status).hexdigest(),
    }
    monkeypatch.setattr(
        runner_module,
        "current_capacity_worktree_state",
        lambda: dict(dirty_state),
    )
    monkeypatch.setattr(
        approval_module,
        "current_capacity_worktree_state",
        lambda: dict(dirty_state),
    )
    plan = load_test_module.build_capacity_execution_plan(_args())
    payload = _approval_payload(plan)
    _install_public_only_verifier(monkeypatch, payload)
    approval_path = tmp_path / "dirty-worktree-approval.json"
    _write_private_json(approval_path, payload)

    approval = load_test_module.verify_capacity_execution_approval(
        approval_path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=NOW,
    )

    assert plan["code"]["worktree_clean"] is False
    assert load_test_module.is_verified_capacity_execution_approval(approval) is False
    assert "capacity_execution_worktree_not_clean" in approval["failure_reasons"]
    assert "backend/private/path.py" not in json.dumps(plan.public_dict())


def test_verified_nonce_is_atomically_consumed_once_with_private_bound_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_clean_worktree(monkeypatch)
    ledger_dir = tmp_path / "nonce-ledger"
    wrong_ledger_dir = tmp_path / "other-ledger"
    plan = load_test_module.build_capacity_execution_plan(
        _args("--execution-nonce-ledger-dir", str(ledger_dir))
    )
    payload = _approval_payload(plan)
    _install_public_only_verifier(monkeypatch, payload)
    approval_path = tmp_path / "single-use-approval.json"
    _write_private_json(approval_path, payload)
    approval = load_test_module.verify_capacity_execution_approval(
        approval_path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=NOW,
    )
    assert load_test_module.is_verified_capacity_execution_approval(approval) is True

    wrong_ledger = load_test_module.consume_capacity_execution_approval(
        approval,
        plan=plan,
        ledger_dir=wrong_ledger_dir,
        consumed_at=NOW,
    )
    assert wrong_ledger["trusted"] is False
    assert "capacity_execution_nonce_ledger_binding" in wrong_ledger["failure_reasons"]
    assert wrong_ledger_dir.exists() is False

    first = load_test_module.consume_capacity_execution_approval(
        approval,
        plan=plan,
        ledger_dir=ledger_dir,
        consumed_at=NOW,
    )
    second = load_test_module.consume_capacity_execution_approval(
        approval,
        plan=plan,
        ledger_dir=ledger_dir,
        consumed_at=NOW,
    )

    assert load_test_module.is_consumed_capacity_execution_approval(first) is True
    assert first["nonce_consumed"] is True
    assert second["trusted"] is False
    assert second["nonce_consumed"] is False
    assert second["failure_reasons"] == ["execution_approval_nonce_already_consumed"]
    assert ledger_dir.stat().st_mode & 0o777 == 0o700
    records = list(ledger_dir.iterdir())
    assert len(records) == 1
    assert records[0].stat().st_mode & 0o777 == 0o600
    ledger_text = records[0].read_text(encoding="utf-8")
    public_text = json.dumps(load_test_module.public_capacity_execution_approval(first))
    assert RUN_NONCE not in ledger_text
    assert RUN_NONCE not in public_text
    assert payload["signature_base64"] not in ledger_text
    assert payload["signature_base64"] not in public_text
    assert str(ledger_dir) not in ledger_text
    assert str(ledger_dir) not in public_text
    assert "Bearer " not in ledger_text


def test_replayed_nonce_blocks_before_token_source_or_http_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_clean_worktree(monkeypatch)
    ledger_dir = tmp_path / "replay-ledger"
    args = _args(
        "--execute-live",
        "--no-raw-samples",
        "--execution-nonce-ledger-dir",
        str(ledger_dir),
    )
    plan = load_test_module.build_capacity_execution_plan(args)
    now = datetime.now(timezone.utc)
    payload = _approval_payload(
        plan,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=30),
    )
    _install_public_only_verifier(monkeypatch, payload)
    approval_path = tmp_path / "replay-approval.json"
    _write_private_json(approval_path, payload)
    approval = load_test_module.verify_capacity_execution_approval(
        approval_path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=now,
    )
    first = load_test_module.consume_capacity_execution_approval(
        approval,
        plan=plan,
        ledger_dir=ledger_dir,
        consumed_at=now,
    )
    assert load_test_module.is_consumed_capacity_execution_approval(first) is True

    def forbidden_token_read(_path=None):
        raise AssertionError("replayed approval must block before token sources")

    monkeypatch.setattr(load_test_module, "resolve_token_pool", forbidden_token_read)
    report = asyncio.run(
        load_test_module.execute(
            args,
            execution_plan=plan,
            operator_approval=approval,
        )
    )

    assert report["network_requests_issued"] == 0
    assert report["safety"]["http_session_created"] is False
    assert report["operator_preflight"]["trusted"] is False
    assert report["operator_preflight"]["failure_reasons"] == [
        "execution_approval_nonce_already_consumed"
    ]


@pytest.mark.parametrize(
    "mutation,expected_reason",
    [
        (
            lambda payload: payload.update({"plan_sha256": "0" * 64}),
            "execution_approval_plan_binding",
        ),
        (
            lambda payload: payload.update({"run_nonce_sha256": "1" * 64}),
            "execution_approval_run_binding",
        ),
        (
            lambda payload: payload.update(
                {
                    "issued_at": "2026-07-13T10:00:00Z",
                    "expires_at": "2026-07-13T11:00:00Z",
                }
            ),
            "execution_approval_time_binding",
        ),
        (
            lambda payload: payload.update({"approval_scope": "write_capacity"}),
            "execution_approval_scope",
        ),
    ],
)
def test_approval_rejects_plan_nonce_time_or_scope_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    expected_reason: str,
) -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())
    payload = _approval_payload(plan)
    mutation(payload)
    _install_public_only_verifier(monkeypatch, payload)
    path = tmp_path / "rebound-approval.json"
    _write_private_json(path, payload)

    approval = load_test_module.verify_capacity_execution_approval(
        path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=NOW,
    )

    assert load_test_module.is_verified_capacity_execution_approval(approval) is False
    assert expected_reason in approval["failure_reasons"]


def test_operator_key_cannot_reuse_calibration_or_telemetry_trust_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = load_test_module.build_capacity_execution_plan(_args())
    payload = _approval_payload(plan)
    _install_public_only_verifier(monkeypatch, payload)
    encoded_key = base64.b64encode(PUBLIC_BYTES).decode("ascii")
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS",
        {"independent-telemetry-id": encoded_key},
    )
    path = tmp_path / "cross-role-approval.json"
    _write_private_json(path, payload)

    approval = load_test_module.verify_capacity_execution_approval(
        path,
        plan=plan,
        run_nonce=RUN_NONCE,
        evaluated_at=NOW,
    )

    assert approval["trusted"] is False
    assert approval["operator_role_separated"] is False
    assert "execution_approval_operator_role_not_separated" in approval["failure_reasons"]


def test_missing_or_unallowlisted_approval_blocks_before_token_read_or_session_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _args("--execute-live", "--token-file", "/definitely/not/read/token.json")
    load_test_module.validate_execution_args(args)

    def forbidden_token_read(_path=None):
        raise AssertionError("token sources must not be read before operator approval")

    monkeypatch.setattr(load_test_module, "resolve_token_pool", forbidden_token_read)
    report = asyncio.run(load_test_module.execute(args))

    assert report["operator_preflight"]["trusted"] is False
    assert report["operator_preflight"]["failure_reasons"] == [
        "execution_approval_not_configured"
    ]
    assert report["auth"]["token_file_read"] is False
    assert report["safety"]["http_session_created"] is False
    assert report["network_requests_issued"] == 0
    assert report["pressure_completed"] is False


def test_default_operator_allowlist_is_empty_and_runtime_has_no_signing_surface() -> None:
    assert dict(load_test_module.TRUSTED_CAPACITY_OPERATOR_ED25519_PUBLIC_KEYS) == {}
    source = Path("scripts/ops/load_test_approval.py").read_text(encoding="utf-8")
    assert "Ed25519PrivateKey" not in source
    assert "def sign_" not in source
    assert "signature_base64" not in load_test_module.public_capacity_execution_approval(
        {"signature_base64": "must-not-escape"}
    )


def test_dry_run_emits_exact_approval_request_without_reading_files(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "must-not-be-created-by-dry-run"
    report = load_test_module.build_dry_run_report(
        _args(
            "--token-file",
            "/not/read/token.json",
            "--execution-approval",
            "/not/read/approval.json",
            "--execution-nonce-ledger-dir",
            str(ledger_dir),
        )
    )

    assert report["network_observed"] is False
    assert report["operator_preflight"]["approval_file_read"] is False
    assert report["operator_preflight"]["token_file_read"] is False
    assert report["operator_preflight"]["plan_sha256"] == report[
        "capacity_execution_plan"
    ]["plan_sha256"]
    assert report["capacity_execution_plan"]["immutable_canonical_plan"] is True
    assert report["capacity_execution_plan"]["approval_consumption"][
        "ledger_dir_path_sha256"
    ] == load_test_module.capacity_path_binding_sha256(ledger_dir)
    assert ledger_dir.exists() is False


def test_cli_unapproved_live_returns_nonzero_and_does_not_create_raw_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKEN", raising=False)
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKENS_JSON", raising=False)
    output = tmp_path / "blocked-report.json"
    raw_output = tmp_path / "must-not-exist.samples.ndjson"

    exit_code = load_test_module.main(
        [
            "--execute-live",
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
            "--output",
            str(output),
            "--raw-output",
            str(raw_output),
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert summary["status"] == "blocked"
    assert summary["raw_evidence"] is None
    assert report["operator_preflight"]["trusted"] is False
    assert report["network_requests_issued"] == 0
    assert raw_output.exists() is False


def test_cli_live_incomplete_returns_nonzero_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args_for_plan = load_test_module.build_parser().parse_args(
        ["--profiles", "health", "--phases", "1", "--execution-run-nonce", RUN_NONCE]
    )
    plan = load_test_module.build_capacity_execution_plan(args_for_plan)
    fake_approval = {
        "status": "trusted_operator_execution_approval",
        "trusted": True,
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": hashlib.sha256(RUN_NONCE.encode("utf-8")).hexdigest(),
    }
    fake_consumed = {
        **fake_approval,
        "status": "trusted_operator_execution_approval_consumed",
        "nonce_consumed": True,
        "consumption_status": "consumed_once",
    }

    monkeypatch.setattr(load_test_module, "build_capacity_execution_plan", lambda _args: plan)
    monkeypatch.setattr(
        load_test_module,
        "verify_capacity_execution_approval",
        lambda *_args, **_kwargs: fake_approval,
    )
    monkeypatch.setattr(
        load_test_module,
        "is_verified_capacity_execution_approval",
        lambda value: value is fake_approval or value is fake_consumed,
    )
    monkeypatch.setattr(
        load_test_module,
        "consume_capacity_execution_approval",
        lambda *_args, **_kwargs: fake_consumed,
    )
    monkeypatch.setattr(
        load_test_module,
        "is_consumed_capacity_execution_approval",
        lambda value: value is fake_consumed,
    )

    async def incomplete_execute(*_args, **_kwargs):
        return {
            "requested_live": True,
            "live_run": False,
            "network_observed": False,
            "network_requests_issued": 0,
            "pressure_completed": False,
            "operator_preflight": {"trusted": True},
            "profiles": [],
            "overall_capacity": None,
        }

    monkeypatch.setattr(load_test_module, "execute", incomplete_execute)
    output = tmp_path / "incomplete-report.json"
    exit_code = load_test_module.main(
        [
            "--execute-live",
            "--profiles",
            "health",
            "--phases",
            "1",
            "--execution-run-nonce",
            RUN_NONCE,
            "--no-raw-samples",
            "--output",
            str(output),
        ]
    )
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert summary["status"] == "incomplete"
    assert summary["network_observed"] is False


def test_cli_selected_profile_blocked_overrides_stale_complete_and_exits_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    args_for_plan = load_test_module.build_parser().parse_args(
        [
            "--profiles",
            "health,light_db",
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
    plan = load_test_module.build_capacity_execution_plan(args_for_plan)
    fake_approval = {
        "status": "trusted_operator_execution_approval",
        "trusted": True,
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": hashlib.sha256(RUN_NONCE.encode("utf-8")).hexdigest(),
    }
    fake_consumed = {
        **fake_approval,
        "status": "trusted_operator_execution_approval_consumed",
        "nonce_consumed": True,
        "consumption_status": "consumed_once",
    }
    monkeypatch.setattr(load_test_module, "build_capacity_execution_plan", lambda _args: plan)
    monkeypatch.setattr(
        load_test_module,
        "verify_capacity_execution_approval",
        lambda *_args, **_kwargs: fake_approval,
    )
    monkeypatch.setattr(
        load_test_module,
        "is_verified_capacity_execution_approval",
        lambda value: value is fake_approval or value is fake_consumed,
    )
    monkeypatch.setattr(
        load_test_module,
        "consume_capacity_execution_approval",
        lambda *_args, **_kwargs: fake_consumed,
    )
    monkeypatch.setattr(
        load_test_module,
        "is_consumed_capacity_execution_approval",
        lambda value: value is fake_consumed,
    )

    async def partial_execute(*_args, **_kwargs):
        return {
            "requested_live": True,
            "live_run": True,
            "network_observed": True,
            "network_requests_issued": 21,
            "pressure_completed": True,
            "execution_expectations": {
                "selected_profiles": ["health", "light_db"],
                "stages_per_profile": 1,
                "trials_per_stage": 1,
            },
            "operator_preflight": {"trusted": True},
            "profiles": [
                {
                    "profile": "health",
                    "status": "completed",
                    "stages": [
                        {
                            "stage_index": 0,
                            "threshold_pass": True,
                            "trials": [
                                {
                                    "trial_index": 0,
                                    "threshold_pass": True,
                                    "total_requests": 20,
                                }
                            ],
                        }
                    ],
                },
                {"profile": "light_db", "status": "blocked", "stages": []},
            ],
            "overall_capacity": None,
        }

    monkeypatch.setattr(load_test_module, "execute", partial_execute)
    output = tmp_path / "partial-profile-report.json"
    exit_code = load_test_module.main(
        [
            "--execute-live",
            "--profiles",
            "health,light_db",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
            "--execution-run-nonce",
            RUN_NONCE,
            "--no-raw-samples",
            "--output",
            str(output),
        ]
    )
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert summary["status"] == "blocked"
    assert summary["blocked_profiles"] == ["light_db"]


def test_cli_consumes_nonce_before_constructing_raw_evidence_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = load_test_module.build_capacity_execution_plan(
        load_test_module.build_parser().parse_args(
            ["--profiles", "health", "--phases", "1", "--execution-run-nonce", RUN_NONCE]
        )
    )
    fake_approval = {
        "trusted": True,
        "plan_sha256": plan.plan_sha256,
        "run_nonce_sha256": hashlib.sha256(RUN_NONCE.encode("utf-8")).hexdigest(),
    }
    fake_consumed = {
        **fake_approval,
        "nonce_consumed": True,
        "consumption_status": "consumed_once",
    }
    events: list[str] = []

    def consume(*_args, **_kwargs):
        events.append("consume")
        return fake_consumed

    class FakeRawSampleWriter:
        def __init__(self, _path: Path):
            assert events == ["consume"]
            events.append("raw_writer")

        def close(self):
            events.append("raw_close")
            return {"record_count": 0}

    async def incomplete_execute(*_args, raw_writer=None, **_kwargs):
        assert isinstance(raw_writer, FakeRawSampleWriter)
        events.append("execute")
        return {
            "requested_live": True,
            "live_run": False,
            "network_observed": False,
            "network_requests_issued": 0,
            "pressure_completed": False,
            "operator_preflight": {"trusted": True},
            "profiles": [],
            "overall_capacity": None,
        }

    monkeypatch.setattr(load_test_module, "build_capacity_execution_plan", lambda _args: plan)
    monkeypatch.setattr(
        load_test_module,
        "verify_capacity_execution_approval",
        lambda *_args, **_kwargs: fake_approval,
    )
    monkeypatch.setattr(
        load_test_module,
        "is_verified_capacity_execution_approval",
        lambda value: value is fake_approval or value is fake_consumed,
    )
    monkeypatch.setattr(load_test_module, "consume_capacity_execution_approval", consume)
    monkeypatch.setattr(
        load_test_module,
        "is_consumed_capacity_execution_approval",
        lambda value: value is fake_consumed,
    )
    monkeypatch.setattr(load_test_module, "RawSampleWriter", FakeRawSampleWriter)
    monkeypatch.setattr(load_test_module, "execute", incomplete_execute)

    exit_code = load_test_module.main(
        [
            "--execute-live",
            "--profiles",
            "health",
            "--phases",
            "1",
            "--execution-run-nonce",
            RUN_NONCE,
            "--output",
            str(tmp_path / "ordered-report.json"),
            "--raw-output",
            str(tmp_path / "ordered.samples.ndjson"),
        ]
    )
    capsys.readouterr()

    assert exit_code == 2
    assert events == ["consume", "raw_writer", "execute", "raw_close"]
