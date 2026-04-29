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

    semantic: list[str] = []
    state: list[str] = []
    confidence: list[str] = []
    business: list[str] = []
    risk: list[str] = []
    learning: list[str] = []

    intent = str(route_info.get("intent") or "").strip().lower()
    brain = str(route_info.get("brain") or "").strip().lower()
    if intent:
        _push_unique(semantic, intent)
    if brain and brain != intent:
        _push_unique(semantic, brain)
    for label, patterns in _SEMANTIC_PATTERNS.items():
        if _matches(text, patterns):
            _push_unique(semantic, label)

    _push_unique(state, f"surface:{str(current_surface or session.get('current_surface') or 'upload').strip().lower()}")
    if int(session.get("user_id") or 0):
        _push_unique(state, "signed_in")
    else:
        _push_unique(state, "anonymous_session")
    if session_state.get("last_product_labels"):
        _push_unique(state, "product_context")
    if session_state.get("last_business_intent"):
        _push_unique(state, "business_context")
    if int(session_state.get("turn_count") or 0) > 0:
        _push_unique(state, "existing_session_turns")
    if bundle.get("memory_refs"):
        _push_unique(state, "memory_shelf_available")
    if vector_refs:
        _push_unique(state, "vector_memory_hit")

    if guarded:
        _push_unique(risk, str(guarded.get("provider") or "policy_guard"))
        _push_unique(confidence, "policy_lock")
    if route_info.get("needs_memory"):
        _push_unique(confidence, "memory_required")
        if not vector_refs:
            _push_unique(confidence, "retrieval_sparse")
    if route_info.get("use_deep_reasoning"):
        _push_unique(confidence, "complex_path")

    if intent in {"product", "business_support"}:
        _push_unique(business, intent)
    for label, patterns in _BUSINESS_PATTERNS.items():
        if _matches(text, patterns):
            _push_unique(business, label)

    for label, patterns in _LEARNING_PATTERNS.items():
        if _matches(text, patterns):
            _push_unique(learning, label)
    if bundle.get("memory_refs"):
        _push_unique(learning, "memory_hit_available")
    if route_info.get("needs_memory") and vector_refs:
        _push_unique(learning, "memory_hit_used")

    confidence_score = _estimate_confidence(route_info, guarded, vector_refs)
    primary_trigger = (
        risk[0] if risk else business[0] if business else semantic[0] if semantic else state[0] if state else "general_chat"
    )
    recommended_decisions = ["intent_route", "reply_mode"]
    if route_info.get("needs_memory"):
        recommended_decisions.append("retrieval_plan")
    if guarded:
        recommended_decisions.append("risk_gate")
    elif intent in {"quick_chat", "creative_guidance", "deep_reasoning", "visual_reasoning"}:
        recommended_decisions.append("model_choice")
    recommended_decisions.append("memory_promotion")

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
