#!/usr/bin/env python3
"""P4.2A write endpoint mechanical inventory.

This script intentionally does not assign risk levels. It extracts route
metadata from FastAPI router files so P4.2B can do the human audit.
"""
from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ROUTERS_DIR = ROOT / "backend" / "app" / "api" / "routers"
AUDIT_DIR = ROOT / "docs" / "audits"

WRITE_METHODS = {"post", "put", "patch", "delete"}

PERMISSION_KEYWORDS = [
    "require_tab",
    "require_permission",
    "require_admin",
    "require_owner",
    "get_current_staff",
    "current_staff",
    "current_user",
    "Depends",
    "ScopeDenied",
    "can_view_all",
    "MANAGER_ROLES",
    "FINANCE_ROLES",
    "_require_manager_staff",
]

AUDIT_KEYWORDS = [
    "audit_action",
    "audit_log",
    "log_audit",
    "log_settings_change",
    "log_action",
    "record_audit",
    "record_call",
    "settings_change",
    "action_log",
    "workflow_event",
    "create_audit",
    "write_audit",
]

PATH_VKPI_PATTERN = re.compile(
    r"(?:^|/)(?:vkpi|industry-data|kol|outreach|attribution|recommendations?|reports?)(?:/|$)",
    re.IGNORECASE,
)


def _call_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Subscript):
        return _call_name(node.value)
    return ""


def _decorator_method(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    name = _call_name(node.func)
    method = name.rsplit(".", 1)[-1].lower()
    return method if method in WRITE_METHODS else None


def _string_arg(node: ast.Call, keyword: str = "path") -> str:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    for kw in node.keywords:
        if kw.arg == keyword and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return ""


def _source_segment(source: str, node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.get_source_segment(source, node) or ""
    except Exception:
        return ""


def _function_signature(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    lines = source.splitlines()
    start = max(node.lineno - 1, 0)
    end = max(getattr(node, "end_lineno", node.lineno) - 1, start)
    signature_lines: list[str] = []
    for idx in range(start, min(end + 1, len(lines))):
        signature_lines.append(lines[idx].rstrip())
        if lines[idx].rstrip().endswith(":"):
            break
    return " ".join(part.strip() for part in signature_lines)


def _identifier_texts(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            values.add(child.id)
        elif isinstance(child, ast.Attribute):
            values.add(child.attr)
            full = _call_name(child)
            if full:
                values.add(full)
        elif isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name:
                values.add(name)
                values.add(name.rsplit(".", 1)[-1])
    return values


def _match_keyword(texts: set[str], keywords: list[str]) -> str:
    haystack = "\n".join(sorted(texts)).lower()
    for keyword in keywords:
        if keyword.lower() in haystack:
            return keyword
    return ""


def _is_vkpi_file(path: Path) -> bool:
    name = path.name
    return name.startswith("vkpi_") or name.startswith("kol_ops")


def _is_vkpi_path(path_literal: str) -> bool:
    return bool(PATH_VKPI_PATTERN.search(path_literal or ""))


def _iter_router_functions(path: Path) -> list[dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        rel = str(path.relative_to(ROOT))
        return [
            {
                "source_file": rel,
                "router_file": path.name,
                "method": "PARSE_ERROR",
                "path_literal": "",
                "decorator_line": exc.lineno or 0,
                "handler_name": "",
                "handler_def_line": 0,
                "file_is_vkpi": _is_vkpi_file(path),
                "path_is_vkpi": False,
                "has_permission_dep_grep": False,
                "permission_keyword": "",
                "has_audit_grep": False,
                "audit_keyword": "",
                "handler_signature": "",
                "raw_decorator": "",
                "parse_error": str(exc),
            }
        ]

    rows: list[dict[str, Any]] = []
    rel = str(path.relative_to(ROOT))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            method = _decorator_method(decorator)
            if method is None or not isinstance(decorator, ast.Call):
                continue
            path_literal = _string_arg(decorator)
            decorator_text = _source_segment(source, decorator)
            signature = _function_signature(source, node)

            permission_texts = _identifier_texts(decorator) | _identifier_texts(node.args)
            # Local guard calls are inside the function body, so include the body
            # for permission hints without interpreting whether they are sufficient.
            permission_texts |= _identifier_texts(node)
            audit_texts = _identifier_texts(decorator) | _identifier_texts(node)

            permission_keyword = _match_keyword(permission_texts, PERMISSION_KEYWORDS)
            audit_keyword = _match_keyword(audit_texts, AUDIT_KEYWORDS)

            rows.append(
                {
                    "source_file": rel,
                    "router_file": path.name,
                    "method": method.upper(),
                    "path_literal": path_literal,
                    "decorator_line": getattr(decorator, "lineno", 0),
                    "handler_name": node.name,
                    "handler_def_line": node.lineno,
                    "file_is_vkpi": _is_vkpi_file(path),
                    "path_is_vkpi": _is_vkpi_path(path_literal),
                    "has_permission_dep_grep": bool(permission_keyword),
                    "permission_keyword": permission_keyword,
                    "has_audit_grep": bool(audit_keyword),
                    "audit_keyword": audit_keyword,
                    "handler_signature": signature,
                    "raw_decorator": " ".join(decorator_text.split()),
                }
            )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "source_file",
        "router_file",
        "method",
        "path_literal",
        "decorator_line",
        "handler_name",
        "handler_def_line",
        "file_is_vkpi",
        "path_is_vkpi",
        "has_permission_dep_grep",
        "permission_keyword",
        "has_audit_grep",
        "audit_keyword",
        "handler_signature",
        "raw_decorator",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _rg_like_count() -> int:
    pattern = re.compile(r"@router\.(post|put|patch|delete)\b")
    total = 0
    for path in ROUTERS_DIR.rglob("*.py"):
        total += len(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return total


def _write_summary(path: Path, rows: list[dict[str, Any]], jsonl_path: Path, csv_path: Path) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    router_counts = Counter(row["router_file"] for row in rows)
    method_counts = Counter(row["method"] for row in rows)
    vkpi_count = sum(1 for row in rows if row["file_is_vkpi"] or row["path_is_vkpi"])
    no_permission = sum(1 for row in rows if not row["has_permission_dep_grep"])
    no_audit = sum(1 for row in rows if not row["has_audit_grep"])
    no_both = sum(1 for row in rows if not row["has_permission_dep_grep"] and not row["has_audit_grep"])
    rg_count = _rg_like_count()
    alias_decorator_count = sum(1 for row in rows if not str(row.get("raw_decorator") or "").startswith("router."))

    lines = [
        "# P4.2A Write Endpoint Mechanical Inventory",
        "",
        f"Generated at: {generated_at}",
        f"Repository: `{ROOT}`",
        "",
        "> This inventory is the P4.2A mechanical extraction output. It does not include risk judgement. Risk level, confirmation, audit effectiveness, and rollback capability are handled in P4.2B.",
        "",
        "## Outputs",
        "",
        f"- JSONL: `{jsonl_path.relative_to(ROOT)}`",
        f"- CSV: `{csv_path.relative_to(ROOT)}`",
        "",
        "## Sanity Check",
        "",
        f"- AST write endpoint rows: `{len(rows)}`",
        f"- rg-like `@router.(post|put|patch|delete)` count: `{rg_count}`",
        f"- Delta: `{len(rows) - rg_count}`",
        f"- APIRouter alias decorators captured by AST: `{alias_decorator_count}`",
        "- Delta is expected when routes use APIRouter aliases such as `public_router` or `webhook_router`; P4.2A keeps these rows because they are still write endpoints.",
        "",
        "## Method Distribution",
        "",
        "| Method | Count |",
        "|---|---:|",
    ]
    for method, count in sorted(method_counts.items()):
        lines.append(f"| {method} | {count} |")

    lines.extend(
        [
            "",
            "## Mechanical Flags",
            "",
            f"- `file_is_vkpi OR path_is_vkpi`: `{vkpi_count}`",
            f"- `has_permission_dep_grep=false`: `{no_permission}`",
            f"- `has_audit_grep=false`: `{no_audit}`",
            f"- `has_permission_dep_grep=false AND has_audit_grep=false`: `{no_both}`",
            "",
            "## Top Routers By Write Endpoint Count",
            "",
            "| Router | Count |",
            "|---|---:|",
        ]
    )
    for router, count in router_counts.most_common(30):
        lines.append(f"| `{router}` | {count} |")

    lines.extend(
        [
            "",
            "## Launch Scope Note",
            "",
            "- Launch-before focus for P4.2B-1 should be selected from this inventory, not from route count alone.",
            "- First-pass candidates remain `vkpi_settings.py`, `vkpi_industry_automation.py`, `vkpi_evidence_assets.py`, and `vkpi_operations.py`, subject to P4.2B review.",
            "- Launch-after routers should be audited after the launch-critical P0/P1 slice is understood.",
            "",
            "## P4.2B Suggested Filters",
            "",
            "```bash",
            f"jq -r 'select(.file_is_vkpi == true or .path_is_vkpi == true)' {jsonl_path.relative_to(ROOT)}",
            f"jq -r 'select((.file_is_vkpi == true or .path_is_vkpi == true) and .has_permission_dep_grep == false and .has_audit_grep == false)' {jsonl_path.relative_to(ROOT)}",
            "```",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in sorted(ROUTERS_DIR.rglob("*.py")):
        rows.extend(_iter_router_functions(path))
    rows.sort(key=lambda row: (row["source_file"], int(row["decorator_line"] or 0), row["method"], row["path_literal"]))

    jsonl_path = AUDIT_DIR / "p4_2a_write_endpoint_inventory.jsonl"
    csv_path = AUDIT_DIR / "p4_2a_write_endpoint_inventory.csv"
    summary_path = AUDIT_DIR / "2026-05-15-p4-2a-write-endpoint-inventory.md"

    _write_jsonl(jsonl_path, rows)
    _write_csv(csv_path, rows)
    _write_summary(summary_path, rows, jsonl_path, csv_path)

    print(f"rows={len(rows)}")
    print(f"jsonl={jsonl_path}")
    print(f"csv={csv_path}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
