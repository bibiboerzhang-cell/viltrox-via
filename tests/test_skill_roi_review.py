"""Skill【roi_review_v1】单测:run 产出符合 OUTPUT_SCHEMA + record 被调 + eval 能跑。

策略(铁律:零真 LLM、零真 DB 写业务表):
  - monkeypatch 现有服务边界(aggregate_project_metrics / get_kol_roi_summary /
    compute_next_recommendation_weight)→ 注入假 ROI,不碰活 DB;
  - monkeypatch skill_registry.record_skill_run → 断言被调一次(不真写 vkpi_skill_runs);
  - model_fn 用本地假函数(不真烧 LLM);
  - eval 用 skill 内置 EVAL_CASES + run_eval 跑,断言全绿。
红线:本测试零触 viltrox_fit_score。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain.skills import roi_review  # noqa: E402


# OUTPUT_SCHEMA 顶层键集合 + roi 子键集合,做形状自检。
_TOP_KEYS = {"scope", "subject_id", "roi", "outcome_labels", "next_action", "confidence", "missing_data", "status"}
_ROI_KEYS = {"spend_cents", "attributed_gmv_cents", "roi_ratio"}


def _assert_shape(out: dict) -> None:
    assert isinstance(out, dict)
    assert set(out.keys()) == _TOP_KEYS, out.keys()
    assert set(out["roi"].keys()) == _ROI_KEYS, out["roi"].keys()
    assert isinstance(out["outcome_labels"], list) and out["outcome_labels"]
    assert isinstance(out["confidence"], (int, float)) and 0.0 <= out["confidence"] <= 1.0
    assert isinstance(out["missing_data"], bool)
    assert out["status"] in {"ready", "missing_data", "not_found", "invalid_input"}


def _capture_record(monkeypatch):
    """monkeypatch record_skill_run,捕获调用;返回 calls list。"""
    from app.domains.marketing_brain import skill_registry

    calls = []

    def _fake_record(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "run_id": 1}

    monkeypatch.setattr(skill_registry, "record_skill_run", _fake_record)
    return calls


def test_invalid_input_returns_shape_and_records(monkeypatch):
    calls = _capture_record(monkeypatch)
    out = roi_review.run({})
    _assert_shape(out)
    assert out["status"] == "invalid_input"
    assert out["missing_data"] is True
    assert out["roi"] == {"spend_cents": None, "attributed_gmv_cents": None, "roi_ratio": None}
    assert len(calls) == 1
    assert calls[0]["skill_name"] == "roi_review"
    assert calls[0]["skill_version"] == "v1"


def test_project_with_revenue_ready(monkeypatch):
    """注入假项目聚合(有 revenue + cost)→ status=ready, roi_ratio 自算, record 被调。"""
    calls = _capture_record(monkeypatch)
    from app.domains.metrics import aggregation

    monkeypatch.setattr(
        aggregation, "aggregate_project_metrics",
        lambda pid, window_days=30: {
            "status": "ready", "cost_cents": 10000, "revenue_cents": 30000, "roi": 2.0, "project_id": pid,
        },
    )
    out = roi_review.run({"project_id": 42, "window": 14})
    _assert_shape(out)
    assert out["scope"] == "project"
    assert out["subject_id"] == 42
    assert out["status"] == "ready"
    assert out["missing_data"] is False
    assert out["roi"]["spend_cents"] == 10000
    assert out["roi"]["attributed_gmv_cents"] == 30000
    assert out["roi"]["roi_ratio"] == 2.0
    assert "has_revenue" in out["outcome_labels"]
    assert out["next_action"] == "scale_up_investment"
    assert len(calls) == 1
    # record 落 model_used=rule_v0(无 model_fn)、cost_cents=0(规则路径零成本)。
    assert calls[0]["model_used"] == "rule_v0"
    assert calls[0]["cost_cents"] == 0
    assert calls[0]["output"] == out


def test_project_missing_data_awaiting_m5(monkeypatch):
    """聚合返回 awaiting_m5(无 revenue)→ 诚实 missing_data,roi 留空,不臆造 0。"""
    _capture_record(monkeypatch)
    from app.domains.metrics import aggregation

    monkeypatch.setattr(
        aggregation, "aggregate_project_metrics",
        lambda pid, window_days=30: {
            "status": "awaiting_m5", "cost_cents": 5000, "revenue_cents": None, "roi": None, "project_id": pid,
        },
    )
    out = roi_review.run({"project_id": 7})
    _assert_shape(out)
    assert out["status"] == "missing_data"
    assert out["missing_data"] is True
    assert out["roi"]["attributed_gmv_cents"] is None
    assert out["roi"]["roi_ratio"] is None
    assert "missing_data" in out["outcome_labels"]
    assert out["next_action"] == "collect_attribution_data"


def test_kol_path_with_weight(monkeypatch):
    """KOL 维度:注入 get_kol_roi_summary + 漏斗权重 → labels 含 funnel 信号。"""
    _capture_record(monkeypatch)
    from app.domains.kol import roi_aggregate

    monkeypatch.setattr(
        roi_aggregate, "get_kol_roi_summary",
        lambda kid: {"status": "ready", "cost_cents": 2000, "revenue_cents": 8000, "roi": 3.0, "kol_pool_id": kid},
    )
    monkeypatch.setattr(roi_aggregate, "compute_next_recommendation_weight", lambda kid: 0.7)
    out = roi_review.run({"kol_pool_id": 88})
    _assert_shape(out)
    assert out["scope"] == "kol"
    assert out["subject_id"] == 88
    assert out["status"] == "ready"
    assert "high_funnel_traction" in out["outcome_labels"]


def test_not_found_path(monkeypatch):
    """聚合返回 not_found → status=not_found, missing_data=True, record 仍被调。"""
    calls = _capture_record(monkeypatch)
    from app.domains.metrics import aggregation

    monkeypatch.setattr(
        aggregation, "aggregate_project_metrics",
        lambda pid, window_days=30: {"status": "not_found", "scope": "project", "project_id": pid},
    )
    out = roi_review.run({"project_id": 99})
    _assert_shape(out)
    assert out["status"] == "not_found"
    assert out["missing_data"] is True
    assert out["next_action"] == "verify_subject_exists"
    assert len(calls) == 1


def test_model_fn_injection_overrides(monkeypatch):
    """注入 model_fn 覆盖 next_action/confidence/labels;model_used 记为注入模型。"""
    calls = _capture_record(monkeypatch)
    from app.domains.metrics import aggregation

    monkeypatch.setattr(
        aggregation, "aggregate_project_metrics",
        lambda pid, window_days=30: {"status": "ready", "cost_cents": 1000, "revenue_cents": 5000, "roi": 4.0},
    )

    def fake_model(ctx):
        assert "roi" in ctx and "scope" in ctx  # 喂给 model_fn 的上下文形状
        return {"next_action": "double_down", "confidence": 0.95, "outcome_labels": ["llm_label"], "model": "fake-llm"}

    out = roi_review.run({"project_id": 3}, model_fn=fake_model)
    _assert_shape(out)
    assert out["next_action"] == "double_down"
    assert out["confidence"] == 0.95
    assert out["outcome_labels"] == ["llm_label"]
    assert calls[0]["model_used"] == "fake-llm"


def test_record_skipped_when_record_false(monkeypatch):
    calls = _capture_record(monkeypatch)
    roi_review.run({}, record=False)
    assert calls == []


def test_eval_runs_green():
    """skill 内置 EVAL_CASES + run_eval 跑通,全命中(纯 hermetic 输入校验路径)。"""
    report = roi_review.evaluate()
    assert report["suite"] == "roi_review_v1"
    assert report["total"] == len(roi_review.EVAL_CASES) == 4
    assert report["hits"] == report["total"], report
    assert report["hit_rate"] == 1.0


def test_input_output_schema_present():
    assert isinstance(roi_review.INPUT_SCHEMA, dict) and roi_review.INPUT_SCHEMA
    assert set(roi_review.OUTPUT_SCHEMA.keys()) >= {"roi", "outcome_labels", "next_action", "confidence"}
    assert set(roi_review.OUTPUT_SCHEMA["roi"].keys()) == _ROI_KEYS
