"""
services/via/trigger_engine.py — Trigger-first control layer for Via
"""
from __future__ import annotations

import re
from typing import Any


_BUSINESS_PATTERNS = {
    "purchase_intent": [r"\b(buy|price|budget|cart|checkout|order)\b", r"(买|预算|下单|价格|购物车)"],
    "creator_program": [r"\b(vip|affiliate|commission|creator)\b", r"(会员|达人|佣金|返利|创作者)"],
    "official_product": [r"\b(viltrox|epic|luna|air|pro|lab|evo|z1|z2)\b", r"(唯卓仕|epic|luna|air|pro|lab|evo|z1|z2)"],
}

_LEARNING_PATTERNS = {
    "user_correction": [r"\b(no|not that|you are wrong|wrong|actually)\b", r"(不对|不是这个|你错了|其实)"],
    "followup_question": [r"\b(what about|how about|then|next|also)\b", r"(那|然后|还有|继续|那这个)"],
    "preference_signal": [r"\b(i like|prefer|usually|always|mostly)\b", r"(我喜欢|我更偏向|我一般|我通常)"],
}

_SEMANTIC_PATTERNS = {
    "memory_query": [r"\b(memory|remember|last time)\b", r"(记得|记忆|上次)"],
    "visual_query": [r"\b(video|image|shot|scene|monitor|lighting)\b", r"(视频|画面|镜头|监视器|灯光|拍摄)"],
    "deep_reasoning": [r"\b(compare|analysis|strategy|why|plan)\b", r"(对比|分析|策略|为什么|方案)"],
}


def _matches(text: str, patterns: list[str]) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)


def _push_unique(items: list[str], value: str) -> None:
    token = str(value or "").strip()
    if token and token not in items:
        items.append(token)


def _estimate_confidence(route_info: dict[str, Any], guarded: dict[str, Any] | None, vector_refs: list[dict[str, Any]]) -> float:
    if guarded:
        return 0.98
    intent = str(route_info.get("intent") or "").strip().lower()
    score = 0.48
    if intent in {"product", "business_support"}:
        score += 0.34
    elif intent in {"memory", "creative_guidance"}:
        score += 0.2
    elif intent in {"deep_reasoning", "visual_reasoning"}:
        score += 0.12
    if route_info.get("needs_memory"):
        score += 0.12 if vector_refs else -0.06
    if route_info.get("use_deep_reasoning"):
        score -= 0.04
    return max(0.05, min(0.99, score))


def _semantic_triggers(
    text: str,
    *,
    intent: str,
    brain: str,
) -> list[str]:
    semantic: list[str] = []
    if intent:
        _push_unique(semantic, intent)
    if brain and brain != intent:
        _push_unique(semantic, brain)
    for label, patterns in _SEMANTIC_PATTERNS.items():
        if _matches(text, patterns):
            _push_unique(semantic, label)
    return semantic


def _state_triggers(
    bundle: dict[str, Any],
    *,
    session: dict[str, Any],
    session_state: dict[str, Any],
    current_surface: str,
    vector_refs: list[dict[str, Any]],
) -> list[str]:
    state: list[str] = []
    surface = str(
        current_surface or session.get("current_surface") or "upload"
    ).strip().lower()
    _push_unique(state, f"surface:{surface}")
    _push_unique(
        state,
        "signed_in" if int(session.get("user_id") or 0) else "anonymous_session",
    )
    for condition, label in (
        (session_state.get("last_product_labels"), "product_context"),
        (session_state.get("last_business_intent"), "business_context"),
        (int(session_state.get("turn_count") or 0) > 0, "existing_session_turns"),
        (bundle.get("memory_refs"), "memory_shelf_available"),
        (vector_refs, "vector_memory_hit"),
    ):
        if condition:
            _push_unique(state, label)
    return state


def _confidence_and_risk_triggers(
    route_info: dict[str, Any],
    *,
    guarded: dict[str, Any] | None,
    vector_refs: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    confidence: list[str] = []
    risk: list[str] = []
    if guarded:
        _push_unique(risk, str(guarded.get("provider") or "policy_guard"))
        _push_unique(confidence, "policy_lock")
    if route_info.get("needs_memory"):
        _push_unique(confidence, "memory_required")
        if not vector_refs:
            _push_unique(confidence, "retrieval_sparse")
    if route_info.get("use_deep_reasoning"):
        _push_unique(confidence, "complex_path")
    return confidence, risk


def _pattern_triggers(
    text: str,
    patterns_by_label: dict[str, list[str]],
) -> list[str]:
    triggers: list[str] = []
    for label, patterns in patterns_by_label.items():
        if _matches(text, patterns):
            _push_unique(triggers, label)
    return triggers


def _business_triggers(text: str, *, intent: str) -> list[str]:
    business: list[str] = []
    if intent in {"product", "business_support"}:
        _push_unique(business, intent)
    for trigger in _pattern_triggers(text, _BUSINESS_PATTERNS):
        _push_unique(business, trigger)
    return business


def _learning_triggers(
    text: str,
    *,
    bundle: dict[str, Any],
    route_info: dict[str, Any],
    vector_refs: list[dict[str, Any]],
) -> list[str]:
    learning = _pattern_triggers(text, _LEARNING_PATTERNS)
    if bundle.get("memory_refs"):
        _push_unique(learning, "memory_hit_available")
    if route_info.get("needs_memory") and vector_refs:
        _push_unique(learning, "memory_hit_used")
    return learning


def _recommended_decisions(
    route_info: dict[str, Any],
    *,
    intent: str,
    guarded: dict[str, Any] | None,
) -> list[str]:
    decisions = ["intent_route", "reply_mode"]
    if route_info.get("needs_memory"):
        decisions.append("retrieval_plan")
    if guarded:
        decisions.append("risk_gate")
    elif intent in {"quick_chat", "creative_guidance", "deep_reasoning", "visual_reasoning"}:
        decisions.append("model_choice")
    decisions.append("memory_promotion")
    return decisions


def build_via_trigger_snapshot(
    bundle: dict[str, Any],
    user_text: str,
    *,
    current_surface: str = "upload",
    route_info: dict[str, Any] | None = None,
    guarded: dict[str, Any] | None = None,
    vector_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    route_info = dict(route_info or {})
    vector_refs = list(vector_refs or [])
    session = bundle.get("session") or {}
    persona = bundle.get("persona") or {}
    session_state = session.get("state") or {}
    text = str(user_text or "").strip()

    intent = str(route_info.get("intent") or "").strip().lower()
    brain = str(route_info.get("brain") or "").strip().lower()
    semantic = _semantic_triggers(text, intent=intent, brain=brain)
    state = _state_triggers(
        bundle,
        session=session,
        session_state=session_state,
        current_surface=current_surface,
        vector_refs=vector_refs,
    )
    confidence, risk = _confidence_and_risk_triggers(
        route_info,
        guarded=guarded,
        vector_refs=vector_refs,
    )
    business = _business_triggers(text, intent=intent)
    learning = _learning_triggers(
        text,
        bundle=bundle,
        route_info=route_info,
        vector_refs=vector_refs,
    )

    confidence_score = _estimate_confidence(route_info, guarded, vector_refs)
    primary_trigger = (
        risk[0] if risk else business[0] if business else semantic[0] if semantic else state[0] if state else "general_chat"
    )
    recommended_decisions = _recommended_decisions(
        route_info,
        intent=intent,
        guarded=guarded,
    )

    return {
        "primary_trigger": primary_trigger,
        "semantic": semantic,
        "state": state,
        "confidence": confidence,
        "business": business,
        "risk": risk,
        "learning": learning,
        "confidence_score": round(confidence_score, 4),
        "recommended_decisions": recommended_decisions,
        "state_snapshot": {
            "session_key": session.get("session_key") or "",
            "surface": current_surface or session.get("current_surface") or "upload",
            "user_id": int(session.get("user_id") or 0),
            "persona_key": persona.get("persona_key") or "",
            "turn_count": int(session_state.get("turn_count") or 0),
            "last_intent": str(session_state.get("last_intent") or ""),
            "last_brain": str(session_state.get("last_brain") or ""),
            "last_product_labels": list(session_state.get("last_product_labels") or [])[:4],
            "last_business_intent": str(session_state.get("last_business_intent") or ""),
            "memory_ref_count": len(bundle.get("memory_refs") or []),
            "vector_ref_count": len(vector_refs),
            "talk_style": str(persona.get("talk_style") or ""),
            "temperament": str(persona.get("temperament") or ""),
        },
    }
