"""Skill brief_generate_v1 单测:run 产出符合 OUTPUT_SCHEMA + record 被调 + eval 能跑。

策略(hermetic,零真 LLM / 零真 DB 写):
  - monkeypatch outreach._load_creators —— 不碰活 DB,喂确定性 KOL 档案。
  - monkeypatch skill_registry.record_skill_run —— 截获落账调用,断言被调且不真写业务表。
  - model_fn=None 默认走模板规则(不真烧 LLM);另测可注入 model_fn 路径 + 抛错回落。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain import evals as evals_mod  # noqa: E402
from app.domains.marketing_brain import skill_registry  # noqa: E402
from app.domains.marketing_brain.skills import brief_generate_v1 as skill  # noqa: E402
from app.domains.projects import outreach as outreach_mod  # noqa: E402


_FAKE_KOL = {
    "id": 7,
    "platform": "youtube",
    "handle": "lenslab",
    "display_name": "Lens Lab",
    "primary_topic": "camera reviews",
    "followers": 120000,
    "email": "x@example.com",
}


@pytest.fixture
def patched(monkeypatch):
    """喂确定性 KOL 档案 + 截获 record_skill_run(不碰真 DB / 不真落账)。"""
    monkeypatch.setattr(outreach_mod, "_load_creators", lambda ids: ([dict(_FAKE_KOL)], []))
    calls: list[dict] = []

    def _fake_record(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "run_id": 999}

    monkeypatch.setattr(skill_registry, "record_skill_run", _fake_record)
    return calls


def _assert_output_schema(out: dict):
    assert out["ok"] is True
    assert out["editable"] is True
    brief = out["brief"]
    assert isinstance(brief, dict)
    assert isinstance(brief["hook"], str) and brief["hook"]
    for key in ("talking_points", "do", "dont", "deliverables"):
        assert isinstance(brief[key], list) and brief[key]
        assert all(isinstance(x, str) and x for x in brief[key])


def test_run_template_path_matches_output_schema(patched):
    """model_fn=None 默认走模板规则,产出符合 OUTPUT_SCHEMA。"""
    out = skill.run(
        {"kol_pool_id": 7, "product": {"product_name": "Viltrox AF 85mm"}, "angle": "bokeh portraits"},
        record=False,
    )
    _assert_output_schema(out)
    assert out["model_used"] == "rule_v0"
    # angle 应进入 hook/talking_points。
    blob = out["brief"]["hook"] + " ".join(out["brief"]["talking_points"])
    assert "bokeh portraits" in blob


def test_product_as_string_accepted(patched):
    out = skill.run({"kol_pool_id": 7, "product": "Viltrox AF 27mm"}, record=False)
    _assert_output_schema(out)


def test_record_skill_run_called_when_record_true(patched):
    """record=True → record_skill_run 被调一次,落 skill_name/version/output。"""
    out = skill.run({"kol_pool_id": 7, "product": "Viltrox AF 35mm"}, record=True)
    assert out["ok"] is True
    assert len(patched) == 1
    rec = patched[0]
    assert rec["skill_name"] == "brief_generate"
    assert rec["skill_version"] == "v1"
    assert rec["model_used"] == "rule_v0"
    assert rec["cost_cents"] == 0  # 不真烧 LLM
    assert rec["output"]["brief"]["hook"]


def test_record_skipped_when_record_false(patched):
    skill.run({"kol_pool_id": 7, "product": "Viltrox AF 35mm"}, record=False)
    assert patched == []


def test_model_fn_injected_path(patched):
    """可注入 model_fn 产出 hook/talking_points;deliverables 仍强制走 SOW 基线。"""
    def fake_model(material):
        assert material["kol"]["handle"] == "lenslab"
        return {
            "hook": "INJECTED HOOK",
            "talking_points": ["tp1", "tp2"],
            "do": ["do1"],
            "dont": ["dont1"],
            "deliverables": ["BOGUS — should be overridden"],
            "_model": "claude-fake",
        }

    out = skill.run({"kol_pool_id": 7, "product": "Viltrox AF 85mm"}, model_fn=fake_model, record=True)
    _assert_output_schema(out)
    assert out["brief"]["hook"] == "INJECTED HOOK"
    assert out["model_used"] == "claude-fake"
    # deliverables 永远来自 SOW 基线,不被 model_fn 污染。
    assert "BOGUS — should be overridden" not in out["brief"]["deliverables"]
    assert patched[0]["model_used"] == "claude-fake"


def test_model_fn_error_falls_back_to_template(patched):
    def boom(material):
        raise RuntimeError("model blew up")

    out = skill.run({"kol_pool_id": 7, "product": "Viltrox AF 85mm"}, model_fn=boom, record=False)
    _assert_output_schema(out)
    assert out["model_used"] == "rule_v0_fallback"


def test_missing_kol_pool_id_rejected(patched):
    out = skill.run({"product": "Viltrox AF 85mm"}, record=True)
    assert out["ok"] is False
    assert out["reason"] == "kol_pool_id required"
    assert out["editable"] is True
    assert patched == []  # 校验失败提前返回,不落账


def test_missing_product_rejected(patched):
    out = skill.run({"kol_pool_id": 7}, record=False)
    assert out["ok"] is False
    assert out["reason"] == "product required"


def test_record_failure_does_not_break_run(monkeypatch):
    """record_skill_run 抛错也不拖垮主返回(best-effort)。"""
    monkeypatch.setattr(outreach_mod, "_load_creators", lambda ids: ([dict(_FAKE_KOL)], []))

    def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(skill_registry, "record_skill_run", _boom)
    out = skill.run({"kol_pool_id": 7, "product": "Viltrox AF 85mm"}, record=True)
    _assert_output_schema(out)


def test_eval_runs_green(patched):
    """EVAL_CASES 经 run_eval 可跑,模板路径下结构完整 → 全命中。"""
    report = evals_mod.run_eval(
        skill._eval_skill_fn,
        skill.EVAL_CASES,
        suite="brief_generate_v1",
    )
    d = report.to_dict()
    assert d["total"] == len(skill.EVAL_CASES)
    assert d["hits"] == d["total"]
    assert d["hit_rate"] == 1.0
    assert d["avg_score"] == 1.0
