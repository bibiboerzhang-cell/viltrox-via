#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = ROOT / "backend" / "app"


@dataclass
class Finding:
    path: str
    line: int
    kind: str


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _find_silent_exceptions(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    rel = _relative(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        handler_type = node.type
        if handler_type is not None:
            if not isinstance(handler_type, ast.Name) or handler_type.id not in {"Exception", "BaseException"}:
                continue
        if len(node.body) != 1:
            continue
        stmt = node.body[0]
        if isinstance(stmt, ast.Pass):
            findings.append(Finding(path=rel, line=getattr(stmt, "lineno", node.lineno), kind="pass"))
            continue
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                findings.append(Finding(path=rel, line=getattr(stmt, "lineno", node.lineno), kind="return_none"))
                continue
            if isinstance(stmt.value, ast.Dict) and not stmt.value.keys:
                findings.append(Finding(path=rel, line=getattr(stmt, "lineno", node.lineno), kind="return_empty_dict"))
    return findings


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        default="scripts/silent_exception_baseline.json",
        help="Baseline JSON with {'max_total': <int>}",
    )
    args = parser.parse_args()

    findings: list[Finding] = []
    for path in _iter_python_files(BACKEND_APP):
        findings.extend(_find_silent_exceptions(path))

    baseline_path = Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = ROOT / baseline_path

    max_total = 0
    if baseline_path.exists():
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        max_total = int(payload.get("max_total", 0))

    summary = {
        "total": len(findings),
        "max_total": max_total,
        "files": {},
    }
    for finding in findings:
        summary["files"].setdefault(finding.path, 0)
        summary["files"][finding.path] += 1

    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 1 if len(findings) > max_total else 0


if __name__ == "__main__":
    raise SystemExit(run())
