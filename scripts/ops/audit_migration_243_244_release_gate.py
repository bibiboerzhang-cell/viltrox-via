#!/usr/bin/env python3
"""Offline advisory evidence audit for V-KPI migration 243 -> 244.

This program validates local evidence bytes only.  It cannot authorize a
canary or migration: authorization requires a future external controller that
issues a one-time challenge, binds it to the live target, and atomically
consumes it in a durable ledger.  None of those controls are simulated here.
"""

from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any, Mapping

try:
    from . import migration_gate_backup as backup_gate
    from . import migration_gate_contract as contract
    from . import migration_gate_evidence as evidence_gate
    from . import migration_gate_io as gate_io
    from .migration_gate_contract import Checks
except ImportError:  # direct ``python scripts/ops/...py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.ops import migration_gate_backup as backup_gate
    from scripts.ops import migration_gate_contract as contract
    from scripts.ops import migration_gate_evidence as evidence_gate
    from scripts.ops import migration_gate_io as gate_io
    from scripts.ops.migration_gate_contract import Checks


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_MANIFEST = Path(
    "runtime/ops/vkpi-migration-244-approved-source-manifest.json"
)
DEFAULT_RESTORE_EVIDENCE = Path(
    "runtime/ops/vkpi-migration-243-isolated-restore-evidence.json"
)
DEFAULT_REHEARSAL_EVIDENCE = Path(
    "runtime/ops/vkpi-migration-244-rehearsal-evidence.json"
)

# Compatibility exports for existing tooling and tests.  The authoritative
# values remain in the focused contract module.
EXPECTED_PRE_MIGRATION = contract.EXPECTED_PRE_MIGRATION
EXPECTED_POST_MIGRATION = contract.EXPECTED_POST_MIGRATION
PRE_MIGRATION = Path(contract.PRE_MIGRATION)
UP_MIGRATION = Path(contract.UP_MIGRATION)
DOWN_MIGRATION = Path(contract.DOWN_MIGRATION)
ATTESTATION_TYPE = contract.ATTESTATION_TYPE
RUNNER_ATTESTATION_TYPE = contract.RUNNER_ATTESTATION_TYPE
SOURCE_MANIFEST_TYPE = contract.SOURCE_MANIFEST_TYPE
RECEIPT_TYPE = contract.RECEIPT_TYPE
REQUIRED_ROW_ANCHORS = contract.REQUIRED_ROW_ANCHORS
REQUIRED_BACKUP_RECEIPTS = contract.REQUIRED_BACKUP_RECEIPTS
REQUIRED_RESTORE_RECEIPTS = contract.REQUIRED_RESTORE_RECEIPTS
REQUIRED_REHEARSAL_RECEIPTS = contract.REQUIRED_REHEARSAL_RECEIPTS
REQUIRED_POST_APPLY_CHECKS = contract.REQUIRED_POST_APPLY_CHECKS
REQUIRED_POST_ROLLBACK_CHECKS = contract.REQUIRED_POST_ROLLBACK_CHECKS
REQUIRED_TOC_OBJECTS = contract.REQUIRED_TOC_OBJECTS
MAX_RECEIPT_BYTES = contract.MAX_RECEIPT_BYTES

# There is deliberately no repository-shipped trust anchor.  These objects are
# immutable and empty until a controlled key ceremony and external controller
# are separately reviewed and approved.
TRUSTED_PRODUCER_PUBLIC_KEYS = contract.TRUSTED_PRODUCER_PUBLIC_KEYS
TRUSTED_RUNNER_PUBLIC_KEYS = contract.TRUSTED_RUNNER_PUBLIC_KEYS

_utc_now = contract.utc_now
_iso_z = contract.iso_z
_parse_time = contract.parse_time
_canonical_json = contract.canonical_json
_json_sha256 = contract.json_sha256
_attestation_message = contract.attestation_message


def _runner_message(value: Mapping[str, Any]) -> bytes:
    try:
        from .migration_gate_receipts import runner_message
    except ImportError:
        from scripts.ops.migration_gate_receipts import runner_message
    return runner_message(value)


def _candidate_inventory(backup_dir: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    """Compatibility shim; selection is securely reopened before validation."""

    return backup_gate.compatibility_candidate_inventory(backup_dir)


def _hours(value: float) -> timedelta:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
        or value > 24
    ):
        raise ValueError("max_age_hours must be > 0 and <= the hard 24-hour limit")
    return timedelta(hours=float(value))


def _translate_path(original_root: Path, pinned_root: Path, value: Path) -> Path:
    """Translate paths below a caller-provided repo alias to the pinned root."""

    raw = Path(value)
    if not raw.is_absolute():
        return pinned_root / raw
    lexical_original = Path(original_root.absolute())
    lexical_value = Path(raw.absolute())
    try:
        relative = lexical_value.relative_to(lexical_original)
    except ValueError:
        return lexical_value
    return pinned_root / relative


def _real_runner_receipts(
    backup: Mapping[str, Any],
    restore: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    receipts: list[Mapping[str, Any]] = []
    selected = backup.get("selected")
    if isinstance(selected, dict):
        receipts.extend(
            item
            for item in selected.get("archive_list_receipts") or []
            if isinstance(item, dict) and item.get("label") == "pg_restore_list"
        )
    receipts.extend(
        item
        for item in restore.get("verified_receipts") or []
        if isinstance(item, dict) and item.get("label") == "pg_restore_execute"
    )
    return receipts


def _all_producer_attestations_trusted(
    source: Mapping[str, Any],
    backup: Mapping[str, Any],
    restore: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
) -> bool:
    selected = backup.get("selected")
    if not isinstance(selected, dict):
        return False
    receipts: list[Mapping[str, Any]] = []
    for collection in (
        selected.get("archive_list_receipts") or [],
        restore.get("verified_receipts") or [],
        rehearsal.get("verified_receipts") or [],
    ):
        receipts.extend(item for item in collection if isinstance(item, dict))
    expected_receipts = (
        len(contract.REQUIRED_BACKUP_RECEIPTS)
        + len(contract.REQUIRED_RESTORE_RECEIPTS)
        + len(contract.REQUIRED_REHEARSAL_RECEIPTS)
    )
    return (
        source.get("trusted_attestation") is True
        and selected.get("trusted_attestation") is True
        and restore.get("trusted_attestation") is True
        and rehearsal.get("trusted_attestation") is True
        and len(receipts) == expected_receipts
        and all(item.get("trusted_attestation") is True for item in receipts)
    )


def _advisory_decision(
    *,
    evidence_complete: bool,
    producers_trusted: bool,
    runners_trusted: bool,
    replay_mode: bool,
    checks: Checks,
) -> dict[str, Any]:
    """Map evidence to a non-authorizing decision, including complete evidence."""

    return {
        "gate_status": "advisory_complete" if evidence_complete else "failed",
        "evidence_bundle_complete": evidence_complete,
        "trusted_producer_attestations": producers_trusted,
        "trusted_real_runner_attestations": runners_trusted,
        "replay_mode": replay_mode,
        "safe_to_apply": False,
        "safe_to_start_separately_authorized_canary": False,
        "migration_execution_authorized_by_this_audit": False,
        "authorization_controller_present": False,
        "failed_checks": checks.failed,
        "passed_checks": checks.passed,
        "claim_status": (
            "replay_advisory_only"
            if replay_mode and evidence_complete
            else (
                "advisory_evidence_complete"
                if evidence_complete
                else "not_ready_fail_closed"
            )
        ),
    }


def _evaluate(
    *,
    repo_root: Path,
    backup_dir: Path,
    source_manifest: Path,
    restore_evidence: Path,
    rehearsal_evidence: Path,
    current: datetime,
    max_age_hours: float,
    producer_keys: Mapping[str, str],
    runner_keys: Mapping[str, str],
    replay_mode: bool,
) -> dict[str, Any]:
    max_age = _hours(max_age_hours)
    original_root = Path(repo_root)
    root = original_root.resolve(strict=True)
    translated_backup = _translate_path(original_root, root, backup_dir)
    translated_manifest = _translate_path(original_root, root, source_manifest)
    translated_restore = _translate_path(original_root, root, restore_evidence)
    translated_rehearsal = _translate_path(original_root, root, rehearsal_evidence)
    # Lexical scope checks happen before any artifact open.
    for path in (
        translated_backup,
        translated_manifest,
        translated_restore,
        translated_rehearsal,
    ):
        gate_io.lexical_path(root, path)

    checks = Checks()
    source = backup_gate.validate_source_manifest(
        root=root,
        manifest_path=translated_manifest,
        now=current,
        max_age=max_age,
        producer_keys=producer_keys,
        checks=checks,
    )
    backup = backup_gate.validate_backup(
        root=root,
        backup_dir=translated_backup,
        source_manifest=source,
        now=current,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )
    selected = backup.get("selected")
    selected_mapping = selected if isinstance(selected, dict) else None
    backup_finalized = (
        contract.parse_time(selected_mapping.get("finalized_at"))
        if selected_mapping
        else None
    )
    restore = evidence_gate.validate_restore(
        root=root,
        evidence_path=translated_restore,
        selected=selected_mapping,
        source_manifest=source,
        earliest=backup_finalized,
        now=current,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )
    rehearsal = evidence_gate.validate_rehearsal(
        root=root,
        evidence_path=translated_rehearsal,
        restore_path=translated_restore,
        restore_result=restore,
        selected=selected_mapping,
        source_manifest=source,
        earliest=contract.parse_time(restore.get("finalized_at")),
        now=current,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )

    producers_trusted = _all_producer_attestations_trusted(
        source, backup, restore, rehearsal
    )
    runner_receipts = _real_runner_receipts(backup, restore)
    runners_trusted = len(runner_receipts) == 2 and all(
        item.get("real_runner_attestation") is True for item in runner_receipts
    )
    evidence_complete = checks.failed == 0 and producers_trusted and runners_trusted

    # Critical boundary: local files can never satisfy authorization.  A future
    # controller must verify and consume a one-time target-bound challenge.
    return {
        "schema_version": 1,
        "gate": "vkpi_migration_243_to_244_release_preflight",
        "generated_at": contract.iso_z(current),
        "policy": {
            "default_mode": "offline_read_only_advisory",
            "clock_mode": "historical_replay" if replay_mode else "system_clock",
            "trusted_producer_key_count": len(producer_keys),
            "trusted_runner_key_count": len(runner_keys),
            "hard_max_age_hours": 24,
            "network_calls": 0,
            "database_connections": 0,
            "database_writes": 0,
            "migration_executions": 0,
            "service_reloads": 0,
            "authorization_controller_present": False,
            "external_one_time_challenge_verified": False,
            "live_target_binding_verified": False,
            "challenge_consumption_ledger_verified": False,
        },
        "expectations": {
            "pre_migration": contract.EXPECTED_PRE_MIGRATION,
            "post_migration": contract.EXPECTED_POST_MIGRATION,
            "max_backup_age_hours": max_age_hours,
            "required_row_anchors": list(contract.REQUIRED_ROW_ANCHORS),
            "required_toc_objects": list(contract.REQUIRED_TOC_OBJECTS),
        },
        "source_manifest": source,
        "backup": backup,
        "restore": restore,
        "rehearsal": rehearsal,
        "checks": checks.items,
        "decision": _advisory_decision(
            evidence_complete=evidence_complete,
            producers_trusted=producers_trusted,
            runners_trusted=runners_trusted,
            replay_mode=replay_mode,
            checks=checks,
        ),
        "claim_boundary": (
            "This offline result is advisory even when every local evidence check "
            "passes. It cannot prove current live-target state or authorize work. "
            "Authorization requires a separately reviewed external controller with "
            "a one-time target-bound challenge and durable atomic consumption ledger."
        ),
    }


def audit_gate(
    *,
    repo_root: Path,
    backup_dir: Path,
    source_manifest: Path | None = None,
    restore_evidence: Path,
    rehearsal_evidence: Path,
    max_age_hours: float = 24.0,
) -> dict[str, Any]:
    """Production entry: system clock and immutable approved keysets only."""

    return _evaluate(
        repo_root=repo_root,
        backup_dir=backup_dir,
        source_manifest=source_manifest or (Path(repo_root) / DEFAULT_SOURCE_MANIFEST),
        restore_evidence=restore_evidence,
        rehearsal_evidence=rehearsal_evidence,
        current=contract.utc_now(),
        max_age_hours=max_age_hours,
        producer_keys=contract.TRUSTED_PRODUCER_PUBLIC_KEYS,
        runner_keys=contract.TRUSTED_RUNNER_PUBLIC_KEYS,
        replay_mode=False,
    )


def audit_replay(
    *,
    repo_root: Path,
    backup_dir: Path,
    source_manifest: Path | None = None,
    restore_evidence: Path,
    rehearsal_evidence: Path,
    now: datetime,
    max_age_hours: float = 24.0,
    producer_public_keys: Mapping[str, str] | None = None,
    runner_public_keys: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Non-authorizing historical/test verifier with explicit replay inputs."""

    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("replay now must be timezone-aware")
    producer_keys = MappingProxyType(dict(producer_public_keys or {}))
    runner_keys = MappingProxyType(dict(runner_public_keys or {}))
    return _evaluate(
        repo_root=repo_root,
        backup_dir=backup_dir,
        source_manifest=source_manifest or (Path(repo_root) / DEFAULT_SOURCE_MANIFEST),
        restore_evidence=restore_evidence,
        rehearsal_evidence=rehearsal_evidence,
        current=now.astimezone(timezone.utc),
        max_age_hours=max_age_hours,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        replay_mode=True,
    )


def _resolve_cli_path(root: Path, value: str) -> Path:
    return gate_io.resolve_input(root, Path(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline advisory evidence audit for V-KPI migration 243 -> 244."
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--backup-dir", default="runtime/db-backups")
    parser.add_argument("--source-manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    parser.add_argument("--restore-evidence", default=str(DEFAULT_RESTORE_EVIDENCE))
    parser.add_argument("--rehearsal-evidence", default=str(DEFAULT_REHEARSAL_EVIDENCE))
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument(
        "--now",
        help="Historical replay clock; replay remains advisory and cannot authorize.",
    )
    parser.add_argument("--output", help="Optional production-clock report path")
    args = parser.parse_args(argv)
    try:
        root = Path(args.repo_root).resolve(strict=True)
        common = {
            "repo_root": root,
            "backup_dir": _resolve_cli_path(root, args.backup_dir),
            "source_manifest": _resolve_cli_path(root, args.source_manifest),
            "restore_evidence": _resolve_cli_path(root, args.restore_evidence),
            "rehearsal_evidence": _resolve_cli_path(root, args.rehearsal_evidence),
            "max_age_hours": args.max_age_hours,
        }
        if args.now:
            if args.output:
                raise ValueError("historical replay cannot write a production report")
            parsed = contract.parse_time(args.now)
            if parsed is None:
                raise ValueError("--now must be an ISO timestamp with timezone")
            report = audit_replay(now=parsed, **common)
        else:
            report = audit_gate(**common)
            if args.output:
                gate_io.write_report(_resolve_cli_path(root, args.output), report)
    except (OSError, ValueError, gate_io.SafeFileError) as exc:
        stdout_out(
            json.dumps(
                {
                    "gate": "vkpi_migration_243_to_244_release_preflight",
                    "decision": {
                        "gate_status": "failed",
                        "safe_to_apply": False,
                        "safe_to_start_separately_authorized_canary": False,
                        "claim_status": "not_ready_fail_closed",
                    },
                    "error": "invalid local gate input",
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
        return 2
    stdout_out(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    # Offline evidence is never an authorization success exit.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
