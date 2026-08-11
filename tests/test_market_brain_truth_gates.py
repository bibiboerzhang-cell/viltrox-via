from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.domains.intelligence.marketing_brain_scorecard import _dimension
from app.domains.learning import forecast_feedback
from app.domains.market_brain import (
    data_readiness,
    prediction_ledger,
    signal_ledger,
    summary,
    weekly_answers,
)
from app.domains.recommendations import outcomes


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows: list[dict] | None = None, *, rowcount: int = 0):
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_data_readiness_requires_sample_and_freshness():
    ready = data_readiness.evaluate_requirements(
        [
            data_readiness.DataRequirement(
                key="feedback",
                observed=5,
                minimum=5,
                freshest_at=NOW - timedelta(days=3),
                max_age_days=30,
            )
        ],
        now=NOW,
    ).to_dict()
    assert ready["claimable"] is True
    assert ready["claim_level"] == "validated"

    insufficient = data_readiness.evaluate_requirements(
        [
            data_readiness.DataRequirement(
                key="feedback",
                observed=4,
                minimum=5,
                freshest_at=NOW,
                max_age_days=30,
            )
        ],
        now=NOW,
    ).to_dict()
    assert insufficient["claimable"] is False
    assert insufficient["checks"]["feedback"]["status"] == "insufficient"

    stale = data_readiness.evaluate_requirements(
        [
            data_readiness.DataRequirement(
                key="feedback",
                observed=5,
                minimum=5,
                freshest_at=NOW - timedelta(days=31),
                max_age_days=30,
            )
        ],
        now=NOW,
    ).to_dict()
    assert stale["claimable"] is False
    assert stale["status"] == "stale"


class _LearningReadinessConn:
    def __init__(self):
        self.eval_sql = ""

    def execute(self, sql: str, params: tuple = ()):
        del params
        if "FROM vkpi_gtm_outcomes" in sql:
            return _Cursor([{"finalized_total": 6, "observed": 5, "freshest_at": NOW}])
        if "FROM vkpi_prediction_evals" in sql:
            self.eval_sql = sql
            return _Cursor([
                {"raw_actual": 8, "finite_actual": 7, "observed": 5, "freshest_at": NOW}
            ])
        if "FROM vkpi_recommendation_feedback" in sql:
            return _Cursor([{"observed": 5, "freshest_at": NOW}])
        raise AssertionError(sql)


def test_learning_readiness_requires_all_three_real_evidence_legs(monkeypatch):
    monkeypatch.setattr(data_readiness, "table_exists", lambda _name: True)
    conn = _LearningReadinessConn()
    result = data_readiness.build_learning_readiness(
        conn=conn,
        now=NOW,
    )
    assert result["claimable"] is True
    assert result["facts"] == {
        "finalized_outcomes_total": 6,
        "evidence_backed_finalized_outcomes": 5,
        "prediction_evals_with_actual_raw": 8,
        "prediction_evals_with_finite_actual": 7,
        "prediction_evals_with_actual": 5,
        "distinct_prediction_outcomes_with_verified_actual": 5,
        "real_human_feedback": 5,
    }
    assert result["policy"]["automatic_business_outcome_creation"] is False
    assert result["policy"]["prediction_eval_requires_human_finalized_outcome_evidence"] is True
    assert result["policy"]["prediction_eval_counts_distinct_outcomes"] is True
    assert result["policy"]["prediction_eval_requires_verified_metric_binding"] is True
    assert result["policy"]["prediction_eval_claim_unit"] == "distinct_outcome_id"
    assert "COUNT(DISTINCT e.outcome_id)" in conn.eval_sql
    assert "verified_against_outcome" in conn.eval_sql
    assert "'nan', 'infinity', '-infinity'" in conn.eval_sql
    assert "LEFT JOIN vkpi_gtm_outcomes o ON o.id = e.outcome_id" in conn.eval_sql
    assert "o.decided_by IS NOT NULL" in conn.eval_sql
    assert "o.action_inbox_id IS NOT NULL" in conn.eval_sql
    assert "vkpi_gtm_observation_window/v1" in conn.eval_sql


def test_feedback_readiness_counts_distinct_real_action_units(monkeypatch):
    monkeypatch.setattr(data_readiness, "table_exists", lambda _name: True)
    captured: list[str] = []

    class _Conn(_LearningReadinessConn):
        def execute(self, sql: str, params: tuple = ()):
            if "FROM vkpi_recommendation_feedback" in sql:
                captured.append(sql)
                return _Cursor([{"observed": 1, "freshest_at": NOW}])
            return super().execute(sql, params)

    data_readiness.build_learning_readiness(conn=_Conn(), now=NOW)
    assert "COUNT(DISTINCT recommendation_id)" in captured[0]
    assert "'claim', 'shortlist', 'reject', 'create_project'" in captured[0]


def test_pending_or_unlinked_window_is_not_observed_outcome_evidence():
    assert data_readiness.has_observed_outcome_evidence(
        {"window_28d": {"status": "pending", "metrics": {"orders_n": 0}}}
    ) is False
    assert data_readiness.has_observed_outcome_evidence(
        {"window_7d": {"status": "no_kol_linked", "honesty": "unavailable"}}
    ) is False
    assert data_readiness.has_observed_outcome_evidence(
        {"window_14d": {"status": "filled", "metrics": {"videos_posted_n": 0}}}
    ) is False
    assert data_readiness.has_observed_outcome_evidence({
        "window_14d": {
            "schema": "vkpi_gtm_observation_window/v1",
            "status": "filled",
            "window": "14d",
            "source": (
                "auto:evidence+shortlinks(vkpi_kol_video_evidence/"
                "vkpi_link_clicks JOIN vkpi_links)"
            ),
            "window_start": "2026-07-01T00:00:00Z",
            "window_end": "2026-07-15T00:00:00Z",
            "filled_at": "2026-07-16T00:00:00Z",
            "metrics": {"videos_posted_n": 0},
            "evidence_sha256": data_readiness.outcome_window_evidence_sha256({
                "schema": "vkpi_gtm_observation_window/v1",
                "status": "filled",
                "window": "14d",
                "source": (
                    "auto:evidence+shortlinks(vkpi_kol_video_evidence/"
                    "vkpi_link_clicks JOIN vkpi_links)"
                ),
                "window_start": "2026-07-01T00:00:00Z",
                "window_end": "2026-07-15T00:00:00Z",
                "filled_at": "2026-07-16T00:00:00Z",
                "metrics": {"videos_posted_n": 0},
            }),
        }
    }) is True


class _StaticRowsConn:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def execute(self, _sql: str, _params: tuple = ()):
        return _Cursor(self.rows)


def test_stale_signal_ledger_rows_are_descriptive_only(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        connection,
        "get_conn",
        lambda: _StaticRowsConn([
            {
                "id": 1,
                "source_type": "market",
                "source_name": "snapshot",
                "signal_kind": "mention",
                "signal_text": "historical signal",
                "sample_size": 10,
                "captured_at": "2020-01-01T00:00:00+00:00",
            }
        ]),
    )
    result = signal_ledger.summarize_for_preview("AF-85")
    assert result["status"] == "data_missing"
    assert result["claimable"] is False
    assert result["data_readiness"]["status"] == "stale"
    assert result["items"]  # Historical rows remain inspectable.


def test_prediction_rollup_keeps_raw_metrics_but_gates_claims():
    row = {
        "actual_value": 100,
        "error_abs": 10,
        "interval_hit": True,
        "direction_hit": True,
    }
    under_gate = prediction_ledger.weekly_rollup([row] * 4)
    assert under_gate["wape"] == 0.1
    assert under_gate["claimable"] is False
    assert under_gate["claimable_metrics"]["wape"] is None

    at_gate = prediction_ledger.weekly_rollup([row] * 5)
    assert at_gate["wape"] == 0.1
    assert at_gate["claimable"] is False
    assert at_gate["claimable_metrics"]["wape"] is None

    verified = prediction_ledger.weekly_rollup([
        {**row, "outcome_id": index, "verified_actual": True}
        for index in range(1, 6)
    ])
    assert verified["claimable"] is True
    assert verified["claimable_metrics"]["wape"] == 0.1

    duplicate = prediction_ledger.weekly_rollup([
        {**row, "outcome_id": 1, "verified_actual": True}
        for _ in range(5)
    ])
    assert duplicate["claimable"] is False


def test_record_eval_rejects_missing_actual_without_db_access():
    result = prediction_ledger.record_eval("run-1", None)
    assert result["ok"] is False
    assert result["reason"] == "missing_required_field"
    assert result["missing"] == ["actual_value"]


class _OpenOutcomeConn:
    def execute(self, sql: str, params: tuple = ()):
        del params
        if "FROM vkpi_gtm_outcomes" in sql:
            return _Cursor([{"decision": "open", "decided_at": None, "decided_by": None}])
        raise AssertionError(sql)


def test_legacy_record_eval_rejects_all_outcome_bound_writes(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _OpenOutcomeConn())
    result = prediction_ledger.record_eval("run-1", 100, outcome_id=7)
    assert result["ok"] is False
    assert result["reason"] == "verified_actual_writer_required"


class _FinalizedEmptyOutcomeConn:
    def execute(self, sql: str, params: tuple = ()):
        del params
        if "FROM vkpi_gtm_outcomes" in sql:
            return _Cursor([
                {
                    "decision": "validated",
                    "decided_at": NOW,
                    "decided_by": 1,
                    "actual_result": None,
                    "window_7d": None,
                    "window_14d": None,
                    "window_28d": {"status": "pending"},
                }
            ])
        raise AssertionError(sql)


def test_legacy_record_eval_cannot_bypass_verified_actual_writer(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _FinalizedEmptyOutcomeConn())
    result = prediction_ledger.record_eval("run-1", 100, outcome_id=7)
    assert result["ok"] is False
    assert result["reason"] == "verified_actual_writer_required"


class _FinalizedObservedOutcomeConn:
    def __init__(self):
        self.commits = 0
        self.insert_params = None

    def execute(self, sql: str, params: tuple = ()):
        if "FROM vkpi_gtm_outcomes" in sql:
            return _Cursor([
                {
                    "decision": "validated",
                    "decided_at": NOW,
                    "decided_by": 1,
                    "actual_result": None,
                    "window_7d": None,
                    "window_14d": {"status": "filled", "metrics": {"views": 100}},
                    "window_28d": None,
                }
            ])
        if "SELECT p10, p50, p90" in sql:
            return _Cursor([{"p10": 80, "p50": 90, "p90": 110}])
        if "FROM vkpi_prediction_evals" in sql and "SELECT id" in sql:
            return _Cursor([])
        if "INSERT INTO vkpi_prediction_evals" in sql:
            self.insert_params = params
            return _Cursor([{"id": 12}])
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1


def test_legacy_record_eval_cannot_poison_outcome_eval_key(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    conn = _FinalizedObservedOutcomeConn()
    monkeypatch.setattr(connection, "get_conn", lambda: conn)

    rejected = prediction_ledger.record_eval(
        "run-1",
        100,
        outcome_id=7,
        actual_json={
            "outcome_id": 7,
            "evidence_field": "window_14d",
            "metric_path": "metrics.views",
            "value": 101,
        },
    )
    assert rejected["reason"] == "verified_actual_writer_required"
    assert conn.commits == 0

    still_rejected = prediction_ledger.record_eval(
        "run-1",
        100,
        outcome_id=7,
        actual_json={
            "outcome_id": 7,
            "evidence_field": "window_14d",
            "metric_path": "metrics.views",
            "value": 100,
        },
    )
    assert still_rejected["reason"] == "verified_actual_writer_required"
    assert conn.commits == 0
    assert conn.insert_params is None


def test_record_eval_rejects_nonfinite_actual_without_db_access():
    for value in (float("nan"), float("inf"), float("-inf")):
        result = prediction_ledger.record_eval("run-1", value)
        assert result["reason"] == "missing_required_field"


class _ForecastRefreshConn:
    def __init__(self):
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):
        del params
        if "FROM vkpi_forecast_log" in sql and "outcome = 'pending'" in sql:
            return _Cursor([
                {
                    "id": 1,
                    "kol_pool_id": 9,
                    "p10": 80,
                    "p50": 100,
                    "p90": 120,
                    "created_at": datetime.now(timezone.utc) - timedelta(days=40),
                    "actual_views": None,
                }
            ])
        if "FROM vkpi_kol_video_evidence" in sql:
            return _Cursor([
                {
                    "view_count": 100,
                    "posted_at": datetime.now(timezone.utc) - timedelta(days=10),
                    "is_active": True,
                }
            ])
        if "UPDATE vkpi_forecast_log" in sql:
            raise AssertionError("one auto-observed video must not finalize a forecast")
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1


def test_forecast_auto_refresh_does_not_finalize_one_video_sample():
    conn = _ForecastRefreshConn()
    result = forecast_feedback.refresh_forecast_outcomes(conn=conn)
    assert result["status"] == "ok"
    assert result["updated"] == 0
    assert result["kept_pending_insufficient_evidence"] == 1
    assert conn.commits == 0


class _ForecastSummaryConn:
    def __init__(self, n: int):
        self.n = n

    def execute(self, sql: str, params: tuple = ()):
        del params
        if "GROUP BY outcome, context, method" in sql:
            return _Cursor([
                {"outcome": "hit_in_band", "context": "launch", "method": "rule", "n": self.n}
            ])
        if "SELECT MAX(actual_at)" in sql:
            return _Cursor([{"freshest_at": datetime.now(timezone.utc)}])
        raise AssertionError(sql)


def test_forecast_summary_exposes_observation_without_small_sample_claim():
    under_gate = forecast_feedback.forecast_log_summary(
        recent_limit=0,
        conn=_ForecastSummaryConn(1),
    )
    assert under_gate["observed_in_band_rate"] == 1.0
    assert under_gate["in_band_rate"] is None
    assert under_gate["claimable"] is False

    at_gate = forecast_feedback.forecast_log_summary(
        recent_limit=0,
        conn=_ForecastSummaryConn(5),
    )
    assert at_gate["in_band_rate"] == 1.0
    assert at_gate["claimable"] is True


class _OutcomeCoverageConn:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, sql: str, params: tuple = ()):
        del params
        self.calls.append(sql)
        if "SELECT recommendation_id FROM vkpi_recommendation_outcomes" in sql:
            return _Cursor([{"recommendation_id": 2}])
        raise AssertionError(sql)


def test_display_outcome_coverage_is_read_only_by_default(monkeypatch):
    conn = _OutcomeCoverageConn()
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    monkeypatch.setattr(
        outcomes,
        "ensure_vkpi_product_industry_schema",
        lambda: (_ for _ in ()).throw(AssertionError("read path must not run schema DDL")),
    )
    result = outcomes.ensure_outcomes_for_display([{"id": 1}, {"id": 2}, {"id": 1}])
    assert result == {
        "ensured": 0,
        "existing": 1,
        "missing": 1,
        "create_missing": False,
        "writes": False,
    }
    assert len(conn.calls) == 1


class _NoBusinessEvidenceConn:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, sql: str, params: tuple = ()):
        del params
        self.calls.append(sql)
        if "FROM vkpi_kol_recommendations" in sql:
            return _Cursor([
                {
                    "id": 3,
                    "kol_pool_id": None,
                    "linked_main_kol_id": None,
                    "launch_id": None,
                    "created_at": NOW,
                    "feature_snapshot_json": "{}",
                    "scoring_breakdown_json": "{}",
                }
            ])
        if "FROM vkpi_recommendation_outcomes" in sql:
            return _Cursor([])
        if "FROM vkpi_projects" in sql:
            return _Cursor([])
        raise AssertionError(sql)

    def commit(self):
        raise AssertionError("no evidence must not produce a write")


def test_business_refresh_does_not_create_empty_outcome(monkeypatch):
    conn = _NoBusinessEvidenceConn()
    monkeypatch.setattr(outcomes, "get_conn", lambda: conn)
    monkeypatch.setattr(outcomes, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(outcomes, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(
        outcomes,
        "ensure_outcome",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("no evidence must not create an outcome")
        ),
    )
    result = outcomes.refresh_business_outcome(3)
    assert result["outcome"] is None
    assert result["aggregates"]["status"] == "no_observed_business_evidence"
    assert all("INSERT" not in sql and "UPDATE" not in sql for sql in conn.calls)


def test_scorecard_dimension_separates_capability_from_observed_evidence():
    result = _dimension(
        "learning",
        "Learning",
        20,
        0.9,
        0.2,
        facts={},
        target="target",
        next_step="next",
    )
    assert result["capability_score"] == 0.9
    assert result["observed_evidence_score"] == 0.2
    assert result["score"] == 0.2
    assert result["weighted_score"] == 4.0


def test_learning_digest_suppresses_validated_labels_when_unready(monkeypatch):
    def add_validated(items, _sources):
        items.append("small-sample win rate")

    def add_dropped(items, _sources):
        items.append("small-sample channel loss")
        return ["channel"]

    monkeypatch.setattr(summary, "_ledger_validated", add_validated)
    monkeypatch.setattr(
        summary,
        "_scorecard_digest",
        lambda _sources: {"pending_total": 2, "judged": 1},
    )
    monkeypatch.setattr(summary, "_miss_review_dropped", add_dropped)
    monkeypatch.setattr(summary, "_effective_styles", lambda _sources: ["correlated style"])
    monkeypatch.setattr(
        data_readiness,
        "build_learning_readiness",
        lambda: {"claimable": False, "status": "insufficient", "blockers": ["prediction_evals:sample<5"]},
    )

    result = summary._learning_digest_card()
    assert result["validated"] == []
    assert result["effective_styles"] == []
    assert result["dropped_channels"] == []
    assert result["observed_patterns"]["validated"] == ["small-sample win rate"]
    assert result["claim_status"] == "descriptive_only"


class _WeeklyAnswersConn:
    def execute(self, sql: str, params: tuple = ()):
        del params
        if "SELECT id, gtm_plan_id" in sql:
            return _Cursor([
                {
                    "id": index,
                    "market": "US",
                    "channel": "creator",
                    "content_angle": "review",
                    "action_type": "bet",
                    "decision": "validated",
                    "lesson": "observed",
                    "next_weight_change": None,
                    "actual_result": None,
                    "window_7d": data_readiness.seal_outcome_window_evidence({
                        "schema": "vkpi_gtm_observation_window/v1",
                        "status": "filled",
                        "window": "7d",
                        "source": (
                            "auto:outreach+fulfillment+gifted"
                            "(vkpi_messages/vkpi_shipments/vkpi_content_posts/"
                            "vkpi_kol_video_evidence/vkpi_project_kol_assignments)"
                        ),
                        "window_start": "2026-07-01T00:00:00Z",
                        "window_end": "2026-07-08T00:00:00Z",
                        "filled_at": "2026-07-09T00:00:00Z",
                        "metrics": {"contacted": True},
                    }),
                    "window_14d": None,
                    "window_28d": None,
                    "created_at": NOW,
                    "decided_at": NOW,
                    "decided_by": 1,
                }
                for index in range(1, 6)
            ])
        if "SELECT COUNT(*) AS n" in sql:
            return _Cursor([{"n": 0}])
        raise AssertionError(sql)


def test_weekly_answers_marks_group_rates_descriptive_when_global_gate_fails(monkeypatch):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _WeeklyAnswersConn())
    monkeypatch.setattr(
        data_readiness,
        "build_learning_readiness",
        lambda **_kwargs: {"claimable": False, "status": "insufficient", "blockers": ["real_feedback:sample<5"]},
    )
    monkeypatch.setattr(data_readiness, "has_verified_outcome_evidence", lambda *_args: True)
    result = weekly_answers.weekly_report(days=7)
    market_group = result["groups"]["market"][0]
    assert market_group["observed_win_rate"] == 1.0
    assert market_group["claimable"] is False
    assert result["what_worked"]["group_highlights"] == []
    assert result["claim_status"] == "descriptive_only"


def test_weekly_answers_requires_exact_server_window_event(monkeypatch):
    import app.db.connection as connection

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_gtm_outcomes (
          id INTEGER PRIMARY KEY, gtm_plan_id INTEGER, product_sku TEXT, market TEXT,
          segment TEXT, channel TEXT, action_type TEXT, content_angle TEXT,
          decision TEXT, lesson TEXT, next_weight_change TEXT, actual_result TEXT,
          window_7d TEXT, window_14d TEXT, window_28d TEXT, action_inbox_id INTEGER,
          created_at TEXT, decided_at TEXT, decided_by INTEGER
        );
        CREATE TABLE vkpi_event_ledger (
          id INTEGER PRIMARY KEY, organization_id INTEGER, event_type TEXT,
          entity_type TEXT, entity_id TEXT, actor_type TEXT, actor_id TEXT,
          source TEXT, payload_json TEXT, trace_id TEXT, provenance_json TEXT
        );
        """
    )
    window = data_readiness.seal_outcome_window_evidence({
        "schema": "vkpi_gtm_observation_window/v1",
        "status": "filled",
        "window": "7d",
        "source": (
            "auto:outreach+fulfillment+gifted"
            "(vkpi_messages/vkpi_shipments/vkpi_content_posts/"
            "vkpi_kol_video_evidence/vkpi_project_kol_assignments)"
        ),
        "window_start": "2026-07-01T00:00:00Z",
        "window_end": "2026-07-08T00:00:00Z",
        "filled_at": "2026-07-09T00:00:00Z",
        "metrics": {"contacted": True},
    })
    conn.execute(
        """INSERT INTO vkpi_gtm_outcomes
           (id,market,channel,action_type,content_angle,decision,window_7d,
            action_inbox_id,created_at,decided_at,decided_by)
           VALUES (1,'US','creator','bet','review','validated',?,901,?,?,1)""",
        (json.dumps(window), NOW.isoformat(), NOW.isoformat()),
    )
    conn.execute(
        """INSERT INTO vkpi_event_ledger VALUES
           (1,1,'gtm_window_observed','gtm_outcome','1','system','gtm_windows',
            'gtm_windows.refresh',?,'weekly-window-1',?)""",
        (
            json.dumps({
                "outcome_id": 1, "action_inbox_id": 901,
                "evidence_field": "window_7d",
                "schema": "vkpi_gtm_observation_window/v1", "window": "7d",
                "evidence_sha256": window["evidence_sha256"],
            }),
            json.dumps({
                "evidence_verification": "server_produced_observation_window",
            }),
        ),
    )
    conn.commit()
    monkeypatch.setattr(connection, "table_exists", lambda _name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(
        data_readiness,
        "build_learning_readiness",
        lambda **_kwargs: {"claimable": False, "status": "insufficient", "blockers": []},
    )

    exact = weekly_answers.weekly_report(days=7)
    assert exact["totals"]["evidence_backed_finalized_total"] == 1
    conn.execute("UPDATE vkpi_event_ledger SET payload_json='{}'")
    conn.commit()
    mismatched = weekly_answers.weekly_report(days=7)
    assert mismatched["totals"]["evidence_backed_finalized_total"] == 0
