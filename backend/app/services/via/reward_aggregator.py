"""
services/via/reward_aggregator.py — Outcome and reward shaping for Via replies
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


_TRACE_EVENT_ALIASES = {
    "click": "click",
    "product_click": "click",
    "link_click": "click",
    "open_link": "click",
    "compare": "compare",
    "compare_open": "compare",
    "add_to_cart": "add_to_cart",
    "cart_add": "add_to_cart",
    "purchase": "purchase",
    "checkout_success": "purchase",
    "affiliate_order": "affiliate_order",
    "shopify_order": "affiliate_order",
    "thumb_up": "thumb_up",
    "thumb_down": "thumb_down",
}


def summarize_via_reward_traces(traces: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = list(traces or [])
    counts: Counter[str] = Counter()
    value_total = 0.0
    commission_total = 0.0
    thumb_feedback = 0
    last_event_type = ""
    last_event_at = ""
    for item in items:
        raw_type = str(item.get("event_type") or "").strip().lower()
        event_type = _TRACE_EVENT_ALIASES.get(raw_type, raw_type or "unknown")
        counts[event_type] += 1
        payload = dict(item.get("event_payload") or {})
        value_total += float(item.get("event_value") or 0.0)
        commission_total += float(
            payload.get("estimated_commission")
            or payload.get("commission_total")
            or payload.get("commission")
            or 0.0
        )
        if event_type == "thumb_up":
            thumb_feedback = 1
        elif event_type == "thumb_down":
            thumb_feedback = -1
        last_event_type = event_type
        last_event_at = str(item.get("created_at") or "").strip()

    clicked_product = bool(
        counts.get("click")
        or counts.get("compare")
        or counts.get("add_to_cart")
        or counts.get("purchase")
        or counts.get("affiliate_order")
    )
    added_to_cart = bool(counts.get("add_to_cart") or counts.get("purchase") or counts.get("affiliate_order"))
    purchased = bool(counts.get("purchase") or counts.get("affiliate_order"))
    reward_delta = 0.0
    reward_delta += min(0.18, counts.get("click", 0) * 0.06)
    reward_delta += min(0.12, counts.get("compare", 0) * 0.08)
    reward_delta += min(0.2, counts.get("add_to_cart", 0) * 0.14)
    reward_delta += min(0.24, counts.get("purchase", 0) * 0.2)
    reward_delta += min(0.28, counts.get("affiliate_order", 0) * 0.24)
    if thumb_feedback > 0:
        reward_delta += 0.06
    elif thumb_feedback < 0:
        reward_delta -= 0.08
    if value_total > 0:
        reward_delta += min(0.08, value_total / 2000.0)
    reward_delta = _clamp(reward_delta, lower=-0.3, upper=0.7)
    return {
        "event_count": len(items),
        "event_types": dict(counts),
        "click_count": int(counts.get("click", 0)),
        "compare_count": int(counts.get("compare", 0)),
        "add_to_cart_count": int(counts.get("add_to_cart", 0)),
        "purchase_count": int(counts.get("purchase", 0)),
        "affiliate_order_count": int(counts.get("affiliate_order", 0)),
        "clicked_product": clicked_product,
        "added_to_cart": added_to_cart,
        "purchased": purchased,
        "thumb_feedback": thumb_feedback,
        "order_value_total": round(value_total, 2),
        "estimated_commission_total": round(commission_total, 2),
        "reward_delta": round(reward_delta, 4),
        "last_event_type": last_event_type,
        "last_event_at": last_event_at,
    }


def merge_via_reward_trace_summary(
    *,
    outcome: dict[str, Any],
    reward_trace_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace_summary = summarize_via_reward_traces([]) if reward_trace_summary is None else dict(reward_trace_summary)
    payload = dict(outcome.get("outcome_payload") or {})
    base_reward = float(payload.get("base_reward_score") or outcome.get("reward_score") or 0.0)
    merged_payload = {
        **payload,
        "base_reward_score": round(base_reward, 4),
        "reward_trace_summary": trace_summary,
        "trace_event_count": int(trace_summary.get("event_count") or 0),
    }
    return {
        **outcome,
        "clicked_product": bool(outcome.get("clicked_product")) or bool(trace_summary.get("clicked_product")),
        "added_to_cart": bool(outcome.get("added_to_cart")) or bool(trace_summary.get("added_to_cart")),
        "purchased": bool(outcome.get("purchased")) or bool(trace_summary.get("purchased")),
        "thumb_feedback": int(trace_summary.get("thumb_feedback") or outcome.get("thumb_feedback") or 0),
        "reward_score": round(_clamp(base_reward + float(trace_summary.get("reward_delta") or 0.0)), 4),
        "outcome_payload": merged_payload,
    }


def aggregate_via_reply_outcome(
    *,
    session_state: dict[str, Any] | None,
    route_info: dict[str, Any] | None,
    reply: dict[str, Any],
    user_text: str,
    guarded: dict[str, Any] | None = None,
    vector_refs: list[dict[str, Any]] | None = None,
    learning_signals: dict[str, Any] | None = None,
    reward_traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    session_state = dict(session_state or {})
    route_info = dict(route_info or {})
    learning_signals = dict(learning_signals or {})
    vector_refs = list(vector_refs or [])
    payload = reply.get("payload") or {}
    reply_text = str(reply.get("text") or "").strip()

    accepted = bool(reply_text)
    followup_depth = max(0, int(session_state.get("turn_count") or 0))
    rephrase_needed = accepted and len(reply_text) < 28 and str(route_info.get("intent") or "") not in {"product", "business_support"}
    clicked_product = bool(payload.get("product_mode") and "viltrox.com" in reply_text.lower())
    added_to_cart = False
    purchased = False
    thumb_feedback = 0
    abuse_flag = 1 if guarded else 0

    reward = 0.18 if accepted else 0.0
    if payload.get("product_mode"):
        reward += 0.18
    if payload.get("business_mode"):
        reward += 0.14
    if route_info.get("needs_memory") and vector_refs:
        reward += 0.12
    if learning_signals.get("keywords"):
        reward += min(0.12, 0.03 * len(list(learning_signals.get("keywords") or [])[:4]))
    if learning_signals.get("traits"):
        reward += 0.08
    if route_info.get("use_deep_reasoning"):
        reward += 0.05
    if rephrase_needed:
        reward -= 0.12
    if abuse_flag:
        reward -= 0.08
    base_reward = _clamp(reward)

    trace_summary = summarize_via_reward_traces(reward_traces)
    clicked_product = clicked_product or bool(trace_summary.get("clicked_product"))
    added_to_cart = added_to_cart or bool(trace_summary.get("added_to_cart"))
    purchased = purchased or bool(trace_summary.get("purchased"))
    thumb_feedback = int(trace_summary.get("thumb_feedback") or thumb_feedback)
    reward = _clamp(base_reward + float(trace_summary.get("reward_delta") or 0.0))

    return {
        "accepted": accepted,
        "followup_depth": followup_depth,
        "rephrase_needed": rephrase_needed,
        "clicked_product": clicked_product,
        "added_to_cart": added_to_cart,
        "purchased": purchased,
        "thumb_feedback": thumb_feedback,
        "abuse_flag": abuse_flag,
        "reward_score": round(reward, 4),
        "outcome_payload": {
            "intent": route_info.get("intent") or "",
            "brain": route_info.get("brain") or "",
            "provider": payload.get("provider") or "",
            "model": payload.get("model") or "",
            "quick_actions": list(payload.get("quick_actions") or [])[:3],
            "user_text_excerpt": str(user_text or "").strip()[:160],
            "base_reward_score": round(base_reward, 4),
            "reward_trace_summary": trace_summary,
        },
    }
