from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
from pathlib import Path

from scripts import vkpi_engineering_health_collect as collector


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(encoding="utf-8"))
OBSERVED_AT = "2026-08-29T12:00:00Z"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)


def test_ast_cc_distribution_and_nested_scope_are_exact(tmp_path: Path) -> None:
    _write(
        tmp_path / "backend/app/example.py",
        """def outer(a, b, rows):
    if a and b:
        return [item for item in rows if item]
    def nested(value):
        return 1 if value else 0
    return []
""",
    )

    files = collector.inventory_sources(tmp_path)
    trees, failures = collector.parse_python_sources(files)
    rows = collector.collect_complexity(trees)

    assert failures == []
    assert [(item.qualified_name, item.cc) for item in rows] == [("outer", 5), ("outer.nested", 2)]


def test_lambda_receives_its_own_complexity_row(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/example.py", "choose = lambda a, b: 1 if a and b else 0\n")

    trees, failures = collector.parse_python_sources(collector.inventory_sources(tmp_path))
    rows = collector.collect_complexity(trees)

    assert failures == []
    assert [(item.qualified_name, item.line, item.cc) for item in rows] == [("<lambda@1:9>", 1, 3)]


def test_class_spans_include_nested_sync_and_async_scopes_exactly(tmp_path: Path) -> None:
    _write(
        tmp_path / "backend/app/example.py",
        """@marker
class Outer:
    class Nested:
        value = 1
    async def build(self):
        class AsyncLocal:
            pass
        return AsyncLocal
async def factory():
    class FromAsync(Protocol):
        pass
    return FromAsync
""",
    )

    trees, failures = collector.parse_python_sources(collector.inventory_sources(tmp_path))
    rows = collector.architecture_tools.collect_class_spans(trees)

    assert failures == []
    assert [
        (row.qualified_name, row.line, row.end_line, row.loc, row.is_abstract)
        for row in rows
    ] == [
        ("Outer", 2, 8, 7, False),
        ("Outer.Nested", 3, 4, 2, False),
        ("Outer.build.AsyncLocal", 6, 7, 2, False),
        ("factory.FromAsync", 10, 11, 2, True),
    ]


def test_reverse_dependency_count_matches_formal_layering_rules_and_deduplicates(tmp_path: Path) -> None:
    _write(
        tmp_path / "backend/app/domains/alpha.py",
        "import app.api.routers.foo\n"
        "import app.api.routers.foo\n"
        "from app.workers import lane\n"
        "from . import local\n",
    )
    _write(
        tmp_path / "backend/app/services/beta.py",
        "from app.api.routers import foo\nimport app.workers.allowed_here\n",
    )
    _write(tmp_path / "backend/app/api/router.py", "import app.workers.not_a_scanned_layer\n")

    trees, failures = collector.parse_python_sources(collector.inventory_sources(tmp_path))
    violations = collector.architecture_tools.collect_reverse_dependencies(trees)

    assert failures == []
    assert violations == [
        {
            "path": "backend/app/domains/alpha.py",
            "imported_module": "app.api.routers.foo",
            "source_layer": "domains",
        },
        {
            "path": "backend/app/domains/alpha.py",
            "imported_module": "app.workers",
            "source_layer": "domains",
        },
        {
            "path": "backend/app/services/beta.py",
            "imported_module": "app.api.routers",
            "source_layer": "services",
        },
    ]
    assert CONTRACT["static_evidence_methodology"]["reverse_dependency_count"]["rules"] == {
        key: list(value) for key, value in collector.architecture_tools.LAYER_RULES.items()
    }


def test_main_sequence_distance_uses_second_level_units_and_nearest_rank_p90() -> None:
    trees = {
        "backend/app/domains/alpha/a.py": ast.parse(
            "class Half(ABC):\n    pass\nclass Concrete:\n    pass\n"
        ),
        "backend/app/domains/beta/b.py": ast.parse("value = 1\n"),
        "backend/app/domains/gamma/c.py": ast.parse("class Abstract(Protocol):\n    pass\n"),
        "backend/app/domains/delta/d.py": ast.parse("class Concrete:\n    pass\n"),
    }
    module_paths = {
        "app.domains.alpha.a": "backend/app/domains/alpha/a.py",
        "app.domains.beta.b": "backend/app/domains/beta/b.py",
        "app.domains.gamma.c": "backend/app/domains/gamma/c.py",
        "app.domains.delta.d": "backend/app/domains/delta/d.py",
    }
    graph = {
        "app.domains.alpha.a": {"app.domains.beta.b"},
        "app.domains.beta.b": {"app.domains.gamma.c"},
        "app.domains.gamma.c": set(),
        "app.domains.delta.d": set(),
    }

    result = collector.architecture_tools.collect_main_sequence(trees, graph, module_paths)

    assert result["unit_count"] == 4
    assert result["p90"] == 1.0
    assert result["zero_coupling_unit_count"] == 1
    assert result["units"] == [
        {
            "unit": "app.domains.delta",
            "ca": 0,
            "ce": 0,
            "class_count": 1,
            "abstract_class_count": 0,
            "abstractness": 0.0,
            "instability": 0.0,
            "distance": 1.0,
            "zero_coupling": True,
        },
        {
            "unit": "app.domains.alpha",
            "ca": 0,
            "ce": 1,
            "class_count": 2,
            "abstract_class_count": 1,
            "abstractness": 0.5,
            "instability": 1.0,
            "distance": 0.5,
            "zero_coupling": False,
        },
        {
            "unit": "app.domains.beta",
            "ca": 1,
            "ce": 1,
            "class_count": 0,
            "abstract_class_count": 0,
            "abstractness": 0.0,
            "instability": 0.5,
            "distance": 0.5,
            "zero_coupling": False,
        },
        {
            "unit": "app.domains.gamma",
            "ca": 1,
            "ce": 0,
            "class_count": 1,
            "abstract_class_count": 1,
            "abstractness": 1.0,
            "instability": 0.0,
            "distance": 0.0,
            "zero_coupling": False,
        },
    ]


def test_line_scope_excludes_tests_generated_and_reports_frontend_script_debt(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/kept.py", "x = 1\n")
    _write(tmp_path / "backend/app/generated/skip.py", "\n" * 900)
    _write(tmp_path / "backend/app/test_skip.py", "\n" * 900)
    _write(tmp_path / "frontend/src/Page.tsx", "x\n" * 801)
    _write(tmp_path / "frontend/src/theme.css", "x\n" * 802)
    _write(tmp_path / "scripts/job.py", "x = 1\n" * 803)

    observations = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)
    line_guard = observations["line_guard"]

    assert line_guard["violation_count"] == 3
    assert line_guard["frontend_violation_count"] == 1
    assert line_guard["style_violation_count"] == 1
    assert line_guard["script_violation_count"] == 1
    assert {row["path"] for row in line_guard["violations"]} == {
        "frontend/src/Page.tsx",
        "frontend/src/theme.css",
        "scripts/job.py",
    }


def test_tarjan_reports_cross_domain_cycle_with_replayable_witness(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/domains/__init__.py", "")
    _write(tmp_path / "backend/app/domains/kol/__init__.py", "")
    _write(tmp_path / "backend/app/domains/projects/__init__.py", "")
    _write(
        tmp_path / "backend/app/domains/kol/a.py",
        "from app.domains.projects import b\n",
    )
    _write(
        tmp_path / "backend/app/domains/projects/b.py",
        "from app.domains.kol import a\n",
    )

    observations = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)
    graph = observations["backend_import_graph"]

    assert graph["status"] == "observed"
    assert graph["cycle_scc_count"] == 1
    assert graph["cross_core_scc_count"] == 1
    assert graph["cyclic_sccs"][0]["architecture_owners"] == ["domain:kol", "domain:projects"]
    assert graph["cyclic_sccs"][0]["cycle_witness"] == [
        "app.domains.kol.a",
        "app.domains.projects.b",
        "app.domains.kol.a",
    ]
    edge = graph["cyclic_sccs"][0]["cycle_witness_edges"][0]
    assert {key: edge[key] for key in ("from_module", "from_path", "to_module", "to_path")} == {
        "from_module": "app.domains.kol.a",
        "from_path": "backend/app/domains/kol/a.py",
        "to_module": "app.domains.projects.b",
        "to_path": "backend/app/domains/projects/b.py",
    }
    assert edge["import_evidence"] == [{
        "from_path": "backend/app/domains/kol/a.py",
        "to_path": "backend/app/domains/projects/b.py",
        "line": 1,
        "kind": "ImportFrom",
    }]


def test_constant_dynamic_import_is_an_edge_and_nonliteral_is_explicitly_unresolved(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/kol", "domains/projects"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(
        tmp_path / "backend/app/domains/kol/a.py",
        "import importlib\n"
        "importlib.import_module('app.domains.projects.b')\n"
        "def load(name):\n    return importlib.import_module(name)\n",
    )
    _write(tmp_path / "backend/app/domains/projects/b.py", "from app.domains.kol import a\n")

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "partial"
    assert graph["resolved_constant_dynamic_import_count"] == 1
    assert graph["cycle_scc_count"] == 1
    assert graph["cross_core_scc_count"] == 1
    assert graph["unresolved_dynamic_import_count"] == 1
    assert graph["unresolved_dynamic_imports"] == [{
        "path": "backend/app/domains/kol/a.py",
        "line": 4,
        "callee": "importlib.import_module",
        "reason": "non_literal_or_relative_argument",
    }]
    dynamic_edge = graph["cyclic_sccs"][0]["cycle_witness_edges"][0]["import_evidence"]
    assert dynamic_edge == [{
        "from_path": "backend/app/domains/kol/a.py",
        "to_path": "backend/app/domains/projects/b.py",
        "line": 2,
        "kind": "importlib.import_module",
    }]


def test_finite_lazy_facade_targets_are_proven_from_guarded_all(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/catalog"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(
        tmp_path / "backend/app/domains/catalog/__init__.py",
        "from importlib import import_module\n"
        "__all__ = ['alpha', 'beta']\n"
        "def __getattr__(name):\n"
        "    if name not in __all__:\n"
        "        raise AttributeError(name)\n"
        "    return import_module(f'{__name__}.{name}')\n",
    )
    _write(tmp_path / "backend/app/domains/catalog/alpha.py", "value = 1\n")
    _write(tmp_path / "backend/app/domains/catalog/beta.py", "value = 2\n")

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "observed"
    assert graph["resolved_finite_dynamic_import_count"] == 1
    assert graph["unresolved_dynamic_import_count"] == 0
    row = graph["resolved_dynamic_imports"][0]
    assert row["resolution_kind"] == "finite_ast_constant_propagation"
    assert row["targets"] == ["app.domains.catalog.alpha", "app.domains.catalog.beta"]


def test_imported_registry_and_finite_direct_callsites_are_edges(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/catalog", "services"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(
        tmp_path / "backend/app/domains/catalog/__init__.py",
        "MODULES = ['app.domains.catalog.alpha', 'app.domains.catalog.beta']\n",
    )
    _write(tmp_path / "backend/app/domains/catalog/alpha.py", "value = 1\n")
    _write(tmp_path / "backend/app/domains/catalog/beta.py", "value = 2\n")
    _write(
        tmp_path / "backend/app/services/loader.py",
        "import importlib\n"
        "def gated(module):\n"
        "    def run():\n"
        "        return importlib.import_module(module)\n"
        "    return run\n",
    )
    _write(
        tmp_path / "backend/app/services/registry.py",
        "from app.domains.catalog import MODULES\n"
        "from app.services.loader import gated as load\n"
        "for module in MODULES:\n"
        "    job = load(module)\n",
    )

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "observed"
    assert graph["resolved_finite_dynamic_import_count"] == 1
    assert graph["unresolved_dynamic_import_count"] == 0
    row = graph["resolved_dynamic_imports"][0]
    assert row["resolution_kind"] == "finite_callsite_enumeration"
    assert row["targets"] == ["app.domains.catalog.alpha", "app.domains.catalog.beta"]


def test_missing_or_fabricated_finite_target_fails_closed_without_fake_edge(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/catalog"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(tmp_path / "backend/app/domains/catalog/real.py", "value = 1\n")
    _write(
        tmp_path / "backend/app/domains/catalog/loader.py",
        "import importlib\n"
        "MODULES = ('app.domains.catalog.real', 'app.domains.catalog.fabricated')\n"
        "def load_all():\n"
        "    for module in MODULES:\n"
        "        importlib.import_module(module)\n",
    )

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "partial"
    assert graph["resolved_finite_dynamic_import_count"] == 0
    assert graph["unresolved_dynamic_imports"] == [{
        "path": "backend/app/domains/catalog/loader.py",
        "line": 5,
        "callee": "importlib.import_module",
        "reason": "finite_internal_module_not_found",
        "missing_targets": ["app.domains.catalog.fabricated"],
    }]
    assert "app.domains.catalog.real" in graph["module_paths"]
    assert "app.domains.catalog.fabricated" not in graph["module_paths"]


def test_one_arbitrary_callsite_invalidates_otherwise_finite_parameter_domain(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/catalog", "services"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(tmp_path / "backend/app/domains/catalog/real.py", "value = 1\n")
    _write(
        tmp_path / "backend/app/services/loader.py",
        "import importlib\n"
        "def gated(module):\n"
        "    return importlib.import_module(module)\n",
    )
    _write(
        tmp_path / "backend/app/services/registry.py",
        "from app.services.loader import gated\n"
        "gated('app.domains.catalog.real')\n"
        "def operator_path(module):\n"
        "    return gated(module)\n",
    )

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "partial"
    assert graph["resolved_finite_dynamic_import_count"] == 0
    assert graph["unresolved_dynamic_imports"] == [{
        "path": "backend/app/services/loader.py",
        "line": 3,
        "callee": "importlib.import_module",
        "reason": "non_literal_or_relative_argument",
    }]


def test_callback_reference_invalidates_direct_call_enumeration(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/catalog", "services"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(tmp_path / "backend/app/domains/catalog/real.py", "value = 1\n")
    _write(
        tmp_path / "backend/app/services/loader.py",
        "import importlib\n"
        "def gated(module):\n"
        "    return importlib.import_module(module)\n",
    )
    _write(
        tmp_path / "backend/app/services/registry.py",
        "from app.services.loader import gated\n"
        "gated('app.domains.catalog.real')\n"
        "callback = gated\n",
    )

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "partial"
    assert graph["resolved_finite_dynamic_import_count"] == 0
    assert graph["unresolved_dynamic_import_count"] == 1


def test_import_parent_initializers_and_legacy_boundaries_cannot_hide_cross_core_cycles(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/kol", "domains/projects", "services"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(tmp_path / "backend/app/domains/kol/a.py", "import app.domains.projects.b\n")
    _write(tmp_path / "backend/app/domains/projects/__init__.py", "from app.domains.kol import a\n")
    _write(tmp_path / "backend/app/domains/projects/b.py", "value = 1\n")
    _write(tmp_path / "backend/app/domains/kol/service_cycle.py", "from app.services import bridge\n")
    _write(tmp_path / "backend/app/services/bridge.py", "from app.domains.kol import service_cycle\n")

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]
    cross_core = [row for row in graph["cyclic_sccs"] if row["cross_core"]]

    assert graph["cycle_scc_count"] == 2
    assert graph["cross_core_scc_count"] == 2
    assert {tuple(row["architecture_owners"]) for row in cross_core} == {
        ("domain:kol", "domain:projects"),
        ("domain:kol", "legacy:services"),
    }


def test_parse_failure_keeps_cc_and_graph_metrics_unknown(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/broken.py", "def broken(:\n")
    _git_repo(tmp_path)

    evidence = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)

    assert evidence["collector"]["status"] == "partial"
    assert evidence["metrics"]["code"]["max_cc"]["status"] == "unknown"
    assert evidence["metrics"]["architecture"]["package_cycle_count"]["status"] == "unknown"
    assert evidence["metrics"]["architecture"]["class_loc_max"]["status"] == "unknown"
    assert evidence["metrics"]["architecture"]["reverse_dependency_count"]["status"] == "unknown"
    assert evidence["metrics"]["architecture"]["main_sequence_distance_p90"]["status"] == "unknown"
    assert evidence["metrics"]["code"]["cognitive_le_15_ratio"]["status"] == "unknown"
    assert evidence["release_gates"]["functional_gate_pass"]["status"] == "unknown"


def test_fixed_timestamp_and_source_snapshot_are_byte_reproducible(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/domain.py", "def choose(flag):\n    return 1 if flag else 0\n")
    _write(tmp_path / "frontend/src/App.tsx", "export const App = () => null;\n")
    _write(tmp_path / "scripts/job.py", "def run():\n    return True\n")
    _git_repo(tmp_path)

    first = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)
    second = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)

    assert collector._json_bytes(first) == collector._json_bytes(second)
    assert first["candidate"]["source_content_sha256"] == second["candidate"]["source_content_sha256"]
    assert first["contract_sha256"] == collector.health_score.contract_sha256(CONTRACT)
    assert first["metrics"]["code"]["max_cc"]["status"] == "observed"
    assert first["metrics"]["code"]["cognitive_le_15_ratio"]["status"] == "unknown"
    assert first["metrics"]["architecture"]["class_loc_max"]["value"] == 0
    assert first["metrics"]["architecture"]["reverse_dependency_count"]["value"] == 0
    assert first["metrics"]["architecture"]["main_sequence_distance_p90"]["status"] == "observed"


def test_ast_uses_captured_bytes_even_if_path_changes_after_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "backend/app/domain.py"
    _write(source, "def original():\n    return True\n")
    captured = collector._take_source_snapshot(tmp_path)
    _write(source, "def broken(:\n")

    trees, failures = collector.parse_python_sources(captured.files)

    assert failures == []
    assert "backend/app/domain.py" in trees
    assert captured.files[0].content.startswith(b"def original")


def _fake_git_state(status_sha256: str) -> dict[str, object]:
    return {
        "branch": "fixture",
        "head": "a" * 40,
        "clean_worktree": True,
        "tracked_change_count": 0,
        "untracked_change_count": 0,
        "status_sha256": status_sha256,
        "git_binary": "/usr/bin/git",
        "git_binary_sha256": "b" * 64,
    }


def test_source_drift_fails_closed_and_keeps_every_score_metric_unknown(tmp_path: Path) -> None:
    source = tmp_path / "backend/app/domain.py"
    _write(source, "def original():\n    return True\n")
    before = collector._take_source_snapshot(tmp_path)
    _write(source, "def replacement():\n    return False\n")
    after = collector._take_source_snapshot(tmp_path)
    snapshots = iter((before, after))
    state = _fake_git_state("c" * 64)

    evidence = collector.build_evidence(
        tmp_path,
        CONTRACT,
        observed_at=OBSERVED_AT,
        snapshot_reader=lambda _root: next(snapshots),
        git_probe=lambda _root: dict(state),
    )

    assert evidence["collector"]["status"] == "partial"
    assert evidence["collector"]["stability"]["source_unchanged"] is False
    assert evidence["candidate"]["source_and_status_stable"] is False
    assert all(
        metric["status"] == "unknown" and metric["value"] is None
        for dimension in evidence["metrics"].values()
        for metric in dimension.values()
    )


def test_git_status_drift_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/domain.py", "def stable():\n    return True\n")
    captured = collector._take_source_snapshot(tmp_path)
    states = iter((_fake_git_state("c" * 64), _fake_git_state("d" * 64)))

    evidence = collector.build_evidence(
        tmp_path,
        CONTRACT,
        observed_at=OBSERVED_AT,
        snapshot_reader=lambda _root: captured,
        git_probe=lambda _root: next(states),
    )

    assert evidence["collector"]["status"] == "partial"
    assert evidence["collector"]["stability"]["git_status_unchanged"] is False
    assert evidence["metrics"]["code"]["max_cc"]["status"] == "unknown"


def test_symlink_source_is_not_followed_and_makes_snapshot_incomplete(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    _write(outside, "def outside():\n    return True\n")
    link = tmp_path / "backend/app/link.py"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    captured = collector._take_source_snapshot(tmp_path)

    assert captured.complete is False
    assert captured.symlink_sources == ("backend/app/link.py",)
    assert all(item.relative_path != "backend/app/link.py" for item in captured.files)


def test_hostile_environment_cannot_select_git_or_execute_candidate_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    side_effect = tmp_path / "candidate-side-effect"
    _write(
        tmp_path / "backend/app/danger.py",
        "from pathlib import Path\nimport socket\n"
        f"Path({str(side_effect)!r}).write_text('executed')\n"
        "socket.create_connection(('example.invalid', 443))\n"
        "def harmless():\n    return True\n",
    )
    _git_repo(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_marker = tmp_path / "fake-git-ran"
    _write(fake_bin / "git", f"#!/bin/sh\nprintf ran > {str(fake_marker)!r}\nexit 99\n")
    os.chmod(fake_bin / "git", 0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "attacker.config"))
    network_attempts: list[object] = []
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: network_attempts.append((args, kwargs)),
    )
    real_run = subprocess.run
    git_calls: list[list[str]] = []

    def audited_run(*args, **kwargs):
        command = list(args[0] if args else kwargs["args"])
        git_calls.append(command)
        assert Path(command[0]).is_absolute()
        assert set(kwargs["env"]) == {
            "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
            "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT",
        }
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
        return real_run(*args, **kwargs)

    monkeypatch.setattr(collector.snapshot.subprocess, "run", audited_run)
    evidence = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)

    assert git_calls
    assert evidence["collector"]["execution_boundary"]["git_binary"] == "/usr/bin/git"
    assert evidence["collector"]["execution_boundary"]["candidate_code_executed"] is False
    assert evidence["collector"]["execution_boundary"]["network_requested"] is False
    assert not side_effect.exists()
    assert not fake_marker.exists()
    assert network_attempts == []


def test_current_repository_collector_remains_read_only_and_unrated(tmp_path: Path) -> None:
    output = tmp_path / "evidence.json"
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/vkpi_engineering_health_collect.py"),
        "--root",
        str(ROOT),
        "--observed-at",
        OBSERVED_AT,
        "--output",
        str(output),
        "--require-complete",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    score_report = collector.health_score.score_evidence(CONTRACT, payload)
    assert payload["collector"]["mode"] == "read_only_static"
    assert payload["collector"]["status"] == "observed"
    assert payload["metrics"]["code"]["max_cc"]["status"] == "observed"
    assert payload["collector"]["observations"]["backend_import_graph"]["status"] == "observed"
    assert payload["collector"]["observations"]["backend_import_graph"]["unresolved_dynamic_import_count"] == 0
    assert payload["metrics"]["architecture"]["cross_core_scc_count"]["status"] == "observed"
    # This assertion tracks the real repository. Positive detection remains
    # covered independently by the synthetic one-cycle and two-cycle fixtures.
    assert payload["metrics"]["architecture"]["cross_core_scc_count"]["value"] == 0
    assert score_report["status"] == "provisional"
    assert score_report["formal_score"] is None
    assert score_report["release_eligible"] is False
