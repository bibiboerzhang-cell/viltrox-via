"""Contract v1.1 import-time cycle counting (package_cycle_count methodology).

Two families of tests:

* ``test_conservative_*`` are characterization tests: they lock the complete
  conservative v1 graph behavior (imports at any AST depth) that the contract
  says must keep feeding cross_core_scc_count, internal_fan_out_max, and the
  main-sequence coupling.  They pass on the pre-change collector unchanged.
* ``test_import_time_*`` assert the refined v1.1 edge rule that applies to
  package_cycle_count and its companion ratchet cyclic_module_count only:
  an import contributes an edge only when it executes at module import time.
"""
from __future__ import annotations

import ast
from pathlib import Path

from scripts import vkpi_engineering_health_collect as collector
from scripts import vkpi_engineering_health_graph as graph_tools


OBSERVED_AT = "2026-08-30T12:00:00Z"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build(sources: dict[str, str]) -> graph_tools.ImportGraphBuild:
    trees = {path: ast.parse(text) for path, text in sources.items()}
    return graph_tools.build_backend_import_graph(trees)


def _three_cycle_fixture(tmp_path: Path) -> None:
    """One module-level cycle, one function-level cycle, one guarded cycle."""
    for package in ("", "domains", "domains/kol"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(tmp_path / "backend/app/domains/kol/a.py", "from app.domains.kol import b\n")
    _write(tmp_path / "backend/app/domains/kol/b.py", "from app.domains.kol import a\n")
    _write(
        tmp_path / "backend/app/domains/kol/c.py",
        "def load():\n    from app.domains.kol import d\n    return d\n",
    )
    _write(tmp_path / "backend/app/domains/kol/d.py", "from app.domains.kol import c\n")
    _write(
        tmp_path / "backend/app/domains/kol/e.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.domains.kol import f\n",
    )
    _write(tmp_path / "backend/app/domains/kol/f.py", "from app.domains.kol import e\n")


# ---------------------------------------------------------------------------
# characterization: complete conservative v1 graph is unchanged
# ---------------------------------------------------------------------------


def test_conservative_graph_counts_lazy_and_guarded_imports_at_any_depth(tmp_path: Path) -> None:
    _three_cycle_fixture(tmp_path)

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "observed"
    assert graph["cycle_scc_count"] == 3
    assert graph["cyclic_module_count"] == 6
    members = {tuple(row["members"]) for row in graph["cyclic_sccs"]}
    assert members == {
        ("app.domains.kol.a", "app.domains.kol.b"),
        ("app.domains.kol.c", "app.domains.kol.d"),
        ("app.domains.kol.e", "app.domains.kol.f"),
    }


def test_conservative_cross_core_gate_still_sees_a_lazy_cross_domain_cycle(tmp_path: Path) -> None:
    for package in ("", "domains", "domains/kol", "domains/projects"):
        _write(tmp_path / "backend/app" / package / "__init__.py", "")
    _write(
        tmp_path / "backend/app/domains/kol/x.py",
        "def lazy():\n    from app.domains.projects import y\n    return y\n",
    )
    _write(tmp_path / "backend/app/domains/projects/y.py", "from app.domains.kol import x\n")

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["cross_core_scc_count"] == 1
    assert graph["cyclic_sccs"][0]["architecture_owners"] == ["domain:kol", "domain:projects"]


def test_conservative_function_level_unresolved_dynamic_import_stays_partial(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(
        tmp_path / "backend/app/r.py",
        "import importlib\n"
        "def load(name):\n    return importlib.import_module(name)\n",
    )

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "partial"
    assert graph["unresolved_dynamic_import_count"] == 1
    assert graph["unresolved_dynamic_imports"][0]["reason"] == "non_literal_or_relative_argument"


# ---------------------------------------------------------------------------
# v1.1 lexical edge rule on the import-time subgraph
# ---------------------------------------------------------------------------


def test_import_time_edges_follow_the_five_lexical_cases() -> None:
    build = _build(
        {
            "backend/app/__init__.py": "",
            "backend/app/module_body.py": "from app import sink\n",
            "backend/app/function_body.py": (
                "def load():\n    from app import sink\n    return sink\n"
            ),
            "backend/app/async_function_body.py": (
                "async def load():\n    from app import sink\n    return sink\n"
            ),
            "backend/app/guarded.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from app import sink\n"
            ),
            "backend/app/guarded_orelse.py": (
                "import typing\n"
                "if typing.TYPE_CHECKING:\n"
                "    pass\n"
                "else:\n"
                "    from app import sink\n"
            ),
            "backend/app/class_body.py": "class Facade:\n    from app import sink\n",
            "backend/app/sink.py": "",
        }
    )

    conservative = {module for module, targets in build.graph.items() if "app.sink" in targets}
    import_time = {module for module, targets in build.import_time_graph.items() if "app.sink" in targets}

    assert conservative == {
        "app.module_body",
        "app.function_body",
        "app.async_function_body",
        "app.guarded",
        "app.guarded_orelse",
        "app.class_body",
    }
    assert import_time == {"app.module_body", "app.guarded_orelse", "app.class_body"}


def test_import_time_parent_package_edges_count_only_for_import_time_statements() -> None:
    build = _build(
        {
            "backend/app/__init__.py": "",
            "backend/app/pkg/__init__.py": "",
            "backend/app/pkg/leaf.py": "",
            "backend/app/eager.py": "import app.pkg.leaf\n",
            "backend/app/lazy.py": "def load():\n    import app.pkg.leaf\n",
        }
    )

    assert {"app", "app.pkg", "app.pkg.leaf"} <= build.graph["app.eager"]
    assert {"app", "app.pkg", "app.pkg.leaf"} <= build.import_time_graph["app.eager"]
    assert {"app", "app.pkg", "app.pkg.leaf"} <= build.graph["app.lazy"]
    assert build.import_time_graph["app.lazy"] == set()


def test_import_time_dynamic_imports_follow_the_same_lexical_rule() -> None:
    build = _build(
        {
            "backend/app/__init__.py": "",
            "backend/app/sink.py": "",
            "backend/app/eager_dynamic.py": (
                "import importlib\nimportlib.import_module('app.sink')\n"
            ),
            "backend/app/lazy_dynamic.py": (
                "import importlib\n"
                "def load():\n    return importlib.import_module('app.sink')\n"
            ),
        }
    )

    assert build.unresolved_dynamic_imports == []
    assert "app.sink" in build.graph["app.eager_dynamic"]
    assert "app.sink" in build.import_time_graph["app.eager_dynamic"]
    assert "app.sink" in build.graph["app.lazy_dynamic"]
    assert "app.sink" not in build.import_time_graph["app.lazy_dynamic"]


def test_import_time_subgraph_feeds_package_cycle_count_and_its_ratchet(tmp_path: Path) -> None:
    _three_cycle_fixture(tmp_path)

    observations = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)
    graph = observations["backend_import_graph"]
    subgraph = graph["import_time_subgraph"]

    assert graph["cycle_scc_count"] == 3
    assert graph["cyclic_module_count"] == 6
    assert subgraph["cycle_scc_count"] == 1
    assert subgraph["cyclic_module_count"] == 2
    assert subgraph["cyclic_sccs"] == [
        {"size": 2, "members": ["app.domains.kol.a", "app.domains.kol.b"]}
    ]

    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    contract_path = Path(__file__).resolve().parents[1] / "docs/vkpi/engineering-health-score-contract-v1.json"
    import json

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    evidence = collector.build_evidence(tmp_path, contract, observed_at=OBSERVED_AT)
    metric = evidence["metrics"]["architecture"]["package_cycle_count"]

    assert metric["status"] == "observed"
    assert metric["value"] == 1
    assert metric["details"]["cyclic_module_count"] == 2
    assert evidence["collector"]["algorithm_version"].endswith("-importtime-cycles1")
