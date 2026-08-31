from __future__ import annotations

import ast
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_kol_smart_local_runtime.py"
SPEC = importlib.util.spec_from_file_location("benchmark_kol_smart_local_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_cli_preflights_arguments_before_loading_application_graph() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top_level_app_imports = [
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and str(node.module or "").startswith("app.")
    ]
    assert top_level_app_imports == []

    main_source = inspect.getsource(benchmark.main)
    assert main_source.index("parser.parse_args()") < main_source.index(
        "validate_output_path(args.output)"
    ) < main_source.index("run_benchmark(")

    benchmark_source = inspect.getsource(benchmark.run_benchmark)
    assert benchmark_source.index("load_golden(golden_path)") < benchmark_source.index(
        "validate_rounds(rounds)"
    ) < benchmark_source.index("_load_runtime_dependencies()")


def test_runtime_golden_is_fixed_at_five_queries() -> None:
    golden = benchmark.load_golden(benchmark.DEFAULT_GOLDEN)
    assert len(golden) == 5
    assert len({item["id"] for item in golden}) == 5


@pytest.mark.parametrize("rounds", [3, 4, 5])
def test_rounds_contract_accepts_three_to_five(rounds: int) -> None:
    assert benchmark.validate_rounds(rounds) == rounds


@pytest.mark.parametrize("rounds", [0, 2, 6])
def test_rounds_contract_rejects_out_of_range(rounds: int) -> None:
    with pytest.raises(ValueError, match="rounds_must_be_between_3_and_5"):
        benchmark.validate_rounds(rounds)


def test_read_only_sql_tripwire_rejects_application_write() -> None:
    class _Delegate:
        def execute(self, _statement, _params=None):
            return object()

    audited = benchmark.ReadOnlyAuditConnection(_Delegate())
    audited.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="application_write_sql_forbidden"):
        audited.execute("UPDATE vkpi_kol_pool SET followers=1")
    assert audited.read_statement_count == 1
    assert audited.write_statement_count == 1


def test_loopback_guard_accepts_postgres_interface_notation() -> None:
    assert benchmark.ipaddress.ip_interface("127.0.0.1/32").ip.is_loopback is True


def test_query_summary_contains_only_aggregate_contract_metrics() -> None:
    runs = [
        {
            "returned_count": 30,
            "qualified_count": 34,
            "shortfall": 0,
            "retrieved_candidate_count": 38,
            "prequalification_no_match_evidence": 0,
            "route_ms": value,
            "engine_total_ms": value / 2,
            "hard_gate_violations": {},
            "rejected_by_reason": {"followers_below_3000": 1},
        }
        for value in (10.0, 11.0, 12.0)
    ]
    summary = benchmark._summarize_query("golden_one", runs)
    benchmark._assert_report_private(summary)
    assert summary["returned_count"] == {"n": 3, "min": 30, "max": 30, "stable": True}
    assert summary["hard_gates_passed"] is True
    assert summary["target_met"] is True
    assert summary["rejected_by_reason_total"] == {"followers_below_3000": 3}


def test_hermetic_fixture_requires_every_query_to_return_30() -> None:
    valid = [{
        "query_id": f"q{index}",
        "target_met": True,
        "returned_count": {"min": 30, "max": 30},
        "shortfall": {"min": 0, "max": 0},
    } for index in range(5)]
    benchmark.assert_hermetic_fixture_target(valid)
    invalid = [*valid[:-1], {
        "query_id": "q4",
        "target_met": False,
        "returned_count": {"min": 29, "max": 29},
        "shortfall": {"min": 1, "max": 1},
    }]
    with pytest.raises(RuntimeError, match="hermetic_fixture_query_failed_target:q4"):
        benchmark.assert_hermetic_fixture_target(invalid)


def test_hermetic_runtime_fixture_returns_30_for_every_golden_query() -> None:
    report = benchmark.run_benchmark(
        admin_dsn="postgresql://127.0.0.1/postgres",
        golden_path=benchmark.DEFAULT_GOLDEN,
        rounds=3,
    )

    assert report["aggregate"]["query_count"] == 5
    assert report["aggregate"]["target_met_query_count"] == 5
    assert report["aggregate"]["zero_shortfall_query_count"] == 5
    assert all(query["returned_count"]["min"] == 30 for query in report["queries"])
    assert all(query["returned_count"]["max"] == 30 for query in report["queries"])
    assert all(query["shortfall"]["max"] == 0 for query in report["queries"])
    assert report["read_only_receipt"]["application_write_statement_count"] == 0
    assert report["read_only_receipt"]["disposable_database_dropped"] is True
    assert report["claim_status"] == "runtime_algorithm_legacy_compatibility_only"
    assert report["scope"]["legacy_smart_local_compatibility_executed"] is True
    assert report["scope"]["prospective_targeted_query_cells_tested"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"items": []},
        {"handle": "creator"},
        {"nested": {"email": "x@example.test"}},
        {"gate_evidence": []},
    ],
)
def test_report_privacy_guard_rejects_identity_or_contact_keys(payload: dict) -> None:
    with pytest.raises(ValueError, match="identity_or_contact_key_forbidden"):
        benchmark._assert_report_private(payload)


def test_report_writer_uses_private_permissions_and_preserves_caveats(tmp_path: Path) -> None:
    output = tmp_path / "runtime.json"
    report = {
        "schema_version": benchmark.SCHEMA_VERSION,
        "aggregate": {"query_count": 5, "total_max_shortfall": 0},
        "required_caveats": [
            "Production HTTP/UI, session attach and deep analysis were not tested."
        ],
    }
    benchmark.write_report(output, report)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded == report
    assert output.stat().st_mode & 0o777 == 0o600


def test_output_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    output = tmp_path / "runtime.json"
    output.symlink_to(target)
    with pytest.raises(ValueError, match="output_symlink_forbidden"):
        benchmark.write_report(output, {"aggregate": {"query_count": 5}})


def test_cli_rejects_output_symlink_before_benchmark(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    output = tmp_path / "runtime.json"
    output.symlink_to(target)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=SCRIPT.parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    assert "output_symlink_forbidden" in completed.stderr
    assert target.read_text(encoding="utf-8") == "{}"
