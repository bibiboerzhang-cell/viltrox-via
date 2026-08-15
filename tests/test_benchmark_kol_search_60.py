from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_kol_search_60.py"
_SPEC = importlib.util.spec_from_file_location("vkpi_benchmark_kol_search_60", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def _candidate(
    candidate_id: int,
    *,
    followers: int = 5000,
    age_days: int = 10,
    country: str = "US",
    inferred: str = "",
    evidence_count: int = 1,
    contact: bool = True,
) -> dict:
    as_of = date(2026, 8, 15)
    latest = datetime.combine(
        as_of.replace(day=max(1, as_of.day - age_days)),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return {
        "kol_pool_id": candidate_id,
        "followers": followers,
        "latest_video_at": latest,
        "country": country,
        "inferred_country": inferred,
        "query_evidence_count": evidence_count,
        "contact_available": contact,
    }


def _query() -> dict:
    return {
        "id": "q1",
        "query": "US lens review",
        "platforms": ["youtube"],
        "market": "US",
        "evidence_terms": ["lens", "review"],
    }


def test_percentiles_use_nearest_rank():
    assert benchmark._percentile([1, 2, 3, 4, 5], 0.50) == 3
    assert benchmark._percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert benchmark._percentile([], 0.95) is None


def test_market_evidence_preserves_exact_inferred_unknown():
    assert benchmark._market_evidence("美国", "") == ("exact", "US")
    assert benchmark._market_evidence("", "gb") == ("inferred", "GB")
    assert benchmark._market_evidence("", "") == ("unknown", "")
    assert benchmark._market_evidence("Atlantis", "XX") == ("unknown", "")


def test_loopback_interface_text_is_accepted_by_preflight_guard():
    assert benchmark.ipaddress.ip_interface("127.0.0.1/32").ip.is_loopback is True


def test_contract_pass_is_separate_from_unlabelled_precision_at_30():
    query = _query()
    rows = [_candidate(index) for index in range(1, 31)]
    ranked = benchmark._rank_candidates(rows, query, as_of=date(2026, 8, 15))
    metrics = benchmark._query_metrics(
        query,
        ranked,
        limit=30,
        min_market_known_ratio=0.5,
        human_labels=None,
    )

    assert metrics["contract"] == {
        "status": "pass",
        "pass": True,
        "minimum_returned": 30,
        "minimum_followers": 3000,
        "maximum_video_age_days": 45,
        "minimum_market_known_ratio": 0.5,
        "shortfall_reasons": [],
    }
    assert metrics["human_relevance"] == {
        "status": "not_evaluated",
        "precision_at_30": None,
        "reason": "human_labels_missing",
    }


def test_contract_reports_count_and_market_shortfalls_without_relaxing_gates():
    query = _query()
    rows = [
        _candidate(index, country="", inferred="")
        for index in range(1, 21)
    ]
    ranked = benchmark._rank_candidates(rows, query, as_of=date(2026, 8, 15))
    metrics = benchmark._query_metrics(
        query,
        ranked,
        limit=30,
        min_market_known_ratio=0.5,
        human_labels=None,
    )

    assert metrics["returned_count"] == 0
    assert metrics["qualified_count"] == 0
    assert metrics["pre_market_candidate_count"] == 20
    assert metrics["market_gate_filtered_count"] == 20
    assert metrics["contract"]["pass"] is False
    assert metrics["contract"]["shortfall_reasons"] == [
        "target_market_candidates_below_30",
        "market_evidence_ratio_below_contract",
    ]


def test_target_market_gate_never_fills_30_with_other_or_unknown_markets():
    query = _query()
    rows = (
        [_candidate(index, country="US") for index in range(1, 14)]
        + [_candidate(index, country="GB") for index in range(14, 24)]
        + [_candidate(index, country="", inferred="") for index in range(24, 41)]
    )
    ranked = benchmark._rank_candidates(rows, query, as_of=date(2026, 8, 15))
    metrics = benchmark._query_metrics(
        query,
        ranked,
        limit=30,
        min_market_known_ratio=0.5,
        human_labels=None,
    )

    assert metrics["pre_market_candidate_count"] == 40
    assert metrics["target_market_candidate_count"] == 13
    assert metrics["returned_count"] == 13
    assert metrics["qualified_count"] == 13
    assert metrics["market_match_count"] == 13
    assert metrics["market_gate_filtered_count"] == 27
    assert metrics["contract"]["pass"] is False
    assert metrics["contract"]["shortfall_reasons"] == ["target_market_candidates_below_30"]


def test_duplicate_rows_are_deduplicated_before_contract_count():
    query = _query()
    rows = [_candidate(1), _candidate(1), _candidate(2)]
    ranked = benchmark._rank_candidates(rows, query, as_of=date(2026, 8, 15))
    assert [item["canonical_id"] for item in ranked] == [1, 2]


def test_human_labels_enable_precision_without_changing_contract():
    query = _query()
    rows = [_candidate(index) for index in range(1, 31)]
    ranked = benchmark._rank_candidates(rows, query, as_of=date(2026, 8, 15))
    metrics = benchmark._query_metrics(
        query,
        ranked,
        limit=30,
        min_market_known_ratio=0.5,
        human_labels={"q1": list(range(1, 16))},
    )
    assert metrics["contract"]["pass"] is True
    assert metrics["human_relevance"]["status"] == "evaluated"
    assert metrics["human_relevance"]["precision_at_30"] == 0.5


def test_golden_query_file_is_fixed_at_five_queries():
    queries = benchmark.load_golden_queries(benchmark.DEFAULT_QUERIES)
    assert len(queries) == 5
    assert len({item["id"] for item in queries}) == 5


def test_output_rejects_symlink(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "report.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="output_path_must_be_regular_file"):
        benchmark._write_private_text(link, "{}")


def test_csv_contains_aggregate_only_and_no_identity_columns():
    report = {
        "runs": [
            {
                "run": 1,
                "queries": [
                    {
                        "query_id": "q1",
                        "returned_count": 30,
                        "qualified_count": 30,
                        "unique_count": 30,
                        "market_evidence": {"exact": 20, "inferred": 5, "unknown": 5},
                        "contact_status": {"available_count": 10},
                        "contract": {"status": "pass", "shortfall_reasons": []},
                        "human_relevance": {"status": "not_evaluated", "precision_at_30": None},
                    }
                ],
            }
        ]
    }
    output = benchmark._csv_text(report)
    header = output.splitlines()[0]
    assert "handle" not in header
    assert "email" not in header
    assert "profile_url" not in header
    assert "q1" in output
