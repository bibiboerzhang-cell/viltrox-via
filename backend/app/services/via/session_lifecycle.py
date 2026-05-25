"""Via session lifecycle, persona updates, and memory refresh endpoints."""
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


from app.services.via.session_memory import (
    _anonymous_persona_key,
    _fire_and_forget,
    _hash_ip,
    _load_memory_candidates,
    _prime_via_memory_assets,
    _sanitize_persona_patch,
)

async def bootstrap_via_session(
    *,
    user: dict[str, Any] | None,
    signed_device_id: str = "",
    client_fingerprint: str = "",
    current_surface: str = "upload",
    persona_patch: dict[str, Any] | None = None,
    request_ip: str = "",
    event_bus: Any = None,
) -> dict[str, Any]:
    user_id = int((user or {}).get("id") or 0)
    persona_key = f"user:{user_id}:primary" if user_id else _anonymous_persona_key(signed_device_id, client_fingerprint, request_ip)
    sanitized_patch = _sanitize_persona_patch(persona_patch)

    persona = await asyncio.to_thread(
        get_or_create_via_persona,
        user_id=user_id,
        persona_key=persona_key,
        display_name=sanitized_patch.get("display_name", "Via"),
        archetype=sanitized_patch.get("archetype", "brand_avatar"),
        temperament=sanitized_patch.get("temperament", "balanced"),
        talk_style=sanitized_patch.get("talk_style", "warm"),
        talkativeness=float(sanitized_patch.get("talkativeness", 0.55)),
        curiosity=float(sanitized_patch.get("curiosity", 0.7)),
        outfit_code=sanitized_patch.get("outfit_code", "viltrox_core_black"),
        accessory_code=sanitized_patch.get("accessory_code", ""),
        profile=sanitized_patch.get("profile_json", {}),
        memory_policy=sanitized_patch.get("memory_policy_json", {}),
    )
    session = await asyncio.to_thread(
        create_via_session,
        user_id=user_id,
        persona_id=int(persona["id"]),
        signed_device_id=signed_device_id[:160],
        client_fingerprint=client_fingerprint[:240],
        ip_hash=_hash_ip(request_ip),
        current_surface=current_surface[:60] or "upload",
        base_model=VIA_BASE_MODEL,
        session_state={
            "surface": current_surface,
            "mode": "idle",
            "signed_in": bool(user_id),
        },
    )
    memory_candidates = await asyncio.to_thread(_load_memory_candidates, user_id, session["session_key"], 12)
    for item in memory_candidates:
        await asyncio.to_thread(
            add_via_memory_ref,
            session_id=int(session["id"]),
            memory_kind=item["memory_kind"],
            source_ref=item["source_ref"],
            memory_key=item.get("memory_key", ""),
            weight=float(item.get("weight", 0.5)),
            payload=item.get("payload") or {},
        )
    bundle = await asyncio.to_thread(get_via_session_bundle, session["session_key"], 24)
    vector_stats, seed_stats = await _prime_via_memory_assets(bundle, include_remote=False)
    _fire_and_forget(_prime_via_memory_assets(bundle, include_remote=True), label="via_seed_remote_bootstrap")
    event_id = ""
    if event_bus is not None:
        try:
            event_id = await event_bus.publish(
                session["session_key"],
                "session_ready",
                {
                    "title": "Via is here",
                    "text": "Ready when you are.",
                    "surface": current_surface,
                    "activity_state": resolve_via_activity_state(
                        title="Via is here",
                        text="Ready when you are.",
                        current_surface=current_surface,
                    ),
                    "persona": {
                        "display_name": bundle.get("persona", {}).get("display_name", "Via"),
                        "outfit_code": bundle.get("persona", {}).get("outfit_code", "viltrox_core_black"),
                    },
                    "memory_ref_count": len(bundle.get("memory_refs", [])),
                    "model_plan": get_via_model_plan(),
                    "vector_backend": vector_stats.get("backend") or "none",
                    "seed_memory": seed_stats,
                },
            )
            bundle["session"] = await asyncio.to_thread(
                touch_via_session,
                session["session_key"],
                current_surface=current_surface[:60] or "upload",
                last_event_id=event_id,
            )
        except Exception:
            logger.warning("via.session_ready_publish_failed", extra={"session_key": session.get("session_key")}, exc_info=True)
    bundle["event_backend"] = getattr(event_bus, "backend_name", "none")
    bundle["published_event_id"] = event_id
    bundle["vector_backend"] = vector_stats.get("backend") or "none"
    bundle["seed_memory"] = seed_stats

    # ── Phase 1 middleware: party stitch + via.session_started event (non-fatal) ──
    try:
        _emit_via_session_to_party_layer(
            user_id=user_id,
            user=user,
            session_key=session["session_key"],
            signed_device_id=signed_device_id,
            client_fingerprint=client_fingerprint,
            current_surface=current_surface,
        )
    except Exception:
        logger.debug("phase1 party-layer emit failed for via session (non-fatal)", exc_info=True)

    return bundle


def _emit_via_session_to_party_layer(
    *,
    user_id: int,
    user: dict[str, Any] | None,
    session_key: str,
    signed_device_id: str,
    client_fingerprint: str,
    current_surface: str,
) -> None:
    """
    Phase 1 wire: Via session bootstrap → party (via user_id or email, falls back to anonymous) → via.session_started event.

    Silently no-ops when PG runtime unavailable or party layer not migrated.
    """
    from app.db.connection import is_postgres_runtime

    if not is_postgres_runtime():
        return

    from app.services.party.party_service import (
        get_or_create_by_email,
        get_or_create_by_user_id,
    )
    from app.services.party.event_writer import emit_via_session_started

    party_id: str | None = None
    if user_id:
        email = (user or {}).get("email") or ""
        if email:
            party_id = get_or_create_by_email(
                email,
                origin_source="via_runtime",
                origin_channel=current_surface or "",
            )
        if not party_id:
            party_id = get_or_create_by_user_id(user_id, origin_source="via_runtime")

    # Anonymous: party_id stays None for now. Future stitch on sign-in will backfill.
    emit_via_session_started(
        party_id=party_id,
        via_session_id=session_key,
        signed_device_id=signed_device_id or "",
        client_fingerprint=client_fingerprint or "",
        entry_source=current_surface or "",
    )


async def publish_via_session_event(
    *,
    session_key: str,
    event_bus: Any,
    event_type: str,
    title: str,
    text: str,
    payload: dict[str, Any] | None = None,
    current_surface: str = "",
) -> dict[str, Any]:
    session = await asyncio.to_thread(find_via_session, session_key)
    if not session:
        return {}
    event_payload = {
        "title": title[:120],
        "text": text[:500],
        **(payload or {}),
    }
    try:
        event_id = await event_bus.publish(session_key, event_type, event_payload)
    except Exception:
        logger.warning("via.publish_event_failed", extra={"session_key": session_key, "event_type": event_type}, exc_info=True)
        event_id = ""
    updated = await asyncio.to_thread(
        touch_via_session,
        session_key,
        current_surface=current_surface[:60],
        last_event_id=event_id,
        session_state={
            **(session.get("state") or {}),
            "last_event_type": event_type,
            "last_title": title[:120],
        },
    )
    return {"event_id": event_id, "session": updated}


async def patch_via_persona_for_session(
    *,
    session_key: str,
    patch: dict[str, Any],
    event_bus: Any = None,
) -> dict[str, Any]:
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    if not bundle:
        return {}
    persona = await asyncio.to_thread(update_via_persona, int(bundle["persona"]["id"]), _sanitize_persona_patch(patch))
    if event_bus is not None:
        try:
            event_id = await event_bus.publish(
                session_key,
                "persona_updated",
                {
                    "title": "Via changed style",
                    "text": "I changed my little mood and outfit for this session, but I kept your context with me.",
                    "persona": {
                        "display_name": persona.get("display_name", "Via"),
                        "temperament": persona.get("temperament", "balanced"),
                        "talk_style": persona.get("talk_style", "warm"),
                        "outfit_code": persona.get("outfit_code", "viltrox_core_black"),
                    },
                },
            )
            await asyncio.to_thread(touch_via_session, session_key, last_event_id=event_id)
        except Exception:
            logger.warning("via.persona_publish_failed", extra={"session_key": session_key}, exc_info=True)
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    return bundle


async def refresh_via_memory_refs(session_key: str, event_bus: Any = None) -> dict[str, Any]:
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    if not bundle:
        return {}
    session = bundle["session"]
    current_count = len(bundle.get("memory_refs", []))
    if current_count < 12:
        candidates = await asyncio.to_thread(_load_memory_candidates, int(session.get("user_id") or 0), session_key, 12 - current_count)
        for item in candidates:
            await asyncio.to_thread(
                add_via_memory_ref,
                session_id=int(session["id"]),
                memory_kind=item["memory_kind"],
                source_ref=item["source_ref"],
                memory_key=item.get("memory_key", ""),
                weight=float(item.get("weight", 0.5)),
                payload=item.get("payload") or {},
            )
    bundle = await asyncio.to_thread(get_via_session_bundle, session_key, 24)
    vector_stats, seed_stats = await _prime_via_memory_assets(bundle, include_remote=False)
    _fire_and_forget(_prime_via_memory_assets(bundle, include_remote=True), label="via_seed_remote_refresh")
    if event_bus is not None:
        try:
            event_id = await event_bus.publish(
                session_key,
                "memory_refreshed",
                {
                    "title": "Memory refreshed",
                    "text": "Memory shelf refreshed. The strongest context is back in reach.",
                    "memory_ref_count": len(bundle.get("memory_refs", [])),
                    "vector_backend": vector_stats.get("backend") or "none",
                    "seed_memory": seed_stats,
                },
            )
            await asyncio.to_thread(touch_via_session, session_key, last_event_id=event_id)
        except Exception:
            logger.warning("via.memory_refresh_publish_failed", extra={"session_key": session_key}, exc_info=True)
    bundle["seed_memory"] = seed_stats
    return bundle
