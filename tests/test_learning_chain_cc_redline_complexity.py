"""CC 棘轮:学习链两把刀的复杂度红线(_DecisionCounter 口径实测报数)。

shadow_eval._run_forecast_backtest    52 → ≤10(壳),helper ≤12;
via_learning_evaluator._build_policy_proposals 51 → ≤10(壳),新 helper ≤12。

注意:via_learning_evaluator 里 run_via_offline_evaluator(38)与
get_via_control_debug_snapshot(19)不在本刀名下,红线按现值封顶防继续恶化。
"""
from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity

ROOT = Path(__file__).resolve().parents[1]
SHADOW_EVAL = ROOT / "backend/app/domains/learning/shadow_eval.py"
VIA_EVALUATOR = ROOT / "backend/app/services/memory/via_learning_evaluator.py"


def _rows(path: Path):
    return collect_complexity({str(path): ast.parse(path.read_text(encoding="utf-8"))})


def test_forecast_backtest_shell_and_helpers_stay_under_redline() -> None:
    rows = _rows(SHADOW_EVAL)
    shell = next(row for row in rows if row.qualified_name == "_run_forecast_backtest")
    assert shell.cc <= 10
    # 整个文件的 helper 都在 ≤12(本文件无豁免)。
    assert max(row.cc for row in rows) <= 12
    assert len(SHADOW_EVAL.read_text(encoding="utf-8").splitlines()) < 800


def test_build_policy_proposals_shell_and_helpers_stay_under_redline() -> None:
    rows = _rows(VIA_EVALUATOR)
    shell = next(row for row in rows if row.qualified_name == "_build_policy_proposals")
    assert shell.cc <= 10
    # 名下新 helper 全部 ≤12。
    for name in (
        "_retrieval_tuning_proposals",
        "_rollout_provider_candidates",
        "_routing_exploration_proposals",
        "_fallback_reduction_proposals",
        "_memory_promotion_proposals",
        "_risk_review_proposals",
    ):
        row = next(r for r in rows if r.qualified_name == name)
        assert row.cc <= 12, f"{name} cc={row.cc}"
    # 不在名下的两个既有函数按现值封顶,防悄悄恶化。
    legacy_caps = {"run_via_offline_evaluator": 38, "get_via_control_debug_snapshot": 19}
    for name, cap in legacy_caps.items():
        row = next(r for r in rows if r.qualified_name == name)
        assert row.cc <= cap, f"{name} cc={row.cc} 超过封顶 {cap}"
    assert len(VIA_EVALUATOR.read_text(encoding="utf-8").splitlines()) < 800
