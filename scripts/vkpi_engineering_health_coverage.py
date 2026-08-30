#!/usr/bin/env python3
"""Create and validate fresh, source-bound backend coverage receipts.

The runner always uses a newly-created private coverage workspace and the
reviewed full backend test command.  A receipt is accepted only while its
source snapshot, Git state, command, coverage data and coverage JSON remain
identical to the evidence candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts import vkpi_engineering_health_snapshot as snapshot
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import vkpi_engineering_health_snapshot as snapshot
    from stdout_utils import out as stdout_out


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "vkpi_engineering_health_coverage_receipt_v1"
SOURCE_ROOTS = ("backend/app", "frontend/src", "scripts")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".css"}
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "fixtures",
    "generated",
    "migrations",
    "node_modules",
}
TEST_DIRECTORY_NAMES = {"test", "tests", "__tests__"}
TEST_FILENAME_MARKERS = (".test.", ".spec.")
CANONICAL_TEST_COMMAND = (
    ".venv/bin/python",
    "-m",
    "pytest",
    "tests",
    "backend/tests",
    "-q",
    "--cov=backend/app",
    "--cov-branch",
    "--cov-report=",
)
MAX_RECEIPT_AGE = timedelta(hours=24)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
# Contract v1.1 code_evidence_methodology.core_mutation_score.core_scope_groups.
CORE_PATH_PREFIXES = (
    "backend/app/domains/discovery/",
    "backend/app/domains/kol/",
    "backend/app/domains/launch/",
    "backend/app/domains/projects/",
    "backend/app/domains/search/",
    "backend/app/services/ai/",
)
CHANGE_WINDOW_DAYS = 30
_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SCOPE_METRIC_FIELDS = (
    "core_path_coverage", "core_path_covered_lines", "core_path_num_statements",
    "core_path_file_count", "change_coverage", "change_covered_lines",
    "change_num_statements", "change_file_count", "change_base",
    "change_window_days", "change_coverage_method", "change_coverage_reason",
)


class CoverageReceiptError(ValueError):
    """Raised when coverage provenance is incomplete or has drifted."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise CoverageReceiptError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CoverageReceiptError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command_sha256(command: Sequence[str]) -> str:
    payload = json.dumps(list(command), ensure_ascii=False, separators=(",", ":"))
    return _sha256(payload.encode("utf-8"))


def source_snapshot(root: Path) -> snapshot.SourceSnapshot:
    """Use exactly the production-source scope used by the static collector."""

    return snapshot.snapshot_sources(
        root, SOURCE_ROOTS, SOURCE_SUFFIXES, skip_parts=SKIP_PARTS,
        test_directory_names=TEST_DIRECTORY_NAMES,
        test_filename_markers=TEST_FILENAME_MARKERS,
    )


def _stable_bytes(path: Path, *, label: str) -> bytes:
    try:
        content = snapshot._stable_regular_bytes(path)  # noqa: SLF001
    except (OSError, snapshot.SnapshotError) as exc:
        raise CoverageReceiptError(f"cannot read stable {label}: {path}") from exc
    if not content:
        raise CoverageReceiptError(f"{label} must not be empty: {path}")
    return content


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageReceiptError(f"{label} must be a non-negative integer")
    return value


def _relative_source_path(root: Path, value: str) -> str:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CoverageReceiptError(f"coverage source escapes repository: {value}") from exc


def _git_read(root: Path, *args: str) -> str:
    try:
        return snapshot._git(root, snapshot._trusted_git_binary(), *args)  # noqa: SLF001
    except snapshot.SnapshotError as exc:
        raise CoverageReceiptError(f"change-window git command failed: {args[0]}") from exc


def _hunk_added_lines(line: str) -> range:
    match = _HUNK_RE.match(line)
    if match is None:
        raise CoverageReceiptError(f"unparseable diff hunk header: {line[:80]}")
    start, count = int(match.group(1)), int(match.group(2) or "1")
    return range(start, start + count)


def _changed_head_lines(root: Path) -> tuple[str, dict[str, set[int]]]:
    """Added/changed HEAD line numbers per backend file over the 30-day window.

    The window is anchored at the HEAD committer date so that build-time and
    validation-time recomputation see the identical diff; the base is the
    newest HEAD ancestor at or before HEAD-30d (the empty tree when history is
    younger than the window, i.e. every current line counts as changed).
    """
    head_epoch = _git_read(root, "log", "-1", "--format=%ct", "HEAD")
    if not head_epoch.isdigit():
        raise CoverageReceiptError("HEAD committer timestamp is unavailable")
    boundary = int(head_epoch) - CHANGE_WINDOW_DAYS * 86400
    stamp = datetime.fromtimestamp(boundary, UTC).strftime("%Y-%m-%d %H:%M:%S +0000")
    base = _git_read(root, "rev-list", "-1", f"--before={stamp}", "HEAD")
    diff_base = base or _git_read(root, "hash-object", "-t", "tree", os.devnull)
    diff = _git_read(
        root, "-c", "core.quotepath=false", "diff", "--unified=0", "--no-renames",
        diff_base, "HEAD", "--", "backend/app",
    )
    changed: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t", 1)[0]
            if target == "/dev/null":
                current = None
            elif target.startswith("b/"):
                current = changed.setdefault(target[2:], set())
            else:
                raise CoverageReceiptError(f"unparseable diff target: {line[:80]}")
        elif line.startswith("@@ ") and current is not None:
            current.update(_hunk_added_lines(line))
    return base or "empty_tree", changed


def _line_number_set(row: dict[str, Any], field: str, path: str) -> set[int] | None:
    raw = row.get(field)
    if not isinstance(raw, list):
        return None
    lines: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise CoverageReceiptError(f"{path}.{field} must hold positive line numbers")
        lines.add(item)
    return lines


def _measured_line_sets(
    scoped: dict[str, set[int]], per_file: dict[str, dict[str, Any]]
) -> dict[str, tuple[set[int], set[int]]] | None:
    sets: dict[str, tuple[set[int], set[int]]] = {}
    for path in scoped:
        executed = _line_number_set(per_file[path]["row"], "executed_lines", path)
        missing = _line_number_set(per_file[path]["row"], "missing_lines", path)
        if executed is None or missing is None:
            return None
        if executed & missing:
            raise CoverageReceiptError(f"executed/missing line overlap: {path}")
        sets[path] = (executed, executed | missing)
    return sets


def _summary_line_totals(
    per_file: dict[str, dict[str, Any]], paths: Sequence[str]
) -> tuple[int, int]:
    covered = sum(per_file[path]["covered_lines"] for path in paths)
    statements = sum(per_file[path]["num_statements"] for path in paths)
    return covered, statements


def _changed_hit_totals(
    scoped: dict[str, set[int]], line_sets: dict[str, tuple[set[int], set[int]]]
) -> tuple[int, int]:
    covered = sum(len(scoped[path] & hits) for path, (hits, _) in line_sets.items())
    statements = sum(len(scoped[path] & full) for path, (_, full) in line_sets.items())
    return covered, statements


def _core_path_metrics(per_file: dict[str, dict[str, Any]]) -> dict[str, Any]:
    chosen = sorted(path for path in per_file if path.startswith(CORE_PATH_PREFIXES))
    covered, statements = _summary_line_totals(per_file, chosen)
    return {
        "core_path_file_count": len(chosen),
        "core_path_covered_lines": covered,
        "core_path_num_statements": statements,
        "core_path_coverage": covered / statements if statements else None,
    }


def _change_metrics(root: Path, per_file: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base, changed = _changed_head_lines(root)
    scoped = {
        path: lines
        for path, lines in sorted(changed.items())
        if path in per_file and lines
    }
    line_sets = _measured_line_sets(scoped, per_file)
    if line_sets is None:
        method = "changed_file_line_coverage_approx"
        reason = (
            "口径近似:coverage JSON 无逐行 executed/missing 上下文,"
            "以近30天改动文件的整文件行覆盖聚合近似改动行覆盖(窗口锚定 HEAD 提交时间)"
        )
        covered, statements = _summary_line_totals(per_file, sorted(scoped))
    else:
        method = "coverage_line_hits_x_git_diff_30d"
        reason = (
            "coverage 逐行 executed/missing 集合 × git diff 近30天改动行集"
            "(窗口锚定 HEAD 提交时间)"
        )
        covered, statements = _changed_hit_totals(scoped, line_sets)
    if not scoped:
        method, reason = "none", "no_measured_backend_changes_in_window"
    elif statements == 0:
        reason = f"{reason};no_changed_statement_lines_in_window"
    return {
        "change_window_days": CHANGE_WINDOW_DAYS,
        "change_base": base,
        "change_file_count": len(scoped),
        "change_covered_lines": covered,
        "change_num_statements": statements,
        "change_coverage": covered / statements if statements else None,
        "change_coverage_method": method,
        "change_coverage_reason": reason,
    }


def parse_coverage_json(
    data: bytes,
    *,
    root: Path,
    captured_source: snapshot.SourceSnapshot,
) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoverageReceiptError("coverage JSON is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise CoverageReceiptError("coverage JSON top level must be an object")
    meta = payload.get("meta")
    files = payload.get("files")
    totals = payload.get("totals")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise CoverageReceiptError("coverage JSON must contain branch coverage")
    if not isinstance(files, dict) or not files:
        raise CoverageReceiptError("coverage JSON must contain measured files")
    if not isinstance(totals, dict):
        raise CoverageReceiptError("coverage JSON totals are required")

    production_sources = {
        item.relative_path
        for item in captured_source.files
        if item.relative_path.startswith("backend/app/") and item.relative_path.endswith(".py")
    }
    measured: list[str] = []
    per_file: dict[str, dict[str, Any]] = {}
    sums = {"covered_lines": 0, "num_statements": 0, "covered_branches": 0, "num_branches": 0}
    for raw_path, row in sorted(files.items()):
        if not isinstance(raw_path, str) or not isinstance(row, dict):
            raise CoverageReceiptError("coverage file entries must be objects")
        relative = _relative_source_path(root, raw_path)
        if relative not in production_sources:
            raise CoverageReceiptError(
                f"coverage file is outside captured backend sources: {raw_path}"
            )
        if relative in per_file:
            raise CoverageReceiptError(f"coverage source appears more than once: {relative}")
        summary = row.get("summary")
        if not isinstance(summary, dict):
            raise CoverageReceiptError(f"coverage summary missing: {raw_path}")
        counts = {
            field: _nonnegative_int(summary.get(field), label=f"{raw_path}.{field}")
            for field in sums
        }
        for field, count in counts.items():
            sums[field] += count
        per_file[relative] = {"row": row, **counts}
        measured.append(relative)

    measured_sources = set(measured)
    if measured_sources != production_sources:
        missing = sorted(production_sources - measured_sources)
        extra = sorted(measured_sources - production_sources)
        details = []
        if missing:
            details.append(f"missing={missing[:5]}")
        if extra:
            details.append(f"extra={extra[:5]}")
        raise CoverageReceiptError(
            "coverage JSON does not cover the complete backend source scope"
            + (f": {'; '.join(details)}" if details else "")
        )

    for field, observed in sums.items():
        reported = _nonnegative_int(totals.get(field), label=f"totals.{field}")
        if reported != observed:
            raise CoverageReceiptError(
                f"coverage totals mismatch for {field}: {reported} != {observed}"
            )
    if sums["num_statements"] <= 0:
        raise CoverageReceiptError("line coverage denominator must be positive")
    if sums["num_branches"] <= 0:
        raise CoverageReceiptError("branch coverage denominator must be positive")
    if sums["covered_lines"] > sums["num_statements"]:
        raise CoverageReceiptError("covered lines exceed statements")
    if sums["covered_branches"] > sums["num_branches"]:
        raise CoverageReceiptError("covered branches exceed branches")
    return {
        **sums,
        "line_coverage": sums["covered_lines"] / sums["num_statements"],
        "branch_coverage": sums["covered_branches"] / sums["num_branches"],
        "measured_file_count": len(measured),
        "measured_files_sha256": _sha256(("\n".join(measured) + "\n").encode("utf-8")),
        "coverage_format": meta.get("format"),
        "coverage_version": str(meta.get("version") or ""),
        **_core_path_metrics(per_file),
        **_change_metrics(root, per_file),
    }


def _validated_git_state(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoverageReceiptError(f"{label} must be an object")
    state = dict(value)
    for field in ("head", "branch", "status_sha256"):
        if not str(state.get(field) or ""):
            raise CoverageReceiptError(f"{label}.{field} is required")
    if not _SHA256_RE.fullmatch(str(state.get("status_sha256") or "")):
        raise CoverageReceiptError(f"{label}.status_sha256 must be sha256")
    return state


def _artifact_label(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_artifact(root: Path, value: Any, *, label: str) -> Path:
    raw = str(value or "")
    if not raw:
        raise CoverageReceiptError(f"{label} artifact path is required")
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _artifact_entry(
    root: Path, source_path: Path, receipt_path: Path, *, label: str
) -> tuple[dict[str, Any], bytes]:
    content = _stable_bytes(source_path, label=label)
    entry = {
        "path": _artifact_label(root, receipt_path),
        "sha256": _sha256(content),
        "byte_count": len(content),
    }
    return entry, content


def build_coverage_receipt(
    *,
    root: Path,
    coverage_data_source: Path, coverage_json_source: Path,
    coverage_data_receipt_path: Path, coverage_json_receipt_path: Path,
    command: Sequence[str], exit_code: int, started_at: str, finished_at: str,
    source_before: snapshot.SourceSnapshot, source_after: snapshot.SourceSnapshot,
    git_before: dict[str, Any], git_after: dict[str, Any],
    fresh_workspace_nonce: str, artifacts_existed_before: bool,
    stdout_sha256: str = "", stderr_sha256: str = "",
) -> dict[str, Any]:
    if tuple(command) != CANONICAL_TEST_COMMAND:
        raise CoverageReceiptError("non-canonical coverage test command")
    if isinstance(exit_code, bool) or exit_code != 0:
        raise CoverageReceiptError("coverage test command did not pass")
    if artifacts_existed_before:
        raise CoverageReceiptError("fresh coverage workspace contained old artifacts")
    try:
        uuid.UUID(fresh_workspace_nonce)
    except (ValueError, AttributeError) as exc:
        raise CoverageReceiptError("fresh workspace nonce must be a UUID") from exc
    started = _parse_timestamp(started_at, label="test.started_at")
    finished = _parse_timestamp(finished_at, label="test.finished_at")
    if finished < started:
        raise CoverageReceiptError("coverage test finished before it started")
    if not source_before.complete or not source_after.complete:
        raise CoverageReceiptError("source snapshot is incomplete")
    if source_before.identity() != source_after.identity():
        raise CoverageReceiptError("source content drifted during coverage run")
    start_git = _validated_git_state(git_before, label="git_start")
    end_git = _validated_git_state(git_after, label="git_end")
    if start_git != end_git:
        raise CoverageReceiptError("Git status drifted during coverage run")

    data_entry, _ = _artifact_entry(
        root, coverage_data_source, coverage_data_receipt_path, label="coverage data"
    )
    json_entry, json_bytes = _artifact_entry(
        root, coverage_json_source, coverage_json_receipt_path, label="coverage JSON"
    )
    coverage = parse_coverage_json(json_bytes, root=root, captured_source=source_before)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": finished_at,
        "passed": True,
        "candidate": {
            "repo": str(root.resolve()),
            "source_content_sha256": source_before.content_sha256,
            "source_file_count": len(source_before.files),
            "source_start": source_before.identity(),
            "source_end": source_after.identity(),
            "git_start": start_git, "git_end": end_git,
        },
        "test": {
            "command": list(command), "command_sha256": command_sha256(command),
            "exit_code": exit_code, "started_at": started_at, "finished_at": finished_at,
            "fresh_workspace_nonce": fresh_workspace_nonce,
            "artifacts_existed_before": False,
            "stdout_sha256": stdout_sha256, "stderr_sha256": stderr_sha256,
        },
        "artifacts": {"coverage_data": data_entry, "coverage_json": json_entry},
        "coverage": coverage,
    }


def _validate_command(receipt: dict[str, Any]) -> dict[str, Any]:
    test = receipt.get("test")
    if not isinstance(test, dict):
        raise CoverageReceiptError("coverage receipt test object is required")
    command = test.get("command")
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise CoverageReceiptError("coverage test command must be a string array")
    if tuple(command) != CANONICAL_TEST_COMMAND:
        raise CoverageReceiptError("coverage receipt command is not canonical")
    if test.get("command_sha256") != command_sha256(command):
        raise CoverageReceiptError("coverage test command hash mismatch")
    if isinstance(test.get("exit_code"), bool) or test.get("exit_code") != 0:
        raise CoverageReceiptError("coverage receipt test did not pass")
    if test.get("artifacts_existed_before") is not False:
        raise CoverageReceiptError("coverage receipt did not use fresh artifacts")
    try:
        uuid.UUID(str(test.get("fresh_workspace_nonce") or ""))
    except ValueError as exc:
        raise CoverageReceiptError("coverage receipt workspace nonce is invalid") from exc
    return test


def _candidate_binding(
    evidence: dict[str, Any], receipt: dict[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    evidence_candidate = evidence.get("candidate")
    receipt_candidate = receipt.get("candidate")
    if not isinstance(evidence_candidate, dict) or not isinstance(receipt_candidate, dict):
        raise CoverageReceiptError("coverage candidate binding is required")
    if evidence_candidate.get("source_and_status_stable") is not True:
        raise CoverageReceiptError("engineering evidence source/status is not stable")
    root = Path(str(receipt_candidate.get("repo") or "")).resolve()
    if not root.is_dir():
        raise CoverageReceiptError("coverage receipt repository is unavailable")
    evidence_repo = Path(str(evidence_candidate.get("repo") or "")).resolve()
    if root != evidence_repo:
        raise CoverageReceiptError("coverage receipt repository mismatch")
    source_hash = str(receipt_candidate.get("source_content_sha256") or "")
    if not _SHA256_RE.fullmatch(source_hash):
        raise CoverageReceiptError("coverage source_content_sha256 is required")
    if source_hash != str(evidence_candidate.get("source_content_sha256") or ""):
        raise CoverageReceiptError("coverage source content mismatch")
    start_git = _validated_git_state(receipt_candidate.get("git_start"), label="git_start")
    end_git = _validated_git_state(receipt_candidate.get("git_end"), label="git_end")
    if start_git != end_git:
        raise CoverageReceiptError("coverage receipt Git start/end mismatch")
    for evidence_name in ("head", "branch", "status_sha256"):
        if str(evidence_candidate.get(evidence_name) or "") != str(
            start_git.get(evidence_name) or ""
        ):
            raise CoverageReceiptError(f"coverage candidate {evidence_name} mismatch")
    return root, evidence_candidate, receipt_candidate


def _validate_artifact(root: Path, entry: Any, *, label: str) -> bytes:
    if not isinstance(entry, dict):
        raise CoverageReceiptError(f"{label} artifact entry is required")
    declared_hash = entry.get("sha256")
    declared_size = entry.get("byte_count")
    if not isinstance(declared_hash, str) or not _SHA256_RE.fullmatch(declared_hash):
        raise CoverageReceiptError(f"{label} artifact sha256 is invalid")
    if isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size <= 0:
        raise CoverageReceiptError(f"{label} artifact byte_count is invalid")
    path = _resolve_artifact(root, entry.get("path"), label=label)
    content = _stable_bytes(path, label=label)
    if declared_hash != _sha256(content):
        raise CoverageReceiptError(f"{label} artifact hash mismatch")
    if declared_size != len(content):
        raise CoverageReceiptError(f"{label} artifact size mismatch")
    return content


def validate_coverage_receipt(
    evidence: dict[str, Any],
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise CoverageReceiptError("unsupported coverage receipt schema")
    if receipt.get("passed") is not True:
        raise CoverageReceiptError("coverage receipt did not pass")
    test = _validate_command(receipt)
    started = _parse_timestamp(test.get("started_at"), label="test.started_at")
    finished = _parse_timestamp(test.get("finished_at"), label="test.finished_at")
    if finished < started:
        raise CoverageReceiptError("coverage test timestamps are reversed")
    if str(receipt.get("generated_at") or "") != str(test.get("finished_at") or ""):
        raise CoverageReceiptError("coverage receipt generated_at mismatch")
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    if finished > current_time + timedelta(minutes=5):
        raise CoverageReceiptError("coverage receipt timestamp is in the future")
    if current_time - finished > MAX_RECEIPT_AGE:
        raise CoverageReceiptError("coverage receipt is older than 24 hours")

    root, _, receipt_candidate = _candidate_binding(evidence, receipt)
    git_before = snapshot.trusted_git_state(root)
    source_before = source_snapshot(root)
    expected_source = str(receipt_candidate["source_content_sha256"])
    if not source_before.complete or source_before.content_sha256 != expected_source:
        raise CoverageReceiptError("current source content does not match receipt")
    if source_before.identity() != receipt_candidate.get("source_start"):
        raise CoverageReceiptError("coverage receipt source-start identity mismatch")
    if receipt_candidate.get("source_start") != receipt_candidate.get("source_end"):
        raise CoverageReceiptError("coverage receipt source start/end mismatch")
    if git_before != receipt_candidate.get("git_start"):
        raise CoverageReceiptError("current Git status does not match receipt")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        raise CoverageReceiptError("coverage receipt artifacts are required")
    _validate_artifact(root, artifacts.get("coverage_data"), label="coverage data")
    json_bytes = _validate_artifact(root, artifacts.get("coverage_json"), label="coverage JSON")
    parsed = parse_coverage_json(json_bytes, root=root, captured_source=source_before)
    if parsed != receipt.get("coverage"):
        raise CoverageReceiptError("coverage metrics do not match coverage JSON")

    source_after = source_snapshot(root)
    git_after = snapshot.trusted_git_state(root)
    if source_before.identity() != source_after.identity():
        raise CoverageReceiptError("source drifted while merging coverage")
    if git_before != git_after:
        raise CoverageReceiptError("Git status drifted while merging coverage")
    return {
        "observed_at": str(test["finished_at"]),
        "line_coverage": parsed["line_coverage"],
        "branch_coverage": parsed["branch_coverage"],
        "num_statements": parsed["num_statements"],
        "num_branches": parsed["num_branches"],
        "covered_lines": parsed["covered_lines"],
        "covered_branches": parsed["covered_branches"],
        "measured_file_count": parsed["measured_file_count"],
        "measured_files_sha256": parsed["measured_files_sha256"],
        "command": list(CANONICAL_TEST_COMMAND),
        "command_sha256": command_sha256(CANONICAL_TEST_COMMAND),
        "coverage_data_sha256": artifacts["coverage_data"]["sha256"],
        "coverage_json_sha256": artifacts["coverage_json"]["sha256"],
        **{field: parsed[field] for field in _SCOPE_METRIC_FIELDS},
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_output_path(root: Path, path: Path, *, suffix: str) -> Path:
    resolved = path.resolve()
    if resolved.suffix != suffix:
        raise CoverageReceiptError(f"output must use {suffix}: {path}")
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != "runtime":
        raise CoverageReceiptError(
            "repository-local coverage outputs must be under ignored runtime/"
        )
    return resolved


def run_fresh_coverage(
    *,
    root: Path,
    receipt_path: Path,
    coverage_data_path: Path,
    coverage_json_path: Path,
) -> dict[str, Any]:
    root = root.resolve()
    if not (root / ".git").exists():
        raise CoverageReceiptError("repository root with .git is required")
    receipt_path = _validate_output_path(root, receipt_path, suffix=".json")
    coverage_data_path = _validate_output_path(root, coverage_data_path, suffix=".coverage")
    coverage_json_path = _validate_output_path(root, coverage_json_path, suffix=".json")
    if len({receipt_path, coverage_data_path, coverage_json_path}) != 3:
        raise CoverageReceiptError("coverage receipt and artifact paths must be distinct")
    command_executable = root / CANONICAL_TEST_COMMAND[0]
    if not command_executable.is_file():
        raise CoverageReceiptError(f"canonical test interpreter is unavailable: {command_executable}")

    source_before = source_snapshot(root)
    git_before = snapshot.trusted_git_state(root)
    if not source_before.complete:
        raise CoverageReceiptError("source snapshot is incomplete before coverage")
    nonce = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix=f"vkpi-coverage-{nonce}-") as raw_tmp:
        workspace = Path(raw_tmp)
        data_source = workspace / "fresh.coverage"
        json_source = workspace / "fresh-coverage.json"
        stdout_path = workspace / "pytest.stdout"
        stderr_path = workspace / "pytest.stderr"
        existed_before = data_source.exists() or json_source.exists()
        environment = dict(os.environ)
        environment["COVERAGE_FILE"] = str(data_source)
        environment["PYTHONPATH"] = "backend:."
        started_at = _utc_now()
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            completed = subprocess.run(
                list(CANONICAL_TEST_COMMAND), cwd=root, env=environment,
                stdin=subprocess.DEVNULL, stdout=stdout_file, stderr=stderr_file, check=False,
            )
        finished_at = _utc_now()
        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        if completed.returncode != 0:
            stdout_tail = stdout_bytes.decode("utf-8", "replace")[-4000:]
            stderr_tail = stderr_bytes.decode("utf-8", "replace")[-1000:]
            raise CoverageReceiptError(
                "canonical coverage tests failed"
                f" (exit={completed.returncode})\n"
                f"--- pytest stdout tail ---\n{stdout_tail}\n"
                f"--- pytest stderr tail ---\n{stderr_tail}"
            )
        if not data_source.is_file():
            raise CoverageReceiptError("fresh coverage data was not produced")
        json_completed = subprocess.run(
            [
                str(command_executable), "-m", "coverage", "json",
                "--data-file", str(data_source), "-o", str(json_source), "--pretty-print",
            ],
            cwd=root, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, check=False,
        )
        if json_completed.returncode != 0 or not json_source.is_file():
            raise CoverageReceiptError("coverage JSON generation failed")
        source_after = source_snapshot(root)
        git_after = snapshot.trusted_git_state(root)
        receipt = build_coverage_receipt(
            root=root,
            coverage_data_source=data_source, coverage_json_source=json_source,
            coverage_data_receipt_path=coverage_data_path,
            coverage_json_receipt_path=coverage_json_path,
            command=CANONICAL_TEST_COMMAND, exit_code=completed.returncode,
            started_at=started_at, finished_at=finished_at,
            source_before=source_before, source_after=source_after,
            git_before=git_before, git_after=git_after,
            fresh_workspace_nonce=nonce, artifacts_existed_before=existed_before,
            stdout_sha256=_sha256(stdout_bytes), stderr_sha256=_sha256(stderr_bytes),
        )
        _atomic_write(coverage_data_path, data_source.read_bytes())
        _atomic_write(coverage_json_path, json_source.read_bytes())
        serialized = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write(receipt_path, serialized.encode("utf-8"))
        return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--receipt", default="runtime/engineering-health/coverage/receipt.json")
    parser.add_argument(
        "--coverage-data", default="runtime/engineering-health/coverage/fresh.coverage"
    )
    parser.add_argument(
        "--coverage-json", default="runtime/engineering-health/coverage/fresh-coverage.json"
    )
    return parser.parse_args(argv)


def _absolute_output(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    receipt = run_fresh_coverage(
        root=root,
        receipt_path=_absolute_output(root, args.receipt),
        coverage_data_path=_absolute_output(root, args.coverage_data),
        coverage_json_path=_absolute_output(root, args.coverage_json),
    )
    stdout_out(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
