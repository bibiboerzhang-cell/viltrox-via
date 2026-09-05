"""Offline budget/model normalization; all readers and receipts are stubbed."""
from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain.skills import campaign_plan
from app.domains.marketing_brain.skills.campaign_plan_validation import MAX_CENTS

_INPUT = {"product": "Example lens family", "market": "US", "budget_cents": 101, "goal": "launch"}
_POOL = [{"id": 7, "handle": "actual-creator", "platform": "youtube", "viltrox_fit_score": 70}]
_MODEL = {
    "_model": "offline-model",
    "budget_allocation": [{"bucket": "creator_fees", "pct": 1, "amount_cents": 101}],
    "creator_mix": [{"tier": "micro", "share": 1, "count": 1, "sample_creators": [{"id": 7}]}],
    "timeline": [{"phase": "seed", "week": 1, "focus": "Review draft"}],
    "content_angles": [{"angle": "Example angle", "why": "Draft reasoning", "market_signal": "Example signal"}],
}


@pytest.fixture(autouse=True)
def fake_boundaries(monkeypatch):
    from app.domains.marketing_brain import skill_registry

    monkeypatch.setattr(campaign_plan, "_candidate_pool", lambda *_: {"status": "ready", "items": deepcopy(_POOL)})
    monkeypatch.setattr(campaign_plan, "_market_signals", lambda: {"status": "ok", "coverage": "1/5", "sections": {
        "competitor_moves": {"items": [{"brand": "Example", "signal_type": "launch"}]},
    }})
    receipts = []
    monkeypatch.setattr(skill_registry, "record_skill_run", lambda **kwargs: receipts.append(deepcopy(kwargs)))
    return receipts


def _run(*, budget=101, model=None, **kwargs):
    return campaign_plan.run({**_INPUT, "budget_cents": budget, **kwargs}, model_fn=model)


def _assert_unapproved(result):
    assert result["status"] == "ok"
    assert result["planning_readiness"]["executable"] is False
    assert result["planning_readiness"]["approval_status"] == "not_requested"
    assert result["planning_readiness"]["claim_status"] == "descriptive_only"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("budget", [True, False, 1.0, 1.1, -1, float("nan"), float("inf"), float("-inf"),
                                   "unknown", "1.1", "1e2", "NaN", [], {}, MAX_CENTS + 1, 10 ** 400])
def test_invalid_budget_is_unknown_not_zero_and_skips_model(budget, fake_boundaries):
    result = _run(budget=budget, model=lambda _: pytest.fail("invalid input must not invoke model"))
    _assert_unapproved(result)
    assert result["meta"]["budget_cents"] is None
    assert result["meta"]["budget_validation"] == {
        "input_status": "invalid", "allocation_status": "unavailable",
        "allocated_cents": None, "unallocated_cents": None, "warnings": [],
    }
    assert all(row["amount_cents"] is None for row in result["plan"]["budget_allocation"])
    assert result["planning_readiness"]["status"] == "needs_inputs"
    assert result["meta"]["model_cost"] == {"status": "not_applicable", "source": "rule_only"}
    assert "model_skipped_needs_inputs" in result["meta"]["output_warnings"]
    assert fake_boundaries[0]["input_schema"]["budget_cents"] is None


@pytest.mark.parametrize("budget", [None, "", "   "])
def test_missing_budget_is_distinct_from_invalid_or_confirmed_zero(budget):
    result = _run(budget=budget, model=lambda _: pytest.fail("missing budget must not invoke model"))
    assert result["meta"]["budget_validation"]["input_status"] == "missing"
    assert result["meta"]["budget_cents"] is None


def test_confirmed_zero_is_known_but_still_needs_inputs_without_model():
    result = _run(budget=0, model=lambda _: pytest.fail("zero budget must not invoke model"))
    _assert_unapproved(result)
    assert result["meta"]["budget_cents"] == 0
    assert result["meta"]["budget_validation"]["input_status"] == "valid"
    assert result["meta"]["budget_validation"]["allocated_cents"] == 0
    assert result["meta"]["budget_validation"]["unallocated_cents"] == 0
    assert all(row["amount_cents"] == 0 for row in result["plan"]["budget_allocation"])


def test_unknown_goal_skips_model_without_rewriting_budget():
    result = _run(goal="unsupported", model=lambda _: pytest.fail("needs_inputs must not invoke model"))
    assert result["planning_readiness"]["status"] == "needs_inputs"
    assert result["meta"]["budget_cents"] == 101
    assert result["meta"]["model_cost"]["status"] == "not_applicable"


@pytest.mark.parametrize("goal", ["awareness", "launch", "conversion"])
@pytest.mark.parametrize("budget", [0, 1, 2, 3, 7, 101, 100_001, MAX_CENTS])
def test_rule_allocation_is_exact_bounded_and_deterministic(goal, budget):
    first = _run(budget=budget, goal=goal)
    second = _run(budget=budget, goal=goal)
    _assert_unapproved(first)
    rows = first["plan"]["budget_allocation"]
    assert rows == second["plan"]["budget_allocation"]
    assert all(type(row["amount_cents"]) is int and row["amount_cents"] >= 0 for row in rows)
    assert sum(row["amount_cents"] for row in rows) == budget
    assert first["meta"]["budget_validation"]["unallocated_cents"] == 0


def test_decimal_integer_string_is_normalized_without_float_conversion():
    result = _run(budget=" 00101 ")
    assert result["meta"]["budget_cents"] == 101
    assert sum(row["amount_cents"] for row in result["plan"]["budget_allocation"]) == 101


@pytest.mark.parametrize("allocation", [
    None, 7, "budget", {}, [], [None], [{"bucket": "creator_fees"}],
    [{"bucket": "creator_fees", "amount_cents": None}],
    [{"bucket": "creator_fees", "amount_cents": -1}],
    [{"bucket": "creator_fees", "amount_cents": True}],
    [{"bucket": "creator_fees", "amount_cents": 10.5}],
    [{"bucket": "creator_fees", "amount_cents": float("nan")}],
    [{"bucket": "creator_fees", "amount_cents": float("inf")}],
    [{"bucket": "creator_fees", "amount_cents": "unknown"}],
    [{"bucket": "", "amount_cents": 1}],
    [{"bucket": "same", "amount_cents": 1}, {"bucket": "same", "amount_cents": 2}],
])
def test_invalid_model_budget_replaced_not_zeroed(allocation):
    result = _run(model=lambda _: {**deepcopy(_MODEL), "budget_allocation": allocation})
    _assert_unapproved(result)
    assert result["meta"]["budget_validation"]["allocation_status"] == "rule_fallback"
    assert result["meta"]["budget_validation"]["warnings"] == ["model_budget_invalid"]
    assert sum(row["amount_cents"] for row in result["plan"]["budget_allocation"]) == 101
    assert result["meta"]["model_cost"]["status"] == "unknown"


def test_model_overspend_is_replaced_with_bounded_rule_allocation():
    model = {**deepcopy(_MODEL), "budget_allocation": [
        {"bucket": "creator_fees", "amount_cents": 100}, {"bucket": "extra", "amount_cents": 100},
    ]}
    result = _run(model=lambda _: model)
    assert "model_budget_over_limit" in result["meta"]["budget_validation"]["warnings"]
    assert sum(row["amount_cents"] for row in result["plan"]["budget_allocation"]) == 101


def test_known_partial_allocation_preserved_with_recomputed_percentages():
    model = {**deepcopy(_MODEL), "budget_allocation": [
        {"bucket": "creator_fees", "amount_cents": "30", "pct": float("nan")},
        {"bucket": "other", "amount_cents": 20, "pct": 100},
    ]}
    result = _run(model=lambda _: model)
    _assert_unapproved(result)
    assert [row["amount_cents"] for row in result["plan"]["budget_allocation"]] == [30, 20]
    assert [row["pct"] for row in result["plan"]["budget_allocation"]] == [round(30 / 101, 8), round(20 / 101, 8)]
    assert result["meta"]["budget_validation"]["allocated_cents"] == 50
    assert result["meta"]["budget_validation"]["unallocated_cents"] == 51


def test_model_candidates_require_pool_identity_and_server_owned_details():
    model = deepcopy(_MODEL)
    model["creator_mix"][0].update({"count": 4, "sample_creators": [
        {"id": 7, "handle": "invented", "platform": "x", "fit": 999, "approved": True},
        {"id": 999, "handle": "not-in-pool"}, {"id": 7}, {"handle": "actual-creator"},
    ]})
    result = _run(model=lambda _: model)
    samples = result["plan"]["creator_mix"][0]["sample_creators"]
    assert [{key: value for key, value in row.items() if key != "evidence"} for row in samples] == [
        {"id": 7, "handle": "actual-creator", "platform": "youtube", "fit": 70},
    ]
    assert samples[0]["evidence"]["schema_version"] == "campaign_candidate_evidence.v1"
    assert samples[0]["evidence"]["status"] == "unknown"
    assert samples[0]["evidence"]["match_status"] == "unverified"
    assert samples[0]["evidence"]["facts"] == []
    assert "model_candidates_rejected" in result["meta"]["output_warnings"]
    assert _POOL == [{"id": 7, "handle": "actual-creator", "platform": "youtube", "viltrox_fit_score": 70}]


@pytest.mark.parametrize(("tiers", "shares"), [
    (["micro", "micro"], [0.5, 0.5]),
    (["micro", "nano"], [0.6, 0.6]),
    (["micro", "nano"], [0.4, 0.4]),
    (["micro", "nano"], [0, 0]),
    (["micro", "nano"], [0.4, 0.600000002]),
    (["micro", "nano"], [float("inf"), 0]),
    (["micro", "nano"], [float("nan"), 1]),
    (["micro", "nano"], [True, 0]),
])
def test_invalid_tier_or_share_combination_replaces_entire_mix(tiers, shares):
    model = deepcopy(_MODEL)
    model["creator_mix"] = [{"tier": tier, "share": share, "count": 1, "sample_creators": []}
                            for tier, share in zip(tiers, shares)]
    result = _run(model=lambda _: model)
    _assert_unapproved(result)
    assert result["plan"]["creator_mix"] == _run()["plan"]["creator_mix"]
    assert "model_creator_mix_invalid" in result["meta"]["output_warnings"]


@pytest.mark.parametrize("shares", [[0.1, 0.9], [0.4, 0.6000000005]])
def test_unique_tiers_with_share_sum_within_one_billionth_are_retained(shares):
    model = deepcopy(_MODEL)
    model["creator_mix"] = [{"tier": tier, "share": share, "count": 1, "sample_creators": []}
                            for tier, share in zip(["micro", "nano"], shares)]
    result = _run(model=lambda _: model)
    expected_coverage = {
        "planned_count": 1, "unique_sample_count": 0, "status": "empty",
        "gaps": [{"code": "candidate_samples_insufficient", "message": "本梯队仅展示 0 个唯一候选，少于拟定的 1 人；人数提议不是已确认合作人数。"}],
    }
    assert result["plan"]["creator_mix"] == [
        {**row, "candidate_coverage": expected_coverage} for row in model["creator_mix"]
    ]
    assert "model_creator_mix_invalid" not in result["meta"]["output_warnings"]


@pytest.mark.parametrize("field", ["creator_mix", "timeline", "content_angles"])
@pytest.mark.parametrize("value", [7, "wrong", {}, [], [None]])
def test_model_shape_errors_fall_back_per_field_without_crash(field, value):
    result = _run(model=lambda _: {**deepcopy(_MODEL), field: value})
    _assert_unapproved(result)
    assert result["plan"][field]
    assert all(isinstance(row, dict) for row in result["plan"][field])
    assert f"model_{field}_invalid" in result["meta"]["output_warnings"]


@pytest.mark.parametrize("model", [lambda _: 42, lambda _: {}, lambda _: None])
def test_invoked_invalid_model_output_has_unknown_cost_after_rule_fallback(model, fake_boundaries):
    result = _run(model=model)
    assert result["meta"]["model_used"] == "rule_v0"
    assert result["meta"]["model_cost"] == {"status": "unknown", "source": "injected_model_fn"}
    assert fake_boundaries[0]["retrieved_context"]["model_cost"] == result["meta"]["model_cost"]
    assert fake_boundaries[0]["cost_cents"] == 0  # Legacy column, explicitly not proof of free invocation.


def test_failed_model_invocation_keeps_unknown_cost_and_source_scope():
    def model(ctx):
        ctx["budget_cents"] = MAX_CENTS
        ctx["pool_items"].append({"id": 999, "handle": "invented"})
        raise RuntimeError("offline model failed after invocation")

    result = _run(model=model)
    _assert_unapproved(result)
    assert result["meta"]["budget_cents"] == 101
    assert result["meta"]["model_cost"]["status"] == "unknown"
    assert "model_call_failed" in result["meta"]["output_warnings"]
    assert sum(row["amount_cents"] for row in result["plan"]["budget_allocation"]) == 101
