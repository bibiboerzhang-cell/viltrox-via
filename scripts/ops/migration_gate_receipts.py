"""Receipt, runner-attestation and ``pg_restore --list`` verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Mapping

from . import migration_gate_contract as contract
from .migration_gate_contract import Checks
from .migration_gate_io import Artifact, load_json_artifact


TOC_ENTRY_RE = re.compile(r"^[0-9]+;[ \t]+[0-9]+[ \t]+[0-9]+[ \t]+(.+)$")
TOC_COUNT_RE = re.compile(r"^;[ \t]+TOC Entries:[ \t]+([0-9]+)[ \t]*$")
PG_RESTORE_VERSION_RE = re.compile(r"^pg_restore \(PostgreSQL\) [0-9]+(?:\.[0-9]+){0,2}$")


def runner_message(attestation: Mapping[str, Any]) -> bytes:
    unsigned = dict(attestation)
    unsigned.pop("signature", None)
    return contract.canonical_json(unsigned)


def parse_pg_restore_toc(value: object) -> tuple[bool, list[str]]:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False, []
    declared: list[int] = []
    descriptions: list[str] = []
    archive_header = False
    for line in value.splitlines():
        if line.startswith("; Archive created at "):
            archive_header = True
        count_match = TOC_COUNT_RE.fullmatch(line)
        if count_match:
            declared.append(int(count_match.group(1)))
        entry_match = TOC_ENTRY_RE.fullmatch(line)
        if entry_match:
            descriptions.append(entry_match.group(1).strip())
    valid = (
        archive_header
        and len(declared) == 1
        and declared[0] == len(descriptions)
        and 0 < len(descriptions) <= contract.MAX_TOC_ENTRIES
    )
    return valid, descriptions


def _validate_runner_attestation(
    value: object,
    *,
    prefix: str,
    now: datetime,
    earliest: datetime | None,
    expected_argv: list[str],
    dump_sha256: str,
    stdout: str,
    stderr: str,
    required_toc_objects: list[str],
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> tuple[bool, bool]:
    mapping = contract.strict_object(
        value,
        required=(
            "schema_version",
            "attestation_type",
            "runner_class",
            "algorithm",
            "key_id",
            "signed_at",
            "binary_sha256",
            "binary_version",
            "argv",
            "dump_sha256",
            "stdout_sha256",
            "stderr_sha256",
            "critical_toc_objects",
            "signature",
        ),
        prefix=prefix,
        checks=checks,
    )
    if mapping is None:
        return False, False
    schema_ok = contract.schema_version_exact(mapping, prefix=prefix, checks=checks)
    type_ok = checks.add(
        prefix + ".type",
        mapping.get("attestation_type") == contract.RUNNER_ATTESTATION_TYPE,
        "runner attestation type required",
    )
    algorithm_ok = checks.add(
        prefix + ".algorithm", mapping.get("algorithm") == "Ed25519", "Ed25519"
    )
    binary_ok = checks.add(
        prefix + ".binary",
        contract.is_sha256(mapping.get("binary_sha256"))
        and isinstance(mapping.get("binary_version"), str)
        and PG_RESTORE_VERSION_RE.fullmatch(mapping["binary_version"]) is not None,
        "binary sha256 and exact pg_restore version required",
    )
    argv_ok = checks.add(
        prefix + ".argv",
        mapping.get("argv") == expected_argv,
        "exact argv required",
    )
    digest_ok = checks.add(
        prefix + ".io_binding",
        mapping.get("dump_sha256") == dump_sha256
        and mapping.get("stdout_sha256")
        == contract.sha256_bytes(stdout.encode("utf-8"))
        and mapping.get("stderr_sha256")
        == contract.sha256_bytes(stderr.encode("utf-8")),
        "dump/stdout/stderr digests must match receipt bytes",
    )
    toc_ok = checks.add(
        prefix + ".critical_toc_objects",
        mapping.get("critical_toc_objects") == required_toc_objects,
        "exact critical TOC object contract required",
    )
    key_id = mapping.get("key_id")
    key_text = runner_keys.get(key_id) if isinstance(key_id, str) else None
    key_ok = checks.add(
        prefix + ".key_allowlisted",
        isinstance(key_text, str),
        "runner key must be in immutable approved allowlist",
    )
    signed_at = contract.parse_time(mapping.get("signed_at"))
    time_ok = signed_at is not None
    if signed_at is not None:
        time_ok = (
            signed_at <= now + contract.FUTURE_TOLERANCE
            and now - signed_at <= contract.MAX_AUTHORIZING_AGE
            and (earliest is None or signed_at >= earliest)
        )
    checks.add(prefix + ".time", time_ok, "fresh runner signature required")
    encoded, signature_ok = contract._verify_ed25519(
        key_text=key_text,
        signature_text=mapping.get("signature"),
        message=runner_message(mapping),
    )
    checks.add(prefix + ".encoding", encoded, "raw Ed25519 encoding")
    checks.add(prefix + ".signature", signature_ok, "runner signature result")
    controlled = checks.add(
        prefix + ".real_controlled",
        mapping.get("runner_class") == "controlled_production",
        "offline_test_fixture and self-reported runners are advisory only",
    )
    cryptographic = all(
        (
            schema_ok,
            type_ok,
            algorithm_ok,
            binary_ok,
            argv_ok,
            digest_ok,
            toc_ok,
            key_ok,
            time_ok,
            signature_ok,
        )
    )
    return cryptographic, cryptographic and controlled


def _validate_common_receipt(
    payload: dict[str, Any],
    artifact: Artifact,
    *,
    label: str,
    prefix: str,
    bundle_id: str,
    repository_head: str,
    manifest_sha256: str,
    earliest: datetime | None,
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    checks: Checks,
) -> tuple[datetime | None, datetime | None, bool]:
    mapping = contract.strict_object(
        payload,
        required=(
            "schema_version",
            "receipt_type",
            "label",
            "status",
            "execution_mode",
            "bundle_id",
            "repository_head",
            "source_manifest_sha256",
            "started_at",
            "completed_at",
            "details",
            "attestation",
        ),
        prefix=prefix + ".schema",
        checks=checks,
    )
    if mapping is None:
        return None, None, False
    schema_ok = contract.schema_version_exact(mapping, prefix=prefix, checks=checks)
    identity_ok = checks.add(
        prefix + ".identity",
        mapping.get("receipt_type") == contract.RECEIPT_TYPE
        and mapping.get("label") == label
        and mapping.get("status") == "passed"
        and mapping.get("bundle_id") == bundle_id
        and mapping.get("repository_head") == repository_head
        and mapping.get("source_manifest_sha256") == manifest_sha256,
        "receipt identity and cross-binding required",
    )
    non_replay = checks.add(
        prefix + ".non_replay",
        mapping.get("execution_mode") == "controlled_run",
        "controlled_run required",
    )
    started = contract.parse_time(mapping.get("started_at"))
    completed = contract.parse_time(mapping.get("completed_at"))
    mtime = datetime.fromtimestamp(artifact.mtime_ns / 1_000_000_000, timezone.utc)
    time_ok = (
        started is not None
        and completed is not None
        and started <= completed <= mtime + contract.FINALIZATION_TOLERANCE
        and completed <= now + contract.FUTURE_TOLERANCE
        and now - completed <= max_age
        and (earliest is None or started >= earliest)
    )
    checks.add(prefix + ".time_window", time_ok, "ordered fresh receipt window")
    secret_ok = contract.check_no_secrets(payload, prefix=prefix, checks=checks)
    attested = contract.verify_producer_attestation(
        payload,
        prefix=prefix,
        now=now,
        not_before=completed,
        finalized_at=mtime,
        max_age=max_age,
        public_keys=producer_keys,
        checks=checks,
    )
    return started, completed, all(
        (schema_ok, identity_ok, non_replay, time_ok, secret_ok, attested)
    )


def _validate_details(
    details: object,
    *,
    label: str,
    prefix: str,
    dump_name: str,
    dump_sha256: str,
    migration_hashes: Mapping[str, str],
    state_hashes: Mapping[str, str],
    anchor_hashes: Mapping[str, str],
    check_hashes: Mapping[str, str],
    now: datetime,
    runner_not_before: datetime | None,
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> tuple[bool, bool]:
    if not isinstance(details, dict):
        checks.add(prefix + ".object", False, "details object required")
        return False, False

    real_runner = True
    if label == "pg_restore_list":
        mapping = contract.strict_object(
            details,
            required=(
                "exit_code",
                "archive_name",
                "archive_sha256",
                "toc_output",
                "stderr_output",
                "toc_entries",
                "runner_attestation",
            ),
            prefix=prefix,
            checks=checks,
        )
        if mapping is None:
            return False, False
        outputs_ok = checks.add(
            prefix + ".output_types",
            isinstance(mapping.get("toc_output"), str)
            and isinstance(mapping.get("stderr_output"), str),
            "literal stdout/stderr strings required",
        )
        toc_valid, entries = parse_pg_restore_toc(mapping.get("toc_output"))
        required = list(contract.REQUIRED_TOC_OBJECTS)
        toc_ok = checks.add(
            prefix + ".toc_parse",
            toc_valid
            and contract.is_exact_int(mapping.get("toc_entries"))
            and mapping.get("toc_entries") == len(entries),
            f"parsed_entries={len(entries)}",
        )
        objects_ok = checks.add(
            prefix + ".critical_objects",
            all(any(entry.startswith(obj) for entry in entries) for obj in required),
            "all critical objects must occur in parsed TOC",
        )
        base_ok = checks.add(
            prefix + ".archive_binding",
            mapping.get("exit_code") == 0
            and contract.is_exact_int(mapping.get("exit_code"), maximum=0)
            and mapping.get("archive_name") == dump_name
            and mapping.get("archive_sha256") == dump_sha256,
            "exact exit=0 and archive digest required",
        )
        runner_ok, real_runner = _validate_runner_attestation(
            mapping.get("runner_attestation"),
            prefix=prefix + ".runner",
            now=now,
            earliest=runner_not_before,
            expected_argv=["pg_restore", "--list", dump_name],
            dump_sha256=dump_sha256,
            stdout=mapping.get("toc_output") if isinstance(mapping.get("toc_output"), str) else "",
            stderr=mapping.get("stderr_output") if isinstance(mapping.get("stderr_output"), str) else "",
            required_toc_objects=required,
            runner_keys=runner_keys,
            checks=checks,
        )
        return all((outputs_ok, toc_ok, objects_ok, base_ok, runner_ok)), real_runner

    if label == "pg_restore_execute":
        mapping = contract.strict_object(
            details,
            required=(
                "exit_code",
                "archive_name",
                "archive_sha256",
                "target_class",
                "stdout_output",
                "stderr_output",
                "runner_attestation",
            ),
            prefix=prefix,
            checks=checks,
        )
        if mapping is None:
            return False, False
        outputs_ok = checks.add(
            prefix + ".output_types",
            isinstance(mapping.get("stdout_output"), str)
            and isinstance(mapping.get("stderr_output"), str),
            "literal stdout/stderr strings required",
        )
        base_ok = checks.add(
            prefix + ".restore_contract",
            contract.is_exact_int(mapping.get("exit_code"), maximum=0)
            and mapping.get("archive_name") == dump_name
            and mapping.get("archive_sha256") == dump_sha256
            and mapping.get("target_class") == "isolated_non_live",
            "isolated restore and exact exit=0 required",
        )
        runner_ok, real_runner = _validate_runner_attestation(
            mapping.get("runner_attestation"),
            prefix=prefix + ".runner",
            now=now,
            earliest=runner_not_before,
            expected_argv=["pg_restore", "--dbname=isolated_non_live", dump_name],
            dump_sha256=dump_sha256,
            stdout=mapping.get("stdout_output") if isinstance(mapping.get("stdout_output"), str) else "",
            stderr=mapping.get("stderr_output") if isinstance(mapping.get("stderr_output"), str) else "",
            required_toc_objects=list(contract.REQUIRED_TOC_OBJECTS),
            runner_keys=runner_keys,
            checks=checks,
        )
        return outputs_ok and base_ok and runner_ok, real_runner

    required_by_label = {
        "row_anchor_readback": (
            "readback_sha256",
            "source_anchors_sha256",
            "restored_anchors_sha256",
            "migration_state_sha256",
        ),
        "migration_244_up": (
            "migration_file",
            "migration_sha256",
            "exit_code",
            "migration_before",
            "migration_after",
            "migration_state_sha256",
        ),
        "migration_244_down": (
            "migration_file",
            "migration_sha256",
            "exit_code",
            "migration_before",
            "migration_after",
            "migration_state_sha256",
        ),
        "migration_244_post_apply": (
            "checks_sha256",
            "anchors_sha256",
            "migration_max",
            "migration_state_sha256",
        ),
        "migration_244_post_rollback": (
            "checks_sha256",
            "anchors_sha256",
            "migration_max",
            "migration_state_sha256",
        ),
    }
    required = required_by_label.get(label)
    if required is None:
        checks.add(prefix + ".known_label", False, "unsupported receipt label")
        return False, False
    mapping = contract.strict_object(
        details, required=required, prefix=prefix, checks=checks
    )
    if mapping is None:
        return False, False

    if label == "row_anchor_readback":
        expected = {
            "readback_sha256": anchor_hashes.get("readback"),
            "source_anchors_sha256": anchor_hashes.get("source"),
            "restored_anchors_sha256": anchor_hashes.get("restored"),
            "migration_state_sha256": state_hashes.get("restore"),
        }
        ok = all(mapping.get(key) == value for key, value in expected.items())
    elif label in ("migration_244_up", "migration_244_down"):
        up = label.endswith("_up")
        expected_file = contract.UP_MIGRATION if up else contract.DOWN_MIGRATION
        expected_before = (
            contract.EXPECTED_PRE_MIGRATION if up else contract.EXPECTED_POST_MIGRATION
        )
        expected_after = (
            contract.EXPECTED_POST_MIGRATION if up else contract.EXPECTED_PRE_MIGRATION
        )
        expected_state = state_hashes.get("post_forward" if up else "post_rollback")
        ok = (
            mapping.get("migration_file") == expected_file
            and mapping.get("migration_sha256") == migration_hashes.get(expected_file)
            and contract.is_exact_int(mapping.get("exit_code"), maximum=0)
            and mapping.get("migration_before") == expected_before
            and mapping.get("migration_after") == expected_after
            and mapping.get("migration_state_sha256") == expected_state
        )
    else:
        apply = label.endswith("post_apply")
        key = "post_forward" if apply else "post_rollback"
        ok = (
            mapping.get("checks_sha256") == check_hashes.get(key)
            and mapping.get("anchors_sha256") == anchor_hashes.get(key)
            and mapping.get("migration_max")
            == (
                contract.EXPECTED_POST_MIGRATION
                if apply
                else contract.EXPECTED_PRE_MIGRATION
            )
            and mapping.get("migration_state_sha256") == state_hashes.get(key)
        )
    checks.add(prefix + ".binding", ok, "receipt details bind exact evidence state")
    return ok, real_runner


def validate_receipts(
    *,
    root: Path,
    evidence_path: Path,
    descriptors: object,
    required_labels: tuple[str, ...],
    prefix: str,
    bundle_id: str,
    repository_head: str,
    manifest_sha256: str,
    dump_name: str,
    dump_sha256: str,
    migration_hashes: Mapping[str, str],
    state_hashes: Mapping[str, str],
    anchor_hashes: Mapping[str, str],
    check_hashes: Mapping[str, str],
    earliest: datetime | None,
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> list[dict[str, Any]]:
    if not isinstance(descriptors, list):
        checks.add(prefix + ".descriptor_list", False, "receipt descriptor list required")
        return []
    labels = [item.get("label") for item in descriptors if isinstance(item, dict)]
    exact_labels = labels == list(required_labels) and len(labels) == len(descriptors)
    checks.add(prefix + ".labels", exact_labels, "exact ordered receipt labels required")
    verified: list[dict[str, Any]] = []
    prior = earliest
    for index, label in enumerate(required_labels):
        item = descriptors[index] if index < len(descriptors) else None
        item_prefix = f"{prefix}.{label}"
        descriptor = contract.strict_object(
            item,
            required=("label", "path", "sha256"),
            prefix=item_prefix + ".descriptor",
            checks=checks,
        )
        if descriptor is None or descriptor.get("label") != label:
            continue
        relative = descriptor.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            checks.add(item_prefix + ".descriptor_path", False, "relative path required")
            continue
        receipt_path = evidence_path.parent / relative
        payload, artifact = load_json_artifact(
            root,
            receipt_path,
            max_bytes=contract.MAX_RECEIPT_BYTES,
            private=True,
            prefix=item_prefix,
            checks=checks,
        )
        if payload is None or artifact is None:
            continue
        digest_ok = checks.add(
            item_prefix + ".descriptor_sha256",
            descriptor.get("sha256") == artifact.sha256,
            "descriptor binds pinned bytes",
        )
        started, completed, common_ok = _validate_common_receipt(
            payload,
            artifact,
            label=label,
            prefix=item_prefix,
            bundle_id=bundle_id,
            repository_head=repository_head,
            manifest_sha256=manifest_sha256,
            earliest=prior,
            now=now,
            max_age=max_age,
            producer_keys=producer_keys,
            checks=checks,
        )
        details_ok, real_runner = _validate_details(
            payload.get("details"),
            label=label,
            prefix=item_prefix + ".details",
            dump_name=dump_name,
            dump_sha256=dump_sha256,
            migration_hashes=migration_hashes,
            state_hashes=state_hashes,
            anchor_hashes=anchor_hashes,
            check_hashes=check_hashes,
            now=now,
            runner_not_before=completed,
            runner_keys=runner_keys,
            checks=checks,
        )
        valid = all((digest_ok, common_ok, details_ok))
        verified.append(
            {
                "label": label,
                "path": str(receipt_path.relative_to(root)),
                "sha256": artifact.sha256,
                "started_at": contract.iso_z(started) if started else None,
                "completed_at": contract.iso_z(completed) if completed else None,
                "trusted_attestation": valid,
                "real_runner_attestation": real_runner,
                "execution_mode": payload.get("execution_mode"),
            }
        )
        if completed is not None:
            prior = completed
    return verified
