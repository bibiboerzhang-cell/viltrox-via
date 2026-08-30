"""Characterization lock for via_learning_evaluator._build_policy_proposals (CC 刀).

冻结口径(原码全绿,动刀后必须原样绿):
  - 五类提案(retrieval/routing/fallback/memory/risk)的生成条件逐个边界锁死
    (>=/</<=/> 的严格性、max(1, episodic//4) 口径、空 provider 键剔除);
  - payload 逐键相等:proposal_key 拼法、confidence/impact_score、evidence 结构、
    candidate_config(含 rollout_providers 顺序与默认兜底)、window_days 默认 14;
  - 提案顺序固定:retrieval → routing → fallback → memory → risk;
  - 纯函数:同输入两次运行逐位一致,且绝不改写入参。

golden sha256 由动刀前原码实跑冻结。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from app.services.memory.via_learning_evaluator import _build_policy_proposals


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rich_summary() -> dict[str, Any]:
    """同时触发全部五类提案的窗口摘要。"""
    return {
        "window_days": 30,
        "metrics": {
            "memory_required_count": 12,
            "vector_hit_rate": 0.31,
            "model_choice_count": 9,
            "abuse_rate": 0.125,
        },
        "reply_modes": {"fallback": 5, "normal": 40},
        "providers": {"openai": 9},
        "promotion_tiers": {"episodic": 12, "semantic": 2},
        "retrieval_evidence": {
            "avg_score": 0.4321,
            "score_drift": "declining",
            "source_mix": {"vector": 7, "seed": 3},
        },
        "routing_learner": {"provider_count": 1, "providers": {"openai": {"turns": 9}}},
        "memory_retention": {"tracked": 30, "decaying": 4, "confirmed_hits": 11},
        "triggers": {"sensitive": 6, "conversation": 44},
    }


GOLDEN_SHA256 = {
    "rich_all_five": "0094f81d46c78733337ee5207aad2d06448d14ee1881ef8513f3c88c501be96e",
}


# ── 空态与边界(全部不触发)──────────────────────────────────────────


def test_empty_summary_yields_no_proposals() -> None:
    assert _build_policy_proposals({}) == []


def test_thresholds_are_strict_at_every_boundary() -> None:
    # 每项都停在触发线外侧一步:>=8 & <0.45 / >=6 & <=1 / >=3 / >=6 & <=max(1,//4) / >0.08。
    summary = {
        "window_days": 14,
        "metrics": {
            "memory_required_count": 7,
            "vector_hit_rate": 0.1,
            "model_choice_count": 5,
            "abuse_rate": 0.08,
        },
        "reply_modes": {"fallback": 2},
        "providers": {"openai": 1},
        "promotion_tiers": {"episodic": 5, "semantic": 0},
    }
    assert _build_policy_proposals(summary) == []

    # retrieval:vector_hit_rate 恰在 0.45 → 不触发(严格 <)。
    assert _build_policy_proposals(
        {"metrics": {"memory_required_count": 8, "vector_hit_rate": 0.45}}
    ) == []

    # routing:两个非空 provider → 不触发(<=1 是对非空键数说的)。
    assert _build_policy_proposals(
        {"metrics": {"model_choice_count": 100}, "providers": {"a": 1, "b": 2}}
    ) == []

    # memory:episodic=6 时 semantic 上限 max(1, 6//4)=1;semantic=2 → 不触发。
    assert _build_policy_proposals(
        {"promotion_tiers": {"episodic": 6, "semantic": 2}}
    ) == []


# ── 单分支逐键锁 ──────────────────────────────────────────────────────


def test_retrieval_proposal_payload_and_defaults() -> None:
    # window_days 缺省 → 14;retrieval_evidence 缺省 → avg 0.0 / stable / {}。
    proposals = _build_policy_proposals(
        {"metrics": {"memory_required_count": 8, "vector_hit_rate": 0.4499}}
    )
    assert proposals == [
        {
            "proposal_key": "retrieval-14-8",
            "proposal_type": "retrieval_tuning",
            "policy_key": "via.retrieval.selective",
            "status": "proposed",
            "confidence": 0.78,
            "impact_score": 0.72,
            "evidence": {
                "memory_required_count": 8,
                "vector_hit_rate": 0.4499,
                "retrieval_evidence": {
                    "avg_score": 0.0,
                    "score_drift": "stable",
                    "source_mix": {},
                },
            },
            "proposal": {
                "summary": (
                    "Memory-required turns are outrunning useful vector hits. "
                    "Add hybrid retrieval and trigger-based fallback ordering."
                ),
                "actions": [
                    "prioritize hybrid retrieval when memory_required and vector_hit_rate < 0.45",
                    "log retrieval score spread to support later rerank learning",
                ],
                "candidate_config": {
                    "policy_version": "via-offline-evaluator-v1.retrieval.hybrid",
                    "retrieval_mode": "hybrid_vector_seed",
                    "vector_hit_threshold": 0.45,
                    "fallback_order": ["bundle_memory", "vector_memory", "seed_knowledge"],
                },
            },
            "window_days": 14,
        }
    ]


def test_routing_rollout_provider_ordering() -> None:
    # 无有效 provider(空键剔除)→ 默认三家顺序。
    proposals = _build_policy_proposals(
        {"metrics": {"model_choice_count": 6}, "providers": {"": 4}}
    )
    assert len(proposals) == 1
    routing = proposals[0]
    assert routing["proposal_key"] == "routing-14-6"
    assert routing["proposal_type"] == "routing_exploration"
    assert routing["policy_key"] == "via.model.route"
    assert routing["confidence"] == 0.74
    assert routing["impact_score"] == 0.63
    assert routing["evidence"]["providers"] == {"": 4}
    assert routing["evidence"]["routing_learner"] == {"provider_count": 0, "providers": {}}
    assert routing["proposal"]["candidate_config"] == {
        "policy_version": "via-offline-evaluator-v1.routing.explore",
        "execution_mode": "bandit_explore",
        "exploration_ratio": 0.12,
        "providers": ["openai", "gemini", "claude"],
    }

    # 已观测 claude → claude 打头,默认名单去重补位。
    proposals = _build_policy_proposals(
        {"metrics": {"model_choice_count": 7}, "providers": {"claude": 2, "": 9}}
    )
    assert proposals[0]["proposal"]["candidate_config"]["providers"] == [
        "claude",
        "openai",
        "gemini",
    ]


def test_fallback_proposal_at_exact_threshold() -> None:
    proposals = _build_policy_proposals({"reply_modes": {"fallback": 3, "normal": 1}})
    assert len(proposals) == 1
    fallback = proposals[0]
    assert fallback["proposal_key"] == "fallback-14-3"
    assert fallback["proposal_type"] == "fallback_reduction"
    assert fallback["policy_key"] == "via.reply.mode"
    assert fallback["confidence"] == 0.69
    assert fallback["impact_score"] == 0.57
    assert fallback["evidence"] == {
        "fallback_count": 3,
        "reply_modes": {"fallback": 3, "normal": 1},
    }
    assert fallback["proposal"]["candidate_config"] == {
        "policy_version": "via-offline-evaluator-v1.reply.fallback",
        "fallback_mode": "deterministic_soft_landing",
        "capture_provider_error_reason": True,
    }


def test_memory_promotion_boundary_and_payload() -> None:
    proposals = _build_policy_proposals(
        {
            "promotion_tiers": {"episodic": 6, "semantic": 1},
            "memory_retention": {"tracked": 9, "decaying": 2, "confirmed_hits": 4},
        }
    )
    assert len(proposals) == 1
    memory = proposals[0]
    assert memory["proposal_key"] == "memory-14-6-1"
    assert memory["proposal_type"] == "memory_promotion_tuning"
    assert memory["policy_key"] == "via.memory.promotion"
    assert memory["confidence"] == 0.76
    assert memory["impact_score"] == 0.68
    assert memory["evidence"] == {
        "episodic": 6,
        "semantic": 1,
        "promotion_tiers": {"episodic": 6, "semantic": 1},
        "memory_retention": {"tracked": 9, "decaying": 2, "confirmed_hits": 4},
    }
    assert memory["proposal"]["candidate_config"] == {
        "policy_version": "via-offline-evaluator-v1.memory.semantic",
        "semantic_confirmed_hit_threshold": 2,
        "track_semantic_retention": True,
    }


def test_risk_proposal_key_truncates_abuse_rate() -> None:
    proposals = _build_policy_proposals(
        {"metrics": {"abuse_rate": 0.0805}, "triggers": {"sensitive": 2}}
    )
    assert len(proposals) == 1
    risk = proposals[0]
    assert risk["proposal_key"] == "risk-14-80"  # int(0.0805*1000)=80,截断不四舍五入
    assert risk["proposal_type"] == "risk_review"
    assert risk["policy_key"] == "via.guard.policy"
    assert risk["confidence"] == 0.67
    assert risk["impact_score"] == 0.61
    assert risk["evidence"] == {"abuse_rate": 0.0805, "triggers": {"sensitive": 2}}
    assert risk["proposal"]["candidate_config"] == {
        "policy_version": "via-offline-evaluator-v1.risk.redirect",
        "guard_copy_mode": "softer_public_redirect",
        "cluster_guard_buckets": True,
    }


# ── 全量触发:顺序 + 全 payload golden ────────────────────────────────


def test_all_five_proposals_order_and_golden_payload() -> None:
    summary = _rich_summary()
    frozen_input = copy.deepcopy(summary)
    proposals = _build_policy_proposals(summary)
    assert summary == frozen_input, "input summary must never be mutated"

    assert [p["proposal_type"] for p in proposals] == [
        "retrieval_tuning",
        "routing_exploration",
        "fallback_reduction",
        "memory_promotion_tuning",
        "risk_review",
    ]
    assert [p["proposal_key"] for p in proposals] == [
        "retrieval-30-12",
        "routing-30-9",
        "fallback-30-5",
        "memory-30-12-2",
        "risk-30-125",
    ]
    assert all(p["status"] == "proposed" for p in proposals)
    assert all(p["window_days"] == 30 for p in proposals)
    # routing:观测到 openai → openai 打头补 gemini/claude。
    assert proposals[1]["proposal"]["candidate_config"]["providers"] == [
        "openai",
        "gemini",
        "claude",
    ]
    # retrieval evidence 透传原值。
    assert proposals[0]["evidence"]["retrieval_evidence"] == {
        "avg_score": 0.4321,
        "score_drift": "declining",
        "source_mix": {"vector": 7, "seed": 3},
    }
    assert _digest(proposals) == GOLDEN_SHA256["rich_all_five"]

    # 纯函数:两次运行逐位一致。
    assert _build_policy_proposals(_rich_summary()) == proposals
