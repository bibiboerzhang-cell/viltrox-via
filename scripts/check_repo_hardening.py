#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from stdout_utils import out, out_json

ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = ROOT / "backend" / "app"
FRONTEND_SRC = ROOT / "frontend" / "src"
SCRIPTS_DIR = ROOT / "scripts"
ALLOWLIST_PATH = ROOT / "scripts" / "hardening_allowlist.json"
CONTRACTS_PATH = ROOT / "shared" / "contracts.json"


@dataclass
class Finding:
    kind: str
    path: str
    line: int
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class WarningBaseline:
    max_warnings: int
    policy: str
    warning_kind: str


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_warning_baseline(path: Path) -> WarningBaseline:
    """Load the reviewed warning ceiling, failing closed on configuration drift."""

    if not path.is_file():
        raise ValueError(f"warning baseline does not exist: {path}")
    payload = _load_json(path)
    if payload.get("schema_version") != 1:
        raise ValueError("warning baseline schema_version must be 1")
    if payload.get("policy") != "ratchet_only":
        raise ValueError("warning baseline policy must be ratchet_only")
    if payload.get("warning_kind") != "print_call":
        raise ValueError("warning baseline warning_kind must be print_call")
    if payload.get("review_required_for_increase") is not True:
        raise ValueError("warning baseline must require review for any increase")
    raw_max = payload.get("max_warnings")
    if isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max < 0:
        raise ValueError("warning baseline max_warnings must be a non-negative integer")
    return WarningBaseline(
        max_warnings=raw_max,
        policy="ratchet_only",
        warning_kind="print_call",
    )


def _warning_baseline_error(current: int, baseline: WarningBaseline) -> str | None:
    if current <= baseline.max_warnings:
        return None
    return f"warnings exceeded reviewed ratchet: current={current} baseline={baseline.max_warnings}"


ALLOWLIST = _load_json(ALLOWLIST_PATH) if ALLOWLIST_PATH.exists() else {}
CONTRACTS = _load_json(CONTRACTS_PATH)
DEPRECATED_LITERALS = tuple(str(item) for item in CONTRACTS.get("deprecated_literals") or [])


def _matches_allowlist(rel_path: str, key: str) -> bool:
    for pattern in ALLOWLIST.get(key, []):
        if fnmatch(rel_path, pattern):
            return True
    return False


def _parse(path: Path) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "_parent", parent)
    return tree


def _is_router_endpoint(node: ast.AsyncFunctionDef) -> bool:
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "router":
            return True
    return False


def _is_name_main_guard(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.comparators) != 1 or not isinstance(test.comparators[0], ast.Constant):
        return False
    return test.comparators[0].value == "__main__"


def _parent_chain(node: ast.AST) -> list[ast.AST]:
    chain: list[ast.AST] = []
    current = getattr(node, "_parent", None)
    while current is not None:
        chain.append(current)
        current = getattr(current, "_parent", None)
    return chain


def _is_within_nested_scope(node: ast.AST, root: ast.AsyncFunctionDef) -> bool:
    for parent in _parent_chain(node):
        if parent is root:
            return False
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return True
    return False


def _find_async_get_conn(path: Path) -> list[Finding]:
    tree = _parse(path)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or not _is_router_endpoint(node):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if _is_within_nested_scope(inner, node):
                continue
            func = inner.func
            if isinstance(func, ast.Name) and func.id == "get_conn":
                findings.append(
                    Finding(
                        kind="async_get_conn",
                        path=_relative(path),
                        line=getattr(inner, "lineno", node.lineno),
                        message=f"Async route '{node.name}' calls get_conn() directly",
                    )
                )
    return findings


def _find_silent_exceptions(path: Path) -> list[Finding]:
    rel_path = _relative(path)
    tree = _parse(path)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _matches_allowlist(rel_path, "silent_exception_allowlist"):
            continue
        handler_type = node.type
        if handler_type is not None:
            if not isinstance(handler_type, ast.Name) or handler_type.id not in {"Exception", "BaseException"}:
                continue
        if len(node.body) != 1:
            continue
        stmt = node.body[0]
        message = ""
        if isinstance(stmt, ast.Pass):
            message = "Silent exception handler uses pass"
        elif isinstance(stmt, ast.Return):
            if stmt.value is None:
                message = "Silent exception handler returns None"
            elif isinstance(stmt.value, ast.Dict) and not stmt.value.keys:
                message = "Silent exception handler returns {}"
        if message:
            findings.append(
                Finding(
                    kind="silent_exception",
                    path=rel_path,
                    line=getattr(stmt, "lineno", node.lineno),
                    message=message,
                )
            )
    return findings


def _find_prints(path: Path, severity: str) -> list[Finding]:
    rel_path = _relative(path)
    tree = _parse(path)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        if any(_is_name_main_guard(parent) for parent in _parent_chain(node)):
            continue
        if severity == "error" and _matches_allowlist(rel_path, "print_allowlist"):
            continue
        findings.append(
            Finding(
                kind="print_call",
                path=rel_path,
                line=getattr(node, "lineno", 1),
                message="Bare print() is not allowed here",
                severity=severity,
            )
        )
    return findings


def _find_deprecated_literals(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    ignore = {
        "frontend/src/lib/contracts.generated.ts",
        "shared/contracts.json",
    }
    for path in paths:
        rel_path = _relative(path)
        if rel_path in ignore:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for idx, line in enumerate(lines, start=1):
            for literal in DEPRECATED_LITERALS:
                if literal in line:
                    findings.append(
                        Finding(
                            kind="deprecated_literal",
                            path=rel_path,
                            line=idx,
                            message=f"Deprecated contract literal '{literal}' found",
                        )
                    )
    return findings


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _iter_frontend_files() -> list[Path]:
    files: list[Path] = []
    for ext in ("*.ts", "*.tsx"):
        files.extend(path for path in FRONTEND_SRC.rglob(ext) if "__pycache__" not in path.parts)
    return sorted(files)


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Return non-zero on hardening errors")
    parser.add_argument(
        "--warning-baseline",
        default="",
        help="Optional JSON file with {\"max_warnings\": <int>} to cap warning count",
    )
    args = parser.parse_args()

    findings: list[Finding] = []
    warnings: list[Finding] = []

    router_dir = BACKEND_APP / "api" / "routers"
    for path in sorted(router_dir.glob("*.py")):
        findings.extend(_find_async_get_conn(path))

    for path in _iter_python_files(BACKEND_APP):
        findings.extend(_find_silent_exceptions(path))
        findings.extend(_find_prints(path, severity="error"))

    for path in _iter_python_files(SCRIPTS_DIR):
        warnings.extend(_find_prints(path, severity="warning"))

    findings.extend(_find_deprecated_literals(_iter_python_files(BACKEND_APP)))
    findings.extend(_find_deprecated_literals(_iter_frontend_files()))

    for finding in findings + warnings:
        label = "WARN" if finding.severity == "warning" else "FAIL"
        out(f"[{label}] {finding.path}:{finding.line} {finding.kind} — {finding.message}")

    out_json(
        {
            "errors": len(findings),
            "warnings": len(warnings),
        },
        indent=2,
    )

    warning_baseline: WarningBaseline | None = None
    if args.warning_baseline:
        baseline_path = Path(args.warning_baseline)
        if not baseline_path.is_absolute():
            baseline_path = ROOT / baseline_path
        try:
            warning_baseline = _load_warning_baseline(baseline_path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"[FAIL] invalid warning baseline: {exc}\n")
            return 1

    if args.strict and findings:
        return 1
    if args.strict and warning_baseline is not None:
        baseline_error = _warning_baseline_error(len(warnings), warning_baseline)
        if baseline_error:
            sys.stderr.write(f"[FAIL] {baseline_error}\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
