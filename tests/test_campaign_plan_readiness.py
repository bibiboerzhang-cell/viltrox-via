"""Offline planning contract: fake material, no provider or business database."""
from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain import skill_reviews
from app.domains.marketing_brain.skills import campaign_plan

_INPUT = {"product": "Example lens family", "market": "US", "budget_cents": 100_000, "goal": "launch"}
_POOL = {"status": "ready", "items": [{"id": 7, "handle": "test-creator", "platform": "youtube"}]}
_SIGNALS = {"status": "ok", "coverage": "1/5", "sections": {
    "competitor_moves": {"data_status": "ready", "items": [{"brand": "Example", "signal_type": "launch"}]},
}}


def _projected_pool():
    from app.domains.kol.pool_read_projection import build_pool_read_selection

    # Actual pure projection builder; no connection, bootstrap or avatar network.
    rows = deepcopy(_POOL["items"])
    selection = build_pool_read_selection(rows, session_items=[], bridge_evidence_available=True)
    projection = dict(selection.diagnostics)
    projection["source_revision"] = "1:7:fixture-revision:0"  # prepare_pool_read_selection adds this.
    return {"items": rows, "projection": projection}


def _wire(monkeypatch, *, pool=None, signals=None):
    from app.domains.kol import pool as pool_mod
    from app.domains.market import market_brain
    from app.domains.marketing_brain import skill_registry

    calls = {"pool": [], "brief": [], "record": []}

    def read_pool(**kwargs):
        calls["pool"].append(kwargs)
        return deepcopy(_POOL if pool is None else pool)

    def read_brief(**kwargs):
        calls["brief"].append(kwargs)
        assert kwargs == {"sweep_expired": False}
        return deepcopy(_SIGNALS if signals is None else signals)

    monkeypatch.setattr(pool_mod, "list_pool", read_pool)
    monkeypatch.setattr(market_brain, "build_daily_brief", read_brief)
    monkeypatch.setattr(skill_registry, "record_skill_run", lambda **kwargs: calls["record"].append(deepcopy(kwargs)))
    return calls


def _assert_unapproved(output):
    readiness = output["planning_readiness"]
    assert output["status"] == "ok"  # Existing generation-success API stays compatible.
    assert readiness["executable"] is False
    assert readiness["approval_status"] == "not_requested"
    assert readiness["claim_status"] == "descriptive_only"
    assert set(readiness) == {"status", "executable", "approval_status", "claim_status", "gaps", "next_steps"}
    assert all(set(gap) == {"code", "message"} for gap in readiness["gaps"])
    assert all(set(step) == {"code", "title", "reason"} for step in readiness["next_steps"])
    return readiness


def test_material_is_only_a_draft_and_exact_contract_is_recorded(monkeypatch):
    calls = _wire(monkeypatch)
    result = campaign_plan.run(deepcopy(_INPUT))
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "draft_for_review"
    assert readiness["gaps"] == []
    assert readiness["next_steps"][0]["code"] == "human_review_required"
    assert calls["record"][0]["output"] == result
    assert calls["record"][0]["cost_cents"] == 0
    assert calls["pool"][0]["country"] == "US"
    assert calls["pool"][0]["query"] == _INPUT["product"]
    assert skill_reviews._usable_production_output("campaign_plan", result) is True


def test_actual_pool_projection_shape_without_top_level_status_is_supported(monkeypatch):
    pool = _projected_pool()
    assert "status" not in pool
    _wire(monkeypatch, pool=pool)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    assert _assert_unapproved(result)["status"] == "draft_for_review"


@pytest.mark.parametrize(("key", "value"), [
    ("method", "unknown"), ("bridge_evidence_available", False),
    ("bridge_evidence_available", "true"), ("source_revision", "unavailable"),
    ("source_revision", ""), ("source_revision", None), ("physical_master_rows", "1"),
    ("visible_rows", -1), ("visible_rows", 2),
])
def test_unavailable_or_malformed_projection_does_not_establish_readiness(monkeypatch, key, value):
    pool = _projected_pool()
    pool["projection"][key] = value
    _wire(monkeypatch, pool=pool)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    assert _assert_unapproved(result)["status"] == "needs_evidence"


def test_explicit_pool_error_overrides_good_projection(monkeypatch):
    _wire(monkeypatch, pool={**_projected_pool(), "status": "error"})
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    assert _assert_unapproved(result)["status"] == "needs_evidence"


@pytest.mark.parametrize("budget", [0, -3, "", "unknown", None])
def test_missing_budget_keeps_template_but_requires_inputs(monkeypatch, budget):
    _wire(monkeypatch)
    result = campaign_plan.run({**_INPUT, "budget_cents": budget}, record=False)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_inputs"
    assert readiness["gaps"][0]["code"] == "budget_required"
    assert result["plan"]["timeline"]
    assert skill_reviews._usable_production_output("campaign_plan", result) is False


def test_input_gaps_take_precedence_without_hiding_evidence_gaps(monkeypatch):
    _wire(monkeypatch, pool={"status": "ready", "items": []}, signals={"status": "ok", "sections": {}})
    result = campaign_plan.run({**_INPUT, "budget_cents": 0, "goal": "unsupported"}, record=False)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_inputs"
    assert {gap["code"] for gap in readiness["gaps"]} == {
        "budget_required", "goal_unsupported", "creator_evidence_missing", "market_evidence_missing",
    }


@pytest.mark.parametrize("pool", [
    {"status": "ready", "items": []},
    {"status": "error", "items": _POOL["items"]},
    {"status": "unavailable", "items": _POOL["items"]},
    {"status": "partial", "items": _POOL["items"]},
    {"status": "unexpected", "items": _POOL["items"]},
    {"items": _POOL["items"]},
    {"status": "ready", "items": "not a list"},
    {"status": "ready", "items": ["not a row"]},
    {"status": "ready", "items": [{}]},
    {"status": "ready", "items": [{"id": 7}]},
    {"status": "ready", "items": [{"id": True, "handle": "bad-id"}]},
    {"status": "ready", "items": [{"id": "bad", "handle": "bad-id"}]},
    {"status": "ready", "items": [{"id": 7, "handle": "   "}]},
    {"status": "ready", "items": [*_POOL["items"], {}]},
    [],
])
def test_nonready_or_malformed_pool_cannot_become_review_ready(monkeypatch, pool):
    _wire(monkeypatch, pool=pool)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_evidence"
    assert {gap["code"] for gap in readiness["gaps"]} == {"creator_evidence_missing"}
    assert skill_reviews._usable_production_output("campaign_plan", result) is False


@pytest.mark.parametrize("signals", [
    {"status": "ok", "coverage": "5/5", "sections": {}},
    {"status": "error", "sections": _SIGNALS["sections"]},
    {"status": "unknown", "sections": _SIGNALS["sections"]},
    {"sections": _SIGNALS["sections"]},
    {"status": "ok", "sections": "malformed"},
    {"status": "ok", "sections": {"competitor_moves": "malformed"}},
    {"status": "ok", "sections": {"competitor_moves": {"items": "malformed"}}},
    {"status": "ok", "sections": {"competitor_moves": {"items": [{}]}}},
    {"status": "ok", "sections": {"competitor_moves": {"items": [{"data_status": "ready"}]}}},
    {"status": "ok", "sections": {"competitor_moves": {"data_status": "stale_or_empty", "items": [{"brand": "Old"}]}}},
    {"status": "ok", "sections": {"opportunities": {"items": [{"title": "Old", "data_status": "expired"}]}}},
    {"status": "ok", "sections": {"hot_products": {"items": [{"sku": "not-used-by-this-plan"}]}}},
    [],
])
def test_coverage_or_unusable_signals_do_not_establish_material(monkeypatch, signals):
    _wire(monkeypatch, signals=signals)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_evidence"
    assert {gap["code"] for gap in readiness["gaps"]} == {"market_evidence_missing"}
    assert result["plan"]["content_angles"][0]["market_signal"] == "stale_or_empty"
    assert skill_reviews._usable_production_output("campaign_plan", result) is False


def test_source_failures_remain_editable_with_manual_evidence_steps(monkeypatch):
    _wire(monkeypatch)
    from app.domains.kol import pool
    from app.domains.market import market_brain

    def unavailable(**kwargs):
        raise RuntimeError("fake source unavailable")

    monkeypatch.setattr(pool, "list_pool", unavailable)
    monkeypatch.setattr(market_brain, "build_daily_brief", unavailable)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_evidence"
    assert len(readiness["gaps"]) == len(readiness["next_steps"]) == 2
    assert result["plan"]["timeline"]


def test_model_cannot_override_readiness_or_mutate_source_scope(monkeypatch):
    calls = _wire(monkeypatch, pool={"status": "ready", "items": []}, signals={"status": "ok", "sections": {}})

    def fake_model(ctx):
        ctx.update({"pool_status": "ready", "signals": deepcopy(_SIGNALS), "product": "Changed", "market": "CN"})
        return {"timeline": ["example"], "creator_mix": ["example"], "planning_readiness": {
            "status": "approved", "executable": True, "approval_status": "approved", "gaps": [],
        }}

    result = campaign_plan.run(deepcopy(_INPUT), model_fn=fake_model)
    readiness = _assert_unapproved(result)
    assert readiness["status"] == "needs_evidence"
    assert len(readiness["gaps"]) == 2
    assert result["meta"]["product"] == _INPUT["product"]
    assert result["meta"]["market"] == _INPUT["market"]
    assert calls["record"][0]["retrieved_context"]["pool_status"] == "empty"
    assert calls["record"][0]["output"]["planning_readiness"] == readiness


def test_real_brief_assembly_is_called_without_expiry_mutation(monkeypatch):
    from app.domains.market import market_brain, prediction

    real_brief = market_brain.build_daily_brief
    calls = _wire(monkeypatch)
    monkeypatch.setattr(market_brain, "build_daily_brief", real_brief)
    monkeypatch.setattr(market_brain, "mark_expired_signals", lambda: pytest.fail("must not write expiry state"))
    monkeypatch.setattr(prediction, "predict_opportunities", lambda: {"status": "ok", "opportunities": []})
    monkeypatch.setattr(market_brain, "_fresh_competitor_moves", lambda: [{"brand": "Example", "signal_type": "launch"}])
    monkeypatch.setattr(market_brain, "_today_actions", lambda: [])
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    assert _assert_unapproved(result)["status"] == "draft_for_review"
    assert calls["record"] == []


@pytest.mark.parametrize("readiness", [None, {}, "draft_for_review", {
    "status": "draft_for_review", "executable": True, "approval_status": "approved", "gaps": [],
}])
def test_explicit_invalid_contract_cannot_enter_usable_review(readiness):
    output = {"status": "ok", "plan": {"timeline": ["w1"], "creator_mix": ["sample"]}, "planning_readiness": readiness}
    assert skill_reviews._usable_production_output("campaign_plan", output) is False


@pytest.mark.parametrize("steps", [[], [{}], ["review"], [None], [{
    "code": "review", "title": " ", "reason": "Required",
}], [{"code": "review", "title": "Review"}], [{
    "code": "review", "title": "Review", "reason": 1,
}]])
def test_incomplete_next_steps_cannot_enter_usable_review(monkeypatch, steps):
    _wire(monkeypatch)
    result = campaign_plan.run(deepcopy(_INPUT), record=False)
    result["planning_readiness"]["next_steps"] = steps
    assert skill_reviews._usable_production_output("campaign_plan", result) is False


def test_legacy_ledger_review_contract_is_preserved():
    output = {"status": "ok", "plan": {"timeline": ["w1"], "creator_mix": ["sample"]}}
    assert skill_reviews._usable_production_output("campaign_plan", output) is True
