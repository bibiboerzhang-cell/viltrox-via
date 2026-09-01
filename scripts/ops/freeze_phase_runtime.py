#!/usr/bin/env python3
"""Bounded process logging and cleanup for candidate freeze phases."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeVar

from scripts.ops.freeze_worktree_contract import (
    FreezeError,
    path_identity,
    precreate_owned_file,
    write_owned_file_exclusive,
)


PHASE_A_NESTED_SEATBELT_TESTS = (
    ("tests/test_strict_runtime_hardening_redteam.py", 32),
    ("tests/test_deploy_runtime_admission.py", 5),
    ("tests/test_freeze_worktree_candidate.py", 22),
    ("tests/test_phase_a_static_containment.py", 1),
    ("tests/test_candidate_browser_cleanup_behavior.py", 3),
    ("tests/test_candidate_verification_mirror.py", 3),
    ("tests/test_controller_static_receipt.py", 7),
    ("tests/test_local_redis_start_timeout.py", 2),
)
PHASE_A_NESTED_SEATBELT_TEST_FILES = tuple(
    relative for relative, _count in PHASE_A_NESTED_SEATBELT_TESTS
)
PHASE_A_NESTED_SEATBELT_TEST_COUNT = sum(
    count for _relative, count in PHASE_A_NESTED_SEATBELT_TESTS
)
PHASE_A_DEPENDENCY_DIRECTORIES = (
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "pygments",
    "packaging",
    "cryptography",
    "psycopg",
    "psycopg_binary",
    "alembic",
    "sqlalchemy",
    "mako",
    "markupsafe",
    "greenlet",
)
PHASE_A_DEPENDENCY_FILES = ("py.py", "typing_extensions.py")
PHASE_A_DEPENDENCY_MODULES = (
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "pygments",
    "packaging",
    "py",
    "cryptography",
    "psycopg",
    "psycopg_binary",
    "_cffi_backend",
    "alembic",
    "sqlalchemy",
    "mako",
    "markupsafe",
    "greenlet",
    "typing_extensions",
)
PHASE_A_DEPENDENCY_BASELINE = {
    "python_cache_tag": "cpython-314",
    "platform": "darwin",
    "component_directories": list(PHASE_A_DEPENDENCY_DIRECTORIES),
    "component_files": [
        "py.py", "typing_extensions.py", "_cffi_backend.cpython-314-darwin.so",
    ],
    "file_count": 1046,
    "size_bytes": 48_805_613,
    "content_sha256": "1ab9ec7732eb5c8ee61bc6d907e32915ab91d2709826bae20f1dec2c88ab16c7",
}
PHASE_A_NESTED_EXECUTION_BOUNDARY = {
    "candidate_source": "reviewed_clean_git_required_for_deploy",
    "outer_seatbelt": False,
    "same_uid_adversarial_source_resistance": False,
    "reason": "darwin_nested_sandbox_incompatible",
}
_DEPENDENCY_FILE_SUFFIXES = frozenset({".py", ".pyi", ".typed", ".so", ".dylib"})
_DEPENDENCY_MAX_FILE_BYTES = 64 * 1024 * 1024
_DEPENDENCY_MAX_TOTAL_BYTES = 96 * 1024 * 1024
_DEPENDENCY_MAX_FILES = 4096
PHASE_A_PYTEST_BOOTSTRAP = """import importlib
import os
from pathlib import Path
import sys
dependency_root = Path(sys.argv[1]).resolve(strict=True)
snapshot = Path(sys.argv[2]).resolve(strict=True)
if not (sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment):
    raise SystemExit("fixed Phase A runner requires isolated no-site Python")
if any(name in sys.modules for name in ("site", "sitecustomize", "usercustomize")):
    raise SystemExit("fixed Phase A runner loaded a site customization module")
sys.path.insert(0, str(dependency_root))
module_names = (
    "pytest", "_pytest", "pluggy", "iniconfig", "pygments", "packaging",
    "py", "cryptography", "psycopg", "psycopg_binary", "_cffi_backend",
    "alembic", "sqlalchemy", "mako", "markupsafe", "greenlet",
    "typing_extensions",
)
modules = {name: importlib.import_module(name) for name in module_names}
for name, module in modules.items():
    origin = Path(module.__file__).resolve(strict=True)
    if not origin.is_relative_to(dependency_root):
        raise SystemExit(f"fixed Phase A dependency escaped mirror: {name}")
pytest = modules["pytest"]
import _pytest.config
from _pytest.config import PytestPluginManager

hard_exit = os._exit
pytest_main = _pytest.config.main
original_consider_module = PytestPluginManager.consider_module
def reject_module_plugins(manager, module):
    if getattr(module, "pytest_plugins", None):
        raise pytest.UsageError("fixed Phase A tests cannot register plugins")
    return original_consider_module(manager, module)
PytestPluginManager.consider_module = reject_module_plugins
sys.path.extend([str(snapshot), str(snapshot / "scripts"), str(snapshot / "backend")])
exit_code = int(pytest_main(sys.argv[3:]))
for name, module in modules.items():
    current = sys.modules.get(name)
    origin = Path(getattr(current, "__file__", "")).resolve(strict=True)
    if current is not module or not origin.is_relative_to(dependency_root):
        exit_code = 86
if pytest.main is not pytest_main or _pytest.config.main is not pytest_main:
    exit_code = 86
try:
    sys.stdout.flush()
    sys.stderr.flush()
finally:
    hard_exit(exit_code)
"""
_InventoryEntry = TypeVar("_InventoryEntry")


def trusted_pytest_site_packages() -> Path:
    """Locate pytest dependencies without executing a site or .pth file."""

    spec = importlib.util.find_spec("pytest")
    if spec is None or spec.origin is None:
        raise FreezeError("controller pytest dependency root is unavailable")
    origin = Path(spec.origin).resolve(strict=True)
    root = origin.parent.parent
    info = root.lstat()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or origin != root / "pytest/__init__.py"
        or not (root / "_pytest/__init__.py").is_file()
    ):
        raise FreezeError("controller pytest dependency root is unsafe")
    return root


def _read_stable_dependency_file(path: Path) -> bytes:
    """Read one dependency file without following or racing a replacement."""

    before = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid not in {0, os.geteuid()}
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size > _DEPENDENCY_MAX_FILE_BYTES
        or path.suffix not in _DEPENDENCY_FILE_SUFFIXES
    ):
        raise FreezeError(f"Phase A dependency file is unsafe: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        )
        if any(getattr(before, name) != getattr(opened, name) for name in stable_fields):
            raise FreezeError("Phase A dependency changed before it was copied")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FreezeError("Phase A dependency read ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FreezeError("Phase A dependency grew while it was copied")
        after = os.fstat(descriptor)
        if any(getattr(opened, name) != getattr(after, name) for name in stable_fields):
            raise FreezeError("Phase A dependency changed while it was copied")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_dependency_directory(path: Path) -> None:
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise FreezeError(f"Phase A dependency directory is unsafe: {path.name}")


def _copy_dependency_directory(
    source: Path, destination: Path, counters: dict[str, int]
) -> None:
    _validate_dependency_directory(source)
    destination.mkdir(mode=0o700)
    for entry in sorted(os.scandir(source), key=lambda item: item.name):
        if entry.name == "__pycache__":
            continue
        source_path = Path(entry.path)
        destination_path = destination / entry.name
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            _copy_dependency_directory(source_path, destination_path, counters)
            continue
        if not stat.S_ISREG(info.st_mode) or entry.is_symlink():
            raise FreezeError("Phase A dependency mirror rejected a special node")
        if source_path.suffix not in _DEPENDENCY_FILE_SUFFIXES:
            continue
        payload = _read_stable_dependency_file(source_path)
        counters["files"] += 1
        counters["bytes"] += len(payload)
        if (
            counters["files"] > _DEPENDENCY_MAX_FILES
            or counters["bytes"] > _DEPENDENCY_MAX_TOTAL_BYTES
        ):
            raise FreezeError("Phase A dependency mirror exceeds its bound")
        write_owned_file_exclusive(destination_path, payload)
        destination_path.chmod(0o400)
    destination.chmod(0o500)


def _tree_inventory(
    root: Path, *, readonly: bool, include_content: bool
) -> tuple[tuple[object, ...], ...]:
    """Return identity-rich tree state; ctime makes write-and-restore visible."""

    rows: list[tuple[object, ...]] = []
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        common = (
            info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid,
            info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, getattr(info, "st_flags", 0),
        )
        if path.is_symlink():
            rows.append(("symlink", relative, *common, os.readlink(path)))
            continue
        if stat.S_ISDIR(info.st_mode):
            if readonly and (
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o500
            ):
                raise FreezeError("Phase A dependency mirror directory mode changed")
            rows.append(("directory", relative, *common, ""))
            pending.extend(
                Path(entry.path)
                for entry in sorted(os.scandir(path), key=lambda item: item.name, reverse=True)
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            raise FreezeError("Phase A protected tree contains a special node")
        if readonly and (
            info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o400
        ):
            raise FreezeError("Phase A dependency mirror file mode changed")
        digest = hashlib.sha256(_read_stable_dependency_file(path)).hexdigest() \
            if include_content else ""
        rows.append(("file", relative, *common, digest))
    return tuple(sorted(rows, key=lambda row: (str(row[1]), str(row[0]))))


def _content_inventory_digest(rows: Sequence[tuple[object, ...]]) -> str:
    content = [
        (row[0], row[1], row[8], row[-1])
        for row in rows
        if row[0] == "file"
    ]
    return hashlib.sha256(
        json.dumps(content, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _identity_inventory_digest(rows: Sequence[tuple[object, ...]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def phase_a_dependency_proof_is_valid(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if any(value.get(name) != expected for name, expected in PHASE_A_DEPENDENCY_BASELINE.items()):
        return False
    before = value.get("identity_sha256_before")
    return (
        isinstance(before, str)
        and len(before) == 64
        and all(character in "0123456789abcdef" for character in before)
        and value.get("identity_sha256_after") == before
    )


def _prepare_nested_dependency_mirror(
    source_root: Path, runtime_root: Path
) -> tuple[Path, tuple[tuple[object, ...], ...], dict[str, object]]:
    """Copy the fixed runner closure without copying site startup hooks."""

    source = source_root.resolve(strict=True)
    _validate_dependency_directory(source)
    mirror = runtime_root / "controller-pytest-dependencies"
    mirror.mkdir(mode=0o700)
    counters = {"files": 0, "bytes": 0}
    for name in PHASE_A_DEPENDENCY_DIRECTORIES:
        _copy_dependency_directory(source / name, mirror / name, counters)
    for name in PHASE_A_DEPENDENCY_FILES:
        payload = _read_stable_dependency_file(source / name)
        counters["files"] += 1
        counters["bytes"] += len(payload)
        write_owned_file_exclusive(mirror / name, payload)
        (mirror / name).chmod(0o400)
    extension_matches = sorted(
        path for path in source.iterdir()
        if path.name.startswith("_cffi_backend.")
        and any(path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES)
    )
    if len(extension_matches) != 1:
        raise FreezeError("Phase A dependency mirror requires one _cffi_backend extension")
    extension = extension_matches[0]
    payload = _read_stable_dependency_file(extension)
    counters["files"] += 1
    counters["bytes"] += len(payload)
    if (
        counters["files"] > _DEPENDENCY_MAX_FILES
        or counters["bytes"] > _DEPENDENCY_MAX_TOTAL_BYTES
    ):
        raise FreezeError("Phase A dependency mirror exceeds its bound")
    write_owned_file_exclusive(mirror / extension.name, payload)
    (mirror / extension.name).chmod(0o400)
    mirror.chmod(0o500)
    inventory = _tree_inventory(mirror, readonly=True, include_content=True)
    proof = {
        "python_cache_tag": sys.implementation.cache_tag,
        "platform": sys.platform,
        "component_directories": list(PHASE_A_DEPENDENCY_DIRECTORIES),
        "component_files": [*PHASE_A_DEPENDENCY_FILES, extension.name],
        "file_count": counters["files"],
        "size_bytes": counters["bytes"],
        "content_sha256": _content_inventory_digest(inventory),
    }
    if proof != PHASE_A_DEPENDENCY_BASELINE:
        raise FreezeError("Phase A dependency mirror differs from reviewed baseline")
    return mirror, inventory, proof


def phase_a_runtime_environment(sandbox_root: Path) -> dict[str, str]:
    """Create controller-selected private runtime destinations for Phase A."""

    base = sandbox_root / "runtime-data"
    app = base / "app"
    runtime = base / "runtime"
    data = runtime / "data"
    logs = runtime / "logs"
    vendor = runtime / "vendor"
    for path in (base, app, runtime, data, logs, vendor):
        path.mkdir(mode=0o700)
    return {
        "RUNTIME_ROOT": str(runtime),
        "RUNTIME_DATA": str(data),
        "RUNTIME_LOGS": str(logs),
        "RUNTIME_VENDOR": str(vendor),
        "VKPI_RUNTIME_DATA_DIR": str(app),
    }


def inventory_map_digest(
    inventories: Mapping[str, Sequence[_InventoryEntry]],
    entry_digest: Callable[[Sequence[_InventoryEntry]], str],
) -> str:
    payload = {root: entry_digest(entries) for root, entries in inventories.items()}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def bind_nested_inventory_proof(
    proof: dict[str, object], *,
    candidate_before: Sequence[_InventoryEntry],
    candidate_after: Sequence[_InventoryEntry],
    sources_before: Mapping[str, Sequence[_InventoryEntry]],
    sources_after: Mapping[str, Sequence[_InventoryEntry]],
    entry_digest: Callable[[Sequence[_InventoryEntry]], str],
) -> None:
    if candidate_after != candidate_before or sources_after != sources_before:
        raise FreezeError("nested Seatbelt tests changed source bytes")
    proof.update(
        {
            "candidate_digest_before": entry_digest(candidate_before),
            "candidate_digest_after": entry_digest(candidate_after),
            "source_digest_before": inventory_map_digest(sources_before, entry_digest),
            "source_digest_after": inventory_map_digest(sources_after, entry_digest),
        }
    )


def physical_special_paths(root: Path) -> list[str]:
    """List unsupported physical nodes without following candidate symlinks."""

    special: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FreezeError(
                f"candidate physical tree cannot be scanned: {directory}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FreezeError(
                    f"candidate physical node cannot be inspected: {path}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                special.append(path.relative_to(root).as_posix())
    return sorted(special)


def run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    error_log_path: Path | None = None,
    accepted_returncodes: Sequence[int] = (0,),
) -> None:
    with log_path.open("wb") as log:
        from scripts.ops.controlled_candidate_process import run_controlled_candidate

        proc = run_controlled_candidate(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=1200,
            accepted_returncodes=accepted_returncodes,
        )
    if proc.returncode not in accepted_returncodes:
        raise FreezeError(
            f"command failed with exit {proc.returncode}; "
            f"inspect {error_log_path or log_path}"
        )


def _read_nested_junit(
    path: Path, expected_identity: tuple[int, int],
) -> tuple[dict[str, int], str]:
    """Read and validate the bound built-in pytest xunit1 report."""

    before = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) != 0o600
        or (before.st_dev, before.st_ino) != expected_identity
        or before.st_size > 8 * 1024 * 1024
    ):
        raise FreezeError("nested Seatbelt JUnit report is unsafe")
    data = path.read_bytes()
    after = path.lstat()
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
        any(getattr(before, name) != getattr(after, name) for name in stable_fields)
        or len(data) != before.st_size
    ):
        raise FreezeError("nested Seatbelt JUnit report changed while reading")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FreezeError("nested Seatbelt JUnit report is invalid") from exc
    suites = list(root) if root.tag == "testsuites" else []
    if len(suites) != 1 or suites[0].tag != "testsuite":
        raise FreezeError("nested Seatbelt JUnit suite shape is invalid")
    suite = suites[0]
    expected_total = PHASE_A_NESTED_SEATBELT_TEST_COUNT
    if any(
        suite.get(name) != expected
        for name, expected in (
            ("tests", str(expected_total)),
            ("failures", "0"),
            ("errors", "0"),
            ("skipped", "0"),
        )
    ):
        raise FreezeError("nested Seatbelt JUnit totals are invalid")
    counts = {relative: 0 for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES}
    node_keys: set[tuple[str, str, str]] = set()
    testcases = suite.findall("testcase")
    for case in testcases:
        relative = case.get("file", "")
        key = (relative, case.get("classname", ""), case.get("name", ""))
        if relative not in counts or key in node_keys or list(case):
            raise FreezeError("nested Seatbelt JUnit testcase proof is invalid")
        counts[relative] += 1
        node_keys.add(key)
    if len(testcases) != expected_total or counts != dict(PHASE_A_NESTED_SEATBELT_TESTS):
        raise FreezeError("nested Seatbelt JUnit file counts are invalid")
    return counts, hashlib.sha256(data).hexdigest()


def run_nested_seatbelt_tests(
    *, snapshot: Path, python_bin: Path, env: dict[str, str], runtime_root: Path,
    error_log_path: Path, failure_log_path: Path,
    failure_log_identity: tuple[int, int],
    expected_test_file_sha256: Mapping[str, str] | None = None,
    allow_not_present_fixture: bool = False,
    pytest_site_packages: Path | None = None,
    protected_write_paths: Sequence[Path] = (),
) -> dict[str, object]:
    """Run the one fixed suite that Darwin cannot nest below another profile."""

    paths = [snapshot / relative for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES]
    existing = [path.exists() or path.is_symlink() for path in paths]
    if not any(existing):
        if not allow_not_present_fixture:
            raise FreezeError("nested Seatbelt test suite is missing from release candidate")
        return {
            "status": "not_present_fixture",
            "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
            "file_counts": dict(PHASE_A_NESTED_SEATBELT_TESTS),
            "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        }
    if not all(existing):
        raise FreezeError("nested Seatbelt test suite is incomplete")
    for path in paths:
        info = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise FreezeError("nested Seatbelt test suite contains an unsafe file")
    test_file_sha256 = {
        relative: hashlib.sha256(path.read_bytes()).hexdigest()
        for relative, path in zip(PHASE_A_NESTED_SEATBELT_TEST_FILES, paths)
    }
    if expected_test_file_sha256 != test_file_sha256:
        raise FreezeError("nested Seatbelt test suite differs from trusted Git HEAD")
    test_env = dict(env)
    test_env.pop("PYTEST_ADDOPTS", None)
    test_env.pop("PYTEST_PLUGINS", None)
    test_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    test_env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in (
        "COVERAGE_PROCESS_CONFIG", "COVERAGE_PROCESS_START", "PYTHONHOME",
        "PYTHONINSPECT", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE",
    ):
        test_env.pop(name, None)
    run_log = runtime_root / "nested-seatbelt-run.log"
    junit_path = runtime_root / "nested-seatbelt-junit.xml"
    junit_identity = precreate_owned_file(junit_path)
    dependency_source = pytest_site_packages or trusted_pytest_site_packages()
    dependency_root, dependency_before, dependency_proof = (
        _prepare_nested_dependency_mirror(dependency_source, runtime_root)
    )
    candidate_identity_before = _tree_inventory(
        snapshot, readonly=False, include_content=False
    )
    protected_root_identities = {
        str(path.resolve(strict=True)): path_identity(path.resolve(strict=True))
        for path in protected_write_paths
    }
    base = [
        str(python_bin), "-I", "-S", "-B", "-c", PHASE_A_PYTEST_BOOTSTRAP,
        str(dependency_root), str(snapshot), "-c", "/dev/null",
        "--rootdir", str(snapshot), "-o", "junit_family=xunit1",
        "--import-mode=importlib", "--noconftest", "--disable-plugin-autoload",
        "-p", "no:cacheprovider",
    ]
    command = [
        *base, "-q", "--junitxml", str(junit_path),
        *PHASE_A_NESTED_SEATBELT_TEST_FILES,
    ]
    try:
        run_logged(
            command, cwd=snapshot, env=test_env, log_path=run_log,
            error_log_path=error_log_path,
        )
    except BaseException:
        publish_owned_log(run_log, failure_log_path, failure_log_identity)
        raise
    try:
        dependency_after = _tree_inventory(
            dependency_root, readonly=True, include_content=True
        )
        candidate_identity_after = _tree_inventory(
            snapshot, readonly=False, include_content=False
        )
        observed_protected_roots = {
            raw: path_identity(Path(raw)) for raw in protected_root_identities
        }
        if dependency_after != dependency_before:
            raise FreezeError("nested Seatbelt tests changed dependency mirror bytes")
        if candidate_identity_after != candidate_identity_before:
            raise FreezeError("nested Seatbelt tests changed candidate identity metadata")
        if observed_protected_roots != protected_root_identities:
            raise FreezeError("nested Seatbelt tests replaced a protected source root")
    except BaseException:
        publish_owned_log(run_log, failure_log_path, failure_log_identity)
        raise
    try:
        observed_counts, junit_sha256 = _read_nested_junit(
            junit_path, junit_identity
        )
    except BaseException:
        publish_owned_log(run_log, failure_log_path, failure_log_identity)
        raise
    command_contract = [
        str(python_bin), "-I", "-S", "-B", "-c", "<controller-bootstrap>",
        "<controller-dependency-mirror>", "<verification-snapshot>",
        "-c", "/dev/null",
        "--rootdir", "<verification-snapshot>", "-o", "junit_family=xunit1",
        "--import-mode=importlib", "--noconftest", "--disable-plugin-autoload",
        "-p", "no:cacheprovider",
        "-q", "--junitxml", "<controller-bound-junit>",
        *PHASE_A_NESTED_SEATBELT_TEST_FILES,
    ]
    return {
        "status": "passed",
        "execution_boundary": dict(PHASE_A_NESTED_EXECUTION_BOUNDARY),
        "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
        "test_file_sha256": test_file_sha256,
        "file_counts": observed_counts,
        "command": command_contract,
        "exit_code": 0,
        "collected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "passed_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "bootstrap_sha256": hashlib.sha256(
            PHASE_A_PYTEST_BOOTSTRAP.encode("utf-8")
        ).hexdigest(),
        "junit_xml_sha256": junit_sha256,
        "junit_testcase_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
        "junit_failures": 0,
        "junit_errors": 0,
        "junit_skipped": 0,
        "run_log_sha256": hashlib.sha256(run_log.read_bytes()).hexdigest(),
        "dependency_mirror": {
            **dependency_proof,
            "identity_sha256_before": _identity_inventory_digest(dependency_before),
            "identity_sha256_after": _identity_inventory_digest(dependency_after),
        },
        "candidate_identity_sha256_before": _identity_inventory_digest(
            candidate_identity_before
        ),
        "candidate_identity_sha256_after": _identity_inventory_digest(
            candidate_identity_after
        ),
    }


def publish_owned_log(
    sandbox_log: Path,
    destination: Path,
    expected_identity: tuple[int, int],
) -> None:
    """Copy a reaped candidate log into its pre-created controller inode."""

    info = sandbox_log.lstat()
    if (
        sandbox_log.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > 64 * 1024 * 1024
    ):
        raise FreezeError("candidate phase log is unsafe")
    data = sandbox_log.read_bytes()
    if len(data) != info.st_size:
        raise FreezeError("candidate phase log changed while reading")
    flags = os.O_WRONLY | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags)
    try:
        target = os.fstat(descriptor)
        if (
            (target.st_dev, target.st_ino) != expected_identity
            or not stat.S_ISREG(target.st_mode)
        ):
            raise FreezeError("candidate phase log destination identity changed")
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FreezeError("candidate phase log write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if path_identity(destination) != expected_identity:
        raise FreezeError("candidate phase log destination changed after write")


def remove_owned_phase_sandbox(root: Path) -> None:
    """Remove one controller-owned phase sandbox, including read-only fixtures."""

    physical = root.resolve(strict=True)
    info = root.lstat()
    allowed_parents = {
        Path("/private/tmp").resolve(strict=True),
        Path("/private/var/tmp").resolve(strict=True),
    }
    parent_info = physical.parent.lstat()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or physical.parent not in allowed_parents
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != 0
        or not parent_info.st_mode & stat.S_ISVTX
        or not physical.name.startswith("vkpi-phase-a-seatbelt.")
    ):
        raise FreezeError("refusing unsafe phase sandbox cleanup")

    def restore_tree_permission(function: object, raw_path: str, _error: object) -> None:
        target = Path(raw_path).absolute()
        try:
            target.relative_to(physical)
        except ValueError as exc:
            raise FreezeError("phase sandbox cleanup escaped its root") from exc
        for candidate in (target.parent, target):
            try:
                candidate_info = candidate.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(candidate_info.st_mode) and not candidate.is_symlink():
                os.chmod(candidate, 0o700)
        function(raw_path)  # type: ignore[operator]

    shutil.rmtree(physical, onexc=restore_tree_permission)
