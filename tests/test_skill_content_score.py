"""Skill content_score_v1 单测:run 产出符合 OUTPUT_SCHEMA + record 被调 + eval 能跑。

策略(铁律:零真 LLM、零真 DB 写业务表):
  - monkeypatch `cache_repo.get_analysis_cache_entry` 喂内联 final_v1 payload —— 不碰活 DB;
  - monkeypatch `skill_registry.record_skill_run` 捕获落账本调用 —— 不写 vkpi_skill_runs;
  - model_fn 用纯 Python 假函数 —— 不烧 token、不走代理;
  - eval 用模块自带 EVAL_CASES + run_eval,纯结构跑。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain.skills import content_score  # noqa: E402


# --------------------------------------------------------------------------- #
# fixtures：内联 final_v1 缓存行（绕过活 DB）
# --------------------------------------------------------------------------- #
def _ready_entry(scores: dict, *, status: str = "ready") -> dict:
    return {
        "target_type": "video",
        "target_id": "12345",
        "derive_method": content_score.DEFAULT_DERIVE_METHOD,
        "model": "gemini-test",
        "cost": 0,
        "status": status,
        "result": {
            "layer1_visual_content": {"content_summary": "A 30s lens demo with sample shots."},
            "layer5_recommendations": {"cooperation_recommendation": "proceed"},
            "layer6_flags_and_scores": {
                "scores": scores,
                "key_hook": "opens with a sharp before/after",
                "final_verdict": "strong fit",
                "risk_flags": ["minor_audio_clip"],
            },
        },
    }


def _patch_cache(monkeypatch, entry):
    monkeypatch.setattr(content_score, "get_analysis_cache_entry", lambda *a, **k: entry)


def _patch_record(monkeypatch):
    calls = []

    def _fake_record(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "run_id": 1}

    monkeypatch.setattr(content_score.skill_registry, "record_skill_run", _fake_record)
    return calls


def _assert_output_schema(out: dict):
    for key in content_score.OUTPUT_SCHEMA:
        assert key in out, f"missing output key {key}"
    assert isinstance(out["scores"], dict)
    assert isinstance(out["summary"], str)
    mp = out["marketing_potential"]
    assert isinstance(mp, dict)
    for key in ("score", "band", "key_hook", "verdict", "risk_flags"):
        assert key in mp
    assert isinstance(mp["risk_flags"], list)
    assert isinstance(out["source"], dict)


# --------------------------------------------------------------------------- #
# run：产出符合 OUTPUT_SCHEMA + record 被调
# --------------------------------------------------------------------------- #
def test_run_ready_cache_shapes_output_and_records(monkeypatch):
    entry = _ready_entry({
        "marketing_value_score": 84.0,
        "content_quality_score": 78.0,
        "hook_score": 70.0,
        "brand_fit_score": 66.0,
    })
    _patch_cache(monkeypatch, entry)
    calls = _patch_record(monkeypatch)

    out = content_score.run({"analysis_cache_ref": {"target_id": 12345}})

    assert out["status"] == "ok"
    _assert_output_schema(out)
    # 维度键被投影为标准名。
    assert out["scores"]["quality"] == 78.0
    assert out["scores"]["hook"] == 70.0
    assert out["scores"]["brand_fit"] == 66.0
    assert out["scores"]["marketing_value"] == 84.0
    assert out["marketing_potential"]["score"] == 84.0
    assert out["marketing_potential"]["band"] == "high"
    assert out["marketing_potential"]["key_hook"] == "opens with a sharp before/after"
    assert out["marketing_potential"]["risk_flags"] == ["minor_audio_clip"]
    assert out["summary"]  # 规则总结非空
    assert out["model_used"] is None  # 无 model_fn

    # record 被调一次，且带 skill_name/version/output。
    assert len(calls) == 1
    rec = calls[0]
    assert rec["skill_name"] == content_score.SKILL_NAME
    assert rec["skill_version"] == content_score.SKILL_VERSION
    assert rec["output"] == out
    assert rec["cost_cents"] == 0


def test_run_record_false_skips_ledger(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({"marketing_value_score": 50.0}))
    calls = _patch_record(monkeypatch)

    out = content_score.run({"analysis_cache_ref": {"target_id": 9}}, record=False)
    assert out["status"] == "ok"
    assert calls == []  # record=False 不落账本


def test_run_with_model_fn_uses_injected_summary(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({"marketing_value_score": 90.0}))
    _patch_record(monkeypatch)

    def fake_model(payload):
        assert isinstance(payload, dict)
        return "LLM-style polished summary."

    out = content_score.run({"analysis_cache_ref": {"target_id": 1}}, model_fn=fake_model)
    assert out["summary"] == "LLM-style polished summary."
    assert out["model_used"] == "injected_model_fn"


def test_run_model_fn_failure_falls_back_to_rules(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({"marketing_value_score": 90.0}))
    _patch_record(monkeypatch)

    def boom(payload):
        raise RuntimeError("model down")

    out = content_score.run({"analysis_cache_ref": {"target_id": 1}}, model_fn=boom)
    assert out["status"] == "ok"
    assert out["summary"]  # 回退规则总结
    assert out["model_used"] is None


def test_run_missing_ref_unavailable(monkeypatch):
    calls = _patch_record(monkeypatch)
    out = content_score.run({"video_url": "https://example.com/v"})
    assert out["status"] == "unavailable"
    _assert_output_schema(out)
    assert out["source"]["video_url"] == "https://example.com/v"
    # 即便 unavailable 也落账本（记录这次尝试）。
    assert len(calls) == 1


def test_run_no_cache_unavailable(monkeypatch):
    _patch_cache(monkeypatch, None)
    _patch_record(monkeypatch)
    out = content_score.run({"analysis_cache_ref": {"target_id": 777}})
    assert out["status"] == "unavailable"
    assert out["source"]["cache_status"] == "not_found"
    _assert_output_schema(out)


def test_run_not_ready_cache_unavailable(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({"marketing_value_score": 50.0}, status="pending"))
    _patch_record(monkeypatch)
    out = content_score.run({"analysis_cache_ref": {"target_id": 5}})
    assert out["status"] == "unavailable"


def test_dict_shaped_scores_take_score_field(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({
        "marketing_value_score": {"score": 82.0, "confidence": 0.9},
        "content_quality_score": {"score": 71.0},
    }))
    _patch_record(monkeypatch)
    out = content_score.run({"analysis_cache_ref": {"target_id": 3}})
    assert out["scores"]["marketing_value"] == 82.0
    assert out["scores"]["quality"] == 71.0


def test_record_best_effort_never_raises(monkeypatch):
    _patch_cache(monkeypatch, _ready_entry({"marketing_value_score": 50.0}))

    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(content_score.skill_registry, "record_skill_run", boom)
    # record 抛错也不应拖垮 run。
    out = content_score.run({"analysis_cache_ref": {"target_id": 2}})
    assert out["status"] == "ok"


# --------------------------------------------------------------------------- #
# eval：能跑且全绿
# --------------------------------------------------------------------------- #
def test_eval_cases_run_green():
    report = content_score.evaluate()
    assert report["total"] == len(content_score.EVAL_CASES)
    assert report["hit_rate"] == 1.0
    assert report["avg_score"] == 1.0


def test_schemas_are_dicts():
    assert isinstance(content_score.INPUT_SCHEMA, dict)
    assert isinstance(content_score.OUTPUT_SCHEMA, dict)
    assert content_score.INPUT_SCHEMA and content_score.OUTPUT_SCHEMA
