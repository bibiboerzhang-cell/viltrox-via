from __future__ import annotations

import sqlite3

from app.domains.kol import data_completion_priority as priority


def _row(kol_id: int, **overrides):
    return {
        "kol_pool_id": kol_id,
        "platform": "youtube",
        "country": "US",
        "language": "en",
        "followers": 20_000,
        "source_type": "manual",
        "audience_estimated_json": {"method": "ensemble_v1", "sample_size": 40},
        "evidence_count": 5,
        "view_count_known_count": 5,
        "comment_metric_known_count": 5,
        "stored_comment_count": 20,
        "direct_account_comment_count": 0,
        "evidence_bridge_comment_count": 20,
        "final_v1_count": 3,
        **overrides,
    }


def test_unknown_is_actionable_but_observed_zero_video_metric_is_not_missing() -> None:
    report = priority.build_data_completion_priority(
        [
            _row(
                1,
                followers=None,
                evidence_count=1,
                view_count_known_count=1,  # an observed view_count=0 is still known
                comment_metric_known_count=1,
                final_v1_count=1,
            )
        ]
    )
    item = report["priorities"][0]

    assert item["field_status"]["followers"] == "missing"
    assert "followers_unverified" in item["missing_signals"]
    assert "view_count_coverage_insufficient" not in item["missing_signals"]
    assert item["evidence_status"]["view_count_coverage"] == 1.0
    assert report["score_contract"]["not_measures"][:3] == ["accuracy", "precision", "recall"]


def test_zero_followers_is_unverified_sentinel_not_performance_zero() -> None:
    item = priority.build_data_completion_priority([_row(1, followers=0)])["priorities"][0]

    assert item["field_status"]["followers"] == "zero_unverified"
    assert item["recommended_action"] == "refresh_profile_reach_metrics"
    assert item["expected_decision_impact"]["decision"] == "hard_filter_eligibility"


def test_invalid_platform_and_placeholder_codes_remain_actionable() -> None:
    item = priority.build_data_completion_priority(
        [_row(1, platform="mytube", country="[]", language="0")]
    )["priorities"][0]

    assert item["field_status"] == {
        "platform": "invalid",
        "country": "missing",
        "language": "missing",
        "followers": "known",
    }
    assert {"platform_missing", "country_missing", "language_missing"} <= set(item["missing_signals"])


def test_each_required_product_anchor_needs_observed_fact_or_evidence() -> None:
    row = _row(
        1,
        anchor_hits={
            "26mm": {"video_evidence": True},
            "evo": {"factual_profile": False, "video_evidence": False, "final_v1": False},
        },
    )
    item = priority.build_data_completion_priority(
        [row], required_product_anchors={"26mm": ["26mm", "af26mm"], "evo": ["evo"]}
    )["priorities"][0]

    assert item["product_anchor_coverage"]["26mm"]["observed"] is True
    assert item["product_anchor_coverage"]["evo"]["observed"] is False
    assert item["recommended_action"] == "collect_product_specific_video_evidence"
    assert item["expected_decision_impact"]["decision"] == "relevance_gate"
    assert "1/2" in item["reason"]


def test_duplicate_candidates_are_merged_without_summing_evidence() -> None:
    first = _row(1, evidence_count=2, view_count_known_count=1, country="")
    second = _row(1, evidence_count=5, view_count_known_count=4, country="US")
    report = priority.build_data_completion_priority([first, second])
    item = report["priorities"][0]

    assert report["scope"]["input_rows"] == 2
    assert report["scope"]["unique_candidates"] == 1
    assert report["scope"]["duplicate_rows_deduped"] == 1
    assert item["evidence_status"]["video_evidence_count"] == 5
    assert item["evidence_status"]["view_count_known_count"] == 4
    assert item["field_status"]["country"] == "known"


def test_source_bias_reports_coverage_association_but_does_not_change_score() -> None:
    rows = []
    for index in range(10):
        rows.append(_row(index + 1, source_type="source_a", country="US"))
        rows.append(_row(index + 101, source_type="source_b", country=""))
    report = priority.build_data_completion_priority(rows)
    country_bias = report["source_bias"]["fields"]["country"]

    assert country_bias["cramers_v_source_association"] == 1.0
    assert country_bias["coverage_range"] == 1.0
    assert country_bias["severity"] == "high"
    assert report["source_bias"]["priority_score_source_adjustment"] is False
    assert "country" in report["source_bias"]["high_association_fields"]


def test_account_id_only_comments_are_disclosed_as_ambiguous_not_proven() -> None:
    item = priority.build_data_completion_priority(
        [
            _row(
                1,
                comment_metric_known_count=0,
                stored_comment_count=12,
                direct_account_comment_count=12,
                evidence_bridge_comment_count=0,
            )
        ]
    )["priorities"][0]

    assert item["evidence_status"]["comment_evidence_status"] == "account_bridge_unverified"
    assert "comments_bridge_unverified" in item["missing_signals"]
    assert item["evidence_status"]["stored_comment_count"] == 12


def test_comment_metric_does_not_impersonate_verified_comment_text() -> None:
    report = priority.build_data_completion_priority(
        [
            _row(
                1,
                comment_metric_known_count=5,
                stored_comment_count=0,
                direct_account_comment_count=0,
                evidence_bridge_comment_count=0,
            )
        ]
    )

    assert report["summary"]["coverage"]["comments"]["ratio"] == 0.0
    assert report["summary"]["coverage"]["comment_metric_ready"]["ratio"] == 1.0
    assert report["summary"]["coverage"]["comment_text_ready"]["ratio"] == 0.0


def test_sqlite_anchor_fallback_uses_boundaries_and_escapes_wildcards() -> None:
    conn = sqlite3.connect(":memory:")
    clause, params = priority._anchor_match_sql("?", ["evo"], postgres=False)

    assert conn.execute(f"SELECT {clause}", ("revolutionary", *params)).fetchone()[0] == 0
    assert conn.execute(f"SELECT {clause}", ("EVO review", *params)).fetchone()[0] == 1


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if "WITH scoped AS" in sql:
            return _Cursor([_row(1)])
        return _Cursor(
            [
                {
                    "kol_pool_id": 1,
                    "a0_factual": 0,
                    "a0_evidence": 1,
                    "a0_final_v1": 0,
                }
            ]
        )


def test_runtime_loader_uses_bounded_set_queries_and_keeps_required_action_fields(monkeypatch) -> None:
    conn = _Connection()
    monkeypatch.setattr(priority, "is_postgres_runtime", lambda: False)

    report = priority.generate_data_completion_priority(
        kol_pool_ids=[1], required_product_anchors=["26mm"], conn=conn
    )

    assert len(conn.calls) == 2
    assert "COUNT(DISTINCT" in conn.calls[0][0]
    assert "EXISTS" in conn.calls[1][0]
    assert "recommended_product_lines_json" not in conn.calls[1][0]
    assert report["scope"]["query_count"] == 2
    assert report["scope"]["operational_queue_valid"] is True
    assert report["priorities"][0]["product_anchor_coverage"]["26mm"]["observed"] is True
    assert {
        "recommended_action",
        "reason",
        "cost_tier",
        "expected_decision_impact",
    } <= report["priorities"][0].keys()
    assert report["writes_db"] is False
    assert report["provider_calls_made"] is False


def test_uncapped_gap_score_keeps_saturated_candidates_orderable() -> None:
    report = priority.build_data_completion_priority(
        [
            _row(
                1,
                platform="",
                country="",
                language="",
                followers=None,
                evidence_count=0,
                view_count_known_count=0,
                comment_metric_known_count=0,
                stored_comment_count=0,
                direct_account_comment_count=0,
                evidence_bridge_comment_count=0,
                final_v1_count=0,
                audience_estimated_json=None,
            ),
            _row(
                2,
                country="",
                language="",
                followers=None,
                evidence_count=0,
                view_count_known_count=0,
                comment_metric_known_count=0,
                stored_comment_count=0,
                direct_account_comment_count=0,
                evidence_bridge_comment_count=0,
                final_v1_count=0,
                audience_estimated_json=None,
            ),
        ]
    )

    first, second = report["priorities"]
    assert first["priority_score"] == second["priority_score"] == 100.0
    assert first["raw_priority_score"] > second["raw_priority_score"]
    assert first["kol_pool_id"] == 1
