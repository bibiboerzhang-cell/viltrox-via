#!/usr/bin/env python3
"""Collect deterministic, read-only V-KPI engineering-health evidence.

The collector uses only the current worktree and Python's standard library.
It starts no service or network call, never infers missing metrics, and emits
byte-identical JSON for the same inputs and fixed ``--observed-at`` value.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from scripts import vkpi_engineering_health_architecture as architecture_tools
    from scripts import vkpi_engineering_health_score as health_score
    from scripts import vkpi_engineering_health_graph as graph_tools
    from scripts import vkpi_engineering_health_snapshot as snapshot
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import vkpi_engineering_health_architecture as architecture_tools
    import vkpi_engineering_health_score as health_score
    import vkpi_engineering_health_graph as graph_tools
    import vkpi_engineering_health_snapshot as snapshot
    from stdout_utils import out as stdout_out


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"
SCHEMA_VERSION = "vkpi_engineering_health_collector_v1"
ALGORITHM_VERSION = "python-ast-cc2-finite-dynamic-import2-tarjan2-architecture1-snapshot2-lineguard1"
LINE_LIMIT = 800
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".css"}
PYTHON_ROOTS = ("backend/app",)
LINE_ROOTS = ("backend/app", "frontend/src", "scripts")
SKIP_PARTS = {
    ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__",
    "build", "dist", "fixtures", "generated", "migrations", "node_modules",
}
TEST_DIRECTORY_NAMES = {"test", "tests", "__tests__"}
TEST_FILENAME_MARKERS = (".test.", ".spec.")
TOP_COMPLEX_FUNCTIONS = 25

class CollectionError(ValueError):
    """Raised when the requested collection cannot be performed safely."""

@dataclass(frozen=True)
class FunctionComplexity:
    path: str
    qualified_name: str
    line: int
    end_line: int
    loc: int
    cc: int


@dataclass(frozen=True)
class ParseFailure:
    path: str
    error_type: str
    line: int | None




def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_observed_at(value: str | None) -> str:
    if value is None:
        return _utc_now()
    normalized = value.strip()
    if not normalized:
        raise CollectionError("--observed-at must not be empty")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CollectionError("--observed-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CollectionError("--observed-at must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _take_source_snapshot(root: Path) -> snapshot.SourceSnapshot:
    return snapshot.snapshot_sources(
        root,
        LINE_ROOTS,
        SOURCE_SUFFIXES,
        skip_parts=SKIP_PARTS,
        test_directory_names=TEST_DIRECTORY_NAMES,
        test_filename_markers=TEST_FILENAME_MARKERS,
    )


def inventory_sources(root: Path) -> list[snapshot.SourceFile]:
    """Compatibility helper returning the immutable bytes from one snapshot."""
    return list(_take_source_snapshot(root).files)


class _DecisionCounter(ast.NodeVisitor):
    """Count documented McCabe-style decision points inside one function."""

    def __init__(self) -> None:
        self.decisions = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return None  # Nested functions have their own complexity record.

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return None

    def _one(self, node: ast.AST) -> None:
        self.decisions += 1
        self.generic_visit(node)

    visit_If = _one
    visit_For = _one
    visit_AsyncFor = _one
    visit_While = _one
    visit_Assert = _one
    visit_IfExp = _one

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.decisions += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        self.decisions += len(node.handlers) + int(bool(node.orelse))
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802
        self.visit_Try(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:  # noqa: N802
        self.decisions += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:  # noqa: N802
        for case in node.cases:
            pattern = case.pattern
            is_default = (
                isinstance(pattern, ast.MatchAs)
                and pattern.pattern is None
                and pattern.name is None
                and case.guard is None
            )
            self.decisions += 0 if is_default else 1
            if case.guard is not None:
                self.decisions += 1
        self.generic_visit(node)


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, *, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.rows: list[FunctionComplexity] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        counter = _DecisionCounter()
        for statement in node.body:
            counter.visit(statement)
        end_line = int(node.end_lineno or node.lineno)
        qualified_name = ".".join([*self.scope, node.name])
        self.rows.append(
            FunctionComplexity(
                path=self.path,
                qualified_name=qualified_name,
                line=int(node.lineno),
                end_line=end_line,
                loc=end_line - int(node.lineno) + 1,
                cc=1 + counter.decisions,
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        counter = _DecisionCounter()
        counter.visit(node.body)
        end_line = int(node.end_lineno or node.lineno)
        self.rows.append(
            FunctionComplexity(
                path=self.path,
                qualified_name=".".join([*self.scope, f"<lambda@{node.lineno}:{node.col_offset}>"]),
                line=int(node.lineno),
                end_line=end_line,
                loc=end_line - int(node.lineno) + 1,
                cc=1 + counter.decisions,
            )
        )
        self.scope.append(f"<lambda@{node.lineno}:{node.col_offset}>")
        self.generic_visit(node)
        self.scope.pop()


def parse_python_sources(files: Sequence[snapshot.SourceFile]) -> tuple[dict[str, ast.Module], list[ParseFailure]]:
    trees: dict[str, ast.Module] = {}
    failures: list[ParseFailure] = []
    for item in files:
        if item.path.suffix != ".py":
            continue
        try:
            tree = ast.parse(snapshot.decode_python(item), filename=item.relative_path)
        except (snapshot.SnapshotError, SyntaxError) as exc:
            line = int(exc.lineno) if isinstance(exc, SyntaxError) and exc.lineno is not None else None
            failures.append(ParseFailure(item.relative_path, type(exc).__name__, line))
            continue
        trees[item.relative_path] = tree
    return trees, failures


def collect_complexity(trees: dict[str, ast.Module]) -> list[FunctionComplexity]:
    rows: list[FunctionComplexity] = []
    for path in sorted(trees):
        collector = _FunctionCollector(path=path)
        collector.visit(trees[path])
        rows.extend(collector.rows)
    return sorted(rows, key=lambda item: (-item.cc, item.path, item.line, item.qualified_name))


def _unknown(observed_at: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "value": None,
        "source": "collector://vkpi-engineering-health/v1",
        "observed_at": observed_at,
        "reason": reason,
    }


def _observed(value: float | int, observed_at: str, source: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "observed",
        "value": value,
        "source": source,
        "observed_at": observed_at,
        **extra,
    }


def collect_observations(
    root: Path,
    *,
    observed_at: str,
    source_snapshot: snapshot.SourceSnapshot | None = None,
) -> dict[str, Any]:
    captured = source_snapshot or _take_source_snapshot(root)
    files = list(captured.files)
    python_files = [
        item
        for item in files
        if item.path.suffix == ".py"
        and any(item.relative_path == prefix or item.relative_path.startswith(prefix + "/") for prefix in PYTHON_ROOTS)
    ]
    trees, parse_failures = parse_python_sources(python_files)
    complexity_rows = collect_complexity(trees)
    class_rows = architecture_tools.collect_class_spans(trees)
    reverse_dependencies = architecture_tools.collect_reverse_dependencies(trees)

    line_violations = [item for item in files if item.physical_lines > LINE_LIMIT]
    line_violations.sort(key=lambda item: (-item.physical_lines, item.relative_path))
    by_category = Counter(item.category for item in line_violations)
    max_module = min(files, key=lambda item: (-item.physical_lines, item.relative_path), default=None)

    backend_failures = [item for item in parse_failures if item.path.startswith("backend/app/")]
    graph_build = graph_tools.build_backend_import_graph(trees)
    graph = graph_build.graph
    module_paths = graph_build.module_paths
    collisions = graph_build.collisions
    components = graph_tools.strongly_connected_components(graph)
    cyclic = [item for item in components if graph_tools.is_cycle(item, graph)]
    scc_rows: list[dict[str, Any]] = []
    for component in cyclic:
        owners = sorted(
            {owner for module in component if (owner := graph_tools.architecture_owner(module)) is not None}
        )
        witness = graph_tools.cycle_witness(component, graph)
        scc_rows.append(
            {
                "size": len(component),
                "cross_core": len(owners) >= 2,
                "architecture_owners": owners,
                "members": list(component),
                "cycle_witness": witness,
                "cycle_witness_edges": [
                    {"from_module": source, "from_path": module_paths[source],
                     "to_module": target, "to_path": module_paths[target],
                     "import_evidence": graph_build.edge_evidence.get((source, target), [])}
                    for source, target in zip(witness, witness[1:])
                ],
            }
        )
    cross_core = [item for item in scc_rows if item["cross_core"]]
    edge_count = sum(len(targets) for targets in graph.values())
    max_fan_out = max((len(targets) for targets in graph.values()), default=0)
    max_fan_out_modules = sorted(module for module, targets in graph.items() if len(targets) == max_fan_out)
    main_sequence = architecture_tools.collect_main_sequence(trees, graph, module_paths)

    cc_buckets = {
        "le_10": sum(item.cc <= 10 for item in complexity_rows),
        "11_to_20": sum(11 <= item.cc <= 20 for item in complexity_rows),
        "21_to_50": sum(21 <= item.cc <= 50 for item in complexity_rows),
        "gt_50": sum(item.cc > 50 for item in complexity_rows),
    }
    python_loc = sum(item.physical_lines for item in python_files)
    complete_ast = captured.complete and not parse_failures and bool(python_files)
    complete_python = captured.complete and not parse_failures and bool(python_files) and bool(complexity_rows)
    valid_static_graph = captured.complete and not backend_failures and not collisions and bool(graph)
    complete_graph = valid_static_graph and not graph_build.unresolved_dynamic_imports

    return {
        "status": "observed" if complete_python and complete_graph and max_module is not None else "partial",
        "observed_at": observed_at,
        "scope": {
            "python_roots": list(PYTHON_ROOTS),
            "line_roots": list(LINE_ROOTS),
            "excluded_path_parts": sorted(SKIP_PARTS),
            "tests_excluded": True,
            "symlinks_followed": False,
            "source_suffixes": sorted(SOURCE_SUFFIXES),
        },
        "source_snapshot": captured.identity(),
        "python_complexity": {
            "status": "observed" if complete_python else "unknown",
            "algorithm": (
                "CC=1 + If/For/AsyncFor/While/Assert/IfExp + BoolOp operands minus one + "
                "Try handlers and else + comprehension generators and filters + non-default Match cases and guards; "
                "nested functions/classes/lambdas are excluded from the enclosing function; "
                "nested functions and lambdas receive independent rows"
            ),
            "python_file_count": len(python_files),
            "python_physical_loc": python_loc,
            "parsed_file_count": len(trees),
            "function_count": len(complexity_rows),
            "cc_distribution": cc_buckets,
            "cc_le_10_ratio": (
                round(cc_buckets["le_10"] / len(complexity_rows), 8) if complete_python else None
            ),
            "max_cc": complexity_rows[0].cc if complete_python else None,
            "top_functions": [asdict(item) for item in complexity_rows[:TOP_COMPLEX_FUNCTIONS]],
            "parse_errors": [asdict(item) for item in parse_failures],
        },
        "python_classes": {
            "status": "observed" if complete_ast else "unknown",
            "definition": (
                "every AST ClassDef in production Python receives an independent physical span "
                "ClassDef.lineno..end_lineno inclusive; decorators and trailing comments are outside the span; "
                "classes nested in classes, sync functions, and async functions are included and also remain "
                "inside the enclosing class span"
            ),
            "class_count": len(class_rows),
            "class_loc_max": class_rows[0].loc if class_rows else 0,
            "largest_classes": [asdict(item) for item in class_rows[:TOP_COMPLEX_FUNCTIONS]],
            "parse_errors": [asdict(item) for item in parse_failures],
        },
        "line_guard": {
            "status": "observed" if captured.complete and max_module is not None else "unknown",
            "definition": "physical lines from bytes.splitlines(); production tests/generated/migrations excluded",
            "limit": LINE_LIMIT,
            "source_file_count": len(files),
            "module_loc_max": max_module.physical_lines if max_module else None,
            "largest_path": max_module.relative_path if max_module else None,
            "violation_count": len(line_violations),
            "violations_by_category": dict(sorted(by_category.items())),
            "frontend_violation_count": by_category.get("frontend", 0),
            "style_violation_count": by_category.get("style", 0),
            "script_violation_count": by_category.get("script", 0),
            "violations": [
                {
                    "path": item.relative_path,
                    "lines": item.physical_lines,
                    "category": item.category,
                }
                for item in line_violations
            ],
        },
        "backend_import_graph": {
            "status": "observed" if complete_graph else "partial" if valid_static_graph else "unknown",
            "definition": (
                "Python Import/ImportFrom plus literal or statically proven finite internal "
                "importlib.import_module/__import__ edges, including implicit parent-package "
                "initialization; finite proof uses bounded AST constant propagation and complete "
                "direct-call argument enumeration only"
            ),
            "cross_core_definition": (
                "a cyclic SCC is cross-core when it spans at least two architecture owners: "
                "domain:<first app.domains folder>, platform/shared, or legacy:<top app namespace>"
            ),
            "module_count": len(graph),
            "edge_count": edge_count,
            "cycle_scc_count": len(scc_rows) if valid_static_graph else None,
            "cyclic_module_count": sum(item["size"] for item in scc_rows) if valid_static_graph else None,
            "cross_core_scc_count": len(cross_core) if valid_static_graph else None,
            "internal_fan_out_max": max_fan_out if valid_static_graph else None,
            "max_fan_out_modules": max_fan_out_modules if valid_static_graph else [],
            "module_paths": module_paths,
            "cyclic_sccs": scc_rows,
            "parse_errors": [asdict(item) for item in backend_failures],
            "module_name_collisions": collisions,
            "resolved_constant_dynamic_import_count": graph_build.resolved_constant_dynamic_import_count,
            "resolved_finite_dynamic_import_count": graph_build.resolved_finite_dynamic_import_count,
            "resolved_dynamic_import_count": (
                graph_build.resolved_constant_dynamic_import_count
                + graph_build.resolved_finite_dynamic_import_count
            ),
            "resolved_dynamic_imports": graph_build.resolved_dynamic_imports,
            "unresolved_dynamic_import_count": len(graph_build.unresolved_dynamic_imports),
            "unresolved_dynamic_imports": graph_build.unresolved_dynamic_imports,
            "limitations": [
                "non-finite, relative, missing-target, or incompletely enumerated dynamic imports make graph metrics partial and score evidence unknown",
                "finite call-site proof covers direct source-visible calls only and fails closed on any unresolved argument",
                "re-export-heavy package initializers can enlarge an SCC",
                "static cycles are architecture evidence, not proof that every edge executes at runtime",
            ],
        },
        "architecture_static": {
            "reverse_dependencies": {
                "status": "observed" if complete_ast else "unknown",
                "definition": (
                    "unique (production path, imported forbidden module) pairs; domains forbid app.workers/app.api; "
                    "services forbid app.api; absolute Import and ImportFrom anywhere in the AST are scanned"
                ),
                "rules": {key: list(value) for key, value in architecture_tools.LAYER_RULES.items()},
                "count": len(reverse_dependencies),
                "violations": reverse_dependencies,
            },
            "main_sequence": {
                "status": "observed" if complete_graph else "unknown",
                "definition": (
                    "Robert Martin D=|A+I-1| at the first two package directories below backend/app "
                    "(for example app.domains.kol); A=syntactically abstract ClassDef/all ClassDef; "
                    "I=Ce/(Ca+Ce) over unique non-self internal unit dependencies; Ca+Ce=0 sets I=0 "
                    "and retains the unit; p90 uses nearest-rank ceil(0.90*N)"
                ),
                "abstract_class_definition": (
                    "ClassDef is abstract when a base terminates in ABC/Protocol, metaclass terminates in ABCMeta, "
                    "or a direct sync/async method decorator terminates in abstractmethod, "
                    "abstractclassmethod, or abstractstaticmethod"
                ),
                **main_sequence,
            },
        },
    }


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"cannot read contract: {path}") from exc
    if not isinstance(payload, dict):
        raise CollectionError("engineering-health contract must be a JSON object")
    health_score.validate_contract(payload)
    return payload


def build_evidence(
    root: Path,
    contract: dict[str, Any],
    *,
    observed_at: str,
    snapshot_reader: Callable[[Path], snapshot.SourceSnapshot] | None = None,
    git_probe: Callable[[Path], dict[str, object]] | None = None,
) -> dict[str, Any]:
    source_reader = snapshot_reader or _take_source_snapshot
    state_reader = git_probe or snapshot.trusted_git_state
    git_before = state_reader(root)
    primary = source_reader(root)
    observations = collect_observations(root, observed_at=observed_at, source_snapshot=primary)
    source_after = source_reader(root)
    git_after = state_reader(root)
    source_stable = primary.identity() == source_after.identity()
    status_stable = git_before == git_after
    stable = source_stable and status_stable and primary.complete and source_after.complete

    missing_reason = "not collected by read-only static collector v1"
    evidence: dict[str, Any] = {
        "schema_version": "vkpi_engineering_health_evidence_v1",
        "contract_sha256": health_score.contract_sha256(contract),
        "contract_hash_algorithm": "sha256:canonical-json-sort-keys",
        "generated_at": observed_at,
        "candidate": {
            "repo": str(root.resolve()),
            **{key: git_before[key] for key in (
                "branch", "head", "clean_worktree", "tracked_change_count",
                "untracked_change_count", "status_sha256",
            )},
            "source_content_sha256": primary.content_sha256,
            "source_file_count": len(primary.files),
            "source_and_status_stable": stable,
        },
        "metrics": {
            dimension_name: {metric_name: _unknown(observed_at, missing_reason)
                             for metric_name in dimension["metrics"]}
            for dimension_name, dimension in contract["dimensions"].items()
        },
        "release_gates": {
            name: _unknown(observed_at, "release gate requires an independent receipt")
            for name in contract["release_gates"]
        },
    }
    if not stable:
        observations["status"] = "partial"
        missing_reason = "source or trusted Git status drifted during collection"
        for dimension in evidence["metrics"].values():
            for metric_name in dimension:
                dimension[metric_name] = _unknown(observed_at, missing_reason)
    code_metrics = evidence["metrics"]["code"]
    complexity = observations["python_complexity"]
    if stable and complexity["status"] == "observed":
        code_metrics["cc_le_10_ratio"] = _observed(
            complexity["cc_le_10_ratio"],
            observed_at,
            "collector://vkpi-engineering-health/v1/python-ast-cc",
            sample_count=complexity["function_count"],
            details={"distribution": complexity["cc_distribution"]},
        )
        code_metrics["max_cc"] = _observed(
            complexity["max_cc"],
            observed_at,
            "collector://vkpi-engineering-health/v1/python-ast-cc",
            sample_count=complexity["function_count"],
            details={"top_functions": complexity["top_functions"]},
        )
    else:
        reason = missing_reason if not stable else "one or more production Python files could not be parsed"
        code_metrics["cc_le_10_ratio"] = _unknown(observed_at, reason)
        code_metrics["max_cc"] = _unknown(observed_at, reason)

    architecture = evidence["metrics"]["architecture"]
    line_guard = observations["line_guard"]
    if stable and line_guard["status"] == "observed":
        architecture["module_loc_max"] = _observed(
            line_guard["module_loc_max"],
            observed_at,
            "collector://vkpi-engineering-health/v1/physical-line-guard",
            sample_count=line_guard["source_file_count"],
            details={
                "largest_path": line_guard["largest_path"],
                "violation_count": line_guard["violation_count"],
                "violations_by_category": line_guard["violations_by_category"],
            },
        )

    class_observation = observations["python_classes"]
    if stable and class_observation["status"] == "observed":
        architecture["class_loc_max"] = _observed(
            class_observation["class_loc_max"],
            observed_at,
            "collector://vkpi-engineering-health/v1/python-ast-class-span",
            sample_count=class_observation["class_count"],
            details={
                "definition": class_observation["definition"],
                "largest_classes": class_observation["largest_classes"],
            },
        )

    architecture_static = observations["architecture_static"]
    reverse_observation = architecture_static["reverse_dependencies"]
    if stable and reverse_observation["status"] == "observed":
        architecture["reverse_dependency_count"] = _observed(
            reverse_observation["count"],
            observed_at,
            "collector://vkpi-engineering-health/v1/layering-reverse-dependencies",
            sample_count=complexity["parsed_file_count"],
            details={
                "definition": reverse_observation["definition"],
                "rules": reverse_observation["rules"],
                "violations": reverse_observation["violations"],
            },
        )

    import_graph = observations["backend_import_graph"]
    if stable and import_graph["status"] == "observed":
        graph_source = "collector://vkpi-engineering-health/v1/backend-static-import-scc"
        architecture["package_cycle_count"] = _observed(
            import_graph["cycle_scc_count"],
            observed_at,
            graph_source,
            sample_count=import_graph["module_count"],
            details={"cyclic_module_count": import_graph["cyclic_module_count"]},
        )
        architecture["cross_core_scc_count"] = _observed(
            import_graph["cross_core_scc_count"],
            observed_at,
            graph_source,
            sample_count=import_graph["module_count"],
            details={"definition": import_graph["cross_core_definition"]},
        )
        architecture["internal_fan_out_max"] = _observed(
            import_graph["internal_fan_out_max"],
            observed_at,
            graph_source,
            sample_count=import_graph["module_count"],
            details={"modules": import_graph["max_fan_out_modules"]},
        )
        main_sequence_observation = architecture_static["main_sequence"]
        architecture["main_sequence_distance_p90"] = _observed(
            main_sequence_observation["p90"],
            observed_at,
            "collector://vkpi-engineering-health/v1/backend-main-sequence",
            sample_count=main_sequence_observation["unit_count"],
            details={
                "definition": main_sequence_observation["definition"],
                "abstract_class_definition": main_sequence_observation["abstract_class_definition"],
                "zero_coupling_unit_count": main_sequence_observation["zero_coupling_unit_count"],
                "top_distance_units": main_sequence_observation["top_distance_units"],
            },
        )
    else:
        reason = (
            missing_reason if not stable else
            "backend import graph is incomplete because of parse errors, module collisions, or unresolved dynamic imports"
        )
        for name in (
            "package_cycle_count",
            "cross_core_scc_count",
            "internal_fan_out_max",
            "main_sequence_distance_p90",
        ):
            architecture[name] = _unknown(observed_at, reason)

    evidence["collector"] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "mode": "read_only_static",
        "status": "observed" if stable and observations["status"] == "observed" else "partial",
        "stability": {
            "status": "observed" if stable else "failed",
            "source_unchanged": source_stable,
            "git_status_unchanged": status_stable,
            "source_before": primary.identity(),
            "source_after": source_after.identity(),
            "git_before": git_before,
            "git_after": git_after,
        },
        "execution_boundary": {
            "candidate_code_executed": False,
            "network_requested": False,
            "git_binary": git_before["git_binary"],
            "git_binary_sha256": git_before["git_binary_sha256"],
            "git_environment": "allowlisted locale plus GIT_CONFIG_NOSYSTEM/GIT_CONFIG_GLOBAL/GIT_OPTIONAL_LOCKS",
        },
        "determinism_contract": (
            "same source bytes, git status, root path, contract, collector version, and --observed-at "
            "produce byte-for-byte identical JSON"
        ),
        "observations": observations,
    }
    evidence["notes"] = [
        "Unknown metrics are not replaced with neutral values or estimates.",
        "This receipt does not assert canonical, security, functional, provenance, runtime, provider, cloud, or business-outcome gates.",
        "CC is a documented AST metric; cognitive complexity remains unknown.",
        "Class span, layering direction, and Main Sequence evidence are documented static metrics, not runtime behavior.",
        "A drifting source or Git status invalidates every score metric from that collection.",
    ]
    return evidence


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_output(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="Repository root to scan")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--observed-at", default=None, help="Fixed ISO-8601 timestamp for reproducible output")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        raise CollectionError(f"repository root with .git required: {root}")
    observed_at = normalize_observed_at(args.observed_at)
    contract = _load_contract(Path(args.contract))
    evidence = build_evidence(root, contract, observed_at=observed_at)
    data = _json_bytes(evidence)
    if args.output:
        _write_output(Path(args.output), data)
    stdout_out(data.decode("utf-8"), end="")
    return 2 if args.require_complete and evidence["collector"]["status"] != "observed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
