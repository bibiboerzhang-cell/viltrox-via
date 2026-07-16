from app.domains.memory import learning_signals


class _Cursor:
    def fetchone(self):
        return {"n": 1}


class _PercentSafeConn:
    def execute(self, sql, params=()):
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
        "executed_agent_tool_runs": value,
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
        "executed_agent_tool_runs": 19,
    }
    assert learning_signals._maturity_from_truth(almost) == "warming"
    assert learning_signals._maturity_from_truth({**almost, "executed_agent_tool_runs": 20}) == "learning"


def test_verified_learning_filters_bind_sql_wildcards(monkeypatch) -> None:
    monkeypatch.setattr(learning_signals, "table_exists", lambda _table: True)
    monkeypatch.setattr(learning_signals, "get_conn", lambda: _PercentSafeConn())

    truth = learning_signals._verified_learning_evidence()

    assert set(truth.values()) == {1}
