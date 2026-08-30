"""CLI layer for the Phase-A and optional strict-runtime controller."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

from scripts.ops.freeze_git_bridge import GitBridgeError
from scripts.ops.freeze_worktree_candidate import _sha256_path
from scripts.ops.freeze_worktree_contract import FreezeError, write_owned_file_exclusive
from scripts.ops.isolated_strict_runtime_gate import (
    StrictRuntimeGateError,
    run_strict_runtime_gate,
)


ADMISSION_BLOCKERS = (
    "setsid/double-fork descendants lack an OS-level containment boundary",
    "Darwin process-session cleanup evidence is not yet admission-grade",
    "Seatbelt production-equivalent Git and npm/npx dependency access is not verified",
)


def parser(*, default_source: Path) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Build one clean candidate; default Phase A is not runtime acceptance. "
            "Use --strict-runtime for 3 isolated strict gates."
        ),
        epilog="output classification=clean_content_candidate_not_runtime_acceptance",
    )
    result.add_argument("--source", default=str(default_source))
    result.add_argument("--output", required=True)
    result.add_argument("--receipt", default="")
    result.add_argument("--skip-build", action="store_true")
    result.add_argument("--skip-verify", action="store_true")
    result.add_argument("--skip-archive", action="store_true")
    result.add_argument("--strict-runtime", action="store_true")
    result.add_argument("--source-database-url-file", default="")
    result.add_argument("--strict-runtime-timeout", type=int, default=180)
    result.add_argument("--strict-evidence-dir", default="")
    return result


def _strict_payload(args: argparse.Namespace, phase: dict[str, object]) -> dict[str, object]:
    if args.skip_build or args.skip_verify:
        raise StrictRuntimeGateError("strict runtime forbids skip-build and skip-verify")
    database_url = str(args._strict_database_url)
    output = Path(args.output).resolve()
    evidence = Path(
        args._strict_evidence_dir
    ).resolve()
    runtime = run_strict_runtime_gate(
        source=Path(args.source).resolve(), candidate=output, phase_payload=phase,
        source_database_url=database_url, evidence_dir=evidence,
        timeout=max(30, min(1800, int(args.strict_runtime_timeout))),
    )
    phase["runtime_acceptance"] = runtime
    phase["classification"] = "clean_content_candidate_with_strict_runtime_acceptance"
    phase["strict_evidence_dir"] = str(evidence)
    receipt = evidence / "strict-runtime-provenance.json"
    phase["strict_runtime_receipt"] = str(receipt)
    write_owned_file_exclusive(
        receipt, (json.dumps(phase, ensure_ascii=False, indent=2) + "\n").encode()
    )
    digest = _sha256_path(receipt)
    write_owned_file_exclusive(
        receipt.with_suffix(receipt.suffix + ".sha256"),
        f"{digest}  {receipt.name}\n".encode(),
    )
    return phase


def _strict_preflight_args(args: argparse.Namespace) -> None:
    from scripts.ops.isolated_strict_runtime_gate import _source_dump_environment
    from scripts.ops.strict_runtime_seatbelt import require_sandbox_exec
    if not args.source_database_url_file or not args.strict_evidence_dir:
        raise StrictRuntimeGateError(
            "strict runtime requires URL file and explicit external evidence directory"
        )
    path = Path(args.source_database_url_file)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise StrictRuntimeGateError("source database URL path has unsafe parents")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        data = os.read(descriptor, 8193)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077 or len(data) > 8192
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise StrictRuntimeGateError("source database URL file is unsafe")
    database_url = data.decode("utf-8", "strict").strip()
    _source_dump_environment(database_url, Path("/tmp"))
    require_sandbox_exec()
    source, output = Path(args.source).resolve(), Path(args.output).resolve()
    evidence = Path(args.strict_evidence_dir).resolve()
    if evidence.exists() or evidence == source or source in evidence.parents \
            or evidence == output or output in evidence.parents or evidence in output.parents:
        raise StrictRuntimeGateError("strict evidence directory must be new and external")
    args._strict_database_url = database_url
    args._strict_evidence_dir = str(evidence)


def main(*, run_phase_a: Callable[[argparse.Namespace], dict[str, object]]) -> int:
    from scripts.ops.run_isolated_worktree_gate import IsolatedWorktreeGateError

    args = parser(default_source=Path(__file__).resolve().parents[2]).parse_args()
    # Phase A is a filesystem-only, provider-free candidate build.  It starts
    # no service and therefore does not depend on the missing Darwin process
    # containment boundary.  Keep the strict path fail-closed *before* Phase A
    # so a caller asking for runtime acceptance cannot accidentally receive a
    # weaker content-only candidate after the admission failure.
    if args.strict_runtime:
        sys.stderr.write(
            "isolated strict-runtime admission blocked; no candidate work was started:\n- "
            + "\n- ".join(ADMISSION_BLOCKERS) + "\n"
        )
        return 1
    try:
        phase = run_phase_a(args)
    except (FreezeError, GitBridgeError, IsolatedWorktreeGateError, OSError, ValueError) as exc:
        sys.stderr.write(
            f"isolated Phase-A candidate failed: {type(exc).__name__}: {exc}\n"
        )
        return 1
    sys.stdout.write(json.dumps(phase, ensure_ascii=False, sort_keys=True) + "\n")
    return 0
