"""Pure-AST architecture metrics for the engineering-health collector.

This module never imports candidate modules.  It consumes syntax trees and the
already-built internal import graph, keeping the measurement deterministic and
safe for an untrusted worktree.
"""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any


# This is the production-owned copy of the directions enforced by
# tests/test_layering_lint.py.  That test imports this constant, never the other
# way around, so the collector does not import or execute a test module.
LAYER_RULES: dict[str, tuple[str, ...]] = {
    "domains": ("app.workers", "app.api"),
    "services": ("app.api",),
}

ABSTRACT_BASE_TERMINALS = {"ABC", "Protocol"}
ABSTRACT_METACLASS_TERMINALS = {"ABCMeta"}
ABSTRACT_DECORATOR_TERMINALS = {
    "abstractmethod",
    "abstractclassmethod",
    "abstractstaticmethod",
}
MAIN_SEQUENCE_TOP_ROWS = 25


@dataclass(frozen=True)
class ClassSpan:
    path: str
    qualified_name: str
    line: int
    end_line: int
    loc: int
    is_abstract: bool


def _terminal_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Subscript, ast.Call)):
        node = node.value if isinstance(node, ast.Subscript) else node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_abstract_class(node: ast.ClassDef) -> bool:
    if any(_terminal_name(base) in ABSTRACT_BASE_TERMINALS for base in node.bases):
        return True
    if any(
        keyword.arg == "metaclass"
        and _terminal_name(keyword.value) in ABSTRACT_METACLASS_TERMINALS
        for keyword in node.keywords
    ):
        return True
    for statement in node.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(
            _terminal_name(decorator) in ABSTRACT_DECORATOR_TERMINALS
            for decorator in statement.decorator_list
        ):
            return True
    return False


class _ClassCollector(ast.NodeVisitor):
    """Record every ClassDef, including classes nested in sync/async scopes."""

    def __init__(self, *, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.rows: list[ClassSpan] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        end_line = int(node.end_lineno or node.lineno)
        self.rows.append(
            ClassSpan(
                path=self.path,
                qualified_name=".".join([*self.scope, node.name]),
                line=int(node.lineno),
                end_line=end_line,
                loc=end_line - int(node.lineno) + 1,
                is_abstract=_is_abstract_class(node),
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._function(node)


def collect_class_spans(trees: dict[str, ast.Module]) -> list[ClassSpan]:
    rows: list[ClassSpan] = []
    for path in sorted(trees):
        visitor = _ClassCollector(path=path)
        visitor.visit(trees[path])
        rows.extend(visitor.rows)
    return sorted(rows, key=lambda item: (-item.loc, item.path, item.line, item.qualified_name))


def banned_import(module: str, banned_prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in banned_prefixes)


def collect_reverse_dependencies(trees: dict[str, ast.Module]) -> list[dict[str, Any]]:
    """Return unique ``(path, forbidden imported module)`` violations."""
    findings: set[tuple[str, str, str]] = set()
    prefix = "backend/app/"
    for path in sorted(trees):
        if not path.startswith(prefix):
            continue
        relative = path[len(prefix) :]
        layer = relative.split("/", 1)[0]
        banned_prefixes = LAYER_RULES.get(layer)
        if banned_prefixes is None:
            continue
        for node in ast.walk(trees[path]):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            for module in modules:
                if banned_import(module, banned_prefixes):
                    findings.add((path, module, layer))
    return [
        {"path": path, "imported_module": module, "source_layer": layer}
        for path, module, layer in sorted(findings)
    ]


def main_sequence_unit(relative_path: str) -> str:
    """Aggregate a backend source path to two package directories below app."""
    prefix = "backend/app/"
    if not relative_path.startswith(prefix):
        raise ValueError(f"backend/app path required: {relative_path}")
    parts = relative_path[len(prefix) :].split("/")
    directories = parts[:-1]
    return ".".join(["app", *directories[:2]])


def _nearest_rank(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def collect_main_sequence(
    trees: dict[str, ast.Module],
    graph: dict[str, set[str]],
    module_paths: dict[str, str],
) -> dict[str, Any]:
    """Compute Robert Martin distance for internal second-level packages."""
    unit_by_module = {
        module: main_sequence_unit(module_paths[module])
        for module in sorted(graph)
    }
    units = sorted(set(unit_by_module.values()))
    outgoing: dict[str, set[str]] = {unit: set() for unit in units}
    incoming: dict[str, set[str]] = {unit: set() for unit in units}
    for source in sorted(graph):
        source_unit = unit_by_module[source]
        for target in sorted(graph[source]):
            target_unit = unit_by_module[target]
            if source_unit == target_unit:
                continue
            outgoing[source_unit].add(target_unit)
            incoming[target_unit].add(source_unit)

    class_totals = {unit: 0 for unit in units}
    abstract_totals = {unit: 0 for unit in units}
    for module in sorted(module_paths):
        path = module_paths[module]
        unit = unit_by_module[module]
        for node in ast.walk(trees[path]):
            if not isinstance(node, ast.ClassDef):
                continue
            class_totals[unit] += 1
            abstract_totals[unit] += int(_is_abstract_class(node))

    rows: list[dict[str, Any]] = []
    raw_distances: list[float] = []
    for unit in units:
        ca = len(incoming[unit])
        ce = len(outgoing[unit])
        total_classes = class_totals[unit]
        abstractness = abstract_totals[unit] / total_classes if total_classes else 0.0
        instability = ce / (ca + ce) if ca + ce else 0.0
        distance = abs(abstractness + instability - 1.0)
        raw_distances.append(distance)
        rows.append(
            {
                "unit": unit,
                "ca": ca,
                "ce": ce,
                "class_count": total_classes,
                "abstract_class_count": abstract_totals[unit],
                "abstractness": round(abstractness, 8),
                "instability": round(instability, 8),
                "distance": round(distance, 8),
                "zero_coupling": ca + ce == 0,
            }
        )
    rows.sort(key=lambda item: (-item["distance"], item["unit"]))
    p90 = _nearest_rank(raw_distances, 0.9)
    return {
        "unit_count": len(units),
        "p90": round(p90, 8) if p90 is not None else None,
        "zero_coupling_unit_count": sum(row["zero_coupling"] for row in rows),
        "units": rows,
        "top_distance_units": rows[:MAIN_SEQUENCE_TOP_ROWS],
    }
