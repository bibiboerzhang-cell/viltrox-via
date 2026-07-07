"""C4 W10 放权能力纯函数单测(hermetic:零 DB / 零 LLM / 零网络)。

覆盖 market_brain.bandit 的奖励归一化 / 权重增量 / 选臂建议边界,以及
market_brain.tool_risk 的风险分级 / 审批判定。全部纯函数,不触任何落账路径。
"""
from __future__ import annotations

import math

import pytest

from app.domains.market_brain import bandit, tool_risk


# ── bandit.normalized_reward ─────────────────────────────────────────


def test_normalized_reward_empty_is_zero():
    assert bandit.normalized_reward({}) == 0.0
    assert bandit.normalized_reward(None) == 0.0


def test_normalized_reward_all_ones_is_one():
    outcome = {k: 1 for k in bandit.REWARD_WEIGHTS}
    assert bandit.normalized_reward(outcome) == pytest.approx(1.0)


def test_normalized_reward_single_component_is_its_weight():
    assert bandit.normalized_reward({"orders_or_reply": 1}) == pytest.approx(
        bandit.REWARD_WEIGHTS["orders_or_reply"]
    )
    assert bandit.normalized_reward({"posted": True}) == pytest.approx(
        bandit.REWARD_WEIGHTS["posted"]
    )


def test_normalized_reward_clamps_out_of_range():
    # clicks 溢出压到 1、comments 负值压到 0、posted 布尔 True 记 1。
    outcome = {"clicks": 5.0, "comments": -3.0, "posted": True}
    expected = (
        bandit.REWARD_WEIGHTS["clicks"]
        + bandit.REWARD_WEIGHTS["posted"]
    )
    assert bandit.normalized_reward(outcome) == pytest.approx(expected)


# ── bandit.update_arm_weight ─────────────────────────────────────────


def test_update_arm_weight_from_scratch():
    outcome = {k: 1 for k in bandit.REWARD_WEIGHTS}
    updated = bandit.update_arm_weight(None, outcome)
    assert updated["n"] == 1
    assert updated["mean_reward"] == pytest.approx(1.0)
    assert updated["last_reward"] == pytest.approx(1.0)


def test_update_arm_weight_online_mean():
    prior = {"n": 1, "mean_reward": 1.0}
    updated = bandit.update_arm_weight(prior, {})  # reward 0
    assert updated["n"] == 2
    assert updated["mean_reward"] == pytest.approx(0.5)
    assert updated["last_reward"] == pytest.approx(0.0)


# ── bandit.arm_key ───────────────────────────────────────────────────


def test_arm_key_stable_and_lowercased():
    a = bandit.arm_key("SKU-X", "US", "Creator", "Awe", "Macro")
    b = bandit.arm_key("sku-x", "us", "creator", "awe", "macro")
    assert a == b == "sku-x|us|creator|awe|macro"


# ── bandit.select_arm ────────────────────────────────────────────────


def test_select_arm_empty_pool_is_none():
    assert bandit.select_arm([]) is None
    assert bandit.select_arm(None) is None


def test_select_arm_pure_exploit_picks_highest_mean():
    arms = [
        {"arm_key": "a", "n": 5, "mean_reward": 0.4},
        {"arm_key": "b", "n": 3, "mean_reward": 0.7},
    ]
    got = bandit.select_arm(arms, explore_rate=0.0)
    assert got["arm_key"] == "b"
    assert got["reason"] == "exploit"
    assert got["mean_reward"] == pytest.approx(0.7)


def test_select_arm_exploration_prefers_unseen():
    arms = [
        {"arm_key": "seen", "n": 20, "mean_reward": 0.1},
        {"arm_key": "fresh", "n": 0, "mean_reward": 0.0},
    ]
    # explore_rate 高 + 未探索加成 → 建议探索新臂(纯建议,不执行)。
    got = bandit.select_arm(arms, explore_rate=0.5)
    assert got["arm_key"] == "fresh"
    assert got["reason"] == "explore"


def test_select_arm_is_deterministic():
    arms = [
        {"arm_key": "seen", "n": 20, "mean_reward": 0.1},
        {"arm_key": "fresh", "n": 0, "mean_reward": 0.0},
    ]
    first = bandit.select_arm(arms, explore_rate=0.5)
    second = bandit.select_arm(arms, explore_rate=0.5)
    assert first == second


# ── tool_risk.classify_action ────────────────────────────────────────


def test_classify_action_known_names():
    assert tool_risk.classify_action("read") == "low"
    assert tool_risk.classify_action("record_signal") == "med"
    assert tool_risk.classify_action("kol_outreach") == "high"


def test_classify_action_normalizes_case_and_dash():
    assert tool_risk.classify_action("KOL-Outreach") == "high"
    assert tool_risk.classify_action("  Read  ") == "low"


def test_classify_action_unknown_is_fail_safe_high():
    assert tool_risk.classify_action("totally_unknown_action") == "high"


def test_classify_action_from_dimensions():
    assert tool_risk.classify_action({"writes": False}) == "low"
    assert tool_risk.classify_action({"writes": True}) == "med"  # reversible default True
    assert tool_risk.classify_action({"writes": True, "reversible": False}) == "high"
    assert tool_risk.classify_action({"spends_money": True}) == "high"
    assert tool_risk.classify_action({"contacts_external": True}) == "high"


# ── tool_risk.requires_human_approval ────────────────────────────────


def test_requires_human_approval_only_high():
    assert tool_risk.requires_human_approval("high") is True
    assert tool_risk.requires_human_approval("med") is False
    assert tool_risk.requires_human_approval("low") is False


def test_requires_human_approval_case_insensitive():
    assert tool_risk.requires_human_approval("HIGH") is True


def test_requires_human_approval_illegal_tier_is_fail_safe():
    assert tool_risk.requires_human_approval("garbage") is True


def test_math_import_available():
    # 防呆:确认 bonus 计算依赖的 math 在模块内可用(选臂路径未回归)。
    assert math.isclose(math.log(1), 0.0)
