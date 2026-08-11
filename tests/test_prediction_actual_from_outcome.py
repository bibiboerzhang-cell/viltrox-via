"""Hermetic truth binding for prediction actuals resolved from finalized outcomes."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.db import connection
from app.domains.market_brain import data_readiness, prediction_ledger
from app.domains.platform import event_ledger


STAFF = {"id": 31, "organization_id": 1, "organization_scope_status": "resolved"}


def _db(*, metric_value=1.0, run_market: str = "US") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_gtm_outcomes (
            id INTEGER PRIMARY KEY,
            decision TEXT,
            decided_at TEXT,
            decided_by INTEGER,
            action_type TEXT,
            action_inbox_id INTEGER,
            product_sku TEXT,
            market TEXT,
            channel TEXT,
            actual_result TEXT,
            window_7d TEXT,
            window_14d TEXT,
            window_28d TEXT
        );
        CREATE TABLE vkpi_prediction_runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT UNIQUE,
            organization_id TEXT,
            model_name TEXT,
            model_version TEXT,
            task_type TEXT,
            product_sku TEXT,
            market TEXT,
            channel TEXT,
            horizon_days INTEGER,
            input_fingerprint TEXT,
            input_summary TEXT,
            prediction TEXT,
            created_at TEXT,
            p10 REAL,
            p50 REAL,
            p90 REAL
        );
        CREATE TABLE vkpi_prediction_evals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            outcome_id INTEGER,
            actual_value REAL,
            actual_json TEXT NOT NULL,
            error_abs REAL,
            error_pct REAL,
            interval_hit INTEGER,
            direction_hit INTEGER,
            calibrated_bucket TEXT,
            evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            UNIQUE (organization_id, run_id, outcome_id)
        );
        CREATE TABLE vkpi_event_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            confidence REAL,
            provenance_json TEXT NOT NULL,
            occurred_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    observed_payload = data_readiness.seal_outcome_window_evidence({
        "schema": "vkpi_gtm_observation_window/v1",
        "status": "filled",
        "window": "7d",
        "source": (
            "auto:outreach+fulfillment+gifted"
            "(vkpi_messages/vkpi_shipments/vkpi_content_posts/"
            "vkpi_kol_video_evidence/vkpi_project_kol_assignments)"
        ),
        "window_start": "2026-08-01T00:00:00Z",
        "window_end": "2026-08-08T00:00:00Z",
        "filled_at": "2026-08-09T00:00:00Z",
        "metrics": {"reply_outcome": metric_value},
    })
    observed = json.dumps(observed_payload)
    conn.execute(
        """INSERT INTO vkpi_gtm_outcomes
           VALUES (51,'win','2026-08-10T00:00:00Z',77,'kol_outreach',501,
                   'AF 26','US','youtube','{}',?, '{}','{}')""",
        (observed,),
    )
    conn.execute(
        """INSERT INTO vkpi_event_ledger
           (organization_id,event_type,entity_type,entity_id,actor_type,actor_id,
            source,payload_json,trace_id,confidence,provenance_json)
           VALUES (1,'gtm_window_observed','gtm_outcome','51','system','gtm_windows',
                   'gtm_windows.refresh',?,'gtm-window-51',1.0,?)""",
        (
            json.dumps({
                "outcome_id": 51,
                "action_inbox_id": 501,
                "evidence_field": "window_7d",
                "schema": "vkpi_gtm_observation_window/v1",
                "window": "7d",
                "evidence_sha256": observed_payload["evidence_sha256"],
            }),
            json.dumps({
                "evidence_verification": "server_produced_observation_window",
            }),
        ),
    )
    from app.domains.market_brain import prediction_truth

    input_summary = json.dumps({
        "evaluation_contract": prediction_truth.build_registered_gtm_evaluation_contract(
            "kol_outreach_reply_outcome_7d",
            target_action_inbox_id=501,
            observation_start_at="2026-08-01T00:00:00Z",
        )
    })
    conn.execute(
        """INSERT INTO vkpi_prediction_runs
           VALUES (1,'run-51','viltrox','reply-probability-rule','v1','kol_outreach_reply_probability',
                   'AF 26',?,'youtube',7,'fingerprint-51',?,
                   '{"metric_key":"reply_outcome","unit":"ratio","value":0.1}',
                   '2026-08-01T00:00:00Z',0.05,0.1,0.2)""",
        (run_market, input_summary),
    )
    conn.commit()
    return conn


def _wire(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    monkeypatch.setattr(connection, "table_exists", lambda name: True)


def _resolve(**overrides):
    payload = {
        "run_id": "run-51",
        "staff": STAFF,
        "outcome_id": 51,
        "evidence_field": "window_7d",
        "metric_path": "metrics.reply_outcome",
        "correlation_id": "prediction-actual-0001",
        "notes": "七日收入",
    }
    payload.update(overrides)
    run_id = payload.pop("run_id")
    return prediction_ledger.record_eval_from_finalized_outcome(run_id, **payload)


def test_prediction_actual_is_server_resolved_with_reviewer_binding(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    result = _resolve()
    assert result["ok"] is True and result["deduped"] is False
    eval_row = dict(conn.execute("SELECT * FROM vkpi_prediction_evals").fetchone())
    assert eval_row["actual_value"] == 1.0
    binding = json.loads(eval_row["actual_json"])
    assert binding["outcome_id"] == 51
    assert binding["reviewed_by_staff_id"] == 31
    assert binding["outcome_decided_by_staff_id"] == 77
    assert binding["correlation_id"] == "prediction-actual-0001"
    assert binding["unit"] == "ratio"
    assert binding["task_type"] == "kol_outreach_reply_probability"
    assert binding["evaluation_registry_key"] == "kol_outreach_reply_outcome_7d"
    assert len(binding["run_snapshot_sha256"]) == 64
    event = dict(conn.execute(
        "SELECT * FROM vkpi_event_ledger WHERE event_type='prediction_actual_verified'"
    ).fetchone())
    assert event["event_type"] == "prediction_actual_verified"
    assert json.loads(event["provenance_json"])["prediction_run_immutable"] is True
    replay = _resolve()
    assert replay["ok"] is True and replay["deduped"] is True
    assert conn.execute("SELECT COUNT(*) FROM vkpi_prediction_evals").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_event_ledger WHERE event_type='prediction_actual_verified'"
    ).fetchone()[0] == 1


@pytest.mark.parametrize("metric_value", [True, False, "not-a-number", None])
def test_prediction_actual_rejects_bool_and_other_non_numeric_truth(monkeypatch, metric_value):
    conn = _db(metric_value=metric_value)
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "actual_metric_not_numeric"


def test_prediction_actual_rejects_cross_tenant_actor_without_reading_db(monkeypatch):
    def forbidden():
        pytest.fail("cross-tenant request must fail before database access")

    monkeypatch.setattr(connection, "get_conn", forbidden)
    result = _resolve(
        staff={"id": 31, "organization_id": 2, "organization_scope_status": "resolved"}
    )
    assert result["reason"] == "actual_scope_unavailable"


def test_prediction_actual_rejects_nondefault_organization_without_reading_db(monkeypatch):
    def forbidden():
        pytest.fail("nondefault organization must fail before database access")

    monkeypatch.setattr(connection, "get_conn", forbidden)
    result = _resolve(organization_id="another-tenant")
    assert result["reason"] == "actual_scope_unavailable"


def test_prediction_actual_rejects_dimension_mismatch(monkeypatch):
    conn = _db(run_market="JP")
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "actual_market_mismatch"


def test_prediction_actual_rejects_unfinalized_or_placeholder_evidence(monkeypatch):
    conn = _db()
    conn.execute("UPDATE vkpi_gtm_outcomes SET window_7d='{}'")
    conn.commit()
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "outcome_missing_observed_evidence"


def test_prediction_actual_rejects_missing_server_window_event(monkeypatch):
    conn = _db()
    conn.execute("DELETE FROM vkpi_event_ledger WHERE event_type='gtm_window_observed'")
    conn.commit()
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "outcome_missing_observed_evidence"


def test_prediction_actual_rejects_invalid_time_order(monkeypatch):
    conn = _db()
    conn.execute(
        "UPDATE vkpi_prediction_runs SET created_at='2026-08-11T00:00:00Z' WHERE run_id='run-51'"
    )
    conn.commit()
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "actual_chronology_invalid"


def test_prediction_actual_requires_contract_selected_before_outcome(monkeypatch):
    conn = _db()
    conn.execute("UPDATE vkpi_prediction_runs SET input_summary='{}'")
    conn.commit()
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "prediction_evaluation_contract_missing"


def test_prediction_actual_rejects_posthoc_metric_selection(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _resolve(metric_path="metrics.likes")["reason"] == "actual_metric_contract_mismatch"


def test_prediction_actual_requires_closed_observation_window(monkeypatch):
    conn = _db()
    payload = json.loads(conn.execute("SELECT window_7d FROM vkpi_gtm_outcomes").fetchone()[0])
    payload["filled_at"] = "2026-08-11T00:00:00Z"
    payload = data_readiness.seal_outcome_window_evidence(payload)
    conn.execute("UPDATE vkpi_gtm_outcomes SET window_7d=?", (json.dumps(payload),))
    event_payload = json.loads(conn.execute(
        "SELECT payload_json FROM vkpi_event_ledger WHERE event_type='gtm_window_observed'"
    ).fetchone()[0])
    event_payload["evidence_sha256"] = payload["evidence_sha256"]
    conn.execute(
        "UPDATE vkpi_event_ledger SET payload_json=? WHERE event_type='gtm_window_observed'",
        (json.dumps(event_payload),),
    )
    conn.commit()
    _wire(monkeypatch, conn)
    assert _resolve()["reason"] == "actual_window_not_closed"


def test_prediction_actual_rejects_secret_like_notes(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)
    assert _resolve(notes="token=do-not-store")["reason"] == "actual_notes_invalid"


def test_prediction_actual_event_failure_rolls_back_eval_atomically(monkeypatch):
    conn = _db()
    _wire(monkeypatch, conn)

    def fail_event(*args, **kwargs):
        raise RuntimeError("event ledger unavailable")

    monkeypatch.setattr(event_ledger, "insert_required", fail_event)
    assert _resolve()["reason"] == "db_error"
    assert conn.execute("SELECT COUNT(*) FROM vkpi_prediction_evals").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_event_ledger WHERE event_type='prediction_actual_verified'"
    ).fetchone()[0] == 0
    assert conn.in_transaction is False


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _ExistingEvalConn:
    def __init__(self, *, notes: str):
        self.notes = notes

    def execute(self, sql: str, params=()):
        del params
        if "FROM vkpi_gtm_outcomes" in sql:
            return _Cursor(
                {
                    "decision": "win",
                    "decided_at": "2026-08-10T00:00:00Z",
                    "decided_by": 77,
                    "actual_result": {},
                    "window_7d": {"status": "filled", "metrics": {"revenue": 123.5}},
                    "window_14d": {},
                    "window_28d": {},
                }
            )
        if "SELECT p10, p50, p90" in sql:
            return _Cursor({"p10": 100.0, "p50": 120.0, "p90": 140.0})
        if "FROM vkpi_prediction_evals" in sql:
            return _Cursor(
                {
                    "id": 91,
                    "actual_value": 123.5,
                    "actual_json": {
                        "outcome_id": 51,
                        "evidence_field": "window_7d",
                        "metric_path": "metrics.revenue",
                        "value": 123.5,
                        "source": "server_resolved_finalized_outcome",
                        "reviewed_by_staff_id": 31,
                        "outcome_decided_by_staff_id": 77,
                        "correlation_id": "prediction-actual-0001",
                        "binding_status": "verified_against_outcome",
                    },
                    "calibrated_bucket": None,
                    "notes": self.notes,
                }
            )
        raise AssertionError(sql)


def _actual_binding():
    return {
        "outcome_id": 51,
        "evidence_field": "window_7d",
        "metric_path": "metrics.revenue",
        "value": 123.5,
        "source": "server_resolved_finalized_outcome",
        "reviewed_by_staff_id": 31,
        "outcome_decided_by_staff_id": 77,
        "correlation_id": "prediction-actual-0001",
    }


def test_legacy_eval_writer_cannot_poison_outcome_bound_key(monkeypatch):
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: _ExistingEvalConn(notes="original"))

    same = prediction_ledger.record_eval(
        "run-51", 123.5, outcome_id=51, actual_json=_actual_binding(), notes="original"
    )
    changed = prediction_ledger.record_eval(
        "run-51", 123.5, outcome_id=51, actual_json=_actual_binding(), notes="changed"
    )

    expected = {
        "ok": False,
        "id": None,
        "deduped": False,
        "reason": "verified_actual_writer_required",
    }
    assert same == expected
    assert changed == expected
