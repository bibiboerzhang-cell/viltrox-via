#!/usr/bin/env python3
"""Create fresh, source-bound core-mutation receipts (methodology core-mutation-v1).

Mirrors the coverage-receipt pattern: full production-source snapshot sha +
trusted Git state before/after the run, canonical command hash, config hashes,
and start/finish stamps for freshness.  The run never touches the shared
worktree — sources and matched tests are copied into a disposable private
workspace and ``mutmut`` mutates only that copy.  The child environment is
built from scratch (nothing inherited), so production DSNs and secrets cannot
leak; ``--db-mode hermetic`` keeps pytest on its temporary SQLite database,
``--db-mode isolated-pg`` accepts only the disposable ``~/.cache`` recipe
database (127.0.0.1:54333).  Scoring (contract ``core-mutation-v1``):
timeouts count as killed; ``no tests`` counts as survived; suspicious,
skipped and segfault are excluded from both sides; interrupted or unchecked
mutants fail the run closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

try:
    from scripts import vkpi_engineering_health_coverage as coverage_receipt
    from scripts import vkpi_engineering_health_snapshot as snapshot
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import vkpi_engineering_health_coverage as coverage_receipt
    import vkpi_engineering_health_snapshot as snapshot
    from stdout_utils import out as stdout_out


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "vkpi_engineering_health_mutation_receipt_v1"
METHODOLOGY_ID = "core-mutation-v1"
# The exit-code -> status map below is coupled to this exact mutmut release.
MUTMUT_PIN = "3.7.0"
CANONICAL_RUN_COMMAND = (".venv/bin/python", "-m", "mutmut", "run")
# Mirrors contract code_evidence_methodology.core_mutation_score.core_scope_groups.
MODES = ("smoke", "scored")
DB_MODES = ("hermetic", "isolated-pg")
ISOLATED_PG_DEFAULT_DSN = "postgresql://postgres@127.0.0.1:54333/vkpi_closeout_test"
ISOLATED_PG_HOST = "127.0.0.1"
ISOLATED_PG_PORT = 54333
# Same disposable-name discipline as tests/conftest.py _probe_pg.
_DISPOSABLE_DB_RE = re.compile(r"(?:^|[_-])(test|tests|ci|integration|disposable|scratch)(?:[_-]|$)", re.I)
# mutmut 3.7.0 __main__.status_by_exit_code (defaultdict -> "suspicious").
STATUS_BY_EXIT_CODE: dict[int | None, str] = {
    1: "killed", 3: "killed", 0: "survived", 5: "no tests",
    2: "check was interrupted by user", None: "not checked",
    33: "no tests", 34: "skipped", 35: "suspicious", 36: "timeout",
    37: "caught by type check", -24: "timeout", 24: "timeout",
    152: "timeout", 255: "timeout", -11: "segfault", -9: "segfault",
}
COUNT_FIELDS = (
    "killed", "timeout", "survived", "no_tests",
    "suspicious", "skipped", "segfault", "caught_by_type_check",
)
_FIELD_BY_STATUS = {
    "killed": "killed", "timeout": "timeout", "survived": "survived",
    "no tests": "no_tests", "suspicious": "suspicious", "skipped": "skipped",
    "segfault": "segfault", "caught by type check": "caught_by_type_check",
}
FATAL_STATUSES = ("check was interrupted by user", "not checked")
# backend/app appears twice: mutated import copy at app/, untouched mirror at
# backend/app/ for characterization tests that read original sources by path.
WORKSPACE_COPY_DIRS = (
    ("tests", "tests"), ("backend/app", "backend/app"),
    ("backend/tests", "backend/tests"), ("scripts", "scripts"),
    ("docs/vkpi", "docs/vkpi"), ("migrations", "migrations"),
)
WORKSPACE_PYTEST_INI = (
    "[pytest]\n"
    "testpaths = tests\n"
    "markers =\n"
    "    pg: exercises a live Postgres database (auto-skipped when PG is unavailable)\n"
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lines_sha256(lines: Sequence[str]) -> str:
    return _sha256(("\n".join(lines) + "\n").encode("utf-8"))


try:
    from scripts.vkpi_engineering_health_mutation_targets import (  # noqa: F401
        MutationRunError,
        CORE_SCOPE_GROUPS,
        SMOKE_FILE_COUNT,
        MIN_SCORED_FILES,
        MAX_SCORED_FILES,
        MAX_MATCHED_TESTS_PER_FILE,
        group_prefixes,
        eligible_targets,
        _test_file_index,
        match_tests,
        _explicit_targets,
        _smoke_targets,
        _scored_targets,
        select_targets,
    )
except ModuleNotFoundError:  # direct execution: scripts/ is sys.path[0]
    from vkpi_engineering_health_mutation_targets import (  # noqa: F401
        MutationRunError,
        CORE_SCOPE_GROUPS,
        SMOKE_FILE_COUNT,
        MIN_SCORED_FILES,
        MAX_SCORED_FILES,
        MAX_MATCHED_TESTS_PER_FILE,
        group_prefixes,
        eligible_targets,
        _test_file_index,
        match_tests,
        _explicit_targets,
        _smoke_targets,
        _scored_targets,
        select_targets,
    )




def to_workspace_relative(repo_relative: str) -> str:
    """backend/app/... -> app/... (the workspace hosts app at its root)."""
    if not repo_relative.startswith("backend/app/"):
        raise MutationRunError(f"target outside backend/app: {repo_relative}")
    return repo_relative[len("backend/") :]


def render_setup_cfg(
    workspace_targets: Sequence[str], test_selection: Sequence[str]
) -> str:
    """The full mutmut configuration for the private workspace."""
    def block(values: Sequence[str]) -> str:
        return "".join(f"\n    {value}" for value in values)

    also_copy = (
        "pytest.ini", "backend/app", "backend/tests",
        "scripts", "docs/vkpi", "migrations",
    )
    return (
        "[mutmut]\n"
        "source_paths = app\n"
        f"only_mutate ={block(sorted(workspace_targets))}\n"
        f"also_copy ={block(also_copy)}\n"
        "pytest_add_cli_args = -p no:cacheprovider\n"
        f"pytest_add_cli_args_test_selection ={block(sorted(test_selection))}\n"
        "use_git_change_detection = false\n"
        "debug = false\n"
    )


def validate_isolated_dsn(dsn: str) -> dict[str, Any]:
    """Accept only the disposable ~/.cache recipe database; sanitize for logs."""
    parts = urlsplit(dsn)
    if parts.scheme not in ("postgresql", "postgres"):
        raise MutationRunError("isolated DSN must use the postgresql scheme")
    if parts.password:
        raise MutationRunError("isolated DSN must not embed a password")
    if parts.hostname != ISOLATED_PG_HOST or parts.port != ISOLATED_PG_PORT:
        raise MutationRunError(f"isolated DSN must target {ISOLATED_PG_HOST}:{ISOLATED_PG_PORT}")
    database = parts.path.lstrip("/")
    if not _DISPOSABLE_DB_RE.search(database):
        raise MutationRunError("isolated DSN database name must look disposable (test/ci/scratch)")
    return {"host": parts.hostname, "port": parts.port, "database": database}


def build_env(root: Path, workspace: Path, *, db_mode: str, pg_dsn: str) -> tuple[dict[str, str], dict[str, Any]]:
    """A from-scratch child environment; nothing inherited, nothing secret."""
    if db_mode not in DB_MODES:
        raise MutationRunError(f"unsupported db mode: {db_mode}")
    home = workspace / "home"
    tmp = workspace / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{root / '.venv' / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home), "TMPDIR": str(tmp),
        "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "VKPI_SKIP_DOTENV": "1",
        "ENVIRONMENT": "test", "APP_ROLE": "admin-web", "ENABLE_SCHEDULER": "0",
    }
    isolation: dict[str, Any] = {
        "mode": db_mode, "environment_inherited": False, "database": None,
    }
    if db_mode == "isolated-pg":
        sanitized = validate_isolated_dsn(pg_dsn)
        env["VKPI_PYTEST_ALLOW_LIVE_SERVICES"] = "1"
        env["DATABASE_URL"] = pg_dsn
        env["LOCAL_DATABASE_URL"] = pg_dsn
        isolation["database"] = sanitized
    return env, isolation


def _copy_tree(source: Path, destination: Path, *, label: str) -> None:
    if not source.is_dir():
        raise MutationRunError(f"workspace source directory missing: {label}")
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".mypy_cache"),
    )


def build_workspace(
    root: Path,
    workspace: Path,
    *,
    workspace_targets: Sequence[str],
    test_selection: Sequence[str],
) -> dict[str, str]:
    """Copy sources/tests into the private workspace and write configs."""
    _copy_tree(root / "backend" / "app", workspace / "app", label="backend/app")
    for source_name, destination_name in WORKSPACE_COPY_DIRS:
        _copy_tree(root / source_name, workspace / destination_name, label=source_name)
    setup_cfg = render_setup_cfg(workspace_targets, test_selection)
    (workspace / "setup.cfg").write_text(setup_cfg, encoding="utf-8")
    (workspace / "pytest.ini").write_text(WORKSPACE_PYTEST_INI, encoding="utf-8")
    return {
        "setup_cfg_sha256": _sha256(setup_cfg.encode("utf-8")),
        "pytest_ini_sha256": _sha256(WORKSPACE_PYTEST_INI.encode("utf-8")),
    }


def mutmut_version(interpreter: Path) -> str:
    command = [
        str(interpreter), "-c",
        "import importlib.metadata as m; print(m.version('mutmut'))",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MutationRunError("cannot query mutmut version") from exc
    version = completed.stdout.decode("utf-8", "replace").strip()
    if completed.returncode != 0 or version != MUTMUT_PIN:
        raise MutationRunError(
            f"mutmut=={MUTMUT_PIN} is required (found {version or 'nothing'}); "
            "the receipt exit-code map is pinned to that release"
        )
    return version


def prune_uncollectable_tests(
    workspace: Path, env: dict[str, str], interpreter: Path, tests: list[str]
) -> tuple[list[str], list[str]]:
    """Drop test files that fail pytest collection inside the workspace.

    The hermetic workspace hosts only the selected sources; a matched test may
    read repo files that were never copied (collection-time FileNotFoundError).
    Excluding it silently would hide coverage — so exclusions are returned and
    recorded in the receipt. Prune only collection errors, never test failures."""
    cmd = [str(interpreter), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *tests]
    completed = subprocess.run(
        cmd, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, check=False,
    )
    if completed.returncode == 0:
        return tests, []
    bad: set[str] = set()
    for line in completed.stdout.splitlines():
        if line.startswith("ERROR ") and "::" not in line:
            bad.add(line.split()[1].strip())
    kept = [t for t in tests if t not in bad]
    if not bad or not kept:
        raise MutationRunError(
            "test selection failed collection and could not be pruned\n"
            f"--- collect-only stdout tail ---\n{completed.stdout[-2500:]}"
        )
    return kept, sorted(bad)


def run_mutmut(workspace: Path, env: dict[str, str], interpreter: Path) -> dict[str, Any]:
    """Execute the canonical run inside the workspace, capturing provenance."""
    if (workspace / "mutants").exists():
        raise MutationRunError("fresh mutation workspace contained old artifacts")
    stdout_path = workspace / "mutmut.stdout"
    stderr_path = workspace / "mutmut.stderr"
    command = [str(interpreter), *CANONICAL_RUN_COMMAND[1:]]
    started_at = _utc_now()
    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        completed = subprocess.run(
            command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
            stdout=stdout_file, stderr=stderr_file, check=False,
        )
    finished_at = _utc_now()
    stdout_bytes = stdout_path.read_bytes()
    stderr_bytes = stderr_path.read_bytes()
    if completed.returncode != 0:
        stdout_tail = stdout_bytes.decode("utf-8", "replace")[-4000:]
        stderr_tail = stderr_bytes.decode("utf-8", "replace")[-1500:]
        raise MutationRunError(
            f"mutmut run failed (exit={completed.returncode})\n"
            f"--- mutmut stdout tail ---\n{stdout_tail}\n"
            f"--- mutmut stderr tail ---\n{stderr_tail}"
        )
    return {
        "exit_code": completed.returncode,
        "started_at": started_at,
        "finished_at": finished_at,
        "stdout_sha256": _sha256(stdout_bytes),
        "stderr_sha256": _sha256(stderr_bytes),
    }


def _status_for_exit_code(key: str, exit_code: Any) -> str:
    if exit_code is None:
        return "not checked"
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise MutationRunError(f"non-integer mutant exit code for {key}")
    # mutmut uses a defaultdict: every unmapped exit code means "suspicious".
    return STATUS_BY_EXIT_CODE.get(exit_code, "suspicious")


def counts_from_meta(meta_path: Path) -> tuple[dict[str, int], dict[str, str]]:
    """Per-file status counts plus the mutant->status map from one .meta file."""
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MutationRunError(f"cannot read mutmut meta: {meta_path}") from exc
    exit_codes = payload.get("exit_code_by_key")
    if not isinstance(exit_codes, dict) or not exit_codes:
        raise MutationRunError(f"mutmut meta has no mutant results: {meta_path}")
    counts = {field: 0 for field in COUNT_FIELDS}
    statuses: dict[str, str] = {}
    for key in sorted(exit_codes):
        status = _status_for_exit_code(key, exit_codes[key])
        if status in FATAL_STATUSES:
            raise MutationRunError(f"mutant {key} finished as '{status}'; rerun the mutation pass")
        counts[_FIELD_BY_STATUS[status]] += 1
        statuses[key] = status
    return counts, statuses


def collect_results(
    workspace: Path,
    targets: Sequence[str],
    matches: dict[str, tuple[str, ...]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Read every target's meta file; a missing file fails the whole run."""
    per_file: list[dict[str, Any]] = []
    statuses: dict[str, dict[str, str]] = {}
    for target in sorted(targets):
        workspace_target = to_workspace_relative(target)
        meta_path = workspace / "mutants" / f"{workspace_target}.meta"
        if not meta_path.is_file():
            raise MutationRunError(f"mutmut produced no results for {target}")
        counts, mutant_statuses = counts_from_meta(meta_path)
        per_file.append(
            {"path": target, **counts, "matched_tests": list(matches[target])}
        )
        statuses[target] = mutant_statuses
    return per_file, statuses


def score_from_totals(totals: dict[str, int]) -> tuple[float, int]:
    """Contract pooling: (killed+timeout) / (killed+timeout+survived+no_tests)."""
    killed_pool = totals["killed"] + totals["timeout"]
    denominator = killed_pool + totals["survived"] + totals["no_tests"]
    if denominator <= 0:
        raise MutationRunError("mutation score denominator must be positive")
    return killed_pool / denominator, denominator


def sum_totals(per_file: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(int(row[field]) for row in per_file) for field in COUNT_FIELDS
    }


def build_receipt(
    *,
    root: Path,
    mode: str,
    prefixes: dict[str, tuple[str, ...]],
    eligible_count: int,
    targets: Sequence[str],
    per_file: list[dict[str, Any]],
    run: dict[str, Any],
    config_hashes: dict[str, str],
    db_isolation: dict[str, Any],
    source_before: snapshot.SourceSnapshot, source_after: snapshot.SourceSnapshot,
    git_before: dict[str, Any], git_after: dict[str, Any],
    nonce: str,
    statuses_entry: dict[str, Any],
) -> dict[str, Any]:
    if mode not in MODES:
        raise MutationRunError(f"unsupported mutation mode: {mode}")
    if not source_before.complete or not source_after.complete:
        raise MutationRunError("source snapshot is incomplete")
    if source_before.identity() != source_after.identity():
        raise MutationRunError("source content drifted during the mutation run")
    if git_before != git_after:
        raise MutationRunError("Git status drifted during the mutation run")
    totals = sum_totals(per_file)
    score, scored_mutants = score_from_totals(totals)
    return {
        "schema_version": SCHEMA_VERSION,
        "methodology_id": METHODOLOGY_ID,
        "generated_at": run["finished_at"],
        "passed": True,
        "candidate": {
            "repo": str(root.resolve()),
            "source_content_sha256": source_before.content_sha256,
            "source_file_count": len(source_before.files),
            "source_start": source_before.identity(),
            "source_end": source_after.identity(),
            "git_start": git_before, "git_end": git_after,
        },
        "run": {
            "command": list(CANONICAL_RUN_COMMAND),
            "command_sha256": coverage_receipt.command_sha256(CANONICAL_RUN_COMMAND),
            "mutmut_version": MUTMUT_PIN,
            "setup_cfg_sha256": config_hashes["setup_cfg_sha256"],
            "pytest_ini_sha256": config_hashes["pytest_ini_sha256"],
            "exit_code": run["exit_code"],
            "started_at": run["started_at"], "finished_at": run["finished_at"],
            "fresh_workspace_nonce": nonce,
            "artifacts_existed_before": False,
            "stdout_sha256": run["stdout_sha256"], "stderr_sha256": run["stderr_sha256"],
            "db_isolation": db_isolation,
        },
        "scope": {
            "mode": mode,
            "groups": sorted(prefixes),
            "group_prefixes": {name: list(prefixes[name]) for name in sorted(prefixes)},
            "eligible_file_count": eligible_count,
            "target_file_count": len(targets),
            "target_files": sorted(targets),
            "target_files_sha256": _lines_sha256(sorted(targets)),
        },
        "results": {
            "per_file": per_file,
            "totals": totals,
            "scored_mutants": scored_mutants,
            "core_mutation_score": score,
        },
        "artifacts": {"mutant_statuses": statuses_entry},
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


def _validate_output_path(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if resolved.suffix != ".json":
        raise MutationRunError(f"output must use .json: {path}")
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] != "runtime":
        raise MutationRunError("repository-local mutation outputs must be under ignored runtime/")
    return resolved


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _preflight(root: Path, receipt_path: Path, statuses_path: Path) -> tuple[Path, Path, Path]:
    if not (root / ".git").exists():
        raise MutationRunError("repository root with .git is required")
    receipt_path = _validate_output_path(root, receipt_path)
    statuses_path = _validate_output_path(root, statuses_path)
    if receipt_path == statuses_path:
        raise MutationRunError("receipt and statuses artifact paths must differ")
    interpreter = root / CANONICAL_RUN_COMMAND[0]
    if not interpreter.is_file():
        raise MutationRunError(f"canonical interpreter is unavailable: {interpreter}")
    mutmut_version(interpreter)
    return receipt_path, statuses_path, interpreter


def _plan_targets(
    root: Path,
    source_before: snapshot.SourceSnapshot,
    *,
    groups: Sequence[str],
    mode: str,
    max_files: int,
    explicit_files: Sequence[str],
) -> dict[str, Any]:
    prefixes = group_prefixes(groups)
    eligible = eligible_targets(source_before, prefixes)
    matches_all = match_tests(root, [item.relative_path for item in eligible])
    targets = select_targets(
        eligible, matches_all, mode=mode, max_files=max_files, explicit=explicit_files
    )
    matches = {target: matches_all[target] for target in targets}
    test_selection = sorted({rel for tests in matches.values() for rel in tests})
    if not test_selection:
        raise MutationRunError("no matched tests for any target; refusing to run")
    return {
        "prefixes": prefixes,
        "eligible_count": len(eligible),
        "targets": targets,
        "matches": matches,
        "test_selection": test_selection,
    }


def run_fresh_mutation(
    *,
    root: Path,
    mode: str,
    groups: Sequence[str],
    explicit_files: Sequence[str],
    max_files: int,
    db_mode: str, pg_dsn: str,
    receipt_path: Path, statuses_path: Path,
    workspace_parent: str, keep_workspace: bool,
) -> dict[str, Any]:
    root = root.resolve()
    receipt_path, statuses_path, interpreter = _preflight(
        root, receipt_path, statuses_path
    )
    source_before = coverage_receipt.source_snapshot(root)
    if not source_before.complete:
        raise MutationRunError("source snapshot is incomplete before mutation")
    git_before = snapshot.trusted_git_state(root)
    plan = _plan_targets(
        root, source_before,
        groups=groups, mode=mode, max_files=max_files, explicit_files=explicit_files,
    )
    targets, matches = plan["targets"], plan["matches"]

    nonce = str(uuid.uuid4())
    parent = Path(workspace_parent) if workspace_parent else Path(tempfile.gettempdir())
    workspace = Path(tempfile.mkdtemp(prefix=f"vkpi-mutation-{nonce}-", dir=parent))
    try:
        config_hashes = build_workspace(
            root, workspace,
            workspace_targets=[to_workspace_relative(t) for t in targets],
            test_selection=plan["test_selection"],
        )
        env, db_isolation = build_env(root, workspace, db_mode=db_mode, pg_dsn=pg_dsn)
        kept_tests, excluded_tests = prune_uncollectable_tests(
            workspace, env, interpreter, list(plan["test_selection"])
        )
        if excluded_tests:  # 诚实剔除:只重写 setup.cfg(工作区已建,copytree 不可重入)
            setup_cfg = render_setup_cfg(
                [to_workspace_relative(t) for t in targets], kept_tests
            )
            (workspace / "setup.cfg").write_text(setup_cfg, encoding="utf-8")
            config_hashes["setup_cfg_sha256"] = _sha256(setup_cfg.encode("utf-8"))
        run = run_mutmut(workspace, env, interpreter)
        run["collection_excluded_tests"] = excluded_tests
        per_file, statuses = collect_results(workspace, targets, matches)
    finally:
        if not keep_workspace:
            shutil.rmtree(workspace, ignore_errors=True)

    statuses_bytes = _canonical_json_bytes(
        {"schema_version": f"{SCHEMA_VERSION}:statuses", "by_file": statuses}
    )
    statuses_entry = {
        "path": coverage_receipt._artifact_label(root, statuses_path),  # noqa: SLF001
        "sha256": _sha256(statuses_bytes),
        "byte_count": len(statuses_bytes),
    }
    source_after = coverage_receipt.source_snapshot(root)
    git_after = snapshot.trusted_git_state(root)
    receipt = build_receipt(
        root=root,
        mode=mode,
        prefixes=plan["prefixes"],
        eligible_count=plan["eligible_count"],
        targets=targets,
        per_file=per_file,
        run=run,
        config_hashes=config_hashes,
        db_isolation=db_isolation,
        source_before=source_before,
        source_after=source_after,
        git_before=git_before,
        git_after=git_after,
        nonce=nonce,
        statuses_entry=statuses_entry,
    )
    _atomic_write(statuses_path, statuses_bytes)
    _atomic_write(receipt_path, _canonical_json_bytes(receipt))
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--mode", choices=MODES, default="smoke")
    parser.add_argument(
        "--group", action="append", default=[],
        help=f"core scope group(s); default kol_search; known: {sorted(CORE_SCOPE_GROUPS)}",
    )
    parser.add_argument("--files", action="append", default=[])
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--db-mode", choices=DB_MODES, default="hermetic")
    parser.add_argument("--pg-dsn", default=ISOLATED_PG_DEFAULT_DSN)
    parser.add_argument("--receipt", default="runtime/engineering-health/mutation/receipt.json")
    parser.add_argument("--statuses", default="runtime/engineering-health/mutation/mutant-statuses.json")
    parser.add_argument("--workspace-dir", default="")
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args(argv)


def _absolute(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    receipt = run_fresh_mutation(
        root=root,
        mode=args.mode,
        groups=list(args.group) or ["kol_search"],
        explicit_files=list(args.files),
        max_files=args.max_files,
        db_mode=args.db_mode, pg_dsn=args.pg_dsn,
        receipt_path=_absolute(root, args.receipt),
        statuses_path=_absolute(root, args.statuses),
        workspace_parent=args.workspace_dir, keep_workspace=args.keep_workspace,
    )
    stdout_out(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
