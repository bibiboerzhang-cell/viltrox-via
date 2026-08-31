from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any

from app.services.via import session_reply
from app.services.via import session_reply_orchestration as orchestration
from app.services.via import session_reply_response as response


class _EventBus:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def publish(self, _session_key: str, event_type: str, _payload: dict[str, Any]) -> str:
        self.calls.append(f"publish:{event_type}")
        return f"event-{event_type}"


def test_public_signature_remains_keyword_only() -> None:
    signature = inspect.signature(session_reply.reply_in_via_session)
    assert list(signature.parameters) == ["session_key", "user_text", "current_surface", "event_bus"]
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in signature.parameters.values())


def test_fast_reply_preserves_event_and_persistence_order(monkeypatch) -> None:
    calls: list[str] = []
    bundle = {
        "session": {"id": 7, "user_id": 9, "current_surface": "upload", "state": {}},
        "persona": {"id": 11, "affinity_points": 2, "wardrobe_points": 3, "profile": {}},
        "memory_refs": [],
    }

    def get_bundle(_key: str, _limit: int) -> dict[str, Any]:
        calls.append("get_bundle")
        return bundle

    def update_persona(_persona_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        calls.append("update_persona")
        return {**bundle["persona"], **patch}

    def insert_decision(**kwargs: Any) -> dict[str, Any]:
        decision_type = str(kwargs["decision_type"])
        calls.append(f"decision:{decision_type}")
        return {
            "decision_id": f"decision-{decision_type}",
            "decision_type": decision_type,
            "policy_key": kwargs.get("policy_key", ""),
            "policy_version": kwargs.get("policy_version", ""),
        }

    def insert_outcome(**kwargs: Any) -> dict[str, Any]:
        calls.append("outcome")
        return {**kwargs, "created_at": "2026-08-29T00:00:00Z"}

    def touch_session(_key: str, **kwargs: Any) -> dict[str, Any]:
        calls.append("touch_session")
        return {"id": 7, **kwargs}

    monkeypatch.setattr(orchestration, "get_via_session_bundle", get_bundle)
    monkeypatch.setattr(orchestration, "update_via_persona", update_persona)
    monkeypatch.setattr(orchestration, "_guard_sensitive_request", lambda _text: None)
    monkeypatch.setattr(
        orchestration,
        "_classify_via_intent",
        lambda *_args, **_kwargs: {"intent": "quick_chat", "brain": "quick_chat", "needs_memory": False},
    )
    monkeypatch.setattr(orchestration, "get_via_policy", lambda key, **_kwargs: {"policy_key": key, "policy_version": "v1"})
    monkeypatch.setattr(orchestration, "_resolve_retrieval_execution", lambda *_args: {"use_vector": False})
    monkeypatch.setattr(orchestration, "get_via_model_plan", lambda **_kwargs: {"provider": "none"})
    monkeypatch.setattr(orchestration, "build_via_trigger_snapshot", lambda *_args, **_kwargs: {"primary_trigger": "turn", "state_snapshot": {}})
    monkeypatch.setattr(orchestration, "build_context_refs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestration, "build_decision_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(orchestration, "insert_via_decision_record", insert_decision)
    monkeypatch.setattr(orchestration, "_persist_via_learning", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        orchestration,
        "aggregate_via_reply_outcome",
        lambda **_kwargs: {"accepted": True, "reward_score": 1.0, "outcome_payload": {}},
    )
    monkeypatch.setattr(orchestration, "insert_via_outcome_record", insert_outcome)
    monkeypatch.setattr(orchestration, "propose_via_memory_promotions", lambda **_kwargs: [])
    monkeypatch.setattr(orchestration, "list_via_memory_retention_stats", lambda _limit: [])
    monkeypatch.setattr(orchestration, "get_via_shadow_policy", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(orchestration, "evaluate_shadow_memory_promotion", lambda **_kwargs: {})

    async def no_shadow(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(orchestration, "_record_shadow_eval", no_shadow)
    monkeypatch.setattr(orchestration, "summarize_control_loop", lambda **_kwargs: {"policy_versions": {}})
    monkeypatch.setattr(orchestration, "resolve_via_activity_state", lambda **_kwargs: {"mode": "idle"})
    monkeypatch.setattr(orchestration, "touch_via_session", touch_session)

    monkeypatch.setattr(
        response,
        "compose_via_reply",
        lambda *_args, **_kwargs: {
            "title": "Via",
            "text": "hello",
            "payload": {"persona": {}, "product_mode": False, "business_mode": False},
        },
    )
    monkeypatch.setattr(response, "_should_use_ai_dialogue", lambda *_args: False)
    monkeypatch.setattr(response, "get_via_policy", lambda key, **_kwargs: {"policy_key": key, "policy_version": "v1"})
    monkeypatch.setattr(response, "build_decision_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(response, "insert_via_decision_record", insert_decision)

    result = asyncio.run(
        session_reply.reply_in_via_session(
            session_key="session-1",
            user_text="hello",
            current_surface="upload",
            event_bus=_EventBus(calls),
        )
    )

    assert result["user_event_id"] == "event-user_message"
    assert result["reply_event_id"] == "event-via_reply"
    assert result["reply"]["payload"]["provider"] == "rule_brain"
    assert calls == [
        "get_bundle",
        "publish:user_message",
        "update_persona",
        "get_bundle",
        "decision:intent_route",
        "decision:reply_mode",
        "outcome",
        "publish:via_reply",
        "touch_session",
    ]


def test_missing_bundle_short_circuits_without_publishing(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(orchestration, "get_via_session_bundle", lambda *_args: None)
    result = asyncio.run(
        session_reply.reply_in_via_session(
            session_key="missing",
            user_text="hello",
            event_bus=_EventBus(calls),
        )
    )
    assert result == {}
    assert calls == []


def test_memory_retrieval_decision_uses_pre_vector_bundle_snapshot(monkeypatch) -> None:
    bundle_refs = [{"memory_id": "bundle-1"}]
    observed: dict[str, Any] = {}

    def build_evidence(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {
            "candidate_sources": ["bundle_memory"],
            "selected_sources": ["bundle_memory"],
            "bundle_hit_count": 1,
            "seed_hit_count": 0,
            "top_score": 0.0,
            "avg_score": 0.0,
            "score_spread": 0.0,
            "rerank_applied": False,
        }

    monkeypatch.setattr(orchestration, "_build_retrieval_evidence", build_evidence)
    monkeypatch.setattr(orchestration, "build_decision_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        orchestration,
        "insert_via_decision_record",
        lambda **kwargs: {"decision_id": "retrieval-1", **kwargs},
    )
    monkeypatch.setattr(
        orchestration,
        "insert_via_retrieval_evidence",
        lambda **kwargs: {"evidence_id": "evidence-1", **kwargs},
    )
    monkeypatch.setattr(orchestration, "get_via_shadow_policy", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(orchestration, "evaluate_shadow_retrieval_plan", lambda **_kwargs: {})

    async def no_shadow(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(orchestration, "_record_shadow_eval", no_shadow)
    state = SimpleNamespace(
        route={"needs_memory": True},
        retrieval_execution={"plan": "bundle_memory_only", "use_vector": False},
        retrieval_policy={"policy_key": "retrieval_plan", "policy_version": "v1"},
        vector_refs=[],
        bundle_memory_refs_before_vector=bundle_refs,
        session_key="session-memory",
        session={"id": 7, "user_id": 9},
        persona={"id": 11},
        trigger_snapshot={"state_snapshot": {}},
        policy_route={},
        context_refs=[],
        decision_records=[],
        refreshed_bundle={"memory_refs": bundle_refs},
        retrieval_latency_ms=1.25,
    )

    result = asyncio.run(orchestration._record_retrieval_plan_decisions(state))

    assert result and result["decision_id"] == "retrieval-1"
    assert observed["bundle_memory_refs"] is bundle_refs
