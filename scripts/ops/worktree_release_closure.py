#!/usr/bin/env python3
"""Freeze an explicitly reviewed dirty-worktree functional closure.

This is a bundle *preflight*, not a bundler or a deploy command.  It compares
the entire current dirty worktree with a previously captured inventory,
validates exact lane/file review assertions, expands statically resolvable
dirty source dependencies, and writes a hash-addressed manifest.  It never
stages, commits, archives, uploads, deploys, or grants release authority.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterable


_STDOUT_UTILS_DIR = Path(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in sys.path:
    sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402


try:  # package import in tests
    from scripts.ops.worktree_release_scope import (
        SCHEMA as INVENTORY_SCHEMA,
        build_manifest as build_inventory,
    )
except ModuleNotFoundError:  # direct script execution
    from worktree_release_scope import (  # type: ignore[no-redef]
        SCHEMA as INVENTORY_SCHEMA,
        build_manifest as build_inventory,
    )


REVIEW_SCHEMA = "vkpi.worktree-release-review.v1"
CLOSURE_SCHEMA = "vkpi.worktree-release-closure.v1"
REVIEW_STATUS = "reviewed_for_bundle_preflight"
REVIEW_SCOPE = "selection_only_no_deploy_authority"
MAX_FILE_BYTES = 25 * 1024 * 1024

_PYTHON_SUFFIX = ".py"
_FRONTEND_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_RESOLVE_SUFFIXES = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".css",
    ".scss",
)
_FORBIDDEN_COMPONENTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "runtime",
    "secrets",
    "uploads",
    "venv",
}
_FORBIDDEN_NAMES = {".env", "id_ed25519", "id_rsa"}
_FORBIDDEN_SUFFIXES = (".key", ".p12", ".pfx", ".pem")
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"sk_live_[A-Za-z0-9]{16,}"),
)
_JS_SPECIFIER = re.compile(
    r"(?:\b(?:import|export)\b[\s\S]*?\bfrom\s*|\bimport\s*\(|\brequire\s*\()"
    r"['\"]([^'\"]+)['\"]",
)
_SHELL_SOURCE = re.compile(r"^\s*(?:source|\.)\s+(['\"]?)([^\s'\"]+)\1(?:\s|$)")


class ClosureError(RuntimeError):
    """Stable fail-closed error exposed by the command line contract."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _entry_identity(entry: dict[str, object]) -> dict[str, object]:
    return {
        "category": entry.get("category"),
        "path": entry.get("path"),
        "sha256": entry.get("sha256"),
        "size_bytes": entry.get("size_bytes"),
        "status": entry.get("status"),
        "tracked": entry.get("tracked"),
    }


def inventory_identity(manifest: dict[str, object]) -> dict[str, object]:
    """Return the time/path independent identity of a worktree inventory."""

    entries = manifest.get("entries")
    if manifest.get("schema") != INVENTORY_SCHEMA or not isinstance(entries, list):
        raise ClosureError("invalid_inventory_schema")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ClosureError("invalid_inventory_entry")
        entry = _entry_identity(raw)
        path = entry["path"]
        if not isinstance(path, str) or not path or path in seen:
            raise ClosureError("invalid_or_duplicate_inventory_path", repr(path))
        if not isinstance(entry["category"], str) or not isinstance(entry["status"], str):
            raise ClosureError("invalid_inventory_entry", path)
        seen.add(path)
        normalized.append(entry)
    normalized.sort(key=lambda row: str(row["path"]))
    branch = manifest.get("branch")
    head = manifest.get("head")
    if not isinstance(branch, str) or not branch or not isinstance(head, str) or len(head) != 40:
        raise ClosureError("invalid_inventory_git_identity")
    return {"branch": branch, "head": head, "entries": normalized}


def inventory_digest(manifest: dict[str, object]) -> str:
    return _digest(inventory_identity(manifest))


def lane_digest(manifest: dict[str, object], lane: str) -> str:
    rows = [
        _entry_identity(row)
        for row in manifest["entries"]  # type: ignore[index]
        if isinstance(row, dict) and row.get("category") == lane
    ]
    rows.sort(key=lambda row: str(row["path"]))
    return _digest({"lane": lane, "entries": rows})


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClosureError(f"invalid_{label}_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise ClosureError(f"invalid_{label}_json", "top level must be an object")
    return value


def _target_path(display_path: str) -> str:
    if " -> " in display_path:
        raise ClosureError("rename_or_copy_requires_separate_review", display_path)
    path = PurePosixPath(display_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ClosureError("unsafe_path", display_path)
    return path.as_posix()


def _entry_map(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for raw in manifest["entries"]:  # type: ignore[index]
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            raise ClosureError("invalid_inventory_entry")
        target = _target_path(str(raw["path"]))
        if target in result:
            raise ClosureError("duplicate_inventory_target", target)
        result[target] = raw
    return result


def _parse_review_time(value: object) -> str:
    if not isinstance(value, str):
        raise ClosureError("invalid_reviewed_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClosureError("invalid_reviewed_at") from exc
    if parsed.tzinfo is None:
        raise ClosureError("invalid_reviewed_at", "timezone required")
    return value


def _reviewed_selection(
    inventory: dict[str, object], review: dict[str, object]
) -> tuple[set[str], dict[str, str], dict[str, object]]:
    if review.get("schema") != REVIEW_SCHEMA:
        raise ClosureError("invalid_review_schema")
    expected_inventory = inventory_digest(inventory)
    if review.get("inventory_digest") != expected_inventory:
        raise ClosureError("review_inventory_digest_mismatch")
    if review.get("expected_branch") != inventory.get("branch"):
        raise ClosureError("review_branch_mismatch")
    if review.get("expected_head") != inventory.get("head"):
        raise ClosureError("review_head_mismatch")

    assertion = review.get("review")
    if not isinstance(assertion, dict):
        raise ClosureError("missing_review_assertion")
    if assertion.get("status") != REVIEW_STATUS:
        raise ClosureError("selection_not_reviewed")
    if assertion.get("scope") != REVIEW_SCOPE:
        raise ClosureError("invalid_review_scope")
    if assertion.get("deploy_authorized") is not False:
        raise ClosureError("deploy_authority_must_be_false")
    reviewer = assertion.get("reviewer_id")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ClosureError("missing_reviewer_id")
    reviewed_at = _parse_review_time(assertion.get("reviewed_at"))

    entries = _entry_map(inventory)
    selected: set[str] = set()
    inclusion: dict[str, str] = {}
    lanes = review.get("lane_selections", [])
    files = review.get("file_selections", [])
    if not isinstance(lanes, list) or not isinstance(files, list):
        raise ClosureError("invalid_review_selections")
    seen_lanes: set[str] = set()
    for raw in lanes:
        if not isinstance(raw, dict) or not isinstance(raw.get("lane"), str):
            raise ClosureError("invalid_lane_selection")
        lane = str(raw["lane"])
        if lane == "manual_review":
            raise ClosureError("manual_review_lane_forbidden")
        if lane in seen_lanes:
            raise ClosureError("duplicate_lane_selection", lane)
        seen_lanes.add(lane)
        lane_paths = sorted(
            path for path, entry in entries.items() if entry.get("category") == lane
        )
        if not lane_paths:
            raise ClosureError("unknown_or_empty_lane", lane)
        if raw.get("selection_mode") != "all_inventory_paths":
            raise ClosureError("invalid_lane_selection_mode", lane)
        if raw.get("expected_path_count") != len(lane_paths):
            raise ClosureError("lane_path_count_mismatch", lane)
        if raw.get("expected_lane_digest") != lane_digest(inventory, lane):
            raise ClosureError("lane_digest_mismatch", lane)
        for path in lane_paths:
            selected.add(path)
            inclusion[path] = "reviewed_lane"

    for raw in files:
        if not isinstance(raw, dict):
            raise ClosureError("invalid_file_selection")
        path = raw.get("path")
        lane = raw.get("lane")
        if not isinstance(path, str) or not isinstance(lane, str):
            raise ClosureError("invalid_file_selection")
        path = _target_path(path)
        entry = entries.get(path)
        if entry is None:
            raise ClosureError("selected_path_not_in_inventory", path)
        if lane == "manual_review" or entry.get("category") == "manual_review":
            raise ClosureError("manual_review_path_forbidden", path)
        if entry.get("category") != lane:
            raise ClosureError("selected_path_lane_mismatch", path)
        if raw.get("expected_sha256") != entry.get("sha256"):
            raise ClosureError("selected_path_sha_mismatch", path)
        selected.add(path)
        inclusion[path] = "reviewed_file"
    if not selected:
        raise ClosureError("empty_review_selection")
    review_summary = {
        "reviewer_id": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "status": REVIEW_STATUS,
        "scope": REVIEW_SCOPE,
        "deploy_authorized": False,
        "review_document_sha256": _digest(review),
    }
    return selected, inclusion, review_summary


def _resolve_candidate(root: Path, candidate: Path, dirty_paths: set[str]) -> list[str]:
    try:
        relative = candidate.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return []
    variants: list[str] = []
    if candidate.suffix:
        variants.append(relative)
    else:
        variants.extend(relative + suffix for suffix in _RESOLVE_SUFFIXES)
        variants.extend(
            f"{relative}/index{suffix}" for suffix in _RESOLVE_SUFFIXES if suffix != ".py"
        )
        variants.append(f"{relative}/__init__.py")
    return [path for path in variants if path in dirty_paths]


def _python_dependencies(root: Path, path: str, dirty_paths: set[str]) -> set[str]:
    absolute = root / path
    try:
        tree = ast.parse(absolute.read_text(encoding="utf-8"), filename=path)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ClosureError("python_dependency_scan_failed", f"{path}: {exc}") from exc
    dependencies: set[str] = set()
    parent_parts = list(PurePosixPath(path).parent.parts)
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                drop = max(0, node.level - 1)
                base = parent_parts[: len(parent_parts) - drop] if drop else parent_parts
                module = base + (node.module.split(".") if node.module else [])
                module_names.add("/".join(module))
                for alias in node.names:
                    if alias.name != "*":
                        module_names.add("/".join(module + [alias.name]))
            elif node.module:
                module_names.add(node.module)
                for alias in node.names:
                    if alias.name != "*":
                        module_names.add(f"{node.module}.{alias.name}")
    for module_name in module_names:
        module_path = module_name.replace(".", "/")
        candidates = [
            root / module_path,
            root / "backend" / module_path,
            root / "scripts" / module_path,
        ]
        if path.startswith("scripts/"):
            # Directly executed operational scripts put their own directory at
            # sys.path[0], so an unqualified sibling import is deterministic.
            candidates.append((root / path).parent / module_path)
        dependencies.update(
            dependency
            for candidate in candidates
            for dependency in _resolve_candidate(root, candidate, dirty_paths)
        )
    return dependencies


def _frontend_dependencies(root: Path, path: str, dirty_paths: set[str]) -> set[str]:
    try:
        text = (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ClosureError("frontend_dependency_scan_failed", f"{path}: {exc}") from exc
    dependencies: set[str] = set()
    for specifier in _JS_SPECIFIER.findall(text):
        candidates: list[Path] = []
        if specifier.startswith("."):
            candidates.append((root / path).parent / specifier)
        elif specifier.startswith("@/"):
            candidates.append(root / "frontend" / "src" / specifier[2:])
        for candidate in candidates:
            dependencies.update(_resolve_candidate(root, candidate, dirty_paths))
    return dependencies


def _shell_dependencies(root: Path, path: str, dirty_paths: set[str]) -> set[str]:
    try:
        lines = (root / path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ClosureError("shell_dependency_scan_failed", f"{path}: {exc}") from exc
    result: set[str] = set()
    for line in lines:
        match = _SHELL_SOURCE.match(line)
        if not match:
            continue
        source = match.group(2)
        if "$" in source or "`" in source:
            continue
        candidate = (root / path).parent / source if source.startswith(".") else root / source
        result.update(_resolve_candidate(root, candidate, dirty_paths))
    return result


def _dependencies(root: Path, path: str, dirty_paths: set[str]) -> set[str]:
    suffix = Path(path).suffix.lower()
    if suffix == _PYTHON_SUFFIX:
        return _python_dependencies(root, path, dirty_paths)
    if suffix in _FRONTEND_SUFFIXES:
        return _frontend_dependencies(root, path, dirty_paths)
    if suffix in {".sh", ".bash", ".zsh"}:
        return _shell_dependencies(root, path, dirty_paths)
    return set()


def _expand_dependencies(
    root: Path,
    entries: dict[str, dict[str, object]],
    selected: set[str],
    inclusion: dict[str, str],
) -> dict[str, list[str]]:
    dirty_paths = set(entries)
    reasons: dict[str, set[str]] = {}
    queue = deque(sorted(selected))
    visited: set[str] = set()
    while queue:
        source = queue.popleft()
        if source in visited:
            continue
        visited.add(source)
        for dependency in sorted(_dependencies(root, source, dirty_paths)):
            if dependency == source:
                continue
            reasons.setdefault(dependency, set()).add(source)
            if dependency not in selected:
                entry = entries[dependency]
                if entry.get("category") == "manual_review":
                    raise ClosureError("derived_manual_review_dependency", dependency)
                selected.add(dependency)
                inclusion[dependency] = "derived_dependency"
                queue.append(dependency)
    return {path: sorted(sources) for path, sources in sorted(reasons.items())}


def _validate_safe_file(root: Path, path: str, entry: dict[str, object]) -> bytes:
    status_text = entry.get("status")
    if not isinstance(status_text, str) or len(status_text) != 2:
        raise ClosureError("invalid_git_status", path)
    if status_text == "??":
        pass
    elif status_text[0] != " " or status_text[1] not in {"M", "T"}:
        raise ClosureError("unsupported_or_staged_git_status", f"{status_text} {path}")
    if "D" in status_text or "U" in status_text:
        raise ClosureError("deleted_or_conflicted_path_unsupported", path)

    pure = PurePosixPath(path)
    lower_parts = {part.lower() for part in pure.parts}
    lower_name = pure.name.lower()
    if lower_parts & _FORBIDDEN_COMPONENTS:
        raise ClosureError("unsafe_path_component", path)
    if (
        lower_name in _FORBIDDEN_NAMES
        or lower_name.startswith(".env.")
        or lower_name.endswith(_FORBIDDEN_SUFFIXES)
    ):
        raise ClosureError("secret_bearing_path_forbidden", path)

    absolute = root / path
    try:
        mode = absolute.lstat().st_mode
    except OSError as exc:
        raise ClosureError("selected_file_missing", path) from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ClosureError("non_regular_file_forbidden", path)
    size = absolute.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ClosureError("selected_file_too_large", path)
    data = absolute.read_bytes()
    if b"\0" in data[:8192]:
        raise ClosureError("binary_file_requires_separate_bundle_review", path)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(data):
            raise ClosureError("high_confidence_secret_detected", path)
    sha256 = hashlib.sha256(data).hexdigest()
    if entry.get("size_bytes") != size or entry.get("sha256") != sha256:
        raise ClosureError("selected_file_drift", path)
    if entry.get("category") == "manual_review":
        raise ClosureError("manual_review_path_forbidden", path)
    return data


def _drift_detail(expected: dict[str, object], current: dict[str, object]) -> str:
    expected_rows = {str(row["path"]): row for row in inventory_identity(expected)["entries"]}
    current_rows = {str(row["path"]): row for row in inventory_identity(current)["entries"]}
    added = sorted(set(current_rows) - set(expected_rows))
    removed = sorted(set(expected_rows) - set(current_rows))
    changed = sorted(
        path for path in set(expected_rows) & set(current_rows) if expected_rows[path] != current_rows[path]
    )
    return json.dumps(
        {"added": added[:20], "changed": changed[:20], "removed": removed[:20]},
        ensure_ascii=False,
        sort_keys=True,
    )


def _assert_no_worktree_drift(root: Path, expected: dict[str, object]) -> dict[str, object]:
    current = build_inventory(root)
    if inventory_digest(current) != inventory_digest(expected):
        raise ClosureError("dirty_worktree_drift", _drift_detail(expected, current))
    return current


def _closure_digest_payload(manifest: dict[str, object]) -> dict[str, object]:
    return {
        "schema": manifest.get("schema"),
        "source": manifest.get("source"),
        "review": manifest.get("review"),
        "selection": manifest.get("selection"),
        "files": manifest.get("files"),
        "safety": manifest.get("safety"),
        "result": manifest.get("result"),
    }


def build_closure_manifest(
    root: Path,
    inventory: dict[str, object],
    review: dict[str, object],
) -> dict[str, object]:
    """Build a dry-run closure manifest after two full drift checks."""

    root = root.resolve()
    _assert_no_worktree_drift(root, inventory)
    if _git(root, "branch", "--show-current") != inventory.get("branch"):
        raise ClosureError("current_branch_mismatch")
    if _git(root, "rev-parse", "HEAD") != inventory.get("head"):
        raise ClosureError("current_head_mismatch")
    selected, inclusion, review_summary = _reviewed_selection(inventory, review)
    entries = _entry_map(inventory)
    requested = set(selected)
    reasons = _expand_dependencies(root, entries, selected, inclusion)
    files: list[dict[str, object]] = []
    for path in sorted(selected):
        entry = entries[path]
        _validate_safe_file(root, path, entry)
        files.append(
            {
                "category": entry["category"],
                "dependency_of": reasons.get(path, []),
                "inclusion": inclusion[path],
                "path": path,
                "sha256": entry["sha256"],
                "size_bytes": entry["size_bytes"],
                "status": entry["status"],
                "tracked": entry["tracked"],
            }
        )
    _assert_no_worktree_drift(root, inventory)
    source_digest = inventory_digest(inventory)
    manifest: dict[str, object] = {
        "schema": CLOSURE_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "branch": inventory["branch"],
            "head": inventory["head"],
            "inventory_digest": source_digest,
            "worktree_entry_count": len(entries),
            "whole_worktree_drift_checked": True,
        },
        "review": review_summary,
        "selection": {
            "requested_path_count": len(requested),
            "derived_dependency_count": len(selected - requested),
            "closure_path_count": len(files),
            "requested_paths_sha256": _digest(sorted(requested)),
        },
        "files": files,
        "safety": {
            "authority": "none",
            "bundle_created": False,
            "commits_files": False,
            "deletes_files": False,
            "deploy_authorized": False,
            "deploys_files": False,
            "mutates_worktree": False,
            "pushes_files": False,
            "release_authorized": False,
            "stages_files": False,
        },
        "result": {
            "status": "bundle_preflight_passed",
            "deploy_ready": False,
            "release_ready": False,
            "blockers": [
                "selection_scope_is_preflight_only",
                "no_signed_release_authority",
                "tests_and_runtime_acceptance_are_separate_gates",
                "immutable_bundle_not_created",
            ],
        },
    }
    manifest["closure_digest"] = _digest(_closure_digest_payload(manifest))
    return manifest


def verify_closure_manifest(root: Path, manifest: dict[str, object]) -> dict[str, object]:
    if manifest.get("schema") != CLOSURE_SCHEMA:
        raise ClosureError("invalid_closure_schema")
    expected_digest = _digest(_closure_digest_payload(manifest))
    if manifest.get("closure_digest") != expected_digest:
        raise ClosureError("closure_manifest_tampered")
    source = manifest.get("source")
    files = manifest.get("files")
    if not isinstance(source, dict) or not isinstance(files, list):
        raise ClosureError("invalid_closure_manifest")
    root = root.resolve()
    current = build_inventory(root)
    if inventory_digest(current) != source.get("inventory_digest"):
        raise ClosureError("dirty_worktree_drift_since_closure")
    if current.get("branch") != source.get("branch") or current.get("head") != source.get("head"):
        raise ClosureError("git_identity_drift_since_closure")
    current_entries = _entry_map(current)
    for raw in files:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            raise ClosureError("invalid_closure_file")
        path = str(raw["path"])
        entry = current_entries.get(path)
        if entry is None:
            raise ClosureError("closure_file_missing_from_worktree", path)
        _validate_safe_file(root, path, entry)
        if raw.get("sha256") != entry.get("sha256") or raw.get("size_bytes") != entry.get("size_bytes"):
            raise ClosureError("closure_file_drift", path)
    return {
        "schema": "vkpi.worktree-release-closure-verification.v1",
        "closure_digest": expected_digest,
        "verified_file_count": len(files),
        "whole_worktree_drift_checked": True,
        "deploy_authorized": False,
        "status": "verified_preflight_only",
    }


def _write_exclusive_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ClosureError("refusing_to_overwrite_output", str(path)) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _error_payload(exc: ClosureError) -> str:
    return json.dumps(
        {
            "schema": "vkpi.worktree-release-closure-error.v1",
            "status": "blocked",
            "reason": exc.code,
            "detail": exc.detail,
            "deploy_authorized": False,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        root = Path(_git(args.repo.resolve(), "rev-parse", "--show-toplevel"))
        if args.verify is not None:
            if any(value is not None for value in (args.inventory, args.review, args.output)):
                raise ClosureError("verify_mode_argument_conflict")
            result = verify_closure_manifest(root, _load_json(args.verify, "closure"))
            stdout_out(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.inventory is None or args.review is None or args.output is None:
            raise ClosureError("build_mode_requires_inventory_review_and_output")
        manifest = build_closure_manifest(
            root,
            _load_json(args.inventory, "inventory"),
            _load_json(args.review, "review"),
        )
        _write_exclusive_json(args.output, manifest)
        stdout_out(
            json.dumps(
                {
                    "closure_digest": manifest["closure_digest"],
                    "closure_path_count": manifest["selection"]["closure_path_count"],  # type: ignore[index]
                    "deploy_authorized": False,
                    "status": "bundle_preflight_passed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClosureError as exc:
        stdout_out(_error_payload(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
