"""Skill【campaign_plan_v1】单测 —— 形状符合 OUTPUT_SCHEMA + record 被调 + eval 能跑。

策略(hermetic,零真 LLM / 零真 DB 写业务表):
  - monkeypatch 边界:market_brain.build_daily_brief / kol.pool.list_pool 注假数据;
    skill_registry.record_skill_run 换成 spy,断言被调一次且带正确 skill_name/version;
  - 断言 run 输出 plan 四段非空 + budget_allocation 合计 = 总预算 + risks 非空;
  - model_fn 注入路径:返回自定义策略 → model_used 走注入;抛错 → 回落规则启发式;
  - record=False 时 record_skill_run 不被调;
  - run_eval 跑 EVAL_CASES 全绿(纯结构打分,record=False / model_fn=None)。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain import evals  # noqa: E402
from app.domains.marketing_brain.skills import campaign_plan  # noqa: E402


# --- 假边界 -----------------------------------------------------------------
_FAKE_POOL = {
    "items": [
        {"id": 1, "handle": "alpha", "platform": "youtube", "viltrox_fit_score": 88},
        {"id": 2, "handle": "bravo", "platform": "instagram", "viltrox_fit_score": 81},
        {"id": 3, "handle": "charlie", "platform": "tiktok", "viltrox_fit_score": 74},
        {"id": 4, "handle": "delta", "platform": "youtube", "viltrox_fit_score": 70},
    ],
    "status": "ok",
}

_FAKE_BRIEF = {
    "status": "ok",
    "coverage": "3/5",
    "sections": {
        "competitor_moves": {"items": [{"brand": "Sony", "signal_type": "price_drop"}]},
        "opportunities": {"items": [{"window": "Q3"}]},
        "today_actions": {"items": [{"title": "push 85mm", "why": "demand spike"}]},
    },
}


def _patch_boundaries(monkeypatch, *, pool=_FAKE_POOL, brief=_FAKE_BRIEF):
    """注假 pool/signal 边界 + record spy。返回 spy 调用记录 list。"""
    from app.domains.kol import pool as pool_mod
    from app.domains.market import market_brain
    from app.domains.marketing_brain import skill_registry

    monkeypatch.setattr(pool_mod, "list_pool", lambda *a, **k: dict(pool))
    monkeypatch.setattr(market_brain, "build_daily_brief", lambda *a, **k: dict(brief))

    calls: list[dict] = []

    def _spy_record(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "run_id": 999}

    monkeypatch.setattr(skill_registry, "record_skill_run", _spy_record)
    return calls


# --- 形状 -------------------------------------------------------------------
def _assert_output_schema(out: dict):
    assert out["status"] == "ok"
    plan = out["plan"]
    for key in ("creator_mix", "budget_allocation", "timeline", "content_angles"):
        assert isinstance(plan[key], list) and plan[key], f"plan.{key} 应为非空 list"
    assert isinstance(out["risks"], list) and out["risks"]
    assert "meta" in out


def test_run_output_matches_schema(monkeypatch):
    _patch_boundaries(monkeypatch)
    out = campaign_plan.run(
        {"product": "viltrox-af-85mm", "market": "US", "budget_cents": 500_000, "goal": "awareness"}
    )
    _assert_output_schema(out)
    # 创作者梯队 share 合计 ~1.0;sample 从假池切到(只读现成 fit)。
    shares = sum(c["share"] for c in out["plan"]["creator_mix"])
    assert abs(shares - 1.0) < 0.01
    # 预算分配合计 = 总预算(末桶吃余数)。
    total = sum(b["amount_cents"] for b in out["plan"]["budget_allocation"])
    assert total == 500_000
    # 内容角度锚定真实信号(competitor_moves 注了 Sony)。
    signals = [a["market_signal"] for a in out["plan"]["content_angles"]]
    assert any("Sony" in str(s) for s in signals)


def test_record_called_with_skill_identity(monkeypatch):
    calls = _patch_boundaries(monkeypatch)
    campaign_plan.run(
        {"product": "viltrox-af-35mm", "market": "EU", "budget_cents": 200_000, "goal": "conversion"}
    )
    assert len(calls) == 1
    rec = calls[0]
    assert rec["skill_name"] == "campaign_plan"
    assert rec["skill_version"] == "v1"
    assert rec["model_used"] == "rule_v0"
    assert isinstance(rec["output"], dict) and "plan" in rec["output"]
    assert rec["cost_cents"] == 0  # 默认不真烧 LLM


def test_record_skipped_when_record_false(monkeypatch):
    calls = _patch_boundaries(monkeypatch)
    campaign_plan.run(
        {"product": "viltrox-af-27mm", "market": "CN", "budget_cents": 100_000},
        record=False,
    )
    assert calls == []


def test_missing_product_errors(monkeypatch):
    calls = _patch_boundaries(monkeypatch)
    out = campaign_plan.run({"market": "US", "budget_cents": 100_000})
    assert out["status"] == "error"
    assert calls == []  # 校验失败不落账


def test_zero_budget_raises_risk(monkeypatch):
    _patch_boundaries(monkeypatch)
    out = campaign_plan.run({"product": "viltrox-af-85mm", "budget_cents": 0})
    risks = [r["risk"] for r in out["risks"]]
    assert any("预算" in r for r in risks)


def test_model_fn_injection_used(monkeypatch):
    _patch_boundaries(monkeypatch)
    custom = {
        "creator_mix": [{"tier": "mega", "share": 1.0, "count": 5, "sample_creators": []}],
        "budget_allocation": [{"bucket": "creator_fees", "pct": 1.0, "amount_cents": 300_000}],
        "timeline": [{"phase": "seed", "week": 1, "focus": "x"}],
        "content_angles": [{"angle": "custom", "why": "y", "market_signal": "z"}],
        "_model": "fake-strategist",
    }
    out = campaign_plan.run(
        {"product": "viltrox-af-85mm", "budget_cents": 300_000},
        model_fn=lambda ctx: custom,
    )
    _assert_output_schema(out)
    assert out["meta"]["model_used"] == "fake-strategist"
    assert out["plan"]["content_angles"][0]["angle"] == "custom"


def test_model_fn_error_falls_back_to_rules(monkeypatch):
    _patch_boundaries(monkeypatch)

    def _boom(ctx):
        raise RuntimeError("model down")

    out = campaign_plan.run(
        {"product": "viltrox-af-85mm", "budget_cents": 300_000},
        model_fn=_boom,
    )
    _assert_output_schema(out)
    assert out["meta"]["model_used"] == "rule_v0"  # 回落规则


def test_boundaries_unavailable_still_returns_plan(monkeypatch):
    """pool/signal 服务挂掉(抛错)→ skill 仍出可执行蓝图,带空池/无信号风险。"""
    from app.domains.kol import pool as pool_mod
    from app.domains.market import market_brain
    from app.domains.marketing_brain import skill_registry

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pool_mod, "list_pool", _boom)
    monkeypatch.setattr(market_brain, "build_daily_brief", _boom)
    monkeypatch.setattr(skill_registry, "record_skill_run", lambda **k: {"status": "ok"})

    out = campaign_plan.run({"product": "viltrox-af-85mm", "budget_cents": 500_000})
    _assert_output_schema(out)
    risks = [r["risk"] for r in out["risks"]]
    assert any("候选创作者池" in r for r in risks)


def test_eval_runs_green(monkeypatch):
    _patch_boundaries(monkeypatch)
    cases = campaign_plan.build_eval_cases()
    assert cases, "EVAL_CASES 应非空"
    report = evals.run_eval(campaign_plan._eval_skill_fn, cases, suite="campaign_plan_v1")
    assert report.total == len(cases)
    assert report.hit_rate == 1.0  # 结构打分全绿
    assert report.avg_score == 1.0
