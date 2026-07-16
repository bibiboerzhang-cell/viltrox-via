from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
from types import MappingProxyType

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import pytest

from scripts.ops import audit_migration_243_244_release_gate as gate


SOURCE_ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)
BACKUP_TIME = NOW - timedelta(hours=1)
STAMP = BACKUP_TIME.strftime("%Y%m%dT%H%M%SZ")
BUNDLE_ID = "vkpi-migration-244-20260713t230000z"
HEAD = "1" * 40
PRODUCER_KEY_ID = "vkpi-release-test-only"
RUNNER_KEY_ID = "vkpi-runner-test-only"
PRODUCER_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
RUNNER_PRIVATE = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))


def _public(private: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")


PRODUCER_KEYS = {PRODUCER_KEY_ID: _public(PRODUCER_PRIVATE)}
RUNNER_KEYS = {RUNNER_KEY_ID: _public(RUNNER_PRIVATE)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_private(path: Path, data: bytes | str, *, mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    path.chmod(0o600)
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def _attest(payload: dict, *, signed_at: datetime) -> dict:
    unsigned = deepcopy(payload)
    unsigned.pop("attestation", None)
    attestation = {
        "schema_version": 1,
        "attestation_type": gate.ATTESTATION_TYPE,
        "algorithm": "Ed25519",
        "key_id": PRODUCER_KEY_ID,
        "signed_at": gate._iso_z(signed_at),
        "payload_sha256": gate._json_sha256(unsigned),
    }
    attestation["signature"] = base64.b64encode(
        PRODUCER_PRIVATE.sign(gate._attestation_message(attestation))
    ).decode("ascii")
    unsigned["attestation"] = attestation
    return unsigned


def _runner_attestation(
    *,
    argv: list[str],
    dump_sha256: str,
    stdout: str,
    stderr: str,
    signed_at: datetime,
) -> dict:
    runner = {
        "schema_version": 1,
        "attestation_type": gate.RUNNER_ATTESTATION_TYPE,
        "runner_class": "offline_test_fixture",
        "algorithm": "Ed25519",
        "key_id": RUNNER_KEY_ID,
        "signed_at": gate._iso_z(signed_at),
        "binary_sha256": "a" * 64,
        "binary_version": "pg_restore (PostgreSQL) 17.5",
        "argv": argv,
        "dump_sha256": dump_sha256,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "critical_toc_objects": list(gate.REQUIRED_TOC_OBJECTS),
    }
    runner["signature"] = base64.b64encode(
        RUNNER_PRIVATE.sign(gate._runner_message(runner))
    ).decode("ascii")
    return runner


def _write_signed_json(
    path: Path, payload: dict, *, signed_at: datetime, mtime: datetime
) -> dict:
    signed = _attest(payload, signed_at=signed_at)
    _write_private(
        path,
        json.dumps(signed, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        mtime=mtime,
    )
    return signed


def _descriptor(evidence_path: Path, receipt_path: Path, label: str) -> dict[str, str]:
    return {
        "label": label,
        "path": str(receipt_path.relative_to(evidence_path.parent)),
        "sha256": _sha(receipt_path),
    }


def _anchors() -> dict[str, int]:
    return {
        table: index for index, table in enumerate(gate.REQUIRED_ROW_ANCHORS, start=1)
    }


def _state(keys: list[str], content: str) -> dict:
    return {
        "version_keys": keys,
        "version_keys_sha256": gate._json_sha256(keys),
        "content_sha256": content,
    }


def _receipt_payload(
    *,
    label: str,
    details: dict,
    started_at: datetime,
    completed_at: datetime,
    manifest_sha: str,
    execution_mode: str = "controlled_run",
) -> dict:
    return {
        "schema_version": 1,
        "receipt_type": gate.RECEIPT_TYPE,
        "label": label,
        "status": "passed",
        "execution_mode": execution_mode,
        "bundle_id": BUNDLE_ID,
        "repository_head": HEAD,
        "source_manifest_sha256": manifest_sha,
        "started_at": gate._iso_z(started_at),
        "completed_at": gate._iso_z(completed_at),
        "details": details,
    }


def _make_receipt(
    *,
    path: Path,
    label: str,
    details: dict,
    started_at: datetime,
    completed_at: datetime,
    finalized_at: datetime,
    manifest_sha: str,
) -> dict:
    return _write_signed_json(
        path,
        _receipt_payload(
            label=label,
            details=details,
            started_at=started_at,
            completed_at=completed_at,
            manifest_sha=manifest_sha,
        ),
        signed_at=completed_at + timedelta(seconds=10),
        mtime=finalized_at,
    )


def _make_repo(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "repo"
    backup_dir = root / "runtime" / "db-backups"
    ops_dir = root / "runtime" / "ops"
    migrations = root / "migrations"
    migrations.mkdir(parents=True)
    for migration in (gate.PRE_MIGRATION, gate.UP_MIGRATION, gate.DOWN_MIGRATION):
        shutil.copy2(SOURCE_ROOT / migration, root / migration)

    _write_private(
        root / ".git/HEAD",
        "ref: refs/heads/test\n",
        mtime=BACKUP_TIME - timedelta(minutes=20),
    )
    _write_private(
        root / ".git/refs/heads/test",
        HEAD + "\n",
        mtime=BACKUP_TIME - timedelta(minutes=20),
    )

    source_manifest_path = ops_dir / "source-manifest.json"
    source_payload = {
        "schema_version": 1,
        "manifest_type": gate.SOURCE_MANIFEST_TYPE,
        "status": "approved",
        "approval_id": "vkpi-source-approval-20260713",
        "approved_at": gate._iso_z(BACKUP_TIME - timedelta(minutes=15)),
        "repository_head": HEAD,
        "files": {
            str(path): _sha(root / path)
            for path in (gate.PRE_MIGRATION, gate.UP_MIGRATION, gate.DOWN_MIGRATION)
        },
    }
    source_signed = _write_signed_json(
        source_manifest_path,
        source_payload,
        signed_at=BACKUP_TIME - timedelta(minutes=14),
        mtime=BACKUP_TIME - timedelta(minutes=13),
    )
    manifest_sha = _sha(source_manifest_path)

    pre_state = _state([gate.EXPECTED_PRE_MIGRATION], "1" * 64)
    post_state = _state(
        [gate.EXPECTED_PRE_MIGRATION, gate.EXPECTED_POST_MIGRATION], "2" * 64
    )
    rollback_state = deepcopy(pre_state)
    dump = backup_dir / f"vkpi-{STAMP}.dump"
    _write_private(dump, b"PGDMPfixture-custom-archive", mtime=BACKUP_TIME)
    dump_sha = _sha(dump)
    _write_private(
        Path(str(dump) + ".sha256"),
        f"{dump_sha}  {dump.name}\n",
        mtime=BACKUP_TIME + timedelta(minutes=3),
    )
    toc_output = (
        ";\n"
        "; Archive created at 2026-07-13 23:00:00 UTC\n"
        ";     TOC Entries: 4\n"
        ";\n"
        "1; 0 0 TABLE public schema_migrations owner\n"
        "2; 0 0 TABLE DATA public schema_migrations owner\n"
        "3; 0 0 TABLE public vkpi_events owner\n"
        "4; 0 0 TABLE DATA public vkpi_events owner\n"
    )
    archive_receipt_path = backup_dir / "receipts/pg_restore_list.json"
    archive_receipt = _make_receipt(
        path=archive_receipt_path,
        label="pg_restore_list",
        details={
            "exit_code": 0,
            "archive_name": dump.name,
            "archive_sha256": dump_sha,
            "toc_output": toc_output,
            "stderr_output": "",
            "toc_entries": 4,
            "runner_attestation": _runner_attestation(
                argv=["pg_restore", "--list", dump.name],
                dump_sha256=dump_sha,
                stdout=toc_output,
                stderr="",
                signed_at=BACKUP_TIME + timedelta(minutes=2),
            ),
        },
        started_at=BACKUP_TIME + timedelta(minutes=1),
        completed_at=BACKUP_TIME + timedelta(minutes=2),
        finalized_at=BACKUP_TIME + timedelta(minutes=3),
        manifest_sha=manifest_sha,
    )
    metadata_path = backup_dir / f"vkpi-{STAMP}.meta.json"
    metadata_signed = _write_signed_json(
        metadata_path,
        {
            "schema_version": 1,
            "bundle_id": BUNDLE_ID,
            "stamp": STAMP,
            "migration_max": gate.EXPECTED_PRE_MIGRATION,
            "migration_max_source": "schema_migrations",
            "dump": dump.name,
            "dump_bytes": dump.stat().st_size,
            "dump_sha256": dump_sha,
            "archive_verified": True,
            "repository_head": HEAD,
            "source_manifest_sha256": manifest_sha,
            "migration_state": pre_state,
            "archive_list_receipt": _descriptor(
                metadata_path, archive_receipt_path, "pg_restore_list"
            ),
        },
        signed_at=BACKUP_TIME + timedelta(minutes=4),
        mtime=BACKUP_TIME + timedelta(minutes=5),
    )

    anchors = _anchors()
    readback = {
        "migration_max": gate.EXPECTED_PRE_MIGRATION,
        "migration_243_marker_count": 1,
        "migration_244_marker_count": 0,
    }
    restore_path = ops_dir / "restore.json"
    restore_receipt_dir = ops_dir / "restore-receipts"
    pg_restore_receipt_path = restore_receipt_dir / "pg_restore_execute.json"
    row_receipt_path = restore_receipt_dir / "row_anchor_readback.json"
    _make_receipt(
        path=pg_restore_receipt_path,
        label="pg_restore_execute",
        details={
            "exit_code": 0,
            "archive_name": dump.name,
            "archive_sha256": dump_sha,
            "target_class": "isolated_non_live",
            "stdout_output": "restore completed\n",
            "stderr_output": "",
            "runner_attestation": _runner_attestation(
                argv=["pg_restore", "--dbname=isolated_non_live", dump.name],
                dump_sha256=dump_sha,
                stdout="restore completed\n",
                stderr="",
                signed_at=BACKUP_TIME + timedelta(minutes=15),
            ),
        },
        started_at=BACKUP_TIME + timedelta(minutes=10),
        completed_at=BACKUP_TIME + timedelta(minutes=15),
        finalized_at=BACKUP_TIME + timedelta(minutes=16),
        manifest_sha=manifest_sha,
    )
    _make_receipt(
        path=row_receipt_path,
        label="row_anchor_readback",
        details={
            "readback_sha256": gate._json_sha256(readback),
            "source_anchors_sha256": gate._json_sha256(anchors),
            "restored_anchors_sha256": gate._json_sha256(anchors),
            "migration_state_sha256": gate._json_sha256(pre_state),
        },
        started_at=BACKUP_TIME + timedelta(minutes=16),
        completed_at=BACKUP_TIME + timedelta(minutes=18),
        finalized_at=BACKUP_TIME + timedelta(minutes=19),
        manifest_sha=manifest_sha,
    )
    restore_signed = _write_signed_json(
        restore_path,
        {
            "schema_version": 1,
            "evidence_type": "vkpi_migration_243_isolated_restore",
            "evidence_mode": "controlled_run",
            "bundle_id": BUNDLE_ID,
            "status": "passed",
            "isolated_database": True,
            "target_database_not_live": True,
            "network_exposed": False,
            "repository_head": HEAD,
            "source_manifest_sha256": manifest_sha,
            "source_dump": {
                "name": dump.name,
                "sha256": dump_sha,
                "migration_max": gate.EXPECTED_PRE_MIGRATION,
            },
            "backup_metadata_sha256": _sha(metadata_path),
            "started_at": gate._iso_z(BACKUP_TIME + timedelta(minutes=10)),
            "completed_at": gate._iso_z(BACKUP_TIME + timedelta(minutes=20)),
            "pg_restore_exit_code": 0,
            "restore_errors": 0,
            "readback": readback,
            "migration_state": pre_state,
            "source_row_anchors": anchors,
            "restored_row_anchors": deepcopy(anchors),
            "receipts": [
                _descriptor(restore_path, pg_restore_receipt_path, "pg_restore_execute"),
                _descriptor(restore_path, row_receipt_path, "row_anchor_readback"),
            ],
        },
        signed_at=BACKUP_TIME + timedelta(minutes=21),
        mtime=BACKUP_TIME + timedelta(minutes=22),
    )

    post_anchors = deepcopy(anchors)
    post_anchors["schema_migrations"] += 1
    apply_checks = {name: True for name in gate.REQUIRED_POST_APPLY_CHECKS}
    rollback_checks = {name: True for name in gate.REQUIRED_POST_ROLLBACK_CHECKS}
    rehearsal_path = ops_dir / "rehearsal.json"
    rehearsal_receipt_dir = ops_dir / "rehearsal-receipts"
    receipt_specs = {
        "migration_244_up": (
            {
                "migration_file": str(gate.UP_MIGRATION),
                "migration_sha256": _sha(root / gate.UP_MIGRATION),
                "exit_code": 0,
                "migration_before": gate.EXPECTED_PRE_MIGRATION,
                "migration_after": gate.EXPECTED_POST_MIGRATION,
                "migration_state_sha256": gate._json_sha256(post_state),
            },
            25,
            28,
            29,
        ),
        "migration_244_post_apply": (
            {
                "checks_sha256": gate._json_sha256(apply_checks),
                "anchors_sha256": gate._json_sha256(post_anchors),
                "migration_max": gate.EXPECTED_POST_MIGRATION,
                "migration_state_sha256": gate._json_sha256(post_state),
            },
            29,
            31,
            32,
        ),
        "migration_244_down": (
            {
                "migration_file": str(gate.DOWN_MIGRATION),
                "migration_sha256": _sha(root / gate.DOWN_MIGRATION),
                "exit_code": 0,
                "migration_before": gate.EXPECTED_POST_MIGRATION,
                "migration_after": gate.EXPECTED_PRE_MIGRATION,
                "migration_state_sha256": gate._json_sha256(rollback_state),
            },
            32,
            35,
            36,
        ),
        "migration_244_post_rollback": (
            {
                "checks_sha256": gate._json_sha256(rollback_checks),
                "anchors_sha256": gate._json_sha256(anchors),
                "migration_max": gate.EXPECTED_PRE_MIGRATION,
                "migration_state_sha256": gate._json_sha256(rollback_state),
            },
            36,
            39,
            40,
        ),
    }
    descriptors = []
    receipt_paths: dict[str, Path] = {}
    for label, (details, start, end, final) in receipt_specs.items():
        path = rehearsal_receipt_dir / f"{label}.json"
        receipt_paths[label] = path
        _make_receipt(
            path=path,
            label=label,
            details=details,
            started_at=BACKUP_TIME + timedelta(minutes=start),
            completed_at=BACKUP_TIME + timedelta(minutes=end),
            finalized_at=BACKUP_TIME + timedelta(minutes=final),
            manifest_sha=manifest_sha,
        )
        descriptors.append(_descriptor(rehearsal_path, path, label))
    rehearsal_signed = _write_signed_json(
        rehearsal_path,
        {
            "schema_version": 1,
            "evidence_type": "vkpi_migration_244_forward_rollback_rehearsal",
            "evidence_mode": "controlled_run",
            "bundle_id": BUNDLE_ID,
            "status": "passed",
            "isolated_database": True,
            "target_database_not_live": True,
            "network_exposed": False,
            "repository_head": HEAD,
            "source_manifest_sha256": manifest_sha,
            "source_dump": {
                "name": dump.name,
                "sha256": dump_sha,
                "migration_max": gate.EXPECTED_PRE_MIGRATION,
            },
            "restore_evidence_sha256": _sha(restore_path),
            "started_at": gate._iso_z(BACKUP_TIME + timedelta(minutes=25)),
            "completed_at": gate._iso_z(BACKUP_TIME + timedelta(minutes=40)),
            "forward": {
                "migration_file": str(gate.UP_MIGRATION),
                "sha256": _sha(root / gate.UP_MIGRATION),
                "exit_code": 0,
                "migration_244_marker_count": 1,
                "migration_max": gate.EXPECTED_POST_MIGRATION,
                "duration_ms": 125,
            },
            "rollback": {
                "strategy": "down_sql",
                "migration_file": str(gate.DOWN_MIGRATION),
                "sha256": _sha(root / gate.DOWN_MIGRATION),
                "exit_code": 0,
                "migration_244_marker_count": 0,
                "migration_max": gate.EXPECTED_PRE_MIGRATION,
                "duration_ms": 87,
            },
            "rollback_preconditions": {
                "non_legacy_workspace_rows": 0,
                "dealer_identity_alias_rows": 0,
            },
            "post_apply_checks": apply_checks,
            "post_rollback_checks": rollback_checks,
            "migration_states": {
                "pre": pre_state,
                "post_forward": post_state,
                "post_rollback": rollback_state,
            },
            "pre_row_anchors": anchors,
            "post_forward_row_anchors": post_anchors,
            "post_rollback_row_anchors": deepcopy(anchors),
            "receipts": descriptors,
        },
        signed_at=BACKUP_TIME + timedelta(minutes=41),
        mtime=BACKUP_TIME + timedelta(minutes=42),
    )
    return {
        "root": root,
        "backup_dir": backup_dir,
        "dump": dump,
        "metadata": metadata_path,
        "metadata_payload": metadata_signed,
        "archive_receipt_path": archive_receipt_path,
        "archive_receipt_payload": archive_receipt,
        "source_manifest_path": source_manifest_path,
        "source_manifest_payload": source_signed,
        "restore_path": restore_path,
        "restore_payload": restore_signed,
        "rehearsal_path": rehearsal_path,
        "rehearsal_payload": rehearsal_signed,
        "receipt_paths": receipt_paths,
    }


def _audit(bundle: dict[str, object], **kwargs: object) -> dict:
    return gate.audit_replay(
        repo_root=bundle["root"],
        backup_dir=bundle["backup_dir"],
        source_manifest=bundle["source_manifest_path"],
        restore_evidence=bundle["restore_path"],
        rehearsal_evidence=bundle["rehearsal_path"],
        now=kwargs.pop("now", NOW),
        max_age_hours=kwargs.pop("max_age_hours", 24),
        producer_public_keys=kwargs.pop("producer_public_keys", PRODUCER_KEYS),
        runner_public_keys=kwargs.pop("runner_public_keys", RUNNER_KEYS),
        **kwargs,
    )


def _failed_ids(report: dict) -> set[str]:
    return {item["id"] for item in report["checks"] if item["status"] == "failed"}


def _resign(path: Path, payload: dict, *, signed_at: datetime, mtime: datetime) -> None:
    payload = deepcopy(payload)
    payload.pop("attestation", None)
    _write_signed_json(path, payload, signed_at=signed_at, mtime=mtime)


def test_default_trust_is_immutable_empty_and_offline_gate_never_authorizes(
    tmp_path: Path,
) -> None:
    assert isinstance(gate.TRUSTED_PRODUCER_PUBLIC_KEYS, MappingProxyType)
    assert isinstance(gate.TRUSTED_RUNNER_PUBLIC_KEYS, MappingProxyType)
    assert dict(gate.TRUSTED_PRODUCER_PUBLIC_KEYS) == {}
    assert dict(gate.TRUSTED_RUNNER_PUBLIC_KEYS) == {}

    report = _audit(_make_repo(tmp_path))
    assert report["decision"]["trusted_producer_attestations"] is True
    assert report["decision"]["trusted_real_runner_attestations"] is False
    assert report["decision"]["evidence_bundle_complete"] is False
    assert report["decision"]["safe_to_apply"] is False
    assert report["decision"]["safe_to_start_separately_authorized_canary"] is False
    assert report["decision"]["migration_execution_authorized_by_this_audit"] is False
    assert report["policy"]["authorization_controller_present"] is False
    assert {
        "backup.receipt.pg_restore_list.details.runner.real_controlled",
        "restore.receipt.pg_restore_execute.details.runner.real_controlled",
    }.issubset(_failed_ids(report))


def test_even_complete_evidence_maps_to_advisory_false_safe_flags() -> None:
    decision = gate._advisory_decision(
        evidence_complete=True,
        producers_trusted=True,
        runners_trusted=True,
        replay_mode=False,
        checks=gate.Checks(),
    )
    assert decision["gate_status"] == "advisory_complete"
    assert decision["claim_status"] == "advisory_evidence_complete"
    assert decision["safe_to_apply"] is False
    assert decision["safe_to_start_separately_authorized_canary"] is False
    assert decision["migration_execution_authorized_by_this_audit"] is False


def test_production_entry_has_no_clock_key_or_replay_injection(tmp_path: Path) -> None:
    names = set(inspect.signature(gate.audit_gate).parameters)
    assert {"now", "producer_public_keys", "runner_public_keys", "replay_mode"}.isdisjoint(names)
    bundle = _make_repo(tmp_path)
    with pytest.raises(TypeError):
        gate.audit_gate(
            repo_root=bundle["root"],
            backup_dir=bundle["backup_dir"],
            source_manifest=bundle["source_manifest_path"],
            restore_evidence=bundle["restore_path"],
            rehearsal_evidence=bundle["rehearsal_path"],
            now=NOW,
        )


def test_unallowlisted_producer_and_historical_replay_fail_closed(tmp_path: Path) -> None:
    report = _audit(_make_repo(tmp_path), producer_public_keys={})
    assert report["decision"]["gate_status"] == "failed"
    assert report["decision"]["safe_to_apply"] is False
    assert any(item.endswith("attestation.key_allowlisted") for item in _failed_ids(report))


def test_backup_inventory_does_not_follow_metadata_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    backup_dir = root / "runtime/db-backups"
    backup_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"migration_max": gate.EXPECTED_PRE_MIGRATION}))
    (backup_dir / f"vkpi-{STAMP}.meta.json").symlink_to(outside)

    inventory, exact = gate._candidate_inventory(backup_dir)
    assert exact == []
    assert inventory == [
        {
            "metadata": f"vkpi-{STAMP}.meta.json",
            "usable_json": False,
            "rejected_reason": "not_regular_file",
        }
    ]


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("restore_path", "restore.evidence.file"),
        ("source_manifest_path", "source.manifest.file"),
        ("dump", "backup.dump_file"),
    ],
)
def test_symlinked_decision_artifacts_are_rejected(
    tmp_path: Path, target: str, expected: str
) -> None:
    bundle = _make_repo(tmp_path)
    path = bundle[target]
    real = path.with_name(path.name + ".real")
    path.rename(real)
    path.symlink_to(real)
    assert expected in _failed_ids(_audit(bundle))


def test_hardlinked_evidence_is_rejected(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path)
    path = bundle["restore_path"]
    outside = tmp_path / "outside-restore.json"
    path.rename(outside)
    os.link(outside, path)
    report = _audit(bundle)
    assert "restore.evidence.file" in _failed_ids(report)
    assert report["decision"]["safe_to_apply"] is False


def test_duplicate_json_key_and_nonfinite_numbers_are_rejected(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path)
    path = bundle["restore_path"]
    raw = path.read_text()
    raw = raw.replace('  "status": "passed",', '  "status": "failed",\n  "status": "passed",', 1)
    _write_private(path, raw, mtime=BACKUP_TIME + timedelta(minutes=22))
    assert "restore.evidence.strict_json" in _failed_ids(_audit(bundle))
    with pytest.raises(ValueError, match="Out of range|compliant"):
        gate._canonical_json({"bad": float("nan")})


@pytest.mark.parametrize("field", ["exit", "marker", "precondition", "anchor"])
def test_booleans_never_satisfy_numeric_contracts(tmp_path: Path, field: str) -> None:
    bundle = _make_repo(tmp_path)
    payload = deepcopy(bundle["rehearsal_payload"])
    if field == "exit":
        payload["forward"]["exit_code"] = True
        expected = "rehearsal.exact_actions"
    elif field == "marker":
        payload["forward"]["migration_244_marker_count"] = True
        expected = "rehearsal.exact_actions"
    elif field == "precondition":
        payload["rollback_preconditions"]["non_legacy_workspace_rows"] = False
        expected = "rehearsal.rollback_preconditions.exact_zero"
    else:
        payload["pre_row_anchors"]["schema_migrations"] = True
        expected = "rehearsal.pre_anchors.shape"
    _resign(
        bundle["rehearsal_path"],
        payload,
        signed_at=BACKUP_TIME + timedelta(minutes=41),
        mtime=BACKUP_TIME + timedelta(minutes=42),
    )
    assert expected in _failed_ids(_audit(bundle))


def test_pg_bigint_overflow_and_pre_plus_one_are_fail_closed(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path)
    payload = deepcopy(bundle["rehearsal_payload"])
    payload["pre_row_anchors"]["schema_migrations"] = (1 << 63) - 1
    payload["post_forward_row_anchors"]["schema_migrations"] = 1 << 63
    payload["post_rollback_row_anchors"]["schema_migrations"] = (1 << 63) - 1
    _resign(
        bundle["rehearsal_path"],
        payload,
        signed_at=BACKUP_TIME + timedelta(minutes=41),
        mtime=BACKUP_TIME + timedelta(minutes=42),
    )
    failed = _failed_ids(_audit(bundle))
    assert "rehearsal.post_forward_anchors.shape" in failed
    assert "rehearsal.post_forward_anchor_semantics" in failed


def test_migration_key_set_and_content_digest_contract_is_required(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path)
    payload = deepcopy(bundle["rehearsal_payload"])
    payload["migration_states"]["post_forward"]["content_sha256"] = "1" * 64
    _resign(
        bundle["rehearsal_path"],
        payload,
        signed_at=BACKUP_TIME + timedelta(minutes=41),
        mtime=BACKUP_TIME + timedelta(minutes=42),
    )
    assert "rehearsal.migration_key_transition" in _failed_ids(_audit(bundle))


def test_toc_must_parse_and_contain_critical_objects(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path)
    payload = deepcopy(bundle["archive_receipt_payload"])
    payload["details"]["toc_output"] = (
        "; Archive created at fake\n;     TOC Entries: 1\n"
        "1; 0 0 TOTALLY_FAKE public object owner\n"
    )
    payload["details"]["toc_entries"] = 1
    _resign(
        bundle["archive_receipt_path"],
        payload,
        signed_at=BACKUP_TIME + timedelta(minutes=2, seconds=10),
        mtime=BACKUP_TIME + timedelta(minutes=3),
    )
    failed = _failed_ids(_audit(bundle))
    assert "backup.receipt.pg_restore_list.details.critical_objects" in failed
    assert "backup.receipt.pg_restore_list.details.runner.io_binding" in failed


def test_replay_bundle_in_another_root_still_never_authorizes(tmp_path: Path) -> None:
    bundle = _make_repo(tmp_path / "first")
    clone_root = tmp_path / "clone/repo"
    shutil.copytree(bundle["root"], clone_root, copy_function=shutil.copy2)
    clone = {
        **bundle,
        "root": clone_root,
        "backup_dir": clone_root / "runtime/db-backups",
        "source_manifest_path": clone_root / "runtime/ops/source-manifest.json",
        "restore_path": clone_root / "runtime/ops/restore.json",
        "rehearsal_path": clone_root / "runtime/ops/rehearsal.json",
    }
    report = _audit(clone)
    assert report["decision"]["safe_to_apply"] is False
    assert report["decision"]["safe_to_start_separately_authorized_canary"] is False
    assert report["policy"]["live_target_binding_verified"] is False
    assert report["policy"]["challenge_consumption_ledger_verified"] is False


def test_age_cap_cli_replay_and_no_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _make_repo(tmp_path)
    with pytest.raises(ValueError, match="24-hour"):
        _audit(bundle, max_age_hours=24.01)
    output = bundle["root"] / "runtime/ops/should-not-exist.json"
    result = gate.main(
        [
            "--repo-root",
            str(bundle["root"]),
            "--backup-dir",
            "runtime/db-backups",
            "--source-manifest",
            "runtime/ops/source-manifest.json",
            "--restore-evidence",
            "runtime/ops/restore.json",
            "--rehearsal-evidence",
            "runtime/ops/rehearsal.json",
            "--now",
            gate._iso_z(NOW),
            "--output",
            str(output),
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 2
    assert report["decision"]["safe_to_apply"] is False
    assert not output.exists()


def test_gate_modules_are_below_line_guard_and_have_no_execution_clients() -> None:
    paths = [
        SOURCE_ROOT / "scripts/ops/audit_migration_243_244_release_gate.py",
        *(SOURCE_ROOT / "scripts/ops").glob("migration_gate_*.py"),
    ]
    assert paths
    for path in paths:
        assert len(path.read_text().splitlines()) < 1000, path
    source = "\n".join(path.read_text() for path in paths)
    for forbidden in (
        "import socket",
        "import subprocess",
        "import psycopg",
        "import requests",
        "DATABASE_URL",
        "Ed25519PrivateKey",
    ):
        assert forbidden not in source
