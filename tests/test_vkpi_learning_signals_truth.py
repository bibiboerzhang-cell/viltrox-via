import sqlite3

from app.domains.market_brain import data_readiness
from app.domains.memory import learning_signals


class _Cursor:
    def fetchone(self):
        return {"n": 1}


class _PercentSafeConn:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        # psycopg treats raw percent signs as placeholder syntax. Wildcard
        # filters must travel as bound values through the repository adapter.
        assert "%" not in sql
        if "LIKE ?" in sql:
            assert params
            assert any("%" in str(value) for value in params)
        return _Cursor()


def _truth(value: int) -> dict[str, int]:
    return {
        "finalized_outcomes": value,
        "human_feedback": value,
        "prediction_actual_evals": value,
        "human_reviewed_skill_runs": value,
        "reviewed_skill_types": value,
        "executed_agent_tool_runs": value,
        "executed_tool_types": value,
        "verified_action_tool_cases": value,
        "linked_agent_outcomes": value,
    }


def test_raw_activity_cannot_raise_learning_maturity(monkeypatch) -> None:
    monkeypatch.setattr(learning_signals, "_count_by", lambda *_args: {"raw": 10000})
    monkeypatch.setattr(learning_signals, "table_exists", lambda _table: False)
    monkeypatch.setattr(learning_signals, "_verified_learning_evidence", lambda: _truth(0))
    monkeypatch.setattr(learning_signals, "_scalar", lambda *_args: 446)

    result = learning_signals.get_learning_status({"role": "admin"})

    assert result["agent_actions"]["total"] == 10000
    assert result["memory_feedback"]["total"] == 10000
    assert result["maturity"] == "cold"
    assert result["claim_status"] == "descriptive_only"
    assert result["verified_evidence"]["finalized_outcomes"] == 0


def test_maturity_requires_every_verified_evidence_gate() -> None:
    almost = {
        "finalized_outcomes": 100,
        "human_feedback": 20,
        "prediction_actual_evals": 50,
        "human_reviewed_skill_runs": 100,
        "reviewed_skill_types": 4,
        "executed_agent_tool_runs": 19,
        "executed_tool_types": 3,
        "verified_action_tool_cases": 20,
    }
    assert learning_signals._maturity_from_truth(almost) == "warming"
    assert learning_signals._maturity_from_truth({**almost, "executed_agent_tool_runs": 20}) == "learning"


def test_verified_learning_filters_bind_sql_wildcards(monkeypatch) -> None:
    conn = _PercentSafeConn()
    monkeypatch.setattr(learning_signals, "table_exists", lambda _table: True)
    monkeypatch.setattr(learning_signals, "get_conn", lambda: conn)

    truth = learning_signals._verified_learning_evidence()

    # Outreach claimability is an independent coverage gate and may honestly
    # remain zero in this hermetic SQL-shape test; every counted signal is one.
    assert {value for key, value in truth.items() if key != "outreach_prediction_claimable"} == {1}
    assert truth["outreach_prediction_claimable"] in {0, 1}
    feedback_sql = next(sql for sql in conn.sql if "vkpi_recommendation_feedback" in sql)
    assert "COUNT(DISTINCT recommendation_id)" in feedback_sql
    assert "'claim', 'shortlist', 'reject', 'create_project'" in feedback_sql
    assert {"%demo%", "%synthetic%", "%fixture%", "%gtm_weight_feedback%"}.issubset(
        set(data_readiness.real_recommendation_feedback_sql()[1])
    )
    skill_sql = [sql for sql in conn.sql if "FROM vkpi_skill_runs sr" in sql]
    assert len(skill_sql) == 2
    for sql in skill_sql:
        assert "server_bound_input_sha256" in sql
        assert "server_bound_output_sha256" in sql
        assert "LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?" in sql
    tool_sql = [sql for sql in conn.sql if "vkpi_agent_tool_run tr" in sql]
    assert len(tool_sql) == 3
    for sql in tool_sql:
        assert "IN ('state_changed', 'external_confirmed')" in sql
        assert "idempotent_noop" not in sql
        assert "tr.inputs_json->>'entity_type','')=action.entity_type" in sql
        assert "tr.inputs_json->>'entity_id','')=action.entity_id" in sql
        assert "tr.inputs_json->'step_inputs'" in sql
        assert "plan.plan_json->tr.step_index->'inputs'" in sql
        assert "tr.inputs_json->>'contract_sha256'" in sql
        assert "action.payload_json->>'contract_sha256'" in sql
        assert "tr.inputs_json->'affected_tables'" in sql
        assert "action.affected_tables_json" in sql


def test_real_feedback_predicate_rejects_note_and_metadata_fixtures() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE feedback (
               recommendation_id INTEGER, feedback_type TEXT,
               created_by_staff_id INTEGER, note TEXT, metadata_json TEXT
           )"""
    )
    rows = [
        (1, "claim", 9, "真实复核", "{}"),
        (2, "claim", 9, "demo feedback", "{}"),
        (3, "shortlist", 9, "业务反馈", '{"synthetic": true}'),
        (4, "reject", 9, "fixture sample", "{}"),
        (5, "create_project", 9, "真实", '{"source": "gtm_weight_feedback"}'),
        (1, "claim", 9, "同一推荐重复", "{}"),
    ]
    conn.executemany("INSERT INTO feedback VALUES (?,?,?,?,?)", rows)
    predicate, params = data_readiness.real_recommendation_feedback_sql("f")
    observed = conn.execute(
        f"SELECT COUNT(DISTINCT f.recommendation_id) FROM feedback f WHERE {predicate}",
        params,
    ).fetchone()[0]
    assert observed == 1
