"""Private response-generation stage for Via conversational turns."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from app.db.repositories.via_control import insert_via_decision_record
from app.services.via.decision_ledger import build_decision_candidates, estimate_model_cost
from app.services.via.external_viltrox_assets import sanitize_external_via_output
from app.services.via.policy_registry import get_via_policy, get_via_shadow_policy
from app.services.via.session_generation import _generate_via_reply_with_ai, compose_via_reply
from app.services.via.session_guidance import _should_use_ai_dialogue
from app.services.via.session_reward import _record_shadow_eval
from app.services.via.shadow_learning import evaluate_shadow_model_choice

async def _apply_guarded_reply(state: SimpleNamespace, reply: dict[str, Any]) -> dict[str, Any]:
    session_key = state.session_key
    session = state.session
    persona = state.persona
    text = state.text
    surface = state.surface
    guarded = state.guarded
    route = state.route
    policy_route = state.policy_route
    refreshed_bundle = state.refreshed_bundle
    model_policy = state.model_policy
    model_plan = state.model_plan
    trigger_snapshot = state.trigger_snapshot
    context_refs = state.context_refs
    decision_records = state.decision_records
    reply["title"] = guarded["title"]
    reply["text"] = guarded["text"]
    if guarded.get("quick_actions"):
        reply["payload"]["quick_actions"] = guarded["quick_actions"]
    reply["payload"]["provider"] = guarded["provider"]
    risk_policy = get_via_policy("risk_gate", route_info={**policy_route, "guarded": True})
    risk_decision = await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="risk_gate",
        trigger_type=str(guarded.get("provider") or "policy_guard"),
        trigger_payload={"guard_title": guarded.get("title") or "", "guard_provider": guarded.get("provider") or ""},
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=[],
        chosen_action={"mode": "policy_guard", "provider": guarded.get("provider") or ""},
        policy_key=str(risk_policy.get("policy_key") or ""),
        policy_version=str(risk_policy.get("policy_version") or ""),
        context_refs=context_refs,
        latency_ms=0.0,
    )
    decision_records.append(risk_decision)
    reply_policy = get_via_policy("reply_mode", route_info={**policy_route, "guarded": True})
    reply_mode_decision = await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="reply_mode",
        trigger_type=str(guarded.get("provider") or "policy_guard"),
        trigger_payload={"guarded": True},
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=build_decision_candidates("reply_mode", route_info=policy_route, guarded=guarded),
        chosen_action={"mode": "policy_guard", "provider": guarded.get("provider") or ""},
        policy_key=str(reply_policy.get("policy_key") or ""),
        policy_version=str(reply_policy.get("policy_version") or ""),
        context_refs=context_refs,
    )
    decision_records.append(reply_mode_decision)
    return reply_mode_decision


async def _apply_ai_reply(state: SimpleNamespace, reply: dict[str, Any]) -> dict[str, Any]:
    session_key = state.session_key
    session = state.session
    persona = state.persona
    text = state.text
    surface = state.surface
    guarded = state.guarded
    route = state.route
    policy_route = state.policy_route
    refreshed_bundle = state.refreshed_bundle
    model_policy = state.model_policy
    model_plan = state.model_plan
    trigger_snapshot = state.trigger_snapshot
    context_refs = state.context_refs
    decision_records = state.decision_records
    ai_started_at = time.perf_counter()
    ai_reply = await _generate_via_reply_with_ai(
        refreshed_bundle,
        text,
        current_surface=surface,
        route_info=policy_route,
        model_policy=model_policy,
        reply_payload=reply["payload"],
    )
    ai_latency_ms = (time.perf_counter() - ai_started_at) * 1000.0
    if ai_reply:
        sanitized_text = sanitize_external_via_output(str(ai_reply.get("text") or ""))
        if sanitized_text:
            ai_reply["text"] = sanitized_text
        reply["title"] = ai_reply["title"]
        reply["text"] = ai_reply["text"]
        if ai_reply.get("quick_actions"):
            reply["payload"]["quick_actions"] = ai_reply["quick_actions"]
        reply["payload"]["provider"] = ai_reply["provider"]
        reply["payload"]["model"] = ai_reply["model"]
        if ai_reply.get("providers"):
            reply["payload"]["providers"] = ai_reply["providers"]
        if ai_reply.get("models"):
            reply["payload"]["models"] = ai_reply["models"]
        if ai_reply.get("strategy"):
            reply["payload"]["provider_strategy"] = ai_reply["strategy"]
        model_decision = await asyncio.to_thread(
            insert_via_decision_record,
            session_key=session_key,
            session_id=int(session.get("id") or 0),
            user_id=int(session.get("user_id") or 0),
            persona_id=int(persona.get("id") or 0),
            decision_type="model_choice",
            trigger_type="dialogue_generation",
            trigger_payload={"strategy": ai_reply.get("strategy") or "single"},
            state_snapshot=trigger_snapshot.get("state_snapshot") or {},
            candidates=build_decision_candidates("model_choice", route_info=policy_route, model_plan=model_plan),
            chosen_action={
                "provider": ai_reply.get("provider") or "",
                "model": ai_reply.get("model") or "",
                "providers": ai_reply.get("providers") or [],
                "models": ai_reply.get("models") or [],
                "strategy": ai_reply.get("strategy") or "single",
            },
            policy_key=str(model_policy.get("policy_key") or ""),
            policy_version=str(model_policy.get("policy_version") or ""),
            context_refs=context_refs,
            latency_ms=ai_latency_ms,
            cost_estimate=estimate_model_cost(
                model=str(ai_reply.get("model") or ""),
                provider=str(ai_reply.get("provider") or ""),
                input_text=text,
                output_text=reply["text"],
                collab_count=len(list(ai_reply.get("providers") or [])) or 1,
            ),
        )
        decision_records.append(model_decision)
        model_shadow_policy = get_via_shadow_policy("model_choice", route_info=policy_route)
        model_shadow_eval = evaluate_shadow_model_choice(
            route_info=policy_route,
            live_policy=model_policy,
            shadow_policy=model_shadow_policy,
            model_plan=model_plan,
        )
        model_shadow_decision = await _record_shadow_eval(
            session_key=session_key,
            session=session,
            persona=persona,
            trigger_snapshot=trigger_snapshot,
            context_refs=context_refs,
            target="model_choice",
            shadow_eval=model_shadow_eval,
            candidates=build_decision_candidates("model_choice", route_info=policy_route, model_plan=model_plan),
        )
        if model_shadow_decision:
            decision_records.append(model_shadow_decision)
    else:
        reply["payload"]["provider"] = "fallback"
    reply_policy = get_via_policy("reply_mode", route_info=policy_route)
    reply_mode_decision = await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="reply_mode",
        trigger_type="dialogue_generation",
        trigger_payload={"ai": True},
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=build_decision_candidates("reply_mode", route_info=policy_route),
        chosen_action={
            "mode": "ai_dialogue" if ai_reply else "fallback",
            "provider": reply["payload"].get("provider") or "",
            "model": reply["payload"].get("model") or "",
            "strategy": reply["payload"].get("provider_strategy") or "single",
        },
        policy_key=str(reply_policy.get("policy_key") or ""),
        policy_version=str(reply_policy.get("policy_version") or ""),
        context_refs=context_refs,
    )
    decision_records.append(reply_mode_decision)
    return reply_mode_decision


async def _apply_fast_reply(state: SimpleNamespace, reply: dict[str, Any]) -> dict[str, Any]:
    session_key = state.session_key
    session = state.session
    persona = state.persona
    text = state.text
    surface = state.surface
    guarded = state.guarded
    route = state.route
    policy_route = state.policy_route
    refreshed_bundle = state.refreshed_bundle
    model_policy = state.model_policy
    model_plan = state.model_plan
    trigger_snapshot = state.trigger_snapshot
    context_refs = state.context_refs
    decision_records = state.decision_records
    reply["payload"]["provider"] = (
        "business_brain"
        if reply["payload"].get("business_mode")
        else "product_brain"
        if reply["payload"].get("product_mode")
        else "rule_brain"
    )
    reply_policy = get_via_policy("reply_mode", route_info=policy_route)
    reply_mode_decision = await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="reply_mode",
        trigger_type=str(route.get("brain") or "fast_brain"),
        trigger_payload={"fast_path": True},
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=build_decision_candidates("reply_mode", route_info=policy_route),
        chosen_action={
            "mode": "fast_brain",
            "provider": reply["payload"].get("provider") or "",
            "product_mode": bool(reply["payload"].get("product_mode")),
            "business_mode": bool(reply["payload"].get("business_mode")),
        },
        policy_key=str(reply_policy.get("policy_key") or ""),
        policy_version=str(reply_policy.get("policy_version") or ""),
        context_refs=context_refs,
    )
    decision_records.append(reply_mode_decision)
    return reply_mode_decision


async def _generate_reply(state: SimpleNamespace) -> None:
    session_key = state.session_key
    current_surface = state.current_surface
    session = state.session
    persona = state.persona
    text = state.text
    surface = state.surface
    guarded = state.guarded
    route = state.route
    policy_route = state.policy_route
    refreshed_bundle = state.refreshed_bundle
    vector_refs = state.vector_refs
    model_policy = state.model_policy
    model_plan = state.model_plan
    trigger_snapshot = state.trigger_snapshot
    context_refs = state.context_refs
    decision_records = state.decision_records
    intent_decision = state.intent_decision
    retrieval_evidence_row = state.retrieval_evidence_row
    reply = compose_via_reply(
        refreshed_bundle,
        text,
        current_surface=current_surface or session.get("current_surface") or "upload",
        route_info=policy_route,
    )
    if retrieval_evidence_row:
        reply["payload"]["retrieval_evidence"] = retrieval_evidence_row
    reply["payload"]["intent_route"] = {
        "intent": route.get("intent") or "quick_chat",
        "brain": route.get("brain") or "quick_chat",
        "used_memory_refs": len(vector_refs),
        "used_deep_reasoning": bool(route.get("use_deep_reasoning")),
    }
    reply_mode_decision = intent_decision
    if guarded:
        reply_mode_decision = await _apply_guarded_reply(state, reply)
    elif _should_use_ai_dialogue(route, reply["payload"]):
        reply_mode_decision = await _apply_ai_reply(state, reply)
    else:
        reply_mode_decision = await _apply_fast_reply(state, reply)
    state.reply = reply
    state.reply_mode_decision = reply_mode_decision
