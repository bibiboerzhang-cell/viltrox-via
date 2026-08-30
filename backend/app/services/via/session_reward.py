"""Via reward trace and control-loop helper surfaces."""
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


def _control_source_ref(session_key: str, user_text: str, reply_text: str) -> str:
    digest = hashlib.sha256((str(user_text or "") + "|" + str(reply_text or "")).encode("utf-8")).hexdigest()[:16]
    return f"via-control:{session_key}:{digest}"


_VIA_REWARD_TRACE_EVENTS = {
    "click",
    "product_click",
    "link_click",
    "open_link",
    "compare",
    "compare_open",
    "add_to_cart",
    "cart_add",
    "purchase",
    "checkout_success",
    "affiliate_order",
    "shopify_order",
    "thumb_up",
    "thumb_down",
}

_REWARD_TRACE_DEDUPE_EVENTS = {"add_to_cart", "cart_add", "purchase", "checkout_success", "affiliate_order", "shopify_order"}


def _pick_reward_trace_decision(decisions: list[dict[str, Any]], requested_decision_id: str = "") -> dict[str, Any]:
    wanted = str(requested_decision_id or "").strip()
    if wanted:
        match = next((item for item in decisions if str(item.get("decision_id") or "") == wanted), None)
        if match:
            return match
    for decision_type in ("reply_mode", "intent_route"):
        match = next((item for item in decisions if str(item.get("decision_type") or "") == decision_type), None)
        if match:
            return match
    return decisions[0] if decisions else {}


def _routing_bucket_key(route_info: dict[str, Any] | None, current_surface: str = "") -> str:
    route_info = dict(route_info or {})
    intent = str(route_info.get("intent") or "quick_chat").strip().lower()
    surface = str(current_surface or route_info.get("current_surface") or "upload").strip().lower()
    return f"{intent}:{surface}"


def _reward_trace_source(body: dict[str, Any], payload: dict[str, Any], current_surface: str = "") -> dict[str, Any]:
    return {
        "surface": str(body.get("surface") or payload.get("surface") or current_surface or "").strip(),
        "source": str(body.get("source") or payload.get("source") or "").strip(),
        "origin": str(body.get("origin") or payload.get("origin") or "").strip(),
        "product_key": str(body.get("product_key") or payload.get("product_key") or payload.get("product") or "").strip(),
        "idempotency_key": str(body.get("idempotency_key") or payload.get("idempotency_key") or payload.get("order_id") or payload.get("external_id") or "").strip(),
    }


def _build_retrieval_evidence(
    *,
    retrieval_execution: dict[str, Any],
    retrieval_policy: dict[str, Any],
    vector_refs: list[dict[str, Any]],
    bundle_memory_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    vector_scores = [float(item.get("score") or item.get("weight") or 0.0) for item in vector_refs]
    bundle_sources = [str(item.get("source_ref") or "") for item in bundle_memory_refs if str(item.get("source_ref") or "")]
    vector_sources = [str(item.get("source_ref") or "") for item in vector_refs if str(item.get("source_ref") or "")]
    selected_sources: list[str] = []
    if bundle_sources:
        selected_sources.append("bundle_memory")
    if vector_sources:
        selected_sources.append("vector_memory")
    if any(source.startswith("seed:") for source in bundle_sources + vector_sources):
        selected_sources.append("seed_knowledge")
    candidate_sources = list(dict.fromkeys(list(retrieval_execution.get("fallback_order") or retrieval_policy.get("fallback_order") or ["bundle_memory", "vector_memory", "seed_knowledge"])))
    avg_score = sum(vector_scores) / len(vector_scores) if vector_scores else 0.0
    top_score = max(vector_scores) if vector_scores else 0.0
    spread = (max(vector_scores) - min(vector_scores)) if len(vector_scores) > 1 else 0.0
    rerank_summary = {
        "top_refs": [
            {
                "source_ref": str(item.get("source_ref") or ""),
                "score": round(float(item.get("score") or item.get("weight") or 0.0), 4),
                "summary": str((item.get("payload") or {}).get("summary") or "")[:120],
            }
            for item in vector_refs[:3]
        ],
        "vector_source_mix": {
            "seed": sum(1 for source in vector_sources if source.startswith("seed:")),
            "conversation": sum(1 for source in vector_sources if source.startswith("via-vector:")),
            "memory": sum(1 for source in vector_sources if source and not source.startswith(("seed:", "via-vector:"))),
        },
    }
    return {
        "candidate_sources": candidate_sources,
        "selected_sources": selected_sources,
        "vector_hit_count": len(vector_refs),
        "bundle_hit_count": len(bundle_memory_refs),
        "seed_hit_count": sum(1 for source in bundle_sources + vector_sources if source.startswith("seed:")),
        "vector_limit": int(retrieval_execution.get("vector_limit") or 0),
        "top_score": round(top_score, 4),
        "avg_score": round(avg_score, 4),
        "score_spread": round(spread, 4),
        "rerank_applied": bool(str(retrieval_execution.get("plan") or "").startswith("hybrid")),
        "rerank_summary": rerank_summary,
        "evidence_payload": {
            "retrieval_plan": str(retrieval_execution.get("plan") or ""),
            "retrieval_mode": str(retrieval_execution.get("retrieval_mode") or ""),
            "fallback_order": list(retrieval_execution.get("fallback_order") or retrieval_policy.get("fallback_order") or []),
        },
    }


def _reinforce_memory_retention(
    *,
    session_key: str,
    user_id: int,
    current_surface: str,
    memory_refs: list[dict[str, Any]],
    reward_score: float,
) -> list[dict[str, Any]]:
    reinforced: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item in list(memory_refs or [])[:12]:
        source_ref = str(item.get("source_ref") or "").strip()
        if not source_ref:
            continue
        payload = dict(item.get("payload") or {})
        retention_key = f"retain:{source_ref}"
        memory_tier = str(payload.get("memory_tier") or ("semantic" if "semantic" in str(item.get("memory_kind") or "") else "episodic")).strip()
        reinforced.append(
            upsert_via_memory_retention_stat(
                retention_key=retention_key,
                user_id=int(user_id or 0),
                session_key=session_key,
                memory_tier=memory_tier,
                memory_kind=str(item.get("memory_kind") or ""),
                fact_key=str(item.get("memory_key") or ""),
                source_ref=source_ref,
                confirmed_hit_increment=1 if reward_score >= 0.45 else 0,
                reinforcement_increment=1,
                reward_delta=float(reward_score or 0.0),
                last_hit_at=now,
                metrics={"surface": current_surface, "weight": float(item.get("weight") or 0.0)},
            )
        )
    return reinforced


async def _record_shadow_eval(
    *,
    session_key: str,
    session: dict[str, Any],
    persona: dict[str, Any],
    trigger_snapshot: dict[str, Any],
    context_refs: list[str],
    target: str,
    shadow_eval: dict[str, Any],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not shadow_eval:
        return {}
    return await asyncio.to_thread(
        insert_via_decision_record,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="shadow_eval",
        trigger_type=str(target or ""),
        trigger_payload={
            "target": str(target or ""),
            "shadow_version_key": str(shadow_eval.get("shadow_version_key") or ""),
            "would_change": bool(shadow_eval.get("would_change")),
        },
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=candidates or [],
        chosen_action=shadow_eval,
        policy_key=str(shadow_eval.get("policy_key") or target or ""),
        policy_version=str(shadow_eval.get("shadow_policy_version") or ""),
        context_refs=context_refs,
        latency_ms=0.0,
        cost_estimate=0.0,
    )


def _resolve_reward_event_value(event_value: float | None, trace_payload: dict[str, Any]) -> float:
    try:
        return float(
            event_value
            if event_value is not None
            else trace_payload.get("value")
            or trace_payload.get("order_total")
            or trace_payload.get("revenue_total")
            or 0.0
        )
    except Exception:
        return 0.0


async def _deduped_reward_trace_response(
    *,
    session_key: str,
    session: dict[str, Any],
    trace_source: dict[str, Any],
    raw_event_type: str,
    target_decision_id: str,
) -> dict[str, Any] | None:
    if not trace_source["idempotency_key"] or raw_event_type not in _REWARD_TRACE_DEDUPE_EVENTS:
        return None
    existing = await asyncio.to_thread(
        get_via_reward_trace_by_idempotency,
        session_key,
        trace_source["idempotency_key"],
    )
    if not existing:
        return None
    resolved_decision_id = target_decision_id or str(existing.get("decision_id") or "")
    decision_traces = await asyncio.to_thread(
        list_via_reward_traces,
        session_key,
        decision_id=resolved_decision_id,
        limit=64,
    )
    trace_summary = summarize_via_reward_traces(decision_traces)
    latest_outcome = await asyncio.to_thread(
        get_latest_via_outcome_record,
        session_key,
        resolved_decision_id,
    )
    return {
        "trace": existing,
        "summary": trace_summary,
        "decision_id": resolved_decision_id,
        "outcome": latest_outcome,
        "session": session,
        "deduped": True,
    }


async def _insert_session_reward_trace(
    *,
    session_key: str,
    session: dict[str, Any],
    target_decision_id: str,
    raw_event_type: str,
    trace_source: dict[str, Any],
    resolved_value: float,
    trace_payload: dict[str, Any],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        insert_via_reward_trace,
        session_key=session_key,
        decision_id=target_decision_id,
        user_id=int(session.get("user_id") or 0),
        event_type=raw_event_type,
        surface=trace_source["surface"],
        source=trace_source["source"],
        origin=trace_source["origin"],
        product_key=trace_source["product_key"],
        event_value=resolved_value,
        idempotency_key=trace_source["idempotency_key"],
        event_payload=trace_payload,
    )


async def _apply_reward_outcome_merge(
    latest_outcome: dict[str, Any] | None,
    trace_summary: dict[str, Any],
) -> dict[str, Any]:
    if not latest_outcome:
        return {}
    merged = merge_via_reward_trace_summary(
        outcome=latest_outcome,
        reward_trace_summary=trace_summary,
    )
    return await asyncio.to_thread(
        update_via_outcome_record,
        str(latest_outcome.get("outcome_id") or ""),
        clicked_product=bool(merged.get("clicked_product")),
        added_to_cart=bool(merged.get("added_to_cart")),
        purchased=bool(merged.get("purchased")),
        thumb_feedback=int(merged.get("thumb_feedback") or 0),
        reward_score=float(merged.get("reward_score") or 0.0),
        outcome_payload=merged.get("outcome_payload") or {},
    )


def _resolve_reward_decision_stats(
    decisions: list[dict[str, Any]],
    *,
    target_decision_id: str,
    trace_source: dict[str, Any],
    current_surface: str,
    session: dict[str, Any],
) -> tuple[str, float, float, str]:
    for candidate in decisions:
        if str(candidate.get("decision_id") or "") != target_decision_id:
            continue
        chosen = dict(candidate.get("chosen_action") or {})
        provider = str(chosen.get("provider") or "").strip().lower()
        latency_ms = float(candidate.get("latency_ms") or 0.0)
        cost_estimate = float(candidate.get("cost_estimate") or 0.0)
        route_bucket = _routing_bucket_key(candidate.get("state_snapshot") or {}, trace_source["surface"] or current_surface or str(session.get("current_surface") or "upload"))
        return provider, latency_ms, cost_estimate, route_bucket
    return "", 0.0, 0.0, ""


async def _upsert_reward_routing_stat(
    *,
    provider: str,
    route_bucket: str,
    raw_event_type: str,
    trace: dict[str, Any],
    trace_source: dict[str, Any],
    trace_summary: dict[str, Any],
    updated_outcome: dict[str, Any],
    latest_outcome: dict[str, Any] | None,
    latency_ms: float,
    cost_estimate: float,
) -> None:
    if not provider or not route_bucket:
        return
    reward_score = float((updated_outcome or latest_outcome or {}).get("reward_score") or trace_summary.get("reward_delta") or 0.0)
    positive_signal = 1 if raw_event_type in {"click", "link_click", "open_link", "compare", "compare_open", "add_to_cart", "cart_add", "purchase", "checkout_success", "affiliate_order", "shopify_order", "thumb_up"} else 0
    guard_fail = 1 if raw_event_type == "thumb_down" else 0
    await asyncio.to_thread(
        upsert_via_routing_provider_stat,
        bucket_key=route_bucket,
        provider=provider,
        exposure_increment=1 if raw_event_type == "click" else 0,
        success_increment=positive_signal,
        reward_delta=reward_score,
        guard_fail_increment=guard_fail,
        latency_ms=latency_ms,
        cost_estimate=cost_estimate,
        last_outcome_at=str(trace.get("created_at") or ""),
        metrics={"event_type": raw_event_type, "surface": trace_source["surface"], "origin": trace_source["origin"]},
    )


def _record_reward_feedback_signal(
    *,
    session_key: str,
    session: dict[str, Any],
    raw_event_type: str,
    target_decision_id: str,
    resolved_value: float,
    trace_source: dict[str, Any],
    trace_summary: dict[str, Any],
) -> None:
    record_feedback_signal(
        source_type="via_reward_trace",
        source_id=session_key,
        event_type=f"reward_{raw_event_type}",
        actor_role="user",
        user_id=int(session.get("user_id") or 0),
        payload={
            "decision_id": target_decision_id,
            "event_type": raw_event_type,
            "event_value": resolved_value,
            "product_key": trace_source["product_key"],
            "trace_summary": trace_summary,
        },
    )


async def _touch_reward_trace_session(
    *,
    session_key: str,
    session: dict[str, Any],
    raw_event_type: str,
    trace: dict[str, Any],
    trace_summary: dict[str, Any],
    trace_source: dict[str, Any],
    current_surface: str,
) -> dict[str, Any]:
    session_state = dict(session.get("state") or {})
    session_state["last_reward_trace_type"] = raw_event_type
    session_state["last_reward_trace_at"] = trace.get("created_at") or ""
    session_state["last_reward_trace_summary"] = trace_summary
    return await asyncio.to_thread(
        touch_via_session,
        session_key,
        current_surface=(trace_source["surface"] or current_surface)[:60] or (session.get("current_surface") or "upload"),
        session_state=session_state,
    )


async def record_via_reward_trace_for_session(
    *,
    session_key: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    decision_id: str = "",
    current_surface: str = "",
    source: str = "",
    origin: str = "",
    product_key: str = "",
    event_value: float | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 12)
    if not bundle:
        return {}
    session = bundle.get("session") or {}
    raw_event_type = str(event_type or "").strip().lower()
    if raw_event_type not in _VIA_REWARD_TRACE_EVENTS:
        return {"error": "invalid_event_type"}
    trace_payload = dict(payload or {})
    trace_source = _reward_trace_source(
        {
            "surface": current_surface,
            "source": source,
            "origin": origin,
            "product_key": product_key,
            "idempotency_key": idempotency_key,
        },
        trace_payload,
        current_surface=current_surface,
    )
    decisions = await asyncio.to_thread(list_via_decision_records, session_key, 24)
    target_decision = _pick_reward_trace_decision(decisions, decision_id)
    target_decision_id = str(target_decision.get("decision_id") or decision_id or "").strip()
    resolved_value = _resolve_reward_event_value(event_value, trace_payload)
    deduped = await _deduped_reward_trace_response(
        session_key=session_key,
        session=session,
        trace_source=trace_source,
        raw_event_type=raw_event_type,
        target_decision_id=target_decision_id,
    )
    if deduped is not None:
        return deduped
    trace = await _insert_session_reward_trace(
        session_key=session_key,
        session=session,
        target_decision_id=target_decision_id,
        raw_event_type=raw_event_type,
        trace_source=trace_source,
        resolved_value=resolved_value,
        trace_payload=trace_payload,
    )
    decision_traces = await asyncio.to_thread(
        list_via_reward_traces,
        session_key,
        decision_id=target_decision_id,
        limit=64,
    )
    trace_summary = summarize_via_reward_traces(decision_traces)
    latest_outcome = await asyncio.to_thread(
        get_latest_via_outcome_record,
        session_key,
        target_decision_id,
    )
    updated_outcome = await _apply_reward_outcome_merge(latest_outcome, trace_summary)
    provider, latency_ms, cost_estimate, route_bucket = _resolve_reward_decision_stats(
        decisions,
        target_decision_id=target_decision_id,
        trace_source=trace_source,
        current_surface=current_surface,
        session=session,
    )
    await _upsert_reward_routing_stat(
        provider=provider,
        route_bucket=route_bucket,
        raw_event_type=raw_event_type,
        trace=trace,
        trace_source=trace_source,
        trace_summary=trace_summary,
        updated_outcome=updated_outcome,
        latest_outcome=latest_outcome,
        latency_ms=latency_ms,
        cost_estimate=cost_estimate,
    )
    _record_reward_feedback_signal(
        session_key=session_key,
        session=session,
        raw_event_type=raw_event_type,
        target_decision_id=target_decision_id,
        resolved_value=resolved_value,
        trace_source=trace_source,
        trace_summary=trace_summary,
    )
    updated_session = await _touch_reward_trace_session(
        session_key=session_key,
        session=session,
        raw_event_type=raw_event_type,
        trace=trace,
        trace_summary=trace_summary,
        trace_source=trace_source,
        current_surface=current_surface,
    )
    return {
        "trace": trace,
        "summary": trace_summary,
        "decision_id": target_decision_id,
        "outcome": updated_outcome,
        "session": updated_session,
    }
