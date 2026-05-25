"""Via conversational turn orchestration."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.repositories.via_control import (
    get_latest_via_outcome_record,
    get_via_reward_trace_by_idempotency,
    insert_via_decision_record,
    insert_via_outcome_record,
    insert_via_retrieval_evidence,
    insert_via_reward_trace,
    list_via_decision_records,
    list_via_memory_retention_stats,
    list_via_reward_traces,
    upsert_via_memory_retention_stat,
    upsert_via_routing_provider_stat,
    update_via_outcome_record,
)
from app.core.config import VIA_BASE_MODEL
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.via import (
    add_via_memory_ref,
    create_via_session,
    find_via_session,
    get_or_create_via_persona,
    get_via_session_bundle,
    touch_via_session,
    update_via_persona,
)
from app.services.memory.l3_store import record_creator_memory_fact, record_feedback_signal
from app.services.via.activity_pack import resolve_via_activity_state
from app.services.via.business_brain import build_business_context, get_via_business_reply
from app.services.via.decision_ledger import (
    build_context_refs,
    build_decision_candidates,
    estimate_model_cost,
    summarize_control_loop,
)
from app.services.via.external_viltrox_assets import (
    get_external_system_prompt_injection,
    sanitize_external_via_output,
)
from app.services.via.memory_promoter import persist_via_memory_promotions, propose_via_memory_promotions
from app.services.via.model_router import generate_json_with_collab, generate_json_with_route, get_via_model_plan, preview_via_routes
from app.services.via.policy_registry import get_via_policy, get_via_shadow_policy
from app.services.via.product_brain import build_product_context, get_via_product_reply
from app.services.via.reward_aggregator import (
    aggregate_via_reply_outcome,
    merge_via_reward_trace_summary,
    summarize_via_reward_traces,
)
from app.services.via.shadow_learning import (
    evaluate_shadow_memory_promotion,
    evaluate_shadow_model_choice,
    evaluate_shadow_retrieval_plan,
)
from app.services.via.trigger_engine import build_via_trigger_snapshot
from app.services.via.learning_signals import (
    compact_via_profile_context,
    extract_via_learning_signals,
    merge_via_persona_profile,
)
from app.services.via.knowledge_seed import (
    build_via_seed_documents,
    extract_workspace_docx_product_line_catalog,
    extract_workspace_docx_software_catalog,
)
from app.services.via.vector_memory import (
    recall_via_vector_memory,
    store_via_seed_documents,
    store_via_vector_exchange,
    sync_bundle_memory_refs_to_vector,
)

logger = get_logger(__name__)


from app.services.via.session_generation import (
    _generate_via_reply_with_ai,
    _resolve_retrieval_execution,
    compose_via_reply,
)
from app.services.via.session_guidance import (
    _classify_via_intent,
    _guard_sensitive_request,
    _should_use_ai_dialogue,
)
from app.services.via.session_memory import _persist_via_learning
from app.services.via.session_reward import (
    _build_retrieval_evidence,
    _control_source_ref,
    _record_shadow_eval,
    _reinforce_memory_retention,
    _routing_bucket_key,
)

async def reply_in_via_session(
    *,
    session_key: str,
    user_text: str,
    current_surface: str = "",
    event_bus: Any = None,
) -> dict[str, Any]:
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    if not bundle or event_bus is None:
        return {}
    session = bundle.get("session") or {}
    persona = bundle.get("persona") or {}
    text = str(user_text or "").strip()
    if not text:
        return {}
    surface = current_surface or session.get("current_surface") or "upload"
    guarded = _guard_sensitive_request(text)
    route_started_at = time.perf_counter()
    route = _classify_via_intent(
        bundle,
        text,
        current_surface=surface,
    )
    policy_route = {
        **route,
        "session_key": session_key,
        "session_id": int(session.get("id") or 0),
        "user_id": int(session.get("user_id") or 0),
        "client_fingerprint": str(session.get("client_fingerprint") or ""),
    }
    route_latency_ms = (time.perf_counter() - route_started_at) * 1000.0
    try:
        user_event_id = await event_bus.publish(
            session_key,
            "user_message",
            {
                "title": "You",
                "text": text[:500],
                "surface": surface,
            },
        )
    except Exception:
        logger.warning("via.user_message_publish_failed", extra={"session_key": session_key}, exc_info=True)
        user_event_id = ""
    persona_patch = {
        "affinity_points": int(persona.get("affinity_points") or 0) + 1,
        "wardrobe_points": int(persona.get("wardrobe_points") or 0) + (1 if any(token in text.lower() for token in ("upload", "video", "cat", "outfit", "look", "style")) else 0),
    }
    persona = await asyncio.to_thread(update_via_persona, int(bundle["persona"]["id"]), persona_patch)
    refreshed_bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    vector_refs: list[dict[str, Any]] = []
    bundle_memory_refs_before_vector = list(refreshed_bundle.get("memory_refs") or [])
    retrieval_latency_ms = 0.0
    retrieval_policy = get_via_policy("retrieval_plan", route_info=policy_route) if route.get("needs_memory") else {}
    retrieval_execution = _resolve_retrieval_execution(policy_route, retrieval_policy)
    if route.get("needs_memory"):
        retrieval_started_at = time.perf_counter()
        if retrieval_execution.get("use_vector"):
            vector_refs = await recall_via_vector_memory(
                refreshed_bundle,
                text,
                limit=int(retrieval_execution.get("vector_limit") or 6),
            )
        retrieval_latency_ms = (time.perf_counter() - retrieval_started_at) * 1000.0
        if vector_refs:
            refreshed_bundle["memory_refs"] = vector_refs + list(refreshed_bundle.get("memory_refs") or [])
    model_policy = get_via_policy("model_choice", route_info=policy_route)
    model_plan = get_via_model_plan(policy=model_policy, route_info=policy_route)
    trigger_snapshot = build_via_trigger_snapshot(
        refreshed_bundle,
        text,
        current_surface=surface,
        route_info=route,
        guarded=guarded,
        vector_refs=vector_refs,
    )
    context_refs = build_context_refs(refreshed_bundle, vector_refs=vector_refs)
    decision_records: list[dict[str, Any]] = []
    outcome_records: list[dict[str, Any]] = []

    intent_policy = get_via_policy("intent_route", route_info=policy_route)
    intent_decision = await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="intent_route",
        trigger_type=str(trigger_snapshot.get("primary_trigger") or ""),
        trigger_payload={
            "semantic": trigger_snapshot.get("semantic") or [],
            "business": trigger_snapshot.get("business") or [],
            "risk": trigger_snapshot.get("risk") or [],
            "confidence": trigger_snapshot.get("confidence") or [],
            "learning": trigger_snapshot.get("learning") or [],
        },
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=build_decision_candidates("intent_route", route_info=policy_route),
        chosen_action={
            "intent": route.get("intent") or "quick_chat",
            "brain": route.get("brain") or "quick_chat",
            "needs_memory": bool(route.get("needs_memory")),
            "use_deep_reasoning": bool(route.get("use_deep_reasoning")),
            "confidence_score": float(trigger_snapshot.get("confidence_score") or 0.0),
        },
        policy_key=str(intent_policy.get("policy_key") or ""),
        policy_version=str(intent_policy.get("policy_version") or ""),
        context_refs=context_refs,
        latency_ms=route_latency_ms,
    )
    decision_records.append(intent_decision)
    retrieval_evidence_row: dict[str, Any] | None = None

    if route.get("needs_memory"):
        retrieval_evidence = _build_retrieval_evidence(
            retrieval_execution=retrieval_execution,
            retrieval_policy=retrieval_policy,
            vector_refs=vector_refs,
            bundle_memory_refs=bundle_memory_refs_before_vector,
        )
        retrieval_decision = await asyncio.to_thread(
            insert_via_decision_record,
            session_key=session_key,
            session_id=int(session.get("id") or 0),
            user_id=int(session.get("user_id") or 0),
            persona_id=int(persona.get("id") or 0),
            decision_type="retrieval_plan",
            trigger_type="memory_required",
            trigger_payload={"vector_ref_count": len(vector_refs)},
            state_snapshot=trigger_snapshot.get("state_snapshot") or {},
            candidates=build_decision_candidates(
                "retrieval_plan",
                route_info=policy_route,
                vector_refs=vector_refs,
            ),
            chosen_action={
                "plan": str(retrieval_execution.get("plan") or ("vector_memory" if vector_refs else "bundle_memory_only")),
                "vector_ref_count": len(vector_refs),
                "vector_limit": int(retrieval_execution.get("vector_limit") or 0),
                "retrieval_mode": str(retrieval_execution.get("retrieval_mode") or ""),
                "candidate_sources": retrieval_evidence.get("candidate_sources") or [],
                "selected_sources": retrieval_evidence.get("selected_sources") or [],
                "bundle_hit_count": int(retrieval_evidence.get("bundle_hit_count") or 0),
                "seed_hit_count": int(retrieval_evidence.get("seed_hit_count") or 0),
                "top_score": float(retrieval_evidence.get("top_score") or 0.0),
                "avg_score": float(retrieval_evidence.get("avg_score") or 0.0),
                "score_spread": float(retrieval_evidence.get("score_spread") or 0.0),
                "rerank_applied": bool(retrieval_evidence.get("rerank_applied")),
            },
            policy_key=str(retrieval_policy.get("policy_key") or ""),
            policy_version=str(retrieval_policy.get("policy_version") or ""),
            context_refs=context_refs,
            latency_ms=retrieval_latency_ms,
        )
        decision_records.append(retrieval_decision)
        retrieval_evidence_row = await asyncio.to_thread(
            insert_via_retrieval_evidence,
            session_key=session_key,
            decision_id=str(retrieval_decision.get("decision_id") or ""),
            policy_key=str(retrieval_policy.get("policy_key") or ""),
            policy_version=str(retrieval_policy.get("policy_version") or ""),
            retrieval_mode=str(retrieval_execution.get("retrieval_mode") or ""),
            candidate_sources=retrieval_evidence.get("candidate_sources") or [],
            selected_sources=retrieval_evidence.get("selected_sources") or [],
            vector_hit_count=int(retrieval_evidence.get("vector_hit_count") or 0),
            bundle_hit_count=int(retrieval_evidence.get("bundle_hit_count") or 0),
            seed_hit_count=int(retrieval_evidence.get("seed_hit_count") or 0),
            vector_limit=int(retrieval_evidence.get("vector_limit") or 0),
            top_score=float(retrieval_evidence.get("top_score") or 0.0),
            avg_score=float(retrieval_evidence.get("avg_score") or 0.0),
            score_spread=float(retrieval_evidence.get("score_spread") or 0.0),
            rerank_applied=bool(retrieval_evidence.get("rerank_applied")),
            rerank_summary=retrieval_evidence.get("rerank_summary") or {},
            evidence_payload=retrieval_evidence.get("evidence_payload") or {},
        )
        retrieval_shadow_policy = get_via_shadow_policy("retrieval_plan", route_info=policy_route)
        retrieval_shadow_eval = evaluate_shadow_retrieval_plan(
            route_info=policy_route,
            live_policy=retrieval_policy,
            shadow_policy=retrieval_shadow_policy,
            vector_refs=vector_refs,
            bundle_memory_count=len(list(refreshed_bundle.get("memory_refs") or [])),
            live_evidence=retrieval_evidence,
        )
        retrieval_shadow_decision = await _record_shadow_eval(
            session_key=session_key,
            session=session,
            persona=persona,
            trigger_snapshot=trigger_snapshot,
            context_refs=context_refs,
            target="retrieval_plan",
            shadow_eval=retrieval_shadow_eval,
            candidates=build_decision_candidates("retrieval_plan", route_info=policy_route, vector_refs=vector_refs),
        )
        if retrieval_shadow_decision:
            decision_records.append(retrieval_shadow_decision)

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
    elif _should_use_ai_dialogue(route, reply["payload"]):
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
    else:
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
    learning = await asyncio.to_thread(
        _persist_via_learning,
        refreshed_bundle,
        text,
        reply["text"],
        current_surface=surface,
    )
    if learning.get("persona"):
        persona = learning["persona"]
        reply["payload"]["persona"]["profile"] = compact_via_profile_context(persona.get("profile") or {})
    if learning.get("signals"):
        reply["payload"]["learning"] = {
            "summary": learning["signals"].get("summary") or "",
            "keywords": learning["signals"].get("keywords") or [],
        }
        vector_memory = await store_via_vector_exchange(
            refreshed_bundle,
            user_text=text,
            reply_text=reply["text"],
            signals=learning["signals"],
            current_surface=surface,
        )
        if vector_memory.get("upserted"):
            reply["payload"]["vector_memory"] = {
                "backend": vector_memory.get("backend") or "none",
                "summary": vector_memory.get("summary") or "",
                "keywords": vector_memory.get("keywords") or [],
                "provider": vector_memory.get("provider") or "",
                "model": vector_memory.get("model") or "",
            }
    reply_outcome = aggregate_via_reply_outcome(
        session_state=session.get("state") or {},
        route_info=route,
        reply=reply,
        user_text=text,
        guarded=guarded,
        vector_refs=vector_refs,
        learning_signals=learning.get("signals") or {},
    )
    primary_outcome = await asyncio.to_thread(
        insert_via_outcome_record,
        decision_id=str(reply_mode_decision.get("decision_id") or intent_decision.get("decision_id") or ""),
        session_key=session_key,
        accepted=bool(reply_outcome.get("accepted")),
        followup_depth=int(reply_outcome.get("followup_depth") or 0),
        rephrase_needed=bool(reply_outcome.get("rephrase_needed")),
        clicked_product=bool(reply_outcome.get("clicked_product")),
        added_to_cart=bool(reply_outcome.get("added_to_cart")),
        purchased=bool(reply_outcome.get("purchased")),
        thumb_feedback=int(reply_outcome.get("thumb_feedback") or 0),
        abuse_flag=int(reply_outcome.get("abuse_flag") or 0),
        reward_score=float(reply_outcome.get("reward_score") or 0.0),
        outcome_payload=reply_outcome.get("outcome_payload") or {},
    )
    outcome_records.append(primary_outcome)
    routing_provider = str(reply["payload"].get("provider") or "")
    if routing_provider and routing_provider not in {"product_brain", "business_brain", "rule_brain", "identity", "policy"}:
        await asyncio.to_thread(
            upsert_via_routing_provider_stat,
            bucket_key=_routing_bucket_key(route, surface),
            provider=routing_provider,
            exposure_increment=1,
            success_increment=1 if bool(primary_outcome.get("accepted")) and int(primary_outcome.get("abuse_flag") or 0) <= 0 else 0,
            reward_delta=float(primary_outcome.get("reward_score") or 0.0),
            guard_fail_increment=1 if int(primary_outcome.get("abuse_flag") or 0) > 0 else 0,
            latency_ms=float((reply_mode_decision or {}).get("latency_ms") or 0.0),
            cost_estimate=float((reply_mode_decision or {}).get("cost_estimate") or 0.0),
            last_outcome_at=str(primary_outcome.get("created_at") or ""),
            metrics={
                "intent": route.get("intent") or "",
                "surface": surface,
                "strategy": reply["payload"].get("provider_strategy") or "",
                "policy_version": reply["payload"].get("model") or "",
            },
        )
    reply["payload"]["reward_trace_target"] = {
        "session_key": session_key,
        "decision_id": str(reply_mode_decision.get("decision_id") or intent_decision.get("decision_id") or ""),
        "policy_key": str(reply_mode_decision.get("policy_key") or intent_decision.get("policy_key") or ""),
        "policy_version": str(reply_mode_decision.get("policy_version") or intent_decision.get("policy_version") or ""),
    }

    control_source_ref = _control_source_ref(session_key, text, reply["text"])
    promotion_bundle = {**refreshed_bundle, "persona": persona}
    proposed_promotions = propose_via_memory_promotions(
        bundle=promotion_bundle,
        route_info=route,
        user_text=text,
        reply=reply,
        learning_signals=learning.get("signals") or {},
        reward_score=float(reply_outcome.get("reward_score") or 0.0),
        current_surface=surface,
    )
    retention_stats = await asyncio.to_thread(list_via_memory_retention_stats, 24)
    memory_shadow_policy = get_via_shadow_policy("memory_promotion", route_info=policy_route)
    memory_shadow_eval = evaluate_shadow_memory_promotion(
        live_policy=get_via_policy("memory_promotion", route_info=policy_route),
        shadow_policy=memory_shadow_policy,
        promotions=proposed_promotions,
        learning_signals=learning.get("signals") or {},
        reward_score=float(reply_outcome.get("reward_score") or 0.0),
        retention_stats=retention_stats,
    )
    memory_shadow_decision = await _record_shadow_eval(
        session_key=session_key,
        session=session,
        persona=persona,
        trigger_snapshot=trigger_snapshot,
        context_refs=context_refs,
        target="memory_promotion",
        shadow_eval=memory_shadow_eval,
        candidates=build_decision_candidates("memory_promotion"),
    )
    if memory_shadow_decision:
        decision_records.append(memory_shadow_decision)
    persisted_promotions = await asyncio.to_thread(
        persist_via_memory_promotions,
        promotion_bundle,
        proposed_promotions,
        source_ref=control_source_ref,
    ) if proposed_promotions else []
    if refreshed_bundle.get("memory_refs"):
        retention_updates = await asyncio.to_thread(
            _reinforce_memory_retention,
            session_key=session_key,
            user_id=int(session.get("user_id") or 0),
            current_surface=surface,
            memory_refs=refreshed_bundle.get("memory_refs") or [],
            reward_score=float(reply_outcome.get("reward_score") or 0.0),
        )
        if retention_updates:
            reply["payload"]["memory_retention"] = {
                "tracked": len(retention_updates),
                "recent": retention_updates[:4],
            }
    for promotion in persisted_promotions:
        retention_key = f"retain:{promotion.get('source_ref') or control_source_ref}:{promotion.get('fact_key') or promotion.get('memory_kind') or ''}"
        await asyncio.to_thread(
            upsert_via_memory_retention_stat,
            retention_key=retention_key,
            user_id=int(session.get("user_id") or 0),
            session_key=session_key,
            memory_tier=str(promotion.get("tier") or ""),
            memory_kind=str(promotion.get("memory_kind") or ""),
            fact_key=str(promotion.get("fact_key") or ""),
            source_ref=str(promotion.get("source_ref") or control_source_ref),
            reinforcement_increment=1,
            reward_delta=float(reply_outcome.get("reward_score") or 0.0),
            last_promoted_at=str(primary_outcome.get("created_at") or ""),
            metrics={"reason": promotion.get("reason") or "", "persisted_ref_id": int(promotion.get("persisted_ref_id") or 0)},
        )
        promotion_policy = get_via_policy("memory_promotion", route_info=policy_route)
        promotion_decision = await asyncio.to_thread(
            insert_via_decision_record,
            session_key=session_key,
            session_id=int(session.get("id") or 0),
            user_id=int(session.get("user_id") or 0),
            persona_id=int(persona.get("id") or 0),
            decision_type="memory_promotion",
            trigger_type=str(promotion.get("reason") or "learning_signal"),
            trigger_payload={"tier": promotion.get("tier") or "", "reason": promotion.get("reason") or ""},
            state_snapshot=trigger_snapshot.get("state_snapshot") or {},
            candidates=build_decision_candidates("memory_promotion"),
            chosen_action={
                "tier": promotion.get("tier") or "",
                "memory_kind": promotion.get("memory_kind") or "",
                "fact_key": promotion.get("fact_key") or "",
                "persisted_ref_id": int(promotion.get("persisted_ref_id") or 0),
            },
            policy_key=str(promotion_policy.get("policy_key") or ""),
            policy_version=str(promotion_policy.get("policy_version") or ""),
            context_refs=context_refs,
            cost_estimate=0.0,
        )
        decision_records.append(promotion_decision)
        promotion_outcome = await asyncio.to_thread(
            insert_via_outcome_record,
            decision_id=str(promotion_decision.get("decision_id") or ""),
            session_key=session_key,
            accepted=bool(promotion.get("persisted_ref_id")),
            followup_depth=int(reply_outcome.get("followup_depth") or 0),
            rephrase_needed=False,
            clicked_product=False,
            added_to_cart=False,
            purchased=False,
            thumb_feedback=0,
            abuse_flag=0,
            reward_score=float(reply_outcome.get("reward_score") or 0.0),
            outcome_payload={
                "tier": promotion.get("tier") or "",
                "memory_kind": promotion.get("memory_kind") or "",
                "reason": promotion.get("reason") or "",
            },
        )
        outcome_records.append(promotion_outcome)

    reply["payload"]["persona"]["affinity_points"] = int(persona.get("affinity_points") or 0)
    reply["payload"]["persona"]["wardrobe_points"] = int(persona.get("wardrobe_points") or 0)
    reply["payload"]["model_plan"] = model_plan
    reply["payload"]["control_loop"] = summarize_control_loop(
        trigger_snapshot=trigger_snapshot,
        decisions=decision_records,
        outcomes=outcome_records,
        promotions=persisted_promotions,
    )
    reply["payload"]["shadow_learning"] = [
        {
            "target": str(item.get("chosen_action", {}).get("target") or ""),
            "shadow_policy_version": str(item.get("chosen_action", {}).get("shadow_policy_version") or ""),
            "would_change": bool((item.get("chosen_action") or {}).get("would_change")),
        }
        for item in decision_records
        if str(item.get("decision_type") or "") == "shadow_eval"
    ]
    reply["payload"]["activity_state"] = resolve_via_activity_state(
        user_text=text,
        title=reply["title"],
        text=reply["text"],
        current_surface=surface,
        behavior_mode=reply["payload"].get("behavior_mode") or "",
        product_subintent=reply["payload"].get("product_subintent") or "",
        business_subintent=reply["payload"].get("business_subintent") or "",
    )
    try:
        reply_event_id = await event_bus.publish(
            session_key,
            "via_reply",
            {
                "title": reply["title"],
                "text": reply["text"],
                **reply["payload"],
            },
        )
    except Exception:
        logger.warning("via.reply_publish_failed", extra={"session_key": session_key}, exc_info=True)
        reply_event_id = ""
    updated_session = await asyncio.to_thread(
        touch_via_session,
        session_key,
        current_surface=surface[:60],
        last_event_id=reply_event_id,
        session_state={
            **(session.get("state") or {}),
            **(reply["payload"].get("product_state_patch") or {}),
            **(reply["payload"].get("business_state_patch") or {}),
            "turn_count": int((session.get("state") or {}).get("turn_count") or 0) + 1,
            "last_user_text": text[:200],
            "last_user_language": "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "en",
            "last_intent": route.get("intent") or "quick_chat",
            "last_brain": route.get("brain") or "quick_chat",
            "last_reward_score": float(reply_outcome.get("reward_score") or 0.0),
            "last_trigger": trigger_snapshot.get("primary_trigger") or "",
            "last_policy_versions": reply["payload"].get("control_loop", {}).get("policy_versions") or {},
            "last_event_type": "via_reply",
            "last_title": reply["title"],
        },
    )
    return {
        "user_event_id": user_event_id,
        "reply_event_id": reply_event_id,
        "reply": reply,
        "session": updated_session,
        "persona": persona,
    }
