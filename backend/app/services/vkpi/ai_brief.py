"""Read-only AI Brief v0 assembled from traceable evidence.

Despite the product name, this v0 path is deterministic. It does not call an
LLM, provider, crawler, task queue, or write database rows.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.services.vkpi import evidence_summary


SECTION_PRIORITY = {
    "product_fit": 10,
    "competitors": 20,
    "brand_signal": 30,
    "comment_intelligence": 40,
    "video_analysis": 50,
    "memory_card": 60,
    "dimensions11": 70,
    "freshness": 80,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, limit: int = 700) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _hash_key(*parts: Any) -> str:
    joined = "|".join(_text(part, 220) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _ref_key(ref: dict[str, Any]) -> str:
    return _text(ref.get("evidence_id") or _hash_key(ref.get("section"), ref.get("source_table"), ref.get("source_id")), 160)


def _clean_refs(refs: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in _as_list(refs):
        if not isinstance(ref, dict):
            continue
        evidence_id = _ref_key(ref)
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        cleaned.append(
            {
                "section": _text(ref.get("section"), 80),
                "source": _text(ref.get("source"), 120),
                "source_table": _text(ref.get("source_table"), 160),
                "source_id": _text(ref.get("source_id"), 160),
                "source_url": _text(ref.get("source_url"), 500),
                "evidence_id": evidence_id,
                "title": _text(ref.get("title"), 240),
                "confidence": _float(ref.get("confidence")),
            }
        )
        if len(cleaned) >= max(1, min(25, int(limit or 8))):
            break
    return cleaned


def _brief_kind(section: str, status: str) -> str:
    if section == "competitors":
        return "risk"
    if section in {"product_fit", "brand_signal", "memory_card", "video_analysis"}:
        return "evidence"
    if section == "comment_intelligence":
        return "sample_context"
    if status == "empty":
        return "coverage_gap"
    return "context"


def _brief_item(summary: dict[str, Any], *, max_refs: int) -> dict[str, Any] | None:
    refs = _clean_refs(summary.get("evidence_refs"), limit=max_refs)
    if not refs:
        return None
    section = _text(summary.get("section"), 80)
    status = _text(summary.get("status") or "unknown", 40)
    text = _text(summary.get("summary_text"), 700)
    if not section or not text:
        return None
    return {
        "brief_uid": f"aibrief_{section}_{_hash_key(summary.get('summary_uid'), text)}",
        "section": section,
        "label": _text(summary.get("label") or section, 120),
        "kind": _brief_kind(section, status),
        "status": status,
        "text": text,
        "confidence": _float(summary.get("confidence")),
        "evidence_refs": refs,
        "generation": {
            "method": "deterministic_evidence_brief_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "existing_evidence_only",
        },
    }


def _action_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    section = _text(item.get("section"), 80)
    refs = _clean_refs(item.get("evidence_refs"), limit=4)
    if not refs:
        return None
    text = ""
    priority = "medium"
    if section == "product_fit" and item.get("status") == "ready":
        text = "Review Product Fit evidence before choosing a SKU or outreach angle."
        priority = "high"
    elif section == "competitors" and item.get("status") == "ready":
        text = "Review competitor evidence before making a contact decision."
        priority = "high"
    elif section == "comment_intelligence" and item.get("status") == "ready":
        text = "Use cached comment samples with their declared/cached/cap limits."
    elif section == "video_analysis" and item.get("status") == "ready":
        text = "Use only stored video-analysis fields; do not infer missing video facts."
    elif section == "brand_signal" and item.get("status") == "ready":
        text = "Open Brand Signal evidence before treating a mention as cooperation."
    if not text:
        return None
    return {
        "action_uid": f"aibrief_action_{section}_{_hash_key(text, refs[0].get('evidence_id'))}",
        "section": section,
        "priority": priority,
        "text": text,
        "evidence_refs": refs,
        "generation": {
            "method": "deterministic_evidence_brief_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "action_requires_evidence_refs",
        },
    }


def _backlinks(items: list[dict[str, Any]], actions: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [*items, *actions]:
        for ref in _clean_refs(source.get("evidence_refs"), limit=8):
            key = _ref_key(ref)
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _headline(items: list[dict[str, Any]], decision_support: dict[str, Any]) -> str:
    readiness = _text(decision_support.get("readiness") or "unknown", 40)
    if not items:
        return f"No traceable AI Brief items are available; readiness={readiness}."
    top_sections = [item["section"] for item in items[:3]]
    return f"AI Brief v0 is anchored on {', '.join(top_sections)} evidence; readiness={readiness}."


def build_kol_pool_ai_brief(
    kol_pool_id: int,
    *,
    include_product_fit: bool = True,
    ref_limit: int = 8,
    max_items: int = 8,
) -> dict[str, Any]:
    summary = evidence_summary.build_kol_pool_evidence_summary(
        int(kol_pool_id),
        include_product_fit=bool(include_product_fit),
        ref_limit=max(1, min(25, int(ref_limit or 8))),
        include_llm_preflight=False,
    )
    summaries = [
        row
        for row in _as_list(summary.get("summaries"))
        if isinstance(row, dict)
    ]
    summaries.sort(key=lambda row: (SECTION_PRIORITY.get(_text(row.get("section"), 80), 999), -_int(row.get("evidence_count"))))
    items: list[dict[str, Any]] = []
    dropped = 0
    for row in summaries:
        item = _brief_item(row, max_refs=ref_limit)
        if item is None:
            dropped += 1
            continue
        items.append(item)
        if len(items) >= max(1, min(12, int(max_items or 8))):
            break
    actions = [action for action in (_action_from_item(item) for item in items) if isinstance(action, dict)]
    backlinks = _backlinks(items, actions)
    decision_support = _as_dict(summary.get("decision_support"))
    checks = {
        "source_summary_passed": bool(summary.get("passed")),
        "existing_evidence_only": bool(_as_dict(summary.get("policy")).get("existing_evidence_only")),
        "new_fact_generation_disabled": bool(_as_dict(summary.get("policy")).get("new_fact_generation") is False),
        "all_brief_items_traceable": bool(items) and all(bool(item.get("evidence_refs")) for item in items),
        "all_next_actions_traceable": all(bool(action.get("evidence_refs")) for action in actions),
        "no_provider_calls": not bool(summary.get("provider_calls")),
        "no_llm_calls": not bool(summary.get("llm_calls")),
        "no_write_db": not bool(summary.get("write_db")),
        "no_unsupported_recommendations": all(bool(action.get("evidence_refs")) for action in actions),
    }
    return {
        "mode": "read_only_kol_ai_brief_v0",
        "generated_at": _utcnow(),
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "policy": {
            "existing_evidence_only": True,
            "new_fact_generation": False,
            "llm_allowed": False,
            "provider_calls_allowed": False,
            "recommendations_require_evidence": True,
            "empty_sections_do_not_create_suggestions": True,
        },
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "headline": _headline(items, decision_support),
        "item": summary.get("item") if isinstance(summary.get("item"), dict) else {},
        "decision_support": decision_support,
        "brief_items": items,
        "brief_item_count": len(items),
        "next_actions": actions,
        "next_action_count": len(actions),
        "evidence_backlinks": backlinks,
        "evidence_backlink_count": len(backlinks),
        "dropped_untraceable_summary_count": dropped,
        "source_summary": {
            "mode": summary.get("mode"),
            "summary_count": _int(summary.get("summary_count")),
            "evidence_ref_count": _int(summary.get("evidence_ref_count")),
            "checks": summary.get("checks") if isinstance(summary.get("checks"), dict) else {},
        },
    }
