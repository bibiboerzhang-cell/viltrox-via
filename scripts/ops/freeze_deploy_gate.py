#!/usr/bin/env python3
"""Strict deploy-source and manifest-bound controller receipt gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

from scripts.ops.controller_static_receipt import (
    CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN,
    read_bound_regular_file,
    validate_controller_static_receipt,
)
from scripts.ops.deploy_gate_runtime import (
    DeployGateRuntimeError,
    bound_deploy_gate_runtime,
)
from scripts.ops.deploy_runtime_admission import (
    load_admission,
    validate_runtime_binding_values,
)
from scripts.ops.freeze_git_bridge import (
    GIT_REPOSITORY_BINDING_ENV,
    strict_snapshot_identity_environment,
)
from scripts.ops.freeze_phase_runtime import physical_special_paths
from scripts.ops.freeze_worktree_contract import (
    SCHEMA,
    BuildIdentity,
    FreezeError,
    assert_frontend_dist_reproducible,
    write_owned_file_exclusive,
)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bind_admission_runtime_environment(
    environment: dict[str, str], admission: dict[str, object] | None,
) -> None:
    """Use the filtered runtime env only after admission validated its bytes."""

    if admission is None:
        return
    runtime_env_file = admission.get("runtime_env_file")
    if not isinstance(runtime_env_file, str) or not Path(runtime_env_file).is_absolute():
        raise FreezeError("deploy runtime admission environment path is invalid")
    environment["LOCAL_ENV_FILE"] = runtime_env_file


def _private_gate_output_is_stable(
    path: Path, descriptor: int, expected_identity: tuple[int, int],
) -> bool:
    try:
        lexical = path.lstat()
        opened = os.fstat(descriptor)
    except OSError:
        return False
    return (
        not path.is_symlink()
        and stat.S_ISREG(lexical.st_mode)
        and stat.S_ISREG(opened.st_mode)
        and (lexical.st_dev, lexical.st_ino) == expected_identity
        and (opened.st_dev, opened.st_ino) == expected_identity
        and lexical.st_uid == os.geteuid()
        and opened.st_uid == os.geteuid()
        and lexical.st_nlink == 1
        and opened.st_nlink == 1
        and stat.S_IMODE(lexical.st_mode) == 0o600
        and stat.S_IMODE(opened.st_mode) == 0o600
    )


def _replay_private_gate_output(handle: BinaryIO) -> None:
    handle.seek(0)
    destination = getattr(sys.stdout, "buffer", None)
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        if destination is None:
            sys.stdout.write(chunk.decode("utf-8", "replace"))
        else:
            destination.write(chunk)
    if destination is None:
        sys.stdout.flush()
    else:
        destination.flush()


def _run_controlled_candidate_with_private_output(
    arguments: list[str], *, cwd: Path, env: dict[str, str],
    runtime_root: Path, run_nonce: str, timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    """Keep candidate stdio on a Seatbelt-readable inode, then replay it."""

    if re.fullmatch(r"[0-9a-f]{64}", run_nonce) is None:
        raise FreezeError("canonical gate output nonce is invalid")
    output = (
        runtime_root / "controller" / f"canonical-gate-output.{run_nonce}.log"
    )
    try:
        identity = write_owned_file_exclusive(output, b"")
        descriptor = os.open(
            output,
            os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise FreezeError("canonical gate private output is unavailable") from exc
    try:
        if not _private_gate_output_is_stable(output, descriptor, identity):
            raise FreezeError("canonical gate private output is unsafe")
        with os.fdopen(descriptor, "r+b", buffering=0) as handle:
            descriptor = -1
            from scripts.ops.controlled_candidate_process import run_controlled_candidate

            try:
                completed = run_controlled_candidate(
                    arguments,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                )
            finally:
                os.fsync(handle.fileno())
                if not _private_gate_output_is_stable(
                    output, handle.fileno(), identity
                ):
                    raise FreezeError("canonical gate private output changed identity")
                _replay_private_gate_output(handle)
            return completed
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_deploy_source(args: argparse.Namespace) -> dict[str, object]:
    """Bind one verified snapshot to the exact Git identity being deployed."""

    expected_head = str(args.expected_head).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise FreezeError("expected deploy Git SHA must be a lowercase 40-character digest")
    expected_branch = str(args.expected_branch)
    if not expected_branch or any(character in expected_branch for character in "\r\n\0"):
        raise FreezeError("expected deploy Git branch is invalid")

    manifest_path = Path(args.manifest).resolve()
    try:
        payload = json.loads(
            read_bound_regular_file(
                manifest_path,
                label="deploy candidate manifest",
                required_mode=0o600,
            ).decode("utf-8", "strict")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError(f"invalid manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise FreezeError("manifest schema mismatch")

    candidate = payload.get("candidate")
    source = payload.get("source")
    build = payload.get("build")
    identity = build.get("identity") if isinstance(build, dict) else None
    if not isinstance(candidate, dict) or not isinstance(source, dict):
        raise FreezeError("deploy candidate source binding is missing")
    if not isinstance(identity, dict):
        raise FreezeError("deploy candidate build identity is missing")

    raw_snapshot = Path(args.snapshot)
    if raw_snapshot.is_symlink() or not raw_snapshot.is_dir():
        raise FreezeError("deploy candidate snapshot is missing or unsafe")
    snapshot = raw_snapshot.resolve()
    recorded_snapshot_raw = candidate.get("snapshot_path")
    if not isinstance(recorded_snapshot_raw, str) or not recorded_snapshot_raw:
        raise FreezeError("deploy candidate snapshot path is missing")
    if Path(recorded_snapshot_raw).resolve() != snapshot:
        raise FreezeError("deploy candidate snapshot canonical path mismatch")
    if source.get("worktree_dirty") is not False:
        raise FreezeError("deploy candidate was frozen from a dirty worktree")
    if source.get("head") != expected_head:
        raise FreezeError("deploy candidate source HEAD mismatch")
    if identity.get("git_sha") != expected_head:
        raise FreezeError("deploy candidate build identity Git SHA mismatch")
    if source.get("branch") != expected_branch:
        raise FreezeError("deploy candidate source branch mismatch")
    if identity.get("git_branch") != expected_branch:
        raise FreezeError("deploy candidate build identity Git branch mismatch")

    special_paths = physical_special_paths(snapshot)
    if special_paths:
        raise FreezeError(
            "deploy candidate contains unsupported special file: "
            + ", ".join(special_paths[:10])
        )

    build_identity = BuildIdentity(
        git_sha=str(identity.get("git_sha", "")),
        git_branch=str(identity.get("git_branch", "")),
        build_time=str(identity.get("build_time", "")),
    )
    for name, expected in {
        "BUILD_GIT_SHA": build_identity.git_sha,
        "BUILD_GIT_BRANCH": build_identity.git_branch,
        "BUILD_TIME": build_identity.build_time,
    }.items():
        path = snapshot / name
        if path.is_symlink() or not path.is_file():
            raise FreezeError(f"deploy candidate build stamp is missing or unsafe: {name}")
        try:
            observed = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise FreezeError(f"deploy candidate build stamp is unreadable: {name}") from exc
        if observed != expected:
            raise FreezeError(f"deploy candidate build stamp mismatch: {name}")

    from scripts.ops.freeze_worktree_candidate import verify_manifest

    result = verify_manifest(
        argparse.Namespace(manifest=str(manifest_path), snapshot=str(snapshot))
    )
    result.update(
        {
            "build_git_sha": identity.get("git_sha"),
            "source_git_sha": source.get("head"),
        }
    )
    return result


def run_deploy_gate(args: argparse.Namespace) -> dict[str, object]:
    """Run the canonical gate from candidate bytes, then reverify the candidate."""

    try:
        runtime_root = str(args.runtime_root)
        health_url = str(args.health_url)
        base_url = str(args.base_url)
        verify_json_out = str(args.verify_json_out)
        acceptance_json_out = str(args.acceptance_json_out)
    except AttributeError as exc:
        raise FreezeError("deploy gate strict runtime bindings are required") from exc
    before = verify_deploy_source(args)
    snapshot = Path(str(before["snapshot"])).resolve()
    expected_recorded_source = str(
        getattr(args, "expected_recorded_source", None) or args.source
    )
    controller_source_raw = getattr(args, "controller_source", None)
    controller_source = Path(controller_source_raw or args.source).resolve()
    manifest_path = Path(args.manifest).resolve()
    try:
        manifest_bytes = read_bound_regular_file(
            manifest_path,
            label="deploy gate manifest",
            required_mode=0o600,
        )
        manifest = json.loads(manifest_bytes.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("deploy gate manifest is invalid") from exc
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    fixture_hooks = bool(getattr(args, "fixture_allow_test_hooks", False))
    admission_path_raw = getattr(args, "admission_json", None)
    admission: dict[str, object] | None = None
    if admission_path_raw:
        admission = load_admission(
            Path(str(admission_path_raw)),
            runtime_root=Path(runtime_root),
            candidate=snapshot,
            manifest=manifest_path,
            health_env_file=Path(str(getattr(args, "health_env_file", ""))),
            health_url=health_url,
            base_url=base_url,
        )
        runtime_nonce = str(admission.get("nonce", ""))
        runtime_ports = str(admission.get("runtime_ports", ""))
        seatbelt_profile = str(admission.get("_verify_profile", ""))
        expected_manifest_sha256 = str(admission.get("manifest_sha256", ""))
        expected_static_receipt_sha256 = str(
            admission.get("static_receipt_sha256", "")
        )
        if admission.get("candidate_sha256") != before.get("content_sha256"):
            raise FreezeError("deploy runtime admission candidate digest mismatch")
    else:
        if not (
            bool(getattr(args, "controller_owned_runtime", False)) or fixture_hooks
        ):
            raise FreezeError("deploy gate requires controller runtime admission")
        runtime_nonce = str(getattr(args, "runtime_nonce", ""))
        runtime_ports = str(getattr(args, "runtime_ports", ""))
        seatbelt_profile = str(getattr(args, "seatbelt_profile", "") or "")
        expected_manifest_sha256 = str(
            getattr(args, "expected_manifest_sha256", "") or ""
        )
        expected_static_receipt_sha256 = str(
            getattr(args, "expected_static_receipt_sha256", "") or ""
        )
    validate_runtime_binding_values(
        nonce=runtime_nonce,
        ports=runtime_ports,
        health_url=health_url,
        base_url=base_url,
    )
    if not seatbelt_profile and not fixture_hooks:
        raise FreezeError("deploy gate requires a controller-generated Seatbelt profile")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise FreezeError("deploy gate manifest Phase A hash is required")
    if expected_manifest_sha256 != manifest_sha256:
        raise FreezeError("deploy gate manifest Phase A hash mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_static_receipt_sha256):
        raise FreezeError("deploy gate static receipt Phase A hash is required")
    source_record = manifest.get("source") if isinstance(manifest, dict) else None
    recorded_repo = source_record.get("repo") if isinstance(source_record, dict) else None
    if not isinstance(recorded_repo, str) or recorded_repo != expected_recorded_source:
        raise FreezeError("deploy gate source repository binding mismatch")
    build_record = manifest.get("build") if isinstance(manifest, dict) else None
    require_reproducible_frontend = bool(
        isinstance(build_record, dict) and build_record.get("executed") is True
    )
    reproducible_frontend_verified = False
    completed: subprocess.CompletedProcess[bytes] | None = None
    static_receipt_sha256 = ""
    try:
        try:
            with bound_deploy_gate_runtime(
                os.environ,
                source=controller_source,
                requested_python=args.python,
                runtime_root=runtime_root,
                health_env_file=getattr(args, "health_env_file", ""),
                health_url=health_url,
                base_url=base_url,
                verify_json_out=verify_json_out,
                acceptance_json_out=acceptance_json_out,
                allow_test_hooks=bool(getattr(args, "fixture_allow_test_hooks", False)),
            ) as (python_bin, environment):
                _bind_admission_runtime_environment(environment, admission)
                static_receipt, static_receipt_bytes = validate_controller_static_receipt(
                    manifest=manifest,
                    snapshot=snapshot,
                )
                static_receipt_sha256 = hashlib.sha256(static_receipt_bytes).hexdigest()
                if expected_static_receipt_sha256 != static_receipt_sha256:
                    raise FreezeError("deploy gate static receipt Phase A hash mismatch")
                for name in GIT_REPOSITORY_BINDING_ENV:
                    environment.pop(name, None)
                build_time = str(manifest["build"]["identity"]["build_time"])
                rebuilt_frontend = (
                    Path(environment["RUNTIME_ROOT"])
                    / "controller/frontend-dist-rebuild"
                )
                private_static_receipt = (
                    Path(environment["RUNTIME_ROOT"])
                    / "controller"
                    / f"static-gate-receipt.{static_receipt['nonce']}.json"
                )
                write_owned_file_exclusive(private_static_receipt, static_receipt_bytes)
                environment.update(
                    {
                        "APP_BUILD_TIME": build_time,
                        "APP_GIT_BRANCH": str(args.expected_branch),
                        "APP_GIT_SHA": str(args.expected_head),
                        "PYTHON_BIN": str(python_bin),
                        "PYTHON_BIN_FALLBACK": str(python_bin),
                        "VITE_APP_BUILD_TIME": build_time,
                        "VITE_APP_GIT_BRANCH": str(args.expected_branch),
                        "VITE_APP_GIT_SHA": str(args.expected_head),
                        "VKPI_VERIFY_FRONTEND_OUT_DIR": str(rebuilt_frontend),
                        "VKPI_CONTROLLER_STATIC_GATE_RECEIPT": str(private_static_receipt),
                        "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE": "0",
                        "VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE": "1",
                        "VKPI_VERIFY_REQUIRE_RUNTIME": "1",
                        "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY": "0",
                        "VKPI_STRICT_RUN_NONCE": runtime_nonce,
                        "VKPI_STRICT_RUNTIME_PORTS": runtime_ports,
                        "VKPI_STRICT_CANDIDATE_SHA256": str(before["content_sha256"]),
                        "VKPI_STRICT_MANIFEST_SHA256": manifest_sha256,
                        "VKPI_STRICT_STATIC_RECEIPT_SHA256": static_receipt_sha256,
                    }
                )
                from scripts.ops.deploy_gate_runtime import assert_provider_free_environment

                assert_provider_free_environment(environment)
                with strict_snapshot_identity_environment(
                    snapshot,
                    expected_head=str(args.expected_head),
                    expected_branch=str(args.expected_branch),
                    bridge_parent=Path(runtime_root) / "controller",
                    python_bin=python_bin,
                ) as git_environment:
                    environment.update(git_environment)
                    from scripts.ops.freeze_worktree_candidate import _borrow_dependencies

                    with _borrow_dependencies(snapshot, controller_source):
                        completed = _run_controlled_candidate_with_private_output(
                            (
                                ["/usr/bin/sandbox-exec", "-p", seatbelt_profile]
                                if seatbelt_profile
                                else []
                            )
                            + ["/bin/bash", "scripts/verify.sh"],
                            cwd=snapshot,
                            env=environment,
                            runtime_root=Path(runtime_root),
                            run_nonce=runtime_nonce,
                            timeout=1800,
                        )
                if completed.returncode == 0 and require_reproducible_frontend:
                    assert_frontend_dist_reproducible(
                        snapshot / "frontend" / "dist",
                        rebuilt_frontend,
                    )
                    reproducible_frontend_verified = True
                if completed.returncode == 0:
                    try:
                        strict_receipt = json.loads(
                            read_bound_regular_file(
                                Path(verify_json_out),
                                label="candidate canonical deploy receipt",
                            ).decode("utf-8", "strict")
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise FreezeError(
                            "candidate canonical deploy receipt is missing or invalid"
                        ) from exc
                    strict_steps = strict_receipt.get("steps")
                    receipt_steps = [
                        item
                        for item in strict_steps
                        if isinstance(item, dict)
                        and item.get("name")
                        == "controller-bound canonical static receipt"
                    ] if isinstance(strict_steps, list) else []
                    strict_verification = strict_receipt.get("verification")
                    strict_binding = strict_receipt.get("strict_runtime_binding")
                    if (
                        strict_receipt.get("passed") is not True
                        or not isinstance(strict_steps, list)
                        or [
                            item.get("name")
                            for item in strict_steps
                            if isinstance(item, dict)
                        ] != list(CONTROLLER_STATIC_RECEIPT_RUNTIME_STEP_PLAN)
                        or any(
                            not isinstance(item, dict)
                            or item.get("index") != index
                            or item.get("status") != "passed"
                            or item.get("exit_code") != 0
                            for index, item in enumerate(strict_steps, 1)
                        )
                        or strict_receipt.get("failed_steps") != []
                        or len(receipt_steps) != 1
                        or receipt_steps[0].get("status") != "passed"
                        or receipt_steps[0].get("exit_code") != 0
                        or not isinstance(strict_verification, dict)
                        or strict_verification.get("runtime") != "verified"
                        or strict_verification.get("acceptance") != "verified"
                        or strict_verification.get("browser_console") != "not_requested"
                        or strict_verification.get("runtime_log_canary") != "not_requested"
                        or strict_binding != {
                            "nonce": runtime_nonce,
                            "ports": runtime_ports,
                            "candidate_sha256": str(before["content_sha256"]),
                            "static_receipt_sha256": static_receipt_sha256,
                            "manifest_sha256": manifest_sha256,
                        }
                    ):
                        raise FreezeError(
                            "candidate canonical deploy receipt did not consume static proof"
                        )
        except (DeployGateRuntimeError, KeyError, TypeError) as exc:
            raise FreezeError(str(exc)) from exc
    finally:
        candidate_postgres_receipts = [{
            "root": runtime_root,
            "status": "controller_registry_cleanup_required",
            "destructive_cleanup_performed": False,
        }]
        after = verify_deploy_source(args)
    if completed is None or completed.returncode != 0:
        code = completed.returncode if completed is not None else "unavailable"
        raise FreezeError(f"candidate canonical deploy gate failed: {code}")
    after["canonical_deploy_gate"] = True
    if hashlib.sha256(
        read_bound_regular_file(
            manifest_path,
            label="deploy gate manifest",
            required_mode=0o600,
        )
    ).hexdigest() != manifest_sha256:
        raise FreezeError("deploy gate manifest changed while gate was running")
    after["candidate_manifest_sha256"] = manifest_sha256
    after["controller_static_receipt_sha256"] = static_receipt_sha256
    after["runtime_admission"] = (
        {
            "schema": admission.get("schema"),
            "nonce": runtime_nonce,
            "runtime_ports": runtime_ports,
            "provider_credentials_forwarded": False,
            "external_network_allowed": False,
        }
        if admission is not None
        else "controller_internal"
    )
    after["candidate_browser_runtime_postgres"] = candidate_postgres_receipts
    after["frontend_reproducible"] = (
        reproducible_frontend_verified
        if require_reproducible_frontend
        else "not_required_for_unbuilt_fixture"
    )
    return after
