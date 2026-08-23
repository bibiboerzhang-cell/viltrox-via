"""Skill 驾照真跑闸单测(全 hermetic:零真 DB、零真 LLM)。

覆盖:L1 不调 LLM / L2 且预算放行才调(仅 creator_match 拿 model_fn)/ 预算拒绝回退规则 /
model_fn 次数封顶 / 推荐经 producers 进 action_inbox(幂等 dedupe_key、必审)/ jobs.py 接线。
红线:零触 viltrox_fit_score;绝不调用 evaluate_promotions(不自动晋升)。
"""
from __future__ import annotations

import inspect

import pytest

from app.domains.marketing_brain import skill_license_gate as gate
from app.domains.marketing_brain import skill_orchestrator as so
from app.domains.marketing_brain import skill_registry


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VKPI_SKILL_ORCHESTRATION", "1")
    monkeypatch.delenv(gate.ENV_EST_USD, raising=False)
    monkeypatch.delenv(gate.ENV_MAX_LLM_CALLS, raising=False)
    monkeypatch.setattr(gate, "recent_launch_product",
                        lambda: {"product": "AF 35mm F1.4", "source": "product_launches", "launch_id": 9, "market": "US"})
    # orchestrate_skills 自身的 marketing_brain_skill 预算闸:放行(被测的是 agent_skill 闸)。
    from app.domains.costs import budget_guard

    monkeypatch.setattr(budget_guard, "check_budget", lambda scope, est, require_configured=False: True)


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict] = []

    def fake_dispatch(skill_name, skill_input, *, model_fn=None, record=True, staff=None):
        reason = None
        if model_fn is not None:
            reason = model_fn({"product": skill_input.get("product"), "handle": "lensguy", "country": "US",
                               "score": 88, "evidence_pro": ["shoots 35mm"], "evidence_con": []})
        calls.append({"skill_name": skill_name, "model_fn": model_fn, "input": skill_input})
        return {"status": "ok", "skill_name": skill_name,
                "output": {"recommendations": [
                    {"kol_pool_id": 101, "handle": "lensguy", "fit_reason": reason or "rule reason",
                     "risk": "low", "est_cost_cents": 0, "evidence_refs": [{"type": "video", "id": "v1"}]},
                    {"kol_pool_id": None, "handle": "ghost", "fit_reason": "no id"},
                ], "rationale": "stub"}}

    monkeypatch.setattr(skill_registry, "dispatch_skill", fake_dispatch)
    return calls


@pytest.fixture
def inbox_sink(monkeypatch: pytest.MonkeyPatch):
    sink: list[dict] = []
    from app.domains.actions import inbox

    def fake_persist(suggestions):
        sink.extend(suggestions)
        return len(suggestions)

    monkeypatch.setattr(inbox, "persist_suggestions", fake_persist)
    return sink


def _set_level(monkeypatch: pytest.MonkeyPatch, level: int) -> None:
    from app.domains.agents import autonomy_license

    monkeypatch.setattr(autonomy_license, "current_level",
                        lambda action_type: {"status": "ready", "level": level, "level_label": f"L{level}"})


def test_l1_never_calls_llm(monkeypatch, capture, inbox_sink):
    _set_level(monkeypatch, 1)
    factory_called = []
    out = gate.licensed_auto_orchestrate(record=False, model_fn_factory=lambda: factory_called.append(1) or (lambda c: "x"))
    assert out["status"] == "ok" and out["dry_run"] is True
    assert out["gate"]["license_level"] == 1 and out["gate"]["mode"] == "rule" and out["gate"]["llm_allowed"] is False
    assert factory_called == [] and out["llm_calls"] == 0
    assert [c["model_fn"] for c in capture] == [None]
    assert capture[0]["skill_name"] == "creator_match" and capture[0]["input"]["product"] == "AF 35mm F1.4"
    assert inbox_sink and inbox_sink[0]["uses_llm"] is False


def test_l2_with_budget_calls_llm_only_for_creator_match(monkeypatch, capture, inbox_sink):
    _set_level(monkeypatch, 2)
    monkeypatch.setattr(gate, "budget_allows", lambda est_usd=None: (True, "ok"))
    fake_calls = {"calls": 0}

    def fake_model_fn(ctx):
        fake_calls["calls"] += 1
        return "LLM 理由"

    fake_model_fn.calls = fake_calls
    out = gate.licensed_auto_orchestrate(record=False, model_fn_factory=lambda: fake_model_fn)
    assert out["dry_run"] is False and out["gate"]["mode"] == "llm" and out["gate"]["llm_allowed"] is True
    assert out["llm_calls"] == 1
    assert capture[0]["skill_name"] == "creator_match" and capture[0]["model_fn"] is fake_model_fn
    assert out["inbox_persisted"] == 1
    s = inbox_sink[0]
    assert s["category"] == gate.INBOX_CATEGORY and s["dedupe_key"] == "skill_creator_match:af_35mm_f1_4:101"
    assert s["uses_llm"] is True and s["requires_approval"] is True and s["detail"] == "LLM 理由"
    assert s["entity_type"] == "kol_pool" and s["entity_id"] == "101" and s["payload"]["license_level"] == 2


def test_l2_budget_rejected_falls_back_to_rule(monkeypatch, capture, inbox_sink):
    _set_level(monkeypatch, 2)
    monkeypatch.setattr(gate, "budget_allows", lambda est_usd=None: (False, "scope agent_skill 未配置或已 hard stop"))
    factory_called = []
    out = gate.licensed_auto_orchestrate(record=False, model_fn_factory=lambda: factory_called.append(1) or (lambda c: "x"))
    assert out["dry_run"] is True and out["gate"]["mode"] == "rule_budget_blocked"
    assert "hard stop" in out["gate"]["budget"] and factory_called == []
    assert [c["model_fn"] for c in capture] == [None] and out["llm_calls"] == 0


def test_budget_allows_requires_configured_cap(monkeypatch):
    from app.domains.costs import budget_guard

    seen = {}

    def fake_check(scope, est, *, require_configured=False):
        seen.update({"scope": scope, "est": est, "require_configured": require_configured})
        return False

    monkeypatch.setattr(budget_guard, "check_budget", fake_check)
    ok, why = gate.budget_allows()
    assert ok is False and seen == {"scope": "agent_skill", "est": 0.05, "require_configured": True}
    assert "agent_skill" in why


def test_creator_match_only_dispatch():
    fn = lambda c: "r"  # noqa: E731
    pick = gate.creator_match_only(fn)
    assert pick("creator_match") is fn and pick("campaign_plan") is None and pick("brief_generate") is None


def test_model_fn_by_skill_hook_in_orchestrator(monkeypatch, capture):
    """钩子只加不改:dry_run=True 仍强制 None;dry_run=False 按 skill 粒度注入。"""
    fn = lambda c: "r"  # noqa: E731
    so.orchestrate_skills("为产品找人并做战役 campaign", context={"product": "AF 35mm F1.4", "budget_cents": 5000},
                          dry_run=False, record=False, model_fn_by_skill=gate.creator_match_only(fn))
    by_name = {c["skill_name"]: c["model_fn"] for c in capture}
    assert by_name["creator_match"] is fn and by_name["campaign_plan"] is None
    capture.clear()
    so.orchestrate_skills("找人", context={"product": "AF 35mm F1.4"}, dry_run=True, record=False,
                          model_fn_by_skill=gate.creator_match_only(fn))
    assert capture[0]["model_fn"] is None


def test_build_model_fn_caps_calls_and_uses_production_boundary(monkeypatch):
    from app.platform import llm_production

    seen: list[dict] = []

    def fake_generate_text(prompt, **kw):
        seen.append(kw)
        return {"status": "success", "text": "  适合:常拍 35mm 人像  "}

    monkeypatch.setattr(llm_production, "generate_text", fake_generate_text)
    fn = gate.build_creator_match_model_fn(max_calls=2)
    ctx = {"product": "AF 35mm F1.4", "handle": "lensguy", "country": "US", "score": 80, "evidence_pro": ["a"], "evidence_con": []}
    assert fn(ctx) == "适合:常拍 35mm 人像" and fn(ctx)
    with pytest.raises(RuntimeError):
        fn(ctx)  # 第 3 次超封顶 → 抛 → creator_match 自己回退规则理由
    assert fn.calls["calls"] == 2
    kw = seen[0]
    assert kw["provider"] == "google" and kw["model"] == "gemini-3.6-flash"
    assert kw["cost_tag"] == "agent_skill" and kw["purpose"] == gate.LLM_PURPOSE


def test_publish_to_inbox_is_idempotent_by_dedupe_key(inbox_sink):
    orchestration = {"status": "ok", "results": [
        {"skill_name": "creator_match", "status": "ok", "output": {"recommendations": [
            {"kol_pool_id": 5, "handle": "a", "fit_reason": "r1", "risk": "low"},
            {"kol_pool_id": 5, "handle": "a", "fit_reason": "r1", "risk": "low"},
            {"kol_pool_id": 6, "handle": "b", "fit_reason": "r2", "risk": "medium"},
        ]}},
        {"skill_name": "campaign_plan", "status": "ok", "output": {"recommendations": [{"kol_pool_id": 7}]}},
    ]}
    n = gate.publish_to_inbox(orchestration, product="AF 85mm", llm_used=False, level=1)
    keys = [s["dedupe_key"] for s in inbox_sink]
    assert n == 3 and keys == ["skill_creator_match:af_85mm:5", "skill_creator_match:af_85mm:5", "skill_creator_match:af_85mm:6"]
    assert all(s["requires_approval"] and s["category"] == "skill_creator_match" for s in inbox_sink)
    assert all(s["writes_business_data"] is False for s in inbox_sink)


def test_never_promotes_license(monkeypatch, capture, inbox_sink):
    from app.domains.agents import autonomy_license

    _set_level(monkeypatch, 2)
    monkeypatch.setattr(gate, "budget_allows", lambda est_usd=None: (True, "ok"))
    monkeypatch.setattr(autonomy_license, "evaluate_promotions", lambda *a, **k: pytest.fail("must not auto-promote"))
    monkeypatch.setattr(autonomy_license, "manual_override", lambda *a, **k: pytest.fail("must not override"))
    gate.licensed_auto_orchestrate(record=False, model_fn_factory=lambda: (lambda c: "ok"))


def test_license_read_failure_degrades_to_l0(monkeypatch):
    from app.domains.agents import autonomy_license

    def boom(action_type):
        raise RuntimeError("db down")

    monkeypatch.setattr(autonomy_license, "current_level", boom)
    lic = gate.license_level()
    assert lic["level"] == 0 and lic["status"] == "error"


def test_scheduler_job_wired_to_license_gate():
    from app.services.scheduler import jobs

    src = inspect.getsource(jobs)
    assert "skill_license_gate.licensed_auto_orchestrate(record=True, publish=True)" in src
    assert "auto_orchestrate(dry_run=True, record=True)" not in src
