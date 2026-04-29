"""
services/via/memory_promoter.py — Explicit memory promotion for Via
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.db.repositories.via import add_via_memory_ref
from app.services.memory.l3_store import record_creator_memory_fact


def propose_via_memory_promotions(
    *,
    bundle: dict[str, Any],
    route_info: dict[str, Any] | None,
    user_text: str,
    reply: dict[str, Any],
    learning_signals: dict[str, Any] | None,
    reward_score: float,
    current_surface: str,
) -> list[dict[str, Any]]:
    route_info = dict(route_info or {})
    learning_signals = dict(learning_signals or {})
    persona = bundle.get("persona") or {}
    profile = persona.get("profile") or {}
    promotions: list[dict[str, Any]] = []
    base_key = hashlib.sha256((str(user_text or "") + "|" + str(reply.get("text") or "")).encode("utf-8")).hexdigest()[:12]
    confidence = float(learning_signals.get("confidence") or 0.55)

    summary = str(learning_signals.get("summary") or "").strip()
    if summary:
        promotions.append(
            {
                "tier": "episodic",
                "target": "session",
                "memory_kind": "via_episodic_exchange",
                "fact_key": f"episode:{base_key}",
                "fact_value": {
                    "summary": summary,
                    "surface": current_surface,
                    "intent": route_info.get("intent") or "",
                    "brain": route_info.get("brain") or "",
                },
                "confidence": max(0.45, confidence),
                "reason": "conversation_summary",
            }
        )

    existing_traits = dict(profile.get("user_traits") or {})
    semantic_traits = {
        key: value
        for key, value in dict(learning_signals.get("traits") or {}).items()
        if value and existing_traits.get(key) == value
    }
    if semantic_traits:
        promotions.append(
            {
                "tier": "semantic",
                "target": "creator",
                "memory_kind": "via_semantic_traits",
                "fact_key": "stable_traits",
                "fact_value": semantic_traits,
                "confidence": min(0.95, max(0.68, confidence + 0.08)),
                "reason": "repeated_trait_validation",
            }
        )

    repeated_keywords = [
        token
        for token in list(learning_signals.get("keywords") or [])
        if token in list(profile.get("core_keywords") or [])
    ]
    if repeated_keywords:
        promotions.append(
            {
                "tier": "semantic",
                "target": "creator",
                "memory_kind": "via_semantic_keywords",
                "fact_key": "stable_keywords",
                "fact_value": repeated_keywords[:8],
                "confidence": min(0.92, max(0.66, confidence + 0.05)),
                "reason": "repeated_keyword_validation",
            }
        )

    if reward_score >= 0.55:
        promotions.append(
            {
                "tier": "procedural",
                "target": "creator",
                "memory_kind": "via_procedural_pattern",
                "fact_key": f"{route_info.get('intent') or 'chat'}:{route_info.get('brain') or 'reply'}:{current_surface}",
                "fact_value": {
                    "provider": (reply.get("payload") or {}).get("provider") or "",
                    "behavior_mode": (reply.get("payload") or {}).get("behavior_mode") or "",
                    "quick_actions": list((reply.get("payload") or {}).get("quick_actions") or [])[:3],
                },
                "confidence": min(0.94, max(0.58, reward_score)),
                "reason": "successful_reply_pattern",
            }
        )

    return promotions


def persist_via_memory_promotions(bundle: dict[str, Any], promotions: list[dict[str, Any]], *, source_ref: str) -> list[dict[str, Any]]:
    session = bundle.get("session") or {}
    user_id = int(session.get("user_id") or 0)
    persisted: list[dict[str, Any]] = []
    for item in promotions:
        promotion = dict(item)
        promotion["source_ref"] = source_ref
        if promotion.get("target") == "session" or not user_id:
            ref_id = add_via_memory_ref(
                session_id=int(session.get("id") or 0),
                memory_kind=str(promotion.get("memory_kind") or "via_episodic_exchange"),
                source_ref=source_ref,
                memory_key=str(promotion.get("fact_key") or ""),
                weight=float(promotion.get("confidence") or 0.5),
                payload={
                    "memory_tier": promotion.get("tier") or "",
                    "reason": promotion.get("reason") or "",
                    "fact_value": promotion.get("fact_value") or {},
                },
            )
            promotion["persisted_ref_id"] = int(ref_id)
        else:
            creator_memory_id = record_creator_memory_fact(
                user_id=user_id,
                memory_kind=str(promotion.get("memory_kind") or "via_semantic_memory"),
                fact_key=str(promotion.get("fact_key") or ""),
                fact_value=promotion.get("fact_value") or {},
                confidence=float(promotion.get("confidence") or 0.5),
                source_ref=source_ref,
                creator_handle="",
            )
            promotion["persisted_ref_id"] = int(creator_memory_id)
        persisted.append(promotion)
    return persisted
