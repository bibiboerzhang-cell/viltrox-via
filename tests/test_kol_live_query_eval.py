"""One-shot evaluation contracts with an in-memory ledger and fake providers only."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Ledger:
    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.raw.executescript("""
            CREATE TABLE vkpi_provider_budget_caps (
                scope TEXT PRIMARY KEY, cap_usd REAL, current_spend REAL DEFAULT 0,
                hard_stop_at REAL DEFAULT 1, reset_at TEXT, fallback_action TEXT,
                metadata_json TEXT
            );
            CREATE TABLE vkpi_llm_budget_reservations (
                reservation_key TEXT PRIMARY KEY, cost_scope TEXT, state TEXT
            );
            INSERT INTO vkpi_provider_budget_caps(scope,cap_usd,current_spend)
            VALUES ('monthly_total',5,0.25), ('provider:openai',2,0.125), ('single_call',1,0);
        """)
        self.events = []
        self.scale = 6
        self.fail_activation_ack = False
        self.fail_close = False
        self.activating = False

    def execute(self, sql, params=()):
        self.events.append((sql, params))
        if "information_schema.columns" in sql:
            return _Rows([{"column_name": name, "numeric_scale": self.scale}
                          for name in ("cap_usd", "current_spend")])
        if "SET cap_usd=0.10" in sql:
            self.activating = True
        if "SET cap_usd=0.000001" in sql and self.fail_close:
            raise RuntimeError("opaque close detail not for receipt")
        return self.raw.execute(sql.replace(" FOR UPDATE", ""), params)

    def commit(self):
        self.raw.commit()
        if self.activating and self.fail_activation_ack:
            self.activating = False
            self.fail_activation_ack = False
            raise RuntimeError("opaque activation acknowledgement lost")
        self.activating = False

    def rollback(self):
        self.raw.rollback()

    def row(self, scope):
        row = self.raw.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope,)).fetchone()
        return dict(row) if row else None

    def globals(self):
        return [dict(row) for row in self.raw.execute(
            "SELECT * FROM vkpi_provider_budget_caps WHERE scope NOT LIKE 'cron:%' ORDER BY scope")]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/ops/kol_live_query_eval.py"
    spec = importlib.util.spec_from_file_location("_kol_live_query_eval_under_test", source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cli.ROOT = tmp_path / "fixture-repository"
    cli.BASE = cli.ROOT / "runtime/ops/kol-live-eval"
    for name in cli.SOURCES:
        fixture_source = cli.ROOT / name
        fixture_source.parent.mkdir(parents=True, exist_ok=True)
        fixture_source.write_text("reviewed fixture implementation\n", encoding="utf-8")
    monkeypatch.setattr(cli.time, "time", lambda: 1000)
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "1")
    monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
    config = SimpleNamespace(IS_PRODUCTION=False, DB_RUNTIME_BACKEND="postgres",
                             DB_RUNTIME_URL="postgresql://postgres@127.0.0.1:54329/viltrox2")
    import app.core
    monkeypatch.setattr(app.core, "config", config, raising=False)
    ledger = _Ledger()
    calls = []
    state = {"llm": {"status": "success", "queries": ["street photography tutorials"]},
             "apify": {"status": "completed", "items": []}}

    @contextmanager
    def connection_scope():
        calls.append("db_enter")
        try:
            yield
        finally:
            ledger.rollback()
            calls.append("db_exit")

    def probe(stage):
        def run(*args):
            calls.append(stage)
            value = state[stage]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(*args)
            return value
        return run

    db = ModuleType("app.db.connection")
    db.get_conn = lambda: ledger
    db.db_connection_sync_scope = connection_scope
    monkeypatch.setitem(sys.modules, db.__name__, db)
    for stage in ("llm", "apify"):
        module = ModuleType(f"scripts.ops.kol_live_{stage}_probe")
        module.run_probe = probe(stage)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    original_path = sys.path[:]
    plan = cli.prepare()
    fixture = SimpleNamespace(cli=cli, ledger=ledger, calls=calls, state=state, config=config,
                              plan=plan, approval=plan.parent.name)
    yield fixture
    sys.path[:] = original_path
    ledger.raw.close()


def _run(h):
    return h.cli.execute(h.plan, h.approval)


def _replanned(h, **changes):
    plan = json.loads(h.plan.read_text()) | changes
    plan_id = h.cli.digest(plan)
    target = h.cli.BASE / plan_id / "plan.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    h.cli.save(target, plan)
    return target, plan_id


def test_prepare_is_offline_and_hash_binds_exact_contract(harness):
    h = harness
    plan, plan_id = h.cli.validate_plan(h.plan, h.approval)
    assert plan_id == h.cli.digest(plan) == h.plan.parent.name
    assert plan["expires_at"] - plan["created_at"] == 900
    assert plan["implementation"] == h.cli.implementation()
    assert plan["llm_scope_cap_usd"] == plan["apify_max_total_charge_usd"] == "0.10"
    assert plan["llm_max_calls"] == plan["apify_max_starts"] == 1
    assert plan["production_authorized"] is False
    assert h.calls == [] and h.ledger.events == []


@pytest.mark.parametrize("changes", [
    {"created_at": 1001}, {"expires_at": 1000}, {"expires_at": 1901},
])
def test_expiry_and_bounded_lifetime_reject_before_any_side_effect(harness, changes):
    h = harness
    path, approval = _replanned(h, **changes)
    with pytest.raises(ValueError, match="plan_expired_or_invalid"):
        h.cli.execute(path, approval)
    assert h.calls == [] and h.ledger.events == []
    assert not (path.parent / "execution.started").exists()


def test_source_and_approval_changes_reject_before_any_side_effect(harness):
    h = harness
    with pytest.raises(ValueError, match="plan_authorization_mismatch"):
        h.cli.execute(h.plan, "unapproved")
    (h.cli.ROOT / h.cli.SOURCES[1]).write_text("changed implementation", encoding="utf-8")
    with pytest.raises(ValueError, match="plan_contract_or_source_changed"):
        _run(h)
    assert h.calls == [] and h.ledger.events == []


def test_rehashed_contract_change_does_not_expand_calls(harness):
    h = harness
    path, approval = _replanned(h, llm_max_calls=2)
    with pytest.raises(ValueError, match="plan_contract_or_source_changed"):
        h.cli.execute(path, approval)
    assert h.calls == []


@pytest.mark.parametrize("attribute,value,error", [
    ("IS_PRODUCTION", True, "local_postgres_evaluation_only"),
    ("DB_RUNTIME_BACKEND", "sqlite", "local_postgres_evaluation_only"),
    ("DB_RUNTIME_URL", "postgresql://postgres@127.0.0.1:5432/viltrox2", "evaluation_database_target_mismatch"),
    ("DB_RUNTIME_URL", "postgresql://postgres@127.0.0.1:54329/other", "evaluation_database_target_mismatch"),
])
def test_only_exact_local_database_is_accepted(harness, attribute, value, error):
    h = harness
    setattr(h.config, attribute, value)
    with pytest.raises(RuntimeError, match=error):
        _run(h)
    assert h.calls == [] and h.ledger.events == []


def test_success_closes_only_its_budget_and_replay_cannot_call_again(harness):
    h = harness
    global_before = h.ledger.globals()
    report = _run(h)
    assert report["status"] == "completed"
    assert h.calls == ["db_enter", "llm", "apify", "db_exit"]
    assert report["claim_status"] == "descriptive_only"
    assert report["production_authorized"] is False
    assert report["business_inventory_reads"] == report["business_inventory_writes"] == 0
    assert report["budget_opened"] is True
    assert report["budget_closed"]["cap_usd"] == 0.000001
    assert h.ledger.globals() == global_before
    receipt = h.plan.parent / "receipt.json"
    previous = receipt.read_text()
    assert json.loads(previous) == report
    with pytest.raises(FileExistsError):
        _run(h)
    assert receipt.read_text() == previous
    assert h.calls == ["db_enter", "llm", "apify", "db_exit"]


@pytest.mark.parametrize("stage,value,status", [
    ("llm", {"status": "unknown", "queries": []}, "stopped"),
    ("llm", RuntimeError("opaque provider detail not for receipt"), "stopped"),
    ("apify", {"status": "partial", "items": []}, "partial"),
    ("apify", RuntimeError("opaque provider detail not for receipt"), "stopped"),
])
def test_stage_failures_stop_or_report_partial_and_always_close(harness, stage, value, status):
    h = harness
    h.state[stage] = value
    report = _run(h)
    assert report["status"] == status
    assert report["budget_closed"]["cap_usd"] == 0.000001
    if stage == "llm":
        assert "apify" not in h.calls
    assert "opaque provider detail" not in json.dumps(report)


def test_close_preserves_known_spend_and_unknown_reservation(harness):
    h = harness
    global_before = h.ledger.globals()

    def unknown(*_args):
        h.ledger.raw.execute("UPDATE vkpi_provider_budget_caps SET current_spend=?,reset_at=? WHERE scope=?",
                             (0.000033, "2099-01-01", h.cli.SCOPE))
        h.ledger.raw.execute("INSERT INTO vkpi_llm_budget_reservations VALUES (?,?,?)",
                             ("uncertain-one-shot", h.cli.SCOPE, "unknown"))
        h.ledger.commit()
        return {"status": "unknown", "queries": []}

    h.state["llm"] = unknown
    report = _run(h)
    assert report["status"] == "stopped"
    assert report["budget_closed"]["current_spend"] == 0.000033
    assert report["budget_closed"]["reset_at"] == "2099-01-01"
    assert h.ledger.raw.execute("SELECT state FROM vkpi_llm_budget_reservations").fetchone()[0] == "unknown"
    assert h.ledger.globals() == global_before


def test_activation_acknowledgement_failure_still_closes_scope(harness):
    h = harness
    h.ledger.fail_activation_ack = True
    report = _run(h)
    assert report["status"] == "stopped"
    assert h.calls == ["db_enter", "db_exit"]
    assert report["budget_closed"]["cap_usd"] == 0.000001
    assert h.ledger.row(h.cli.SCOPE)["cap_usd"] == 0.000001
    assert "opaque activation" not in json.dumps(report)


def test_close_failure_is_not_reported_closed_and_requires_reconciliation(harness):
    h = harness
    h.ledger.fail_close = True
    report = _run(h)
    assert report["status"] == "stopped"
    assert report["budget_requires_reconciliation"] is True
    assert report["budget_close_error_type"] == "RuntimeError"
    assert "budget_closed" not in report
    assert "opaque close detail" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        _run(h)


@pytest.mark.parametrize("condition,error", [
    ("scale", "micro_usd_schema_required"),
    ("open", "evaluation_scope_not_closed_requires_reconciliation"),
    ("unknown", "prior_evaluation_charge_unresolved"),
])
def test_incompatible_or_unresolved_budget_refuses_before_providers(harness, condition, error):
    h = harness
    if condition == "scale":
        h.ledger.scale = 2
    else:
        h.ledger.raw.execute("INSERT INTO vkpi_provider_budget_caps(scope,cap_usd) VALUES (?,?)",
                             (h.cli.SCOPE, 0.10 if condition == "open" else 0.000001))
        if condition == "unknown":
            h.ledger.raw.execute("INSERT INTO vkpi_llm_budget_reservations VALUES (?,?,?)",
                                 ("previous", h.cli.SCOPE, "unknown"))
        h.ledger.commit()
    before = h.ledger.row(h.cli.SCOPE)
    report = _run(h)
    assert report["status"] == "stopped" and report["error_code"] == error
    assert h.calls == ["db_enter", "db_exit"]
    assert "budget_opened" not in report
    assert h.ledger.row(h.cli.SCOPE) == before
