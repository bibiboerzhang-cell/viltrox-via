from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.memory import via_learning_summaries as summaries
from scripts.vkpi_engineering_health_collect import collect_complexity


# Frozen from the complete pre-split return values at HEAD
# 7c2c5837af71092a29b989b66d7b3d34dc3e4740.  The reviewed source file had
# SHA-256 5a6e4b2d71a3a10c0c8e90cb9e55a942863ad8194f635121453ca60a1fc49e67.
LEGACY_RETURN_SHA256 = {
    "control_empty": "582bff1b9e3954f6f9bfd3393e5277ccdfa2cdc7e75debb9cc5d1f242e8692d3",
    "control_rich": "bebcba19e928661d554fd55234508f8c02cc7edcc1048436b4d930d24ab68ed9",
    "live_empty": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "live_full": "7a15c6f1360995568a0120e184f9d2bd1d98091fb058261d4cbf66d01cc03604",
    "live_healthy": "58244bfd9f768f8da5dbf70d3bf8a0eaaf27d5a803ed457c5601d769e37677df",
    "live_hold": "7007251f6b6a8363ec510861028be16cd0cfb6c6d3ec21c9fc64997e6fff9e1a",
    "live_rollback": "2ea332171712c28a3427ea8eba9013719930418839bbf98b674693c253bfc77c",
    "shadow_broader": "7e6a3ff23e22b2a0f9297994e38abb84dcf30046ca50ea01e0780e899cf987a9",
    "shadow_eligible": "1a34993e9d7d8264e46a057a8a853abe7436323ec4111afd004f3c5b8903c184",
    "shadow_empty": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "shadow_hold": "98a281a812087fa91fda53e11da0ccc513cf0d07fd7b6bea5fd91af1140bc94c",
}

CONTROL_RICH_GOLDEN_PATH = (
    Path(__file__).parent / "fixtures" / "via_learning_control_summary_control_rich_v1.json"
)
CONTROL_RICH_FROZEN_AT = datetime(2026, 8, 29, tzinfo=timezone.utc)
CONTROL_RICH_BASELINE = {
    "git_head": "7c2c5837af71092a29b989b66d7b3d34dc3e4740",
    "source_path": "backend/app/services/memory/via_learning_summaries.py",
    "source_sha256": "5a6e4b2d71a3a10c0c8e90cb9e55a942863ad8194f635121453ca60a1fc49e67",
}


class _FrozenControlSummaryDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        if tz is None:
            return CONTROL_RICH_FROZEN_AT.replace(tzinfo=None)
        return CONTROL_RICH_FROZEN_AT.astimezone(tz)


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _control_inputs() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    decisions = [
        {
            "decision_id": "d1",
            "decision_type": "reply_mode",
            "trigger_type": "conversation",
            "policy_key": "p.reply",
            "chosen_action": {"mode": "fallback", "provider": "openai"},
            "latency_ms": 100,
            "cost_estimate": 0.1,
        },
        {
            "decision_id": "d2",
            "decision_type": "reply_mode",
            "trigger_type": "conversation",
            "policy_key": "p.reply",
            "chosen_action": {"mode": "ai_dialogue", "provider": "gemini"},
            "latency_ms": 200,
            "cost_estimate": 0.2,
        },
        {
            "decision_id": "d3",
            "decision_type": "reply_mode",
            "trigger_type": "conversation",
            "policy_key": "p.reply",
            "chosen_action": {"mode": "fast_brain"},
            "latency_ms": 0,
            "cost_estimate": 0,
        },
        {
            "decision_id": "d4",
            "decision_type": "intent_route",
            "trigger_type": "product",
            "policy_key": "p.intent",
            "chosen_action": {"needs_memory": True},
        },
        {
            "decision_id": "d5",
            "decision_type": "retrieval_plan",
            "trigger_type": "product",
            "policy_key": "p.retrieve",
            "chosen_action": {"plan": "vector_memory"},
        },
        {
            "decision_id": "d6",
            "decision_type": "model_choice",
            "trigger_type": "route",
            "policy_key": "p.model",
            "chosen_action": {"provider": "gemini"},
        },
        {
            "decision_id": "d7",
            "decision_type": "memory_promotion",
            "trigger_type": "memory",
            "policy_key": "p.memory",
            "chosen_action": {"tier": "semantic"},
        },
        {
            "decision_id": "d8",
            "decision_type": "shadow_eval",
            "trigger_type": "fallback-target",
            "policy_key": "p.shadow",
            "chosen_action": {"target": "retrieval_plan", "would_change": True},
        },
        {
            "decision_id": "",
            "decision_type": "",
            "trigger_type": "",
            "policy_key": "",
            "chosen_action": {},
        },
    ]
    outcomes = [
        {
            "decision_id": "d1",
            "accepted": True,
            "clicked_product": True,
            "added_to_cart": True,
            "purchased": True,
            "abuse_flag": 1,
            "reward_score": 0.9,
            "marker": "linked",
        },
        {
            "decision_id": "missing",
            "accepted": False,
            "clicked_product": False,
            "added_to_cart": False,
            "purchased": False,
            "abuse_flag": 0,
            "reward_score": 0.1,
            "marker": "unlinked",
        },
    ]
    reward_traces = [
        {
            "event_type": "compare",
            "event_value": 3.5,
            "event_payload": {"estimated_commission": 2.25},
        },
        {"event_type": "purchase", "event_value": 7, "event_payload": {}},
    ]
    retrieval_evidence = [
        {
            "top_score": 0.9,
            "avg_score": 0.5,
            "score_spread": 0.2,
            "selected_sources": ["video", "video"],
            "retrieval_mode": "hybrid",
            "rerank_applied": True,
        }
    ]
    routing_stats = [
        {
            "provider": "gemini",
            "bucket_key": "b1",
            "exposure_count": 2,
            "success_count": 1,
            "reward_sum": 1.5,
            "guard_fail_count": 1,
        }
    ]
    memory_retention = [
        {
            "memory_tier": "semantic",
            "cumulative_reward": 2.0,
            "confirmed_hits": 3,
            "reinforcement_count": 4,
            "last_hit_at": "2999-01-01T00:00:00Z",
            "status": "active",
        }
    ]
    return (
        decisions,
        outcomes,
        reward_traces,
        retrieval_evidence,
        routing_stats,
        memory_retention,
    )


def _live_inputs(kind: str) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    policy_key = "via.retrieval.selective"
    version_label = "live-v2"
    if kind == "healthy":
        rollout_percentage, sample_count = 0.05, 6
        outcomes = [
            {
                "session_key": f"s{index}",
                "accepted": True,
                "reward_score": 0.8,
                "abuse_flag": 0,
                "created_at": "2999-01-01T00:00:00Z",
            }
            for index in range(sample_count)
        ]
        reward_traces = [{"session_key": "s0", "event_type": "compare"}]
    elif kind == "full":
        rollout_percentage, sample_count = 1.0, 32
        outcomes = [
            {
                "session_key": f"s{index}",
                "accepted": True,
                "reward_score": 0.8,
                "abuse_flag": 0,
                "created_at": "2999-01-01T00:00:00Z",
            }
            for index in range(sample_count)
        ]
        reward_traces = [{"session_key": "s0", "event_type": "purchase"}]
    elif kind == "hold":
        rollout_percentage, sample_count = 0.15, 1
        outcomes, reward_traces = [], []
    elif kind == "rollback":
        rollout_percentage, sample_count = 0.05, 6
        outcomes = [
            {
                "session_key": f"s{index}",
                "accepted": False,
                "reward_score": 0.1,
                "abuse_flag": 0,
                "created_at": "2999-01-01T00:00:00Z",
            }
            for index in range(sample_count)
        ]
        reward_traces = []
    else:  # pragma: no cover - fixture misuse guard
        raise AssertionError(kind)

    decisions = [
        {
            "decision_type": "retrieval_plan",
            "policy_key": policy_key,
            "policy_version": version_label,
            "session_key": f"s{index}",
        }
        for index in range(sample_count)
    ]
    version_history: list[dict[str, Any]] = []
    if kind == "rollback":
        decisions.extend(
            {
                "decision_type": "retrieval_plan",
                "policy_key": policy_key,
                "policy_version": "stable-v1",
                "session_key": f"p{index}",
            }
            for index in range(6)
        )
        outcomes.extend(
            {
                "session_key": f"p{index}",
                "accepted": True,
                "reward_score": 0.9,
                "abuse_flag": 0,
                "created_at": "2999-01-01T00:00:00Z",
            }
            for index in range(6)
        )
        version_history = [
            {
                "policy_key": policy_key,
                "version_key": "v1",
                "version_label": "stable-v1",
                "status": "superseded",
            }
        ]
    live_versions = [
        {
            "policy_key": "unknown.policy",
            "version_key": "skip-rule",
            "version_label": "x",
            "config": {"rollout_mode": "limited", "rollout_percentage": 0.05},
        },
        {
            "policy_key": policy_key,
            "version_key": "skip-mode",
            "version_label": "x",
            "config": {"rollout_mode": "full", "rollout_percentage": 1},
        },
        {
            "policy_key": policy_key,
            "version_key": "v2",
            "version_label": version_label,
            "config": {
                "rollout_mode": " LIMITED ",
                "rollout_percentage": rollout_percentage,
            },
        },
    ]
    return decisions, outcomes, reward_traces, live_versions, version_history


def _shadow_inputs(kind: str) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    policy_key = "via.retrieval.selective"
    version_key = "shadow-v2"
    sample_count = {"hold": 1, "eligible": 6, "broader": 18}[kind]
    decisions = [
        {
            "decision_type": "shadow_eval",
            "trigger_type": "ignored",
            "session_key": f"s{index}",
            "chosen_action": {
                "shadow_version_key": version_key,
                "target": "retrieval_plan",
                "would_change": kind != "hold",
            },
        }
        for index in range(sample_count)
    ]
    outcomes = (
        []
        if kind == "hold"
        else [
            {
                "session_key": f"s{index}",
                "accepted": True,
                "abuse_flag": 0,
                "reward_score": 0.8,
            }
            for index in range(sample_count)
        ]
    )
    reward_traces: list[dict[str, Any]] = []
    if kind in {"eligible", "broader"}:
        reward_traces.append({"session_key": "s0", "event_type": "compare"})
    if kind == "broader":
        reward_traces.append({"session_key": "s1", "event_type": "purchase"})
    staged_versions = [
        {
            "policy_key": "unknown.policy",
            "version_key": "skip-rule",
            "version_label": "x",
        },
        {
            "policy_key": policy_key,
            "version_key": version_key,
            "version_label": "Shadow V2",
        },
    ]
    return decisions, outcomes, reward_traces, staged_versions


def test_control_window_complete_return_contract_matches_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _digest(summaries._summarize_control_window([], [], window_days=0)) == (
        LEGACY_RETURN_SHA256["control_empty"]
    )
    decisions, outcomes, traces, retrieval, routing, retention = _control_inputs()
    golden = json.loads(CONTROL_RICH_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["schema_version"] == "via_learning_control_summary_golden_v1"
    assert golden["baseline"] == CONTROL_RICH_BASELINE
    assert golden["input"]["clock_utc"] == CONTROL_RICH_FROZEN_AT.isoformat().replace(
        "+00:00", "Z"
    )
    input_binding = {
        "clock_utc": golden["input"]["clock_utc"],
        "decisions": decisions,
        "outcomes": outcomes,
        "reward_traces": traces,
        "retrieval_evidence": retrieval,
        "routing_stats": routing,
        "memory_retention": retention,
        "window_days": 14,
    }
    assert _digest(input_binding) == golden["input"]["sha256"]

    # The legacy function consulted wall clock time while calculating retention
    # age. Freeze that hidden input before comparing the split implementation to
    # the complete, reviewable pre-split return value.
    monkeypatch.setattr(summaries, "datetime", _FrozenControlSummaryDateTime)
    result = summaries._summarize_control_window(
        decisions,
        outcomes,
        reward_traces=traces,
        retrieval_evidence=retrieval,
        routing_stats=routing,
        memory_retention=retention,
        window_days=14,
    )
    assert result == golden["output"]["value"]
    assert _digest(result) == golden["output"]["sha256"]
    assert golden["output"]["sha256"] == LEGACY_RETURN_SHA256["control_rich"]
    assert result["recent_outcomes"][0]["decision_type"] == "reply_mode"
    assert result["recent_outcomes"][1]["decision_type"] == ""


@pytest.mark.parametrize("kind", ["healthy", "full", "hold", "rollback"])
def test_live_rollout_complete_return_contract_matches_legacy(kind: str) -> None:
    decisions, outcomes, traces, versions, history = _live_inputs(kind)
    result = summaries._summarize_live_rollout_health(
        decisions,
        outcomes,
        traces,
        versions,
        window_days=14,
        version_history=history,
    )
    assert _digest(result) == LEGACY_RETURN_SHA256[f"live_{kind}"]


def test_live_rollout_empty_and_bad_timestamp_fallback_match_legacy() -> None:
    assert _digest(
        summaries._summarize_live_rollout_health([], [], [], [], window_days=0)
    ) == LEGACY_RETURN_SHA256["live_empty"]
    decisions, outcomes, traces, versions, history = _live_inputs("healthy")
    for item in outcomes:
        item["created_at"] = "not-a-timestamp"
    result = summaries._summarize_live_rollout_health(
        decisions,
        outcomes,
        traces,
        versions,
        window_days=14,
        version_history=history,
    )
    assert _digest(result) == LEGACY_RETURN_SHA256["live_healthy"]


@pytest.mark.parametrize("kind", ["hold", "eligible", "broader"])
def test_shadow_rollout_complete_return_contract_matches_legacy(kind: str) -> None:
    decisions, outcomes, traces, versions = _shadow_inputs(kind)
    result = summaries._summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        traces,
        versions,
        window_days=14,
    )
    assert _digest(result) == LEGACY_RETURN_SHA256[f"shadow_{kind}"]


def test_shadow_rollout_empty_return_contract_matches_legacy() -> None:
    result = summaries._summarize_shadow_rollout_readiness(
        [], [], [], [], window_days=0
    )
    assert _digest(result) == LEGACY_RETURN_SHA256["shadow_empty"]


def test_control_summary_calls_subsummaries_in_legacy_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def retrieval(rows: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append(("retrieval", rows))
        return {"evidence_count": 0}

    def routing(rows: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append(("routing", rows))
        return {"provider_count": 0}

    def memory(rows: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append(("memory", rows))
        return {"tracked": 0}

    monkeypatch.setattr(summaries, "_summarize_retrieval_evidence", retrieval)
    monkeypatch.setattr(summaries, "_summarize_routing_learner_stats", routing)
    monkeypatch.setattr(summaries, "_summarize_memory_retention", memory)
    summaries._summarize_control_window(
        [],
        [],
        retrieval_evidence=[{"kind": "r"}],
        routing_stats=[{"kind": "p"}],
        memory_retention=[{"kind": "m"}],
        window_days=14,
    )
    assert calls == [
        ("retrieval", [{"kind": "r"}]),
        ("routing", [{"kind": "p"}]),
        ("memory", [{"kind": "m"}]),
    ]


def test_numeric_conversion_failures_remain_visible() -> None:
    with pytest.raises(ValueError):
        summaries._summarize_control_window(
            [{"cost_estimate": "not-a-number"}],
            [],
            window_days=14,
        )
    with pytest.raises(ValueError):
        summaries._summarize_live_rollout_health(
            [],
            [],
            [],
            [
                {
                    "policy_key": "via.retrieval.selective",
                    "config": {
                        "rollout_mode": "limited",
                        "rollout_percentage": "not-a-number",
                    },
                }
            ],
            window_days=14,
        )


def test_summary_split_complexity_size_and_dependency_direction_are_bounded() -> None:
    paths = [
        Path("backend/app/services/memory/via_learning_summaries.py"),
        Path("backend/app/services/memory/via_learning_control_summary.py"),
        Path("backend/app/services/memory/via_learning_live_rollout_summary.py"),
        Path("backend/app/services/memory/via_learning_shadow_rollout_summary.py"),
    ]
    trees = {str(path): ast.parse(path.read_text(encoding="utf-8")) for path in paths}
    rows = collect_complexity(trees)
    target_names = {
        "_summarize_control_window",
        "_summarize_live_rollout_health",
        "_summarize_shadow_rollout_readiness",
        "summarize_control_window",
        "summarize_live_rollout_health",
        "summarize_shadow_rollout_readiness",
    }
    target_rows = [row for row in rows if row.qualified_name in target_names]

    assert {row.qualified_name for row in target_rows} == target_names
    assert max(row.cc for row in target_rows) <= 30
    leaf_rows = [row for row in rows if row.path in {str(path) for path in paths[1:]}]
    assert max(row.cc for row in leaf_rows) < 50
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 800 for path in paths)

    for leaf_path in paths[1:]:
        imported_modules = {
            node.module or ""
            for node in ast.walk(trees[str(leaf_path)])
            if isinstance(node, ast.ImportFrom)
        }
        assert "app.services.memory.via_learning_summaries" not in imported_modules
