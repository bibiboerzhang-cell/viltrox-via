"""Precision review controller tests: fake providers and budget only."""
from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def harness(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/ops/kol_precision_review.py"
    spec = importlib.util.spec_from_file_location("_kol_precision_review_under_test", source)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cli.ROOT = tmp_path / "repository"
    cli.BASE = cli.ROOT / "runtime/ops/kol-live-eval"
    for name in cli.SOURCES:
        fixture_source = cli.ROOT / name
        fixture_source.parent.mkdir(parents=True, exist_ok=True)
        fixture_source.write_text("reviewed fixture\n")
    monkeypatch.setattr(cli.time, "time", lambda: 1000)
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "1")
    monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
    config = SimpleNamespace(IS_PRODUCTION=False, DB_RUNTIME_BACKEND="postgres",
                             DB_RUNTIME_URL="postgresql://postgres@127.0.0.1:54329/viltrox2")
    import app.core
    monkeypatch.setattr(app.core, "config", config, raising=False)
    calls = []
    state = {"llm": {"status": "success", "queries": ["street photography"],
                     "accounting_verified": True, "reservation_state": "settled",
                     "provider_calls_performed": 1}, "close_error": False}

    @contextmanager
    def scope():
        calls.append("db_enter")
        try:
            yield
        finally:
            calls.append("db_exit")

    @contextmanager
    def budget(conn, report):
        assert conn == "fake_connection"
        calls.append("budget_open")
        report["budget_opened"] = True
        try:
            yield
        finally:
            calls.append("budget_close_readback")
            if state["close_error"]:
                report["budget_requires_reconciliation"] = True
                raise RuntimeError("evaluation_budget_close_readback_failed")
            report["budget_closed"] = {"cap_usd": "0.000001", "hard_stop_at": 1}

    def probe(query, output_dir, plan_id):
        calls.append("llm")
        assert query == "US English street photography. Public sample: older video; country unknown."
        assert output_dir == cli.BASE / plan_id
        if isinstance(state["llm"], Exception):
            raise state["llm"]
        return state["llm"]

    db = ModuleType("app.db.connection")
    db.db_connection_sync_scope = scope
    db.get_conn = lambda: "fake_connection"
    monkeypatch.setitem(sys.modules, db.__name__, db)
    llm = ModuleType("scripts.ops.kol_live_llm_probe")
    llm.run_probe = probe
    monkeypatch.setitem(sys.modules, llm.__name__, llm)
    apify = ModuleType("scripts.ops.kol_live_apify_probe")
    apify.run_probe = lambda *_: pytest.fail("Apify must never run in review")
    monkeypatch.setitem(sys.modules, apify.__name__, apify)
    monkeypatch.setattr(cli, "evaluation_budget", budget)
    input_path = tmp_path / "input.txt"
    input_path.write_text("US English street photography. Public sample: older video; country unknown.")
    plan = cli.prepare(input_path)
    original_path = sys.path[:]
    yield SimpleNamespace(cli=cli, calls=calls, state=state, config=config,
                          input_path=input_path, plan=plan, approval=plan.parent.name)
    sys.path[:] = original_path


def replan(h, **changes):
    plan = json.loads(h.plan.read_text()) | changes
    plan_id = h.cli.digest(plan)
    path = h.cli.BASE / plan_id / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    h.cli.save(path, plan)
    return path, plan_id


def test_prepare_is_offline_and_binds_input_contract(harness):
    h = harness
    plan, plan_id = h.cli.validate_plan(h.plan, h.approval)
    assert not h.calls
    assert plan_id == h.approval
    assert plan["input_sha256"] == h.cli._input_hash(h.input_path.read_text())
    assert plan["implementation"] == h.cli.implementation()
    assert plan["review_only"] is True and plan["apify_max_starts"] == 0
    assert plan["llm_max_calls"] == 1 and plan["llm_max_output_tokens"] == 1024


@pytest.mark.parametrize("changes", [
    {"created_at": 1001}, {"expires_at": 1000}, {"expires_at": 1901},
])
def test_expiry_before_any_provider_or_db(harness, changes):
    h = harness
    path, approval = replan(h, **changes)
    with pytest.raises(ValueError, match="plan_expired_or_invalid"):
        h.cli.execute(path, approval)
    assert h.calls == []
    assert not (path.parent / "execution.started").exists()


def test_source_change_and_wrong_approval_are_blocked(harness):
    h = harness
    with pytest.raises(ValueError, match="authorization"):
        h.cli.execute(h.plan, "wrong")
    (h.cli.ROOT / h.cli.SOURCES[2]).write_text("unreviewed change")
    with pytest.raises(ValueError, match="source_changed"):
        h.cli.execute(h.plan, h.approval)
    assert not h.calls


@pytest.mark.parametrize("changes", [
    {"llm_max_calls": 2}, {"apify_max_starts": 1}, {"review_only": False},
    {"llm_binding": "openai/other"}, {"llm_scope_cap_usd": "1"},
    {"llm_max_output_tokens": 2048}, {"llm_reserve_usd": "1"},
])
def test_rehash_cannot_expand_contract(harness, changes):
    h = harness
    path, approval = replan(h, **changes)
    with pytest.raises(ValueError, match="source_changed"):
        h.cli.execute(path, approval)
    assert not h.calls


def test_input_hash_and_size_are_enforced(harness):
    h = harness
    path, approval = replan(h, query="changed public query")
    with pytest.raises(ValueError, match="input_hash"):
        h.cli.execute(path, approval)
    h.input_path.write_text("字" * 683)
    with pytest.raises(ValueError, match="input_out_of_bounds"):
        h.cli.prepare(h.input_path)
    assert not h.calls


def test_single_call_no_apify_finally_closed_and_replay_blocked(harness):
    h = harness
    report = h.cli.execute(h.plan, h.approval)
    assert report["status"] == "completed"
    assert report["apify_starts"] == 0
    assert report["business_inventory_reads"] == report["business_inventory_writes"] == 0
    assert h.calls == ["db_enter", "budget_open", "llm", "budget_close_readback", "db_exit"]
    assert report["budget_closed"]["cap_usd"] == "0.000001"
    receipt = h.plan.parent / "receipt.json"
    assert json.loads(receipt.read_text()) == report
    with pytest.raises(FileExistsError):
        h.cli.execute(h.plan, h.approval)
    assert h.calls.count("llm") == 1
    assert json.loads(receipt.read_text()) == report


@pytest.mark.parametrize("failure", [
    {"status": "cost_accounting_unknown", "provider_calls_performed": 1},
    {"status": "success", "accounting_verified": False},
    RuntimeError("opaque provider URL and secret"),
])
def test_unknown_or_error_stops_without_retry_and_closes(harness, failure):
    h = harness
    h.state["llm"] = failure
    report = h.cli.execute(h.plan, h.approval)
    assert report["status"] == "stopped"
    assert report["budget_closed"]["cap_usd"] == "0.000001"
    assert h.calls.count("llm") == 1
    assert "opaque" not in json.dumps(report)


def test_close_failure_cannot_be_reported_as_completion(harness):
    h = harness
    h.state["close_error"] = True
    report = h.cli.execute(h.plan, h.approval)
    assert report["status"] == "stopped"
    assert report["budget_requires_reconciliation"] is True
    assert report["error_code"] == "evaluation_budget_close_readback_failed"
    assert h.calls.count("llm") == 1


def test_exact_loopback_database_required(harness):
    h = harness
    h.config.DB_RUNTIME_URL = "postgresql://postgres@127.0.0.1:5432/viltrox2"
    with pytest.raises(RuntimeError, match="database_target_mismatch"):
        h.cli.execute(h.plan, h.approval)
    assert not h.calls
