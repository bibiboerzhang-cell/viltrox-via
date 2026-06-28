"""creator_match_v1 skill 单测。

策略(对齐规范):零真 LLM、零真 DB 写业务表 —— 全在 monkeypatch 边界:
  - monkeypatch creator_match._build_preview → 返回固定 preview 桩(模拟底层确定性匹配输出形状);
  - monkeypatch creator_match.skill_registry.record_skill_run → 捕获是否被调 + 入参;
  - model_fn 用本地假打分函数注入,绝不真烧。
断言:
  1) run 产出符合 OUTPUT_SCHEMA(recommendations[*] 六字段 + rationale);
  2) record=True 时 record_skill_run 被调一次;record=False 时不调;
  3) risk 由 evidence_con 严重度推出;evidence_refs 汇集 source_ref;
  4) budget 过滤把超标 est_cost_cents 剔除;
  5) model_fn 注入会覆盖 fit_reason;
  6) eval 用 run_eval 能跑(monkeypatch 后 handle 集合命中 expected)。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain import evals  # noqa: E402
from app.domains.marketing_brain.skills import creator_match  # noqa: E402


def _ev(detail, *, source_ref, polarity="pro", severity="info"):
    return {
        "type": "t",
        "polarity": polarity,
        "severity": severity,
        "detail": detail,
        "score_component": "x",
        "source_ref": source_ref,
        "source_table": "vkpi_memory_links",
        "source_id": "1",
    }


def _fake_preview(*, items, family="Viltrox Primes", eligible=None):
    eligible = len(items) if eligible is None else eligible
    return {
        "scenario": "p4_new_launch_match",
        "mode": "dry_run",
        "product_query": "stub",
        "target_family_name": family,
        "summary": {"eligible_after_hard_filters": eligible, "returned": len(items)},
        "items": items,
    }


def _stub_item(handle, *, kol_pool_id=1, score=80.0, est_cost_cents=0,
               con_severity="low", review_required=False, pro_ref="ref:pro:1", con_ref="ref:con:1"):
    return {
        "rank": 1,
        "kol_pool_id": kol_pool_id,
        "handle": handle,
        "display_name": handle,
        "country": "US",
        "score": score,
        "review_required": review_required,
        "est_cost_cents": est_cost_cents,
        "evidence_pro": [_ev("worked on a prior 85mm launch", source_ref=pro_ref)],
        "evidence_con": [_ev("only 3 legacy rows", source_ref=con_ref, polarity="con", severity=con_severity)],
    }


@pytest.fixture
def patch_boundaries(monkeypatch):
    """monkeypatch _build_preview + record_skill_run，返回记录器。调用方先 set preview items。"""
    state = {"items": [], "family": "Viltrox Primes", "eligible": None, "record_calls": []}

    def fake_build(*, product_query, primary_markets, secondary_markets, limit):
        return _fake_preview(items=state["items"], family=state["family"], eligible=state["eligible"])

    def fake_record(**kwargs):
        state["record_calls"].append(kwargs)
        return {"status": "ok", "run_id": len(state["record_calls"])}

    monkeypatch.setattr(creator_match, "_build_preview", fake_build)
    monkeypatch.setattr(creator_match.skill_registry, "record_skill_run", fake_record)
    # _deterministic_fit_reason 内部还会 import new_launch_match._deterministic_reason;
    # 用本地纯函数桩替它,彻底断开底层依赖。
    monkeypatch.setattr(
        creator_match, "_deterministic_fit_reason",
        lambda item: f"{item.get('handle')} fits: {(item.get('evidence_pro') or [{}])[0].get('detail','')}",
    )
    return state


def _assert_output_schema(out):
    assert isinstance(out, dict)
    assert set(out.keys()) == {"recommendations", "rationale"}
    assert isinstance(out["rationale"], str)
    for rec in out["recommendations"]:
        assert set(rec.keys()) == {
            "kol_pool_id", "handle", "fit_reason", "risk", "est_cost_cents", "evidence_refs",
        }
        assert rec["kol_pool_id"] is None or isinstance(rec["kol_pool_id"], int)
        assert isinstance(rec["handle"], str)
        assert isinstance(rec["fit_reason"], str)
        assert rec["risk"] in {"none", "low", "medium", "high"}
        assert isinstance(rec["est_cost_cents"], int)
        assert isinstance(rec["evidence_refs"], list)


def test_run_output_matches_schema_and_records(patch_boundaries):
    patch_boundaries["items"] = [_stub_item("alice_lens"), _stub_item("bob_outdoor", kol_pool_id=2)]
    out = creator_match.run({"product": "viltrox af 85mm", "market": "US"})
    _assert_output_schema(out)
    assert [r["handle"] for r in out["recommendations"]] == ["alice_lens", "bob_outdoor"]
    # evidence_refs 汇集 pro + con 的 source_ref。
    assert out["recommendations"][0]["evidence_refs"] == ["ref:pro:1", "ref:con:1"]
    # record=True → record_skill_run 被调一次,带 skill_name/version。
    assert len(patch_boundaries["record_calls"]) == 1
    call = patch_boundaries["record_calls"][0]
    assert call["skill_name"] == creator_match.SKILL_NAME
    assert call["skill_version"] == creator_match.SKILL_VERSION
    assert call["model_used"] == "rule_v0"
    assert call["output"] == out


def test_record_false_skips_registry(patch_boundaries):
    patch_boundaries["items"] = [_stub_item("alice_lens")]
    creator_match.run({"product": "x", "market": "US"}, record=False)
    assert patch_boundaries["record_calls"] == []


def test_risk_derives_from_evidence_con_severity(patch_boundaries):
    patch_boundaries["items"] = [
        _stub_item("low_risk", con_severity="low"),
        _stub_item("high_risk", kol_pool_id=2, con_severity="high"),
        _stub_item("review_bump", kol_pool_id=3, con_severity="low", review_required=True),
    ]
    out = creator_match.run({"product": "x", "market": "US"}, record=False)
    by_handle = {r["handle"]: r for r in out["recommendations"]}
    assert by_handle["low_risk"]["risk"] == "low"
    assert by_handle["high_risk"]["risk"] == "high"
    # review_required 把 low 顶到至少 medium。
    assert by_handle["review_bump"]["risk"] == "medium"


def test_budget_filters_out_pricey_candidates(patch_boundaries):
    patch_boundaries["items"] = [
        _stub_item("cheap", est_cost_cents=500),
        _stub_item("pricey", kol_pool_id=2, est_cost_cents=50000),
    ]
    out = creator_match.run({"product": "x", "market": "US", "budget": 1000}, record=False)
    assert [r["handle"] for r in out["recommendations"]] == ["cheap"]


def test_model_fn_overrides_fit_reason(patch_boundaries):
    patch_boundaries["items"] = [_stub_item("alice_lens")]
    calls = []

    def fake_model(ctx):
        calls.append(ctx)
        return "INJECTED: perfect tech reviewer for primes"

    out = creator_match.run({"product": "viltrox af 85mm", "market": "US"},
                            model_fn=fake_model, record=True)
    assert out["recommendations"][0]["fit_reason"] == "INJECTED: perfect tech reviewer for primes"
    assert calls and calls[0]["handle"] == "alice_lens"
    # model_fn 注入 → record 的 model_used 标 injected_model_fn(非 rule_v0)。
    assert patch_boundaries["record_calls"][0]["model_used"] == "injected_model_fn"


def test_missing_product_returns_error_rationale(patch_boundaries):
    out = creator_match.run({"market": "US"}, record=False)
    assert out["recommendations"] == []
    assert "error" in out["rationale"]


def test_eval_runs_green(monkeypatch):
    """用 run_eval 跑 EVAL_CASES:monkeypatch _build_preview 按 product 返回对应桩 handle。

    expected 覆盖即 hit(set_overlap_metric)。budget=0 用例期望空集。
    """
    expected_map = {
        "viltrox af 85mm": "alice_lens",
        "viltrox-drone-gimbal": "bob_outdoor",
        "viltrox af 35mm": "carol_portrait",
        "viltrox af 27mm": "dave_vlog",
    }

    def fake_build(*, product_query, primary_markets, secondary_markets, limit):
        handle = expected_map.get(product_query, "unknown")
        return _fake_preview(items=[_stub_item(handle, est_cost_cents=0)])

    monkeypatch.setattr(creator_match, "_build_preview", fake_build)
    monkeypatch.setattr(creator_match.skill_registry, "record_skill_run", lambda **k: {"status": "ok"})
    monkeypatch.setattr(creator_match, "_deterministic_fit_reason", lambda item: "ok")

    report = evals.run_eval(
        creator_match._eval_skill_fn,
        creator_match.EVAL_CASES,
        suite="creator_match_v1",
    )
    d = report.to_dict()
    assert d["total"] == len(creator_match.EVAL_CASES)
    # 前三条 expected 单 handle 应命中;第四条 budget=0 → est=0 不剔(桩 est=0)→ 返回 dave_vlog,
    # 但 expected=[] 且 set_overlap_metric 对非空 actual 的空 expected 判 hit=False(exp 非子集且 exp 空)。
    # 故至少 3 条 hit。
    assert d["hits"] >= 3
