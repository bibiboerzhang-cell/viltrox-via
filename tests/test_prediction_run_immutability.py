from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.db import connection
from app.domains.market_brain import prediction_ledger


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/276_vkpi_prediction_runs_immutable.sql"
DOWN = ROOT / "migrations/276_vkpi_prediction_runs_immutable_down.sql"
TRANSITION_UP = ROOT / "migrations/279_vkpi_verification_transition_guards.sql"
TRANSITION_DOWN = ROOT / "migrations/279_vkpi_verification_transition_guards_down.sql"


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ReplayConn:
    def __init__(self, existing: dict):
        self.existing = existing
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params=()):
        self.calls.append((sql, tuple(params)))
        if "INSERT INTO vkpi_prediction_runs" in sql:
            return _Cursor(None)
        if "FROM vkpi_prediction_runs" in sql:
            return _Cursor(self.existing)
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _existing(*, prediction: dict | None = None) -> dict:
    value = prediction or {"p50": 100}
    return {
        "id": 5,
        "model_name": "rule_baseline",
        "model_version": "v1",
        "task_type": "launch_forecast",
        "product_sku": "AF-26",
        "market": "US",
        "channel": "creator",
        "horizon_days": 7,
        "input_fingerprint": "fixed-input",
        "input_summary": {},
        "prediction": value,
        "p10": 80.0,
        "p50": 100.0,
        "p90": 120.0,
        "confidence": "medium",
        "confidence_score": None,
        "missing_data": [],
        "basis": [],
        "baseline_value": None,
        "source_step": "rule",
    }


def _record(monkeypatch: pytest.MonkeyPatch, conn: _ReplayConn, *, prediction=None):
    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    return prediction_ledger.record_prediction_run(
        "run-immutable",
        "rule_baseline",
        "v1",
        "launch_forecast",
        prediction or {"p50": 100},
        product_sku="AF-26",
        market="US",
        channel="creator",
        horizon_days=7,
        input_fingerprint="fixed-input",
        p10=80,
        p50=100,
        p90=120,
        confidence="medium",
        source_step="rule",
    )


def test_identical_prediction_replay_is_deduped_without_update(monkeypatch):
    conn = _ReplayConn(_existing())
    result = _record(monkeypatch, conn)
    assert result == {"ok": True, "id": 5, "deduped": True}
    insert_sql = conn.calls[0][0]
    assert "DO NOTHING" in insert_sql
    assert "DO UPDATE" not in insert_sql
    assert conn.commits == 1 and conn.rollbacks == 0


def test_prediction_replay_cannot_rewrite_known_actual(monkeypatch):
    conn = _ReplayConn(_existing())
    result = _record(monkeypatch, conn, prediction={"p50": 999})
    assert result == {
        "ok": False,
        "id": 5,
        "deduped": False,
        "reason": "prediction_run_conflict",
    }
    assert conn.commits == 0 and conn.rollbacks == 1


def test_migration_276_is_runner_owned_ordered_and_append_only() -> None:
    sequence = connection._discover_postgres_migrations()
    assert UP.name in sequence
    assert sequence.index(UP.name) < sequence.index("278_vkpi_action_approval_evidence.sql")
    assert TRANSITION_UP.name in sequence
    assert sequence.index("278_vkpi_action_approval_evidence.sql") < sequence.index(TRANSITION_UP.name)
    up = UP.read_text(encoding="utf-8")
    down = DOWN.read_text(encoding="utf-8")
    assert connection._FORWARD_TRANSACTION_CONTROL_RE.search(up) is None
    assert "BEFORE UPDATE OR DELETE" in up
    assert "vkpi_prediction_runs is append-only" in up
    assert "TG_OP = 'UPDATE' AND NEW.outcome_id IS NOT NULL" in up
    assert "TG_OP = 'UPDATE'" in up
    assert "AND NEW.event_type IN" in up
    assert "276_vkpi_prediction_runs_immutable.sql" in down


def test_migration_279_upgrades_existing_trigger_functions_without_tx_control() -> None:
    up = TRANSITION_UP.read_text(encoding="utf-8")
    down = TRANSITION_DOWN.read_text(encoding="utf-8")
    assert connection._FORWARD_TRANSACTION_CONTROL_RE.search(up) is None
    assert "TG_OP = 'UPDATE' AND NEW.outcome_id IS NOT NULL" in up
    assert "AND NEW.event_type IN" in up
    assert "DROP TRIGGER" not in up
    assert "279_vkpi_verification_transition_guards.sql" in down


@pytest.mark.pg
def test_migration_276_blocks_update_and_delete_on_real_postgres(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_prediction_immutable_{uuid.uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
            conn.execute(
                """
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                INSERT INTO schema_migrations VALUES ('276_vkpi_prediction_runs_immutable.sql');
                CREATE TABLE vkpi_prediction_runs (
                  id BIGSERIAL PRIMARY KEY,
                  organization_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  prediction JSONB NOT NULL,
                  UNIQUE (organization_id, run_id)
                );
                INSERT INTO vkpi_prediction_runs(organization_id,run_id,prediction)
                VALUES ('viltrox','run-1','{"p50":100}'::jsonb);
                CREATE TABLE vkpi_prediction_evals (
                  id BIGSERIAL PRIMARY KEY,
                  outcome_id BIGINT,
                  actual_value DOUBLE PRECISION
                );
                INSERT INTO vkpi_prediction_evals(outcome_id,actual_value)
                VALUES (9,100),(NULL,80),(NULL,70);
                CREATE TABLE vkpi_gtm_outcomes (
                  id BIGSERIAL PRIMARY KEY,
                  decision TEXT NOT NULL,
                  actual_result JSONB,
                  window_7d JSONB,
                  window_14d JSONB,
                  window_28d JSONB,
                  product_sku TEXT,
                  market TEXT,
                  channel TEXT,
                  action_type TEXT,
                  action_inbox_id BIGINT,
                  lesson TEXT NOT NULL DEFAULT '',
                  next_weight_change JSONB,
                  decided_at TIMESTAMPTZ,
                  decided_by BIGINT
                );
                INSERT INTO vkpi_gtm_outcomes(
                  decision,actual_result,product_sku,market,channel,action_type,action_inbox_id
                ) VALUES ('accepted','{"status":"filled","views":100}'::jsonb,
                          'AF-26','US','creator','launch',7);
                CREATE TABLE vkpi_event_ledger (
                  id BIGSERIAL PRIMARY KEY,
                  organization_id BIGINT NOT NULL,
                  event_type TEXT NOT NULL,
                  entity_type TEXT NOT NULL,
                  entity_id TEXT NOT NULL,
                  source TEXT NOT NULL
                );
                INSERT INTO vkpi_event_ledger(
                  organization_id,event_type,entity_type,entity_id,source
                ) VALUES (1,'prediction_actual_verified','prediction_eval','1',
                          'prediction_ledger.human_actual_review');
                INSERT INTO vkpi_event_ledger(
                  organization_id,event_type,entity_type,entity_id,source
                ) VALUES (1,'ordinary_event','prediction_eval','3','ordinary.source');
                CREATE TABLE vkpi_skill_runs (
                  id BIGSERIAL PRIMARY KEY,
                  skill_name TEXT NOT NULL,
                  skill_version TEXT NOT NULL,
                  input_schema JSONB NOT NULL,
                  model_used TEXT,
                  prompt_version TEXT,
                  output JSONB NOT NULL,
                  human_score DOUBLE PRECISION,
                  accepted BOOLEAN,
                  business_result TEXT
                );
                INSERT INTO vkpi_skill_runs(
                  skill_name,skill_version,input_schema,model_used,prompt_version,
                  output,human_score,accepted
                ) VALUES ('creator_match','v1','{}','rule','p1',
                          '{"status":"ok"}',4.5,TRUE);
                CREATE TABLE vkpi_action_inbox (
                  id BIGSERIAL PRIMARY KEY,
                  dedupe_key TEXT NOT NULL,
                  category TEXT NOT NULL,
                  suggested_endpoint TEXT NOT NULL,
                  entity_type TEXT NOT NULL,
                  entity_id TEXT NOT NULL,
                  payload_json JSONB NOT NULL,
                  result_checklist_json JSONB NOT NULL,
                  status TEXT NOT NULL
                );
                INSERT INTO vkpi_action_inbox(
                  dedupe_key,category,suggested_endpoint,entity_type,entity_id,
                  payload_json,result_checklist_json,status
                ) VALUES ('a:1','project_observation','internal','project','7',
                          '{}','{"human_verification":{"decision":"accepted"}}','executed');
                INSERT INTO vkpi_event_ledger(
                  organization_id,event_type,entity_type,entity_id,source
                ) VALUES (1,'action_result_accepted','action','1',
                          'action_inbox.human_verification');
                """
            )
            conn.execute(UP.read_text(encoding="utf-8"))
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_prediction_runs SET prediction=%s::jsonb WHERE run_id='run-1'",
                    (json.dumps({"p50": 999}),),
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM vkpi_prediction_runs WHERE run_id='run-1'")
            assert conn.execute("SELECT prediction FROM vkpi_prediction_runs").fetchone()[0] == {"p50": 100}
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE vkpi_prediction_evals SET actual_value=0 WHERE outcome_id=9")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM vkpi_prediction_evals WHERE outcome_id=9")
            conn.execute("UPDATE vkpi_prediction_evals SET actual_value=81 WHERE outcome_id IS NULL")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE vkpi_prediction_evals SET outcome_id=10 WHERE id=3")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_gtm_outcomes SET actual_result=%s::jsonb WHERE id=1",
                    (json.dumps({"status": "filled", "views": 999}),),
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE vkpi_gtm_outcomes SET decision='failed' WHERE id=1")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM vkpi_gtm_outcomes WHERE id=1")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM vkpi_event_ledger WHERE id=1")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_event_ledger SET event_type='prediction_actual_verified' "
                    "WHERE event_type='ordinary_event'"
                )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """INSERT INTO vkpi_event_ledger(
                           organization_id,event_type,entity_type,entity_id,source
                       ) VALUES (1,'prediction_actual_verified','prediction_eval','1',
                                 'prediction_ledger.human_actual_review')"""
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE vkpi_skill_runs SET output='{}'::jsonb WHERE id=1")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE vkpi_skill_runs SET human_score=5 WHERE id=1")
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(
                    "UPDATE vkpi_action_inbox SET result_checklist_json='{}'::jsonb WHERE id=1"
                )
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("DELETE FROM vkpi_action_inbox WHERE id=1")
            conn.execute(DOWN.read_text(encoding="utf-8"))
            conn.execute(
                "UPDATE vkpi_prediction_runs SET prediction=%s::jsonb",
                (json.dumps({"p50": 101}),),
            )
            conn.execute("UPDATE vkpi_prediction_evals SET actual_value=1 WHERE outcome_id=9")
            conn.execute(
                "UPDATE vkpi_gtm_outcomes SET actual_result=%s::jsonb WHERE id=1",
                (json.dumps({"status": "filled", "views": 101}),),
            )
            conn.execute("DELETE FROM vkpi_event_ledger WHERE id=1")
            conn.execute("UPDATE vkpi_skill_runs SET output='{}'::jsonb WHERE id=1")
            conn.execute("UPDATE vkpi_skill_runs SET human_score=5 WHERE id=1")
            conn.execute(
                "UPDATE vkpi_action_inbox SET result_checklist_json='{}'::jsonb WHERE id=1"
            )
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
