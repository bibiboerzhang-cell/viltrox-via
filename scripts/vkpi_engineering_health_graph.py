"""Deterministic Python import-graph construction for engineering health."""
from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from typing import Any, Sequence

try:
    from scripts import vkpi_engineering_health_dynamic_imports as dynamic_imports
except ModuleNotFoundError:  # direct script execution adds scripts/, not repository root
    import vkpi_engineering_health_dynamic_imports as dynamic_imports


@dataclass(frozen=True)
class ImportGraphBuild:
    graph: dict[str, set[str]]
    module_paths: dict[str, str]
    collisions: list[str]
    edge_evidence: dict[tuple[str, str], list[dict[str, Any]]]
    unresolved_dynamic_imports: list[dict[str, Any]]
    resolved_constant_dynamic_import_count: int
    resolved_finite_dynamic_import_count: int
    resolved_dynamic_imports: list[dict[str, Any]]


def _module_name(relative_path: str) -> str | None:
    prefix = "backend/app/"
    if not relative_path.startswith(prefix) or not relative_path.endswith(".py"):
        return None
    parts = ["app", *relative_path[len(prefix) : -3].split("/")]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _relative_import_base(module: str, *, is_package: bool, level: int, imported: str) -> str:
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    ascend = max(0, level - 1)
    if ascend > len(package_parts):
        return ""
    anchor = package_parts[: len(package_parts) - ascend]
    if imported:
        anchor.extend(imported.split("."))
    return ".".join(anchor)


def _known_module_chain(name: str, known_modules: set[str]) -> set[str]:
    parts = name.split(".") if name else []
    return {
        candidate
        for index in range(1, len(parts) + 1)
        if (candidate := ".".join(parts[:index])) in known_modules
    }


def _resolve_import_targets(
    node: ast.Import | ast.ImportFrom,
    *,
    module: str,
    is_package: bool,
    known_modules: set[str],
) -> set[str]:
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.update(_known_module_chain(alias.name, known_modules))
        return targets
    base = (
        _relative_import_base(
            module,
            is_package=is_package,
            level=int(node.level),
            imported=node.module or "",
        )
        if node.level
        else node.module or ""
    )
    targets.update(_known_module_chain(base, known_modules))
    for alias in node.names:
        specific = f"{base}.{alias.name}" if base and alias.name != "*" else ""
        targets.update(_known_module_chain(specific, known_modules))
    return targets


def build_backend_import_graph(trees: dict[str, ast.Module]) -> ImportGraphBuild:
    module_paths: dict[str, str] = {}
    collisions: list[str] = []
    for path in sorted(trees):
        module = _module_name(path)
        if module is None:
            continue
        if module in module_paths:
            collisions.append(module)
        else:
            module_paths[module] = path
    known = set(module_paths)
    graph: dict[str, set[str]] = {module: set() for module in sorted(known)}
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unresolved: list[dict[str, Any]] = []
    resolved_constant = 0
    resolved_finite = 0
    resolved_rows: list[dict[str, Any]] = []
    for module in sorted(known):
        path = module_paths[module]
        is_package = path.endswith("/__init__.py") or path == "backend/app/__init__.py"
        for node in ast.walk(trees[path]):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            targets = _resolve_import_targets(
                node,
                module=module,
                is_package=is_package,
                known_modules=known,
            )
            kind = type(node).__name__
            graph[module].update(targets)
            for target in sorted(targets):
                evidence.setdefault((module, target), []).append(
                    {
                        "from_path": path,
                        "to_path": module_paths[target],
                        "line": int(node.lineno),
                        "kind": kind,
                    }
                )
    for finding in dynamic_imports.analyze_dynamic_imports(trees, module_paths):
        module = _module_name(finding.path)
        if module is None:
            continue
        targets: set[str] = set()
        for name in finding.targets:
            targets.update(_known_module_chain(name, known))
        graph[module].update(targets)
        for target in sorted(targets):
            evidence.setdefault((module, target), []).append(
                {
                    "from_path": finding.path,
                    "to_path": module_paths[target],
                    "line": finding.line,
                    "kind": finding.callee,
                }
            )
        if finding.reason is not None:
            row: dict[str, Any] = {
                "path": finding.path,
                "line": finding.line,
                "callee": finding.callee,
                "reason": finding.reason,
            }
            if finding.missing_targets:
                if finding.literal and len(finding.missing_targets) == 1:
                    row["value"] = finding.missing_targets[0]
                else:
                    row["missing_targets"] = list(finding.missing_targets)
            unresolved.append(row)
            continue
        if not finding.targets:
            continue
        if finding.literal:
            resolved_constant += 1
        else:
            resolved_finite += 1
        resolved_rows.append(
            {
                "path": finding.path,
                "line": finding.line,
                "callee": finding.callee,
                "resolution_kind": finding.resolution_kind,
                "targets": list(finding.targets),
            }
        )
    for rows in evidence.values():
        rows.sort(key=lambda item: (item["line"], item["kind"], item["to_path"]))
    return ImportGraphBuild(
        graph=graph,
        module_paths=module_paths,
        collisions=sorted(set(collisions)),
        edge_evidence=evidence,
        unresolved_dynamic_imports=sorted(
            unresolved,
            key=lambda item: (item["path"], item["line"], item["callee"]),
        ),
        resolved_constant_dynamic_import_count=resolved_constant,
        resolved_finite_dynamic_import_count=resolved_finite,
        resolved_dynamic_imports=resolved_rows,
    )


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(components, key=lambda item: (-len(item), item))


def is_cycle(component: Sequence[str], graph: dict[str, set[str]]) -> bool:
    return len(component) > 1 or (bool(component) and component[0] in graph.get(component[0], set()))


def architecture_owner(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 3 and parts[:2] == ["app", "domains"]:
        return f"domain:{parts[2]}"
    if len(parts) >= 2 and parts[0] == "app" and parts[1] in {"platform", "shared"}:
        return parts[1]
    if len(parts) >= 2 and parts[0] == "app":
        return f"legacy:{parts[1]}"
    return "legacy:app-root" if module == "app" else None


def _path_to_start(graph: dict[str, set[str]], source: str, start: str, allowed: set[str]) -> list[str] | None:
    queue: deque[tuple[str, list[str]]] = deque([(source, [source])])
    seen = {source}
    while queue:
        node, path = queue.popleft()
        if node == start:
            return path
        for target in sorted(graph.get(node, set())):
            if target not in allowed or target in seen:
                continue
            seen.add(target)
            queue.append((target, [*path, target]))
    return None


def cycle_witness(component: Sequence[str], graph: dict[str, set[str]]) -> list[str]:
    if not component:
        return []
    start = min(component)
    allowed = set(component)
    if start in graph.get(start, set()):
        return [start, start]
    for target in sorted(graph.get(start, set()) & allowed):
        suffix = _path_to_start(graph, target, start, allowed)
        if suffix:
            return [start, *suffix]
    return []
