"""Isolated restore and forward/rollback rehearsal evidence validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from . import migration_gate_contract as contract
from .migration_gate_contract import Checks
from .migration_gate_io import load_json_artifact
from .migration_gate_receipts import validate_receipts


def _validate_time_window(
    payload: Mapping[str, Any],
    artifact_mtime_ns: int,
    *,
    prefix: str,
    earliest: datetime | None,
    now: datetime,
    max_age: timedelta,
    checks: Checks,
) -> tuple[datetime | None, datetime | None, datetime]:
    started = contract.parse_time(payload.get("started_at"))
    completed = contract.parse_time(payload.get("completed_at"))
    finalized = datetime.fromtimestamp(artifact_mtime_ns / 1_000_000_000, timezone.utc)
    valid = (
        started is not None
        and completed is not None
        and started <= completed <= finalized + contract.FINALIZATION_TOLERANCE
        and completed <= now + contract.FUTURE_TOLERANCE
        and now - completed <= max_age
        and (earliest is None or started >= earliest)
    )
    checks.add(prefix + ".time_window", valid, "ordered fresh stage window required")
    checks.add(
        prefix + ".after_prior_stage",
        earliest is None or (started is not None and started >= earliest),
        "stage must begin after prior finalized artifact",
    )
    return started, completed, finalized


def _validate_anchors(
    value: object, *, prefix: str, checks: Checks
) -> dict[str, int] | None:
    valid = (
        isinstance(value, dict)
        and set(value) == set(contract.REQUIRED_ROW_ANCHORS)
        and all(contract.is_exact_int(item) for item in value.values())
    )
    checks.add(
        prefix + ".shape",
        valid,
        "exact anchor keys with PostgreSQL-bigint non-boolean counts required",
    )
    return dict(value) if valid and isinstance(value, dict) else None


def _validate_source_dump(
    value: object,
    *,
    selected: Mapping[str, Any] | None,
    prefix: str,
    checks: Checks,
) -> bool:
    mapping = contract.strict_object(
        value,
        required=("name", "sha256", "migration_max"),
        prefix=prefix,
        checks=checks,
    )
    valid = (
        mapping is not None
        and selected is not None
        and mapping.get("name") == selected.get("dump_name")
        and mapping.get("sha256") == selected.get("dump_sha256")
        and mapping.get("migration_max") == contract.EXPECTED_PRE_MIGRATION
    )
    checks.add(prefix + ".binding", valid, "exact selected backup binding required")
    return valid


def validate_restore(
    *,
    root: Path,
    evidence_path: Path,
    selected: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any],
    earliest: datetime | None,
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> dict[str, Any]:
    payload, artifact = load_json_artifact(
        root,
        evidence_path,
        max_bytes=contract.MAX_EVIDENCE_BYTES,
        private=True,
        prefix="restore.evidence",
        checks=checks,
    )
    result: dict[str, Any] = {
        "path": str(evidence_path),
        "sha256": artifact.sha256 if artifact else None,
        "trusted_attestation": False,
        "verified_receipts": [],
    }
    if payload is None or artifact is None:
        return result
    mapping = contract.strict_object(
        payload,
        required=(
            "schema_version",
            "evidence_type",
            "evidence_mode",
            "bundle_id",
            "status",
            "isolated_database",
            "target_database_not_live",
            "network_exposed",
            "repository_head",
            "source_manifest_sha256",
            "source_dump",
            "backup_metadata_sha256",
            "started_at",
            "completed_at",
            "pg_restore_exit_code",
            "restore_errors",
            "readback",
            "migration_state",
            "source_row_anchors",
            "restored_row_anchors",
            "receipts",
            "attestation",
        ),
        prefix="restore.schema",
        checks=checks,
    )
    if mapping is None:
        return result
    schema_ok = contract.schema_version_exact(mapping, prefix="restore", checks=checks)
    identity_ok = checks.add(
        "restore.identity",
        mapping.get("evidence_type") == "vkpi_migration_243_isolated_restore"
        and mapping.get("evidence_mode") == "controlled_run"
        and mapping.get("status") == "passed"
        and selected is not None
        and mapping.get("bundle_id") == selected.get("bundle_id")
        and mapping.get("repository_head") == source_manifest.get("repository_head")
        and mapping.get("source_manifest_sha256") == source_manifest.get("sha256")
        and mapping.get("backup_metadata_sha256") == selected.get("metadata_sha256"),
        "restore identity and full source/backup binding required",
    )
    isolation_ok = checks.add(
        "restore.isolation",
        mapping.get("isolated_database") is True
        and mapping.get("target_database_not_live") is True
        and mapping.get("network_exposed") is False,
        "isolated non-live non-exposed target required",
    )
    exits_ok = checks.add(
        "restore.exact_results",
        contract.is_exact_int(mapping.get("pg_restore_exit_code"), maximum=0)
        and contract.is_exact_int(mapping.get("restore_errors"), maximum=0),
        "exact integer exit=0 and error_count=0 required",
    )
    dump_ok = _validate_source_dump(
        mapping.get("source_dump"),
        selected=selected,
        prefix="restore.source_dump",
        checks=checks,
    )
    readback = contract.strict_object(
        mapping.get("readback"),
        required=(
            "migration_max",
            "migration_243_marker_count",
            "migration_244_marker_count",
        ),
        prefix="restore.readback",
        checks=checks,
    )
    readback_ok = checks.add(
        "restore.readback.markers",
        readback is not None
        and readback.get("migration_max") == contract.EXPECTED_PRE_MIGRATION
        and contract.is_exact_int(readback.get("migration_243_marker_count"), minimum=1, maximum=1)
        and contract.is_exact_int(readback.get("migration_244_marker_count"), maximum=0),
        "exact pre marker=1 and post marker=0 required",
    )
    state = contract.validate_migration_state(
        mapping.get("migration_state"), prefix="restore.migration_state", checks=checks
    )
    state_ok = checks.add(
        "restore.migration_state.binding",
        state is not None
        and selected is not None
        and state == selected.get("migration_state"),
        "restored schema_migrations key-set/content must equal backup state",
    )
    source_anchors = _validate_anchors(
        mapping.get("source_row_anchors"), prefix="restore.source_anchors", checks=checks
    )
    restored_anchors = _validate_anchors(
        mapping.get("restored_row_anchors"), prefix="restore.restored_anchors", checks=checks
    )
    anchors_ok = checks.add(
        "restore.anchor_equality",
        source_anchors is not None
        and restored_anchors == source_anchors
        and state is not None
        and source_anchors.get("schema_migrations") == len(state["version_keys"]),
        "source/restored anchors and migration key count must match",
    )
    started, completed, finalized = _validate_time_window(
        mapping,
        artifact.mtime_ns,
        prefix="restore",
        earliest=earliest,
        now=now,
        max_age=max_age,
        checks=checks,
    )
    state_sha = contract.json_sha256(state) if state else ""
    receipts = validate_receipts(
        root=root,
        evidence_path=evidence_path,
        descriptors=mapping.get("receipts"),
        required_labels=contract.REQUIRED_RESTORE_RECEIPTS,
        prefix="restore.receipt",
        bundle_id=selected.get("bundle_id") if selected else "",
        repository_head=source_manifest.get("repository_head") or "",
        manifest_sha256=source_manifest.get("sha256") or "",
        dump_name=selected.get("dump_name") if selected else "",
        dump_sha256=selected.get("dump_sha256") if selected else "",
        migration_hashes=source_manifest.get("migration_hashes") or {},
        state_hashes={"restore": state_sha},
        anchor_hashes={
            "readback": contract.json_sha256(readback) if readback else "",
            "source": contract.json_sha256(source_anchors) if source_anchors else "",
            "restored": contract.json_sha256(restored_anchors) if restored_anchors else "",
        },
        check_hashes={},
        earliest=started,
        now=now,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )
    receipts_ok = len(receipts) == len(contract.REQUIRED_RESTORE_RECEIPTS) and all(
        item.get("trusted_attestation") is True for item in receipts
    )
    secret_ok = contract.check_no_secrets(payload, prefix="restore.evidence", checks=checks)
    attested = contract.verify_producer_attestation(
        payload,
        prefix="restore",
        now=now,
        not_before=completed,
        finalized_at=finalized,
        max_age=max_age,
        public_keys=producer_keys,
        checks=checks,
    )
    trusted = all(
        (
            schema_ok,
            identity_ok,
            isolation_ok,
            exits_ok,
            dump_ok,
            readback_ok,
            state_ok,
            anchors_ok,
            receipts_ok,
            secret_ok,
            attested,
        )
    )
    result.update(
        {
            "sha256": artifact.sha256,
            "started_at": contract.iso_z(started) if started else None,
            "completed_at": contract.iso_z(completed) if completed else None,
            "finalized_at": contract.iso_z(finalized),
            "migration_state": state,
            "migration_state_sha256": state_sha or None,
            "source_row_anchors": source_anchors,
            "restored_row_anchors": restored_anchors,
            "verified_receipts": receipts,
            "trusted_attestation": trusted,
        }
    )
    return result


def _all_true_checks(
    value: object, *, required: tuple[str, ...], prefix: str, checks: Checks
) -> dict[str, bool] | None:
    valid = (
        isinstance(value, dict)
        and set(value) == set(required)
        and all(value.get(name) is True for name in required)
    )
    checks.add(prefix + ".exact_true_set", valid, "exact check set with literal true required")
    return dict(value) if valid and isinstance(value, dict) else None


def _migration_transition(
    states_value: object,
    *,
    restore_state: Mapping[str, Any] | None,
    pre_anchors: Mapping[str, int] | None,
    post_anchors: Mapping[str, int] | None,
    rollback_anchors: Mapping[str, int] | None,
    checks: Checks,
) -> tuple[dict[str, dict[str, Any]], bool]:
    container = contract.strict_object(
        states_value,
        required=("pre", "post_forward", "post_rollback"),
        prefix="rehearsal.migration_states",
        checks=checks,
    )
    states: dict[str, dict[str, Any]] = {}
    if container is not None:
        for name in ("pre", "post_forward", "post_rollback"):
            state = contract.validate_migration_state(
                container.get(name),
                prefix=f"rehearsal.migration_states.{name}",
                checks=checks,
            )
            if state is not None:
                states[name] = state
    pre = states.get("pre")
    post = states.get("post_forward")
    rollback = states.get("post_rollback")
    keys_ok = (
        pre is not None
        and post is not None
        and rollback is not None
        and restore_state is not None
        and pre == restore_state
        and post["version_keys"] == pre["version_keys"] + [contract.EXPECTED_POST_MIGRATION]
        and rollback["version_keys"] == pre["version_keys"]
        and pre["content_sha256"] == rollback["content_sha256"]
        and post["content_sha256"] != pre["content_sha256"]
    )
    checks.add(
        "rehearsal.migration_key_transition",
        keys_ok,
        "pre + exact 244 then exact rollback key/content state required",
    )
    counts_ok = (
        keys_ok
        and pre_anchors is not None
        and post_anchors is not None
        and rollback_anchors is not None
        and pre_anchors["schema_migrations"] < contract.PG_BIGINT_MAX
        and pre_anchors["schema_migrations"] == len(pre["version_keys"])
        and post_anchors["schema_migrations"] == pre_anchors["schema_migrations"] + 1
        and post_anchors["schema_migrations"] == len(post["version_keys"])
        and rollback_anchors["schema_migrations"] == pre_anchors["schema_migrations"]
        and rollback_anchors["schema_migrations"] == len(rollback["version_keys"])
    )
    checks.add(
        "rehearsal.post_forward_anchor_semantics",
        counts_ok,
        "safe PostgreSQL bigint pre+1 then exact rollback required",
    )
    return states, keys_ok and counts_ok


def validate_rehearsal(
    *,
    root: Path,
    evidence_path: Path,
    restore_path: Path,
    restore_result: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any],
    earliest: datetime | None,
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> dict[str, Any]:
    payload, artifact = load_json_artifact(
        root,
        evidence_path,
        max_bytes=contract.MAX_EVIDENCE_BYTES,
        private=True,
        prefix="rehearsal.evidence",
        checks=checks,
    )
    result: dict[str, Any] = {
        "path": str(evidence_path),
        "sha256": artifact.sha256 if artifact else None,
        "trusted_attestation": False,
        "verified_receipts": [],
    }
    if payload is None or artifact is None:
        return result
    mapping = contract.strict_object(
        payload,
        required=(
            "schema_version",
            "evidence_type",
            "evidence_mode",
            "bundle_id",
            "status",
            "isolated_database",
            "target_database_not_live",
            "network_exposed",
            "repository_head",
            "source_manifest_sha256",
            "source_dump",
            "restore_evidence_sha256",
            "started_at",
            "completed_at",
            "forward",
            "rollback",
            "rollback_preconditions",
            "post_apply_checks",
            "post_rollback_checks",
            "migration_states",
            "pre_row_anchors",
            "post_forward_row_anchors",
            "post_rollback_row_anchors",
            "receipts",
            "attestation",
        ),
        prefix="rehearsal.schema",
        checks=checks,
    )
    if mapping is None:
        return result
    schema_ok = contract.schema_version_exact(mapping, prefix="rehearsal", checks=checks)
    identity_ok = checks.add(
        "rehearsal.identity",
        mapping.get("evidence_type")
        == "vkpi_migration_244_forward_rollback_rehearsal"
        and mapping.get("evidence_mode") == "controlled_run"
        and mapping.get("status") == "passed"
        and selected is not None
        and mapping.get("bundle_id") == selected.get("bundle_id")
        and mapping.get("repository_head") == source_manifest.get("repository_head")
        and mapping.get("source_manifest_sha256") == source_manifest.get("sha256")
        and mapping.get("restore_evidence_sha256") == restore_result.get("sha256"),
        "rehearsal identity and source/restore binding required",
    )
    isolation_ok = checks.add(
        "rehearsal.isolation",
        mapping.get("isolated_database") is True
        and mapping.get("target_database_not_live") is True
        and mapping.get("network_exposed") is False,
        "isolated non-live non-exposed target required",
    )
    dump_ok = _validate_source_dump(
        mapping.get("source_dump"),
        selected=selected,
        prefix="rehearsal.source_dump",
        checks=checks,
    )
    forward = contract.strict_object(
        mapping.get("forward"),
        required=(
            "migration_file",
            "sha256",
            "exit_code",
            "migration_244_marker_count",
            "migration_max",
            "duration_ms",
        ),
        prefix="rehearsal.forward",
        checks=checks,
    )
    rollback = contract.strict_object(
        mapping.get("rollback"),
        required=(
            "strategy",
            "migration_file",
            "sha256",
            "exit_code",
            "migration_244_marker_count",
            "migration_max",
            "duration_ms",
        ),
        prefix="rehearsal.rollback",
        checks=checks,
    )
    migration_hashes = source_manifest.get("migration_hashes") or {}
    actions_ok = checks.add(
        "rehearsal.exact_actions",
        forward is not None
        and rollback is not None
        and forward.get("migration_file") == contract.UP_MIGRATION
        and forward.get("sha256") == migration_hashes.get(contract.UP_MIGRATION)
        and contract.is_exact_int(forward.get("exit_code"), maximum=0)
        and contract.is_exact_int(forward.get("migration_244_marker_count"), minimum=1, maximum=1)
        and forward.get("migration_max") == contract.EXPECTED_POST_MIGRATION
        and contract.is_exact_int(forward.get("duration_ms"))
        and rollback.get("strategy") == "down_sql"
        and rollback.get("migration_file") == contract.DOWN_MIGRATION
        and rollback.get("sha256") == migration_hashes.get(contract.DOWN_MIGRATION)
        and contract.is_exact_int(rollback.get("exit_code"), maximum=0)
        and contract.is_exact_int(rollback.get("migration_244_marker_count"), maximum=0)
        and rollback.get("migration_max") == contract.EXPECTED_PRE_MIGRATION
        and contract.is_exact_int(rollback.get("duration_ms")),
        "exact non-boolean exits/markers/durations and source hashes required",
    )
    preconditions = contract.strict_object(
        mapping.get("rollback_preconditions"),
        required=("non_legacy_workspace_rows", "dealer_identity_alias_rows"),
        prefix="rehearsal.rollback_preconditions",
        checks=checks,
    )
    preconditions_ok = checks.add(
        "rehearsal.rollback_preconditions.exact_zero",
        preconditions is not None
        and all(
            contract.is_exact_int(preconditions.get(name), maximum=0)
            for name in ("non_legacy_workspace_rows", "dealer_identity_alias_rows")
        ),
        "literal integer zero preconditions required",
    )
    apply_checks = _all_true_checks(
        mapping.get("post_apply_checks"),
        required=contract.REQUIRED_POST_APPLY_CHECKS,
        prefix="rehearsal.post_apply_checks",
        checks=checks,
    )
    rollback_checks = _all_true_checks(
        mapping.get("post_rollback_checks"),
        required=contract.REQUIRED_POST_ROLLBACK_CHECKS,
        prefix="rehearsal.post_rollback_checks",
        checks=checks,
    )
    pre_anchors = _validate_anchors(
        mapping.get("pre_row_anchors"), prefix="rehearsal.pre_anchors", checks=checks
    )
    post_anchors = _validate_anchors(
        mapping.get("post_forward_row_anchors"),
        prefix="rehearsal.post_forward_anchors",
        checks=checks,
    )
    rollback_anchors = _validate_anchors(
        mapping.get("post_rollback_row_anchors"),
        prefix="rehearsal.post_rollback_anchors",
        checks=checks,
    )
    non_schema_anchors_ok = checks.add(
        "rehearsal.non_schema_anchor_stability",
        pre_anchors is not None
        and post_anchors is not None
        and rollback_anchors is not None
        and all(
            post_anchors[name] == pre_anchors[name] == rollback_anchors[name]
            for name in contract.REQUIRED_ROW_ANCHORS
            if name != "schema_migrations"
        )
        and pre_anchors == restore_result.get("restored_row_anchors"),
        "all non-schema anchors stable and pre anchors bind restore",
    )
    states, transition_ok = _migration_transition(
        mapping.get("migration_states"),
        restore_state=restore_result.get("migration_state"),
        pre_anchors=pre_anchors,
        post_anchors=post_anchors,
        rollback_anchors=rollback_anchors,
        checks=checks,
    )
    started, completed, finalized = _validate_time_window(
        mapping,
        artifact.mtime_ns,
        prefix="rehearsal",
        earliest=earliest,
        now=now,
        max_age=max_age,
        checks=checks,
    )
    state_hashes = {
        name: contract.json_sha256(value) for name, value in states.items()
    }
    anchor_hashes = {
        "post_forward": contract.json_sha256(post_anchors) if post_anchors else "",
        "post_rollback": contract.json_sha256(rollback_anchors) if rollback_anchors else "",
    }
    check_hashes = {
        "post_forward": contract.json_sha256(apply_checks) if apply_checks else "",
        "post_rollback": contract.json_sha256(rollback_checks) if rollback_checks else "",
    }
    receipts = validate_receipts(
        root=root,
        evidence_path=evidence_path,
        descriptors=mapping.get("receipts"),
        required_labels=contract.REQUIRED_REHEARSAL_RECEIPTS,
        prefix="rehearsal.receipt",
        bundle_id=selected.get("bundle_id") if selected else "",
        repository_head=source_manifest.get("repository_head") or "",
        manifest_sha256=source_manifest.get("sha256") or "",
        dump_name=selected.get("dump_name") if selected else "",
        dump_sha256=selected.get("dump_sha256") if selected else "",
        migration_hashes=migration_hashes,
        state_hashes=state_hashes,
        anchor_hashes=anchor_hashes,
        check_hashes=check_hashes,
        earliest=started,
        now=now,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )
    receipts_ok = len(receipts) == len(contract.REQUIRED_REHEARSAL_RECEIPTS) and all(
        item.get("trusted_attestation") is True for item in receipts
    )
    secret_ok = contract.check_no_secrets(payload, prefix="rehearsal.evidence", checks=checks)
    attested = contract.verify_producer_attestation(
        payload,
        prefix="rehearsal",
        now=now,
        not_before=completed,
        finalized_at=finalized,
        max_age=max_age,
        public_keys=producer_keys,
        checks=checks,
    )
    trusted = all(
        (
            schema_ok,
            identity_ok,
            isolation_ok,
            dump_ok,
            actions_ok,
            preconditions_ok,
            apply_checks is not None,
            rollback_checks is not None,
            non_schema_anchors_ok,
            transition_ok,
            receipts_ok,
            secret_ok,
            attested,
        )
    )
    result.update(
        {
            "sha256": artifact.sha256,
            "started_at": contract.iso_z(started) if started else None,
            "completed_at": contract.iso_z(completed) if completed else None,
            "finalized_at": contract.iso_z(finalized),
            "migration_states": states,
            "verified_receipts": receipts,
            "trusted_attestation": trusted,
        }
    )
    return result
