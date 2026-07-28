from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.ops import cloud_preflight_remediation_bundle as bundle
from scripts.ops import cloud_remediation_authority as authority


ROOT = Path(__file__).resolve().parents[1]


def _preflight() -> dict:
    checks = [
        {"id": check_id, "pass": False, "blocking": True, "observed": False}
        for check_id in bundle.SUPPORTED_BLOCKERS
    ]
    checks.append(
        {"id": "health.worker_fleet_schema", "pass": False, "blocking": False, "observed": False}
    )
    return {
        "schema_version": 1,
        "report_type": bundle.PREFLIGHT_REPORT_TYPE,
        "mode": "remote_read_only_preflight",
        "secret_free": True,
        "generated_at": "2026-07-15T20:31:46Z",
        "decision": "no-go",
        "target": {"root": "/opt/viltrox-2.0", "ssh_target": "viltrox"},
        "candidate": {"expected_migration": "260_vkpi_dealer_map_management.sql"},
        "observed": {"backup": {"latest_name": "20260715T173218Z"}},
        "checks": checks,
        "blocking_check_ids": list(bundle.SUPPORTED_BLOCKERS),
        "safety_contract": {
            "execution_allowed": False,
            "future_mutation_authorization_implemented": False,
        },
    }


def _write_json(path: Path, value: object, mode: int = 0o600) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


def _args(**overrides: str | None) -> argparse.Namespace:
    values: dict[str, str | None] = {
        "project_root": str(ROOT),
        "candidate_release_id": None,
        "candidate_git_sha": None,
        "candidate_bundle_sha256": None,
        "age_recipient": None,
        "offhost_remote": None,
        "pending_migrations": None,
        "env_fingerprint_before": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _bound_args() -> argparse.Namespace:
    return _args(
        candidate_release_id="20260715T210000Z-deadbeef1234",
        candidate_git_sha="a" * 40,
        candidate_bundle_sha256="b" * 64,
        age_recipient="age1" + "a" * 58,
        offhost_remote="backup:vkpi/releases",
        pending_migrations="260_vkpi_dealer_map_management.sql",
        env_fingerprint_before="c" * 64,
    )


def _build(tmp_path: Path, args: argparse.Namespace | None = None) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    preflight = tmp_path / "preflight.json"
    _write_json(preflight, _preflight())
    return bundle.build_plan(preflight, args or _args())


def test_plan_covers_exact_nine_blockers_and_is_non_executable(tmp_path: Path) -> None:
    plan = _build(tmp_path)
    verification = bundle.verify_plan(plan)

    assert verification["valid"] is True
    assert plan["blockers"] == list(bundle.SUPPORTED_BLOCKERS)
    assert len(plan["steps"]) == 9
    assert {step["check_id"] for step in plan["steps"]} == set(bundle.SUPPORTED_BLOCKERS)
    assert plan["execution_ready_for_authorization"] is False
    assert plan["execution_allowed"] is False
    assert plan["mutation_interface_present"] is False
    assert plan["security_contract"]["remote_writes_performed"] == 0
    assert plan["missing_bindings"] == [
        "candidate_release_id",
        "candidate_git_sha",
        "candidate_bundle_sha256",
        "age_recipient",
        "offhost_remote",
        "pending_migrations",
        "staging_database",
        "env_fingerprint_before",
    ]
    encoded = json.dumps(plan).lower()
    assert "bearer " not in encoded
    assert "://user:" not in encoded


def test_fully_bound_plan_is_deterministic_and_only_ready_for_authorization(
    tmp_path: Path,
) -> None:
    first = _build(tmp_path / "first", _bound_args())
    second = _build(tmp_path / "second", _bound_args())

    assert first["plan_sha256"] == second["plan_sha256"]
    assert first["execution_ready_for_authorization"] is True
    assert first["execution_allowed"] is False
    assert bundle.verify_plan(first)["valid"] is True
    pairs = {(item["scope"], item["role"]) for item in first["authorization_templates"]}
    assert pairs == {
        (scope, role)
        for scope in bundle.MUTATION_SCOPES
        for role in bundle.APPROVAL_ROLES
    }


def test_atomic_helper_remediation_carries_split_cli_import_closure(
    tmp_path: Path,
) -> None:
    plan = _build(tmp_path, _bound_args())
    helper = next(
        step
        for step in plan["steps"]
        if step["check_id"] == "release.atomic_helper_present"
    )
    relative = "scripts/ops/atomic_release_cli.py"

    assert plan["source_artifact_sha256"][relative]
    assert any(relative in command for command in helper["preconditions"])
    assert any(relative in command for command in helper["apply_commands"])
    assert any(relative in command for command in helper["rollback_commands"])
    assert any(relative in command for command in helper["verification_commands"])
    assert any(
        "atomic_release_layout.py --help" in command
        for command in helper["verification_commands"]
    )


def test_build_rejects_check_drift_instead_of_guessing(tmp_path: Path) -> None:
    preflight = _preflight()
    preflight["blocking_check_ids"] = preflight["blocking_check_ids"][:-1]
    path = tmp_path / "preflight.json"
    _write_json(path, preflight)

    with pytest.raises(bundle.RemediationError, match="declared blocker ids disagree"):
        bundle.build_plan(path, _args())


def test_build_rejects_secret_shaped_preflight_value(tmp_path: Path) -> None:
    preflight = _preflight()
    preflight["unexpected"] = "postgres://user:raw-password@example.invalid/db"
    path = tmp_path / "preflight.json"
    _write_json(path, preflight)

    with pytest.raises(bundle.RemediationError, match="secret-shaped"):
        bundle.build_plan(path, _args())


def _public_key(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _signed_approval(
    *,
    plan: dict,
    role: str,
    key_id: str,
    private: Ed25519PrivateKey,
    scope: str,
    nonce: str,
    issued: datetime,
) -> tuple[dict, bytes]:
    payload = {
        "schema_version": authority.APPROVAL_SCHEMA,
        "algorithm": "Ed25519",
        "role": role,
        "scope": scope,
        "key_id": key_id,
        "plan_sha256": plan["plan_sha256"],
        "preflight_sha256": plan["source_preflight"]["sha256"],
        "target": plan["target"],
        "immutable_bindings": plan["immutable_bindings"],
        "nonce_sha256": authority.hashlib.sha256(nonce.encode()).hexdigest(),
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": (issued + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    }
    payload["signature_base64"] = base64.b64encode(
        private.sign(authority._approval_message(payload))
    ).decode("ascii")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return payload, raw


def test_dual_authority_is_required_and_consumed_once_without_signature_leak(
    tmp_path: Path,
) -> None:
    plan = _build(tmp_path / "plan", _bound_args())
    release_key = Ed25519PrivateKey.generate()
    operator_key = Ed25519PrivateKey.generate()
    roots = {
        "schema_version": authority.TRUST_ROOTS_SCHEMA,
        "roles": {
            "release_authority": {"release-key": _public_key(release_key)},
            "operator_authority": {"operator-key": _public_key(operator_key)},
        },
    }
    nonce = "release-20260715-unique-nonce"
    evaluated = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)
    approvals = [
        _signed_approval(
            plan=plan,
            role="release_authority",
            key_id="release-key",
            private=release_key,
            scope="apply",
            nonce=nonce,
            issued=evaluated,
        ),
        _signed_approval(
            plan=plan,
            role="operator_authority",
            key_id="operator-key",
            private=operator_key,
            scope="apply",
            nonce=nonce,
            issued=evaluated,
        ),
    ]

    verified = authority.verify_authority(
        plan=plan,
        scope="apply",
        nonce=nonce,
        approvals=approvals,
        trust_roots=roots,
        evaluated_at=evaluated,
    )
    assert verified["trusted"] is True
    assert verified["distinct_signers"] is True
    assert verified["roles_verified"] == ["operator_authority", "release_authority"]

    ledger = tmp_path / "ledger"
    consumed = authority.consume_authority(verified, ledger_dir=ledger, consumed_at=evaluated)
    replay = authority.consume_authority(verified, ledger_dir=ledger, consumed_at=evaluated)
    assert consumed["status"] == "dual_authority_consumed_once"
    assert consumed["nonce_consumed"] is True
    assert replay["trusted"] is False
    assert replay["failure_reasons"] == ["authority_nonce_already_consumed"]
    ledger_text = next(ledger.iterdir()).read_text(encoding="utf-8")
    assert approvals[0][0]["signature_base64"] not in ledger_text
    assert approvals[1][0]["signature_base64"] not in ledger_text
    assert nonce not in ledger_text


def test_unbound_plan_and_duplicate_role_fail_closed(tmp_path: Path) -> None:
    unbound = _build(tmp_path / "unbound")
    key = Ed25519PrivateKey.generate()
    roots = {
        "roles": {
            "release_authority": {"shared-key": _public_key(key)},
            "operator_authority": {"shared-key": _public_key(key)},
        }
    }
    nonce = "release-20260715-unique-nonce"
    evaluated = datetime(2026, 7, 15, 21, 0, tzinfo=timezone.utc)
    approval = _signed_approval(
        plan=unbound,
        role="release_authority",
        key_id="shared-key",
        private=key,
        scope="apply",
        nonce=nonce,
        issued=evaluated,
    )

    result = authority.verify_authority(
        plan=unbound,
        scope="apply",
        nonce=nonce,
        approvals=[approval, approval],
        trust_roots=roots,
        evaluated_at=evaluated,
    )
    assert result["trusted"] is False
    assert "plan_unbound_or_missing_sources" in result["failure_reasons"]
    assert "authority_roles_or_signers_not_distinct" in result["failure_reasons"]


def test_cli_build_writes_owner_private_artifact(tmp_path: Path) -> None:
    preflight = tmp_path / "preflight.json"
    output = tmp_path / "plan.json"
    _write_json(preflight, _preflight())

    rc = bundle.main(
        [
            "build",
            "--preflight",
            str(preflight),
            "--output",
            str(output),
            "--project-root",
            str(ROOT),
        ]
    )
    assert rc == 0
    assert stat_mode(output) == 0o600
    assert bundle.verify_plan(json.loads(output.read_text()))["valid"] is True


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
