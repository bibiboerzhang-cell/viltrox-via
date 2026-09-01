#!/usr/bin/env python3
"""Run reviewed gate Python commands without site, .pth, or PYTHONPATH startup."""

from __future__ import annotations

import os
import importlib.machinery
import importlib.util
import runpy
import stat
import sys
import sysconfig
import tempfile
from pathlib import Path


ALLOWED_MODULES = frozenset({"alembic", "pytest"})
IGNORED_FLAGS = frozenset({"-B", "-E", "-I", "-S", "-s"})


class _CandidateTopLevelFinder:
    """Pin repository namespaces so site-packages cannot shadow gate code."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self._roots = roots

    def find_spec(
        self, fullname: str, path: object = None, target: object = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        if "." in fullname or fullname not in self._roots:
            return None
        directory = self._roots[fullname]
        package_init = directory / "__init__.py"
        if package_init.is_file() and not package_init.is_symlink():
            return importlib.util.spec_from_file_location(
                fullname,
                package_init,
                submodule_search_locations=[str(directory)],
            )
        spec = importlib.machinery.ModuleSpec(
            fullname, loader=None, is_package=True,
        )
        spec.submodule_search_locations = [str(directory)]
        return spec


def _safe_directory(path: Path, *, label: str) -> Path:
    resolved = path.resolve(strict=True)
    info = resolved.lstat()
    if (
        resolved.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise SystemExit(f"safe Python {label} is not a trusted directory")
    return resolved


def _validate_script_chain(root: Path, path: Path, *, label: str) -> None:
    """Reject scripts reached through writable or symlinked candidate entries."""

    if path == root or not path.is_relative_to(root):
        raise SystemExit(f"safe Python {label} is outside the candidate root")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        info = current.lstat()
        is_leaf = current == path
        expected_type = stat.S_ISREG if is_leaf else stat.S_ISDIR
        if (
            current.is_symlink()
            or not expected_type(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o022
            or (is_leaf and info.st_nlink != 1)
        ):
            raise SystemExit(f"safe Python {label} has an untrusted path component")


def _candidate_root() -> Path:
    lexical_router = Path(__file__)
    if not lexical_router.is_absolute():
        lexical_router = Path.cwd() / lexical_router
    router = lexical_router.resolve(strict=True)
    if lexical_router != router:
        raise SystemExit("safe Python router path may not contain symlinks")
    root = _safe_directory(router.parents[2], label="candidate root")
    if router != root / "scripts/ops/safe_python_router.py":
        raise SystemExit("safe Python router path is invalid")
    _validate_script_chain(root, router, label="router")
    return root


def _configure_imports(root: Path) -> None:
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.ignore_environment):
        raise SystemExit("safe Python router requires -I -S")
    paths = sysconfig.get_paths()
    dependency_roots: list[Path] = []
    for name in ("purelib", "platlib"):
        raw = paths.get(name)
        if raw:
            resolved = _safe_directory(Path(raw), label=name)
            if resolved not in dependency_roots:
                dependency_roots.append(resolved)
    stdlib = [entry for entry in sys.path if entry and Path(entry).resolve() != root]
    sys.path[:] = [*stdlib, *(str(path) for path in dependency_roots)]
    sys.path.extend((str(root), str(root / "scripts"), str(root / "backend")))
    candidate_roots = {
        "scripts": root / "scripts",
        "tests": root / "tests",
        "backend": root / "backend",
        "app": root / "backend/app",
    }
    for name, directory in candidate_roots.items():
        _safe_directory(directory, label=f"candidate {name}")
    sys.meta_path.insert(0, _CandidateTopLevelFinder(candidate_roots))
    for name in (
        "PYTHONHOME", "PYTHONINSPECT", "PYTHONPATH", "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    ):
        os.environ.pop(name, None)


def _safe_script(root: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve(strict=True)
    if path != resolved or not resolved.is_relative_to(root):
        raise SystemExit("safe Python rejected an untrusted script path")
    _validate_script_chain(root, resolved, label="script")
    return resolved


def _dispatch(root: Path, arguments: list[str]) -> None:
    while arguments and arguments[0] in IGNORED_FLAGS:
        arguments.pop(0)
    if not arguments:
        raise SystemExit("safe Python requires a reviewed command")
    if arguments[0] == "-m":
        if len(arguments) < 2 or arguments[1] not in ALLOWED_MODULES:
            raise SystemExit("safe Python rejected an unreviewed module")
        sys.argv = arguments[1:]
        runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
        return
    if arguments[0] == "-":
        sys.argv = arguments
        source = sys.stdin.buffer.read()
        namespace = {"__name__": "__main__", "__file__": "<stdin>"}
        exec(compile(source, "<stdin>", "exec"), namespace, namespace)
        return
    if arguments[0].startswith("-"):
        raise SystemExit("safe Python rejected an unsupported option")
    script = _safe_script(root, arguments[0])
    sys.argv = [str(script), *arguments[1:]]
    runpy.run_path(str(script), run_name="__main__")


def main() -> None:
    root = _candidate_root()
    _configure_imports(root)
    from scripts.ops.freeze_phase_runtime import (
        _prepare_nested_dependency_mirror,
        remove_owned_phase_sandbox,
    )

    runtime_root = Path(tempfile.mkdtemp(
        prefix="vkpi-phase-a-seatbelt.", dir="/private/tmp",
    ))
    runtime_root.chmod(0o700)
    try:
        dependency_root, _inventory, _proof = _prepare_nested_dependency_mirror(
            _safe_directory(Path(sysconfig.get_path("purelib")), label="purelib"),
            runtime_root,
        )
        sys.path.insert(0, str(dependency_root))
        _dispatch(root, sys.argv[1:])
    finally:
        remove_owned_phase_sandbox(runtime_root)


if __name__ == "__main__":
    main()
