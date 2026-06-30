"""Marketing Brain · Skill Orchestrator 单测 —— skill 经编排器(registry 分发)被调用,非仅人工 HTTP。

覆盖(全 hermetic:monkeypatch dispatch / budget,零真 DB、零真 LLM):
  - plan_skills 据 goal+context 确定性选 skill + 拼 input;
  - orchestrate_skills 经 skill_registry.dispatch_skill 真分发选中的 skill(captured);
  - gate 关(VKPI_SKILL_ORCHESTRATION=0)→ disabled;
  - 预算闸不放行 → budget_blocked,不真调用;
  - dry_run=True 强制 model_fn=None(不烧 LLM)。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain import skill_orchestrator as so  # noqa: E402
from app.domains.marketing_brain import skill_registry  # noqa: E402


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """默认开 gate(显式编排路径);单测里需要时各 case 自行覆盖。"""
    monkeypatch.setenv("VKPI_SKILL_ORCHESTRATION", "1")


@pytest.fixture
def capture_dispatch(monkeypatch):
    """monkeypatch skill_registry.dispatch_skill,捕获被编排器真调用的 skill + 入参。"""
    calls = []

    def fake_dispatch(skill_name, skill_input, *, model_fn=None, record=True):
        calls.append({"skill_name": skill_name, "input": skill_input,
                      "model_fn": model_fn, "record": record})
        return {"status": "ok", "skill_name": skill_name,
                "output": {"recommendations": [{"handle": "x"}], "rationale": "stub"}}

    monkeypatch.setattr(skill_orchestrator_registry(), "dispatch_skill", fake_dispatch)
    return calls


def skill_orchestrator_registry():
    # 编排器内部用 `from ... import skill_registry`,patch 同一对象即可。
    return skill_registry


def test_plan_selects_roi_for_review_goal():
    plan = so.plan_skills("给这个项目做复盘 roi", context={"project_id": 7})
    names = [n["skill_name"] for n in plan["skill_nodes"]]
    assert "roi_review" in names
    roi = next(n for n in plan["skill_nodes"] if n["skill_name"] == "roi_review")
    assert roi["input"]["project_id"] == 7


def test_plan_selects_multiple_skills():
    plan = so.plan_skills(
        "为 viltrox af 85mm 找人并做内容打分",
        context={"product": "viltrox af 85mm", "market": "US", "target_id": "vid_1"},
    )
    names = [n["skill_name"] for n in plan["skill_nodes"]]
    assert "creator_match" in names
    assert "content_score" in names


def test_orchestrate_dispatches_through_registry(capture_dispatch):
    out = so.orchestrate_skills(
        "为 viltrox af 85mm 找人",
        context={"product": "viltrox af 85mm", "market": "US"},
        dry_run=True,
        record=False,
    )
    assert out["status"] == "ok"
    # skill 真经 registry.dispatch_skill 被调用(非仅人工 HTTP)。
    assert capture_dispatch and capture_dispatch[0]["skill_name"] == "creator_match"
    # dry_run=True → model_fn 强制 None(不烧 LLM)。
    assert capture_dispatch[0]["model_fn"] is None
    assert out["results"][0]["recommendations_count"] == 1


def test_gate_off_returns_disabled(monkeypatch, capture_dispatch):
    monkeypatch.setenv("VKPI_SKILL_ORCHESTRATION", "0")
    out = so.orchestrate_skills("p", context={"product": "viltrox af 85mm"})
    assert out["status"] == "disabled"
    # gate 关 → 一个 skill 都不真调用。
    assert capture_dispatch == []


def test_budget_block_skips_dispatch(monkeypatch, capture_dispatch):
    from app.domains.costs import budget_guard

    monkeypatch.setattr(budget_guard, "check_budget", lambda *a, **k: False)
    out = so.orchestrate_skills(
        "为 viltrox af 85mm 找人",
        context={"product": "viltrox af 85mm"},
        dry_run=False,  # 非 dry_run 才会真过预算闸
    )
    assert out["status"] == "budget_blocked"
    assert capture_dispatch == []  # 拦下不真烧钱


def test_no_signal_returns_no_skill_selected(capture_dispatch):
    out = so.orchestrate_skills("hello there", context={})
    assert out["status"] == "no_skill_selected"
    assert capture_dispatch == []


def test_dry_run_false_passes_model_fn(capture_dispatch):
    sentinel = lambda ctx: "reason"  # noqa: E731
    out = so.orchestrate_skills(
        "为 viltrox af 85mm 找人",
        context={"product": "viltrox af 85mm"},
        dry_run=False,
        model_fn=sentinel,
    )
    assert out["status"] == "ok"
    # 非 dry_run 且显式传 model_fn → 透传给 skill(由更高层决定真烧)。
    assert capture_dispatch[0]["model_fn"] is sentinel
