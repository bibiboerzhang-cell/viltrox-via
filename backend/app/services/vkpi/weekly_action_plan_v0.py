"""P6.77 read-only weekly action plan v0.

This turns the latest launch acceptance estimate and today's signal digest into
specific review/contact actions. It does not create projects, outreach, tasks,
or recommendations.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.services.vkpi import new_launch_acceptance_v0, today_new_signals_v0


PLAN_VERSION = "weekly-action-plan-v0.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except Exception:
        return default


def _priority(score: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 45:
        return "medium"
    return "watch"


def _candidate_actions(acceptance: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    product = acceptance.get("product") if isinstance(acceptance.get("product"), dict) else {}
    sku = product.get("sku") or acceptance.get("summary", {}).get("sku")
    for candidate in acceptance.get("top_candidates") or []:
        score = _float(candidate.get("acceptance_score"))
        if score < 45:
            continue
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        actions.append(
            {
                "action_type": "contact_kol_for_sku",
                "priority": _priority(score),
                "score": round(score, 2),
                "title": f"Review @{candidate.get('handle')} for {sku}",
                "reason": (
                    f"{candidate.get('platform')} candidate tier={candidate.get('acceptance_tier')} "
                    f"fit={evidence.get('kol_product_fit_score')} momentum={evidence.get('platform_signal_count')}"
                ),
                "sku": sku,
                "entity": {
                    "kol_pool_id": candidate.get("kol_pool_id"),
                    "platform": candidate.get("platform"),
                    "handle": candidate.get("handle"),
                    "display_name": candidate.get("display_name"),
                },
                "evidence": {
                    "candidate": candidate,
                    "product": product,
                    "source": "p6_74_new_launch_acceptance_v0",
                },
                "human_next_step": "Open the KOL evidence card, verify fit/risk, then decide contact or watch.",
            }
        )
        if len(actions) >= limit:
            break
    return actions


def _signal_actions(signals: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in signals.get("action_items") or []:
        action = _text(item.get("action"))
        priority = _text(item.get("priority") or "medium")
        if action == "no_action_required":
            continue
        score = {"high": 72.0, "medium": 52.0, "low": 25.0}.get(priority, 45.0)
        actions.append(
            {
                "action_type": action,
                "priority": priority,
                "score": score,
                "title": item.get("reason"),
                "reason": item.get("reason"),
                "entity": item.get("entity") if isinstance(item.get("entity"), dict) else {},
                "evidence": {
                    "source": "p6_76_today_new_signals_v0",
                    "action_item": item,
                },
                "human_next_step": "Review the underlying post/comment before taking action.",
            }
        )
        if len(actions) >= limit:
            break
    return actions


def _market_actions(signals: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    brand_counts: Counter[str] = Counter()
    for item in signals.get("market_events") or []:
        brand = _text(item.get("brand") or "unknown")
        brand_counts[brand] += 1
    for brand, count in brand_counts.most_common(limit):
        actions.append(
            {
                "action_type": "review_competitor_positioning",
                "priority": "medium" if count < 3 else "high",
                "score": min(90.0, 45.0 + count * 10.0),
                "title": f"Review competitor signal cluster: {brand}",
                "reason": f"{count} market events in the current digest window",
                "entity": {"brand": brand},
                "evidence": {"source": "p6_76_today_new_signals_v0"},
                "human_next_step": "Check whether campaign copy or SKU positioning needs a response.",
            }
        )
    return actions


def _dedupe(actions: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for action in sorted(actions, key=lambda item: (_float(item.get("score")), _text(item.get("priority"))), reverse=True):
        entity = action.get("entity") if isinstance(action.get("entity"), dict) else {}
        key = (
            _text(action.get("action_type")),
            _text(entity.get("kol_pool_id") or entity.get("post_uid") or entity.get("comment_id") or entity.get("brand")),
            _text(action.get("sku")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
        if len(unique) >= limit:
            break
    return unique


def build_weekly_action_plan_v0(
    *,
    sku: str = "",
    top_n: int = 12,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    bounded_top = max(1, min(50, int(top_n or 12)))
    acceptance = new_launch_acceptance_v0.build_new_launch_acceptance_v0(
        sku=sku,
        kol_limit=200,
        top_n=bounded_top,
        lookback_days=max(1, int((lookback_hours + 23) / 24)),
    )
    signals = today_new_signals_v0.build_today_new_signals_v0(
        lookback_hours=max(1, min(168, int(lookback_hours or 24))),
        limit=100,
    )
    actions = _dedupe(
        _candidate_actions(acceptance, bounded_top)
        + _signal_actions(signals, bounded_top)
        + _market_actions(signals, 6),
        bounded_top,
    )
    if not actions:
        actions = [
            {
                "action_type": "review_planning_inputs",
                "priority": "watch",
                "score": 20.0,
                "title": "No qualified weekly action met the current threshold",
                "reason": "Launch acceptance and today signals loaded, but no KOL/contact/review action reached the current rule threshold.",
                "entity": {},
                "evidence": {
                    "new_launch_acceptance_summary": acceptance.get("summary"),
                    "today_new_signals_summary": signals.get("summary"),
                },
                "human_next_step": "Check whether the chosen SKU or threshold should change before forcing outreach.",
            }
        ]
    action_counts: Counter[str] = Counter(_text(item.get("action_type")) for item in actions)
    priority_counts: Counter[str] = Counter(_text(item.get("priority")) for item in actions)
    checks = {
        "plan_version_set": bool(PLAN_VERSION),
        "acceptance_report_loaded": bool(acceptance),
        "acceptance_report_passed": bool(acceptance.get("passed")),
        "today_signals_loaded": bool(signals),
        "today_signals_passed": bool(signals.get("passed")),
        "actions_generated": bool(actions),
        "actions_have_evidence": all(bool(item.get("evidence")) for item in actions),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p6_77_weekly_action_plan_v0",
        "generated_at": _now(),
        "plan_version": PLAN_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "sku": sku,
            "top_n": bounded_top,
            "lookback_hours": lookback_hours,
        },
        "summary": {
            "action_count": len(actions),
            "action_types": dict(action_counts),
            "priorities": dict(priority_counts),
            "sku": acceptance.get("summary", {}).get("sku"),
            "candidate_count": acceptance.get("summary", {}).get("candidate_count"),
            "today_abnormal_growth": signals.get("summary", {}).get("abnormal_growth_24h"),
            "today_comment_status": signals.get("summary", {}).get("comment_status"),
            "source_scope": "existing_db_only",
        },
        "actions": actions,
        "source_reports": {
            "new_launch_acceptance": {
                "mode": acceptance.get("mode"),
                "generated_at": acceptance.get("generated_at"),
                "summary": acceptance.get("summary"),
            },
            "today_new_signals": {
                "mode": signals.get("mode"),
                "generated_at": signals.get("generated_at"),
                "summary": signals.get("summary"),
            },
        },
        "policy": {
            "read_only": True,
            "no_project_created": True,
            "no_outreach_triggered": True,
            "human_approval_required": True,
            "not_a_recommendation_engine": True,
        },
        "next_steps": [
            "Use actions as a weekly planning checklist.",
            "Human review must decide contact, watch, caution, or avoid.",
            "P6.78 can later compare action outcomes against observed results.",
        ],
    }
