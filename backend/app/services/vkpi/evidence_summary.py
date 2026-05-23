"""Traceable evidence summaries for V-KPI KOL decision cards.

P4.54 keeps the aperture small: summaries are deterministic and extractive by
default. This module reads the existing IntelligenceCard payload, does not call
providers or LLMs, and does not persist generated prose.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.services.vkpi import kol_intelligence_card, llm_gateway


SECTION_ORDER = (
    "freshness",
    "dimensions11",
    "competitors",
    "brand_signal",
    "comment_intelligence",
    "video_analysis",
    "memory_card",
    "product_fit",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, limit: int = 500) -> str:
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
    joined = "|".join(_text(part, 200) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _section_index(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = card.get("evidence_index") if isinstance(card.get("evidence_index"), list) else []
    return {
        _text(row.get("section"), 80): row
        for row in rows
        if isinstance(row, dict) and _text(row.get("section"), 80)
    }


def _ref_from_mapping(section: str, source: str, row: dict[str, Any], *, fallback_id: str = "") -> dict[str, Any]:
    source_table = _text(row.get("source_table") or row.get("source") or source, 120)
    source_id = row.get("source_id") or row.get("id") or row.get("evidence_id") or row.get("signal_uid") or fallback_id
    title = _text(row.get("title") or row.get("summary") or row.get("detail") or row.get("reasoning") or source_table, 220)
    return {
        "section": section,
        "source": source,
        "source_table": source_table,
        "source_id": _text(source_id, 160),
        "source_url": _text(row.get("source_url") or row.get("url") or row.get("post_url"), 500),
        "evidence_id": _text(row.get("evidence_id") or row.get("signal_uid") or fallback_id or _hash_key(section, source_table, source_id, title), 160),
        "title": title,
        "confidence": _float(row.get("confidence") or row.get("source_confidence") or row.get("risk_score")),
    }


def _section_ref(section: str, index_row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "section": section,
        "source": _text(index_row.get("source") or payload.get("source") or "intelligence_card", 160),
        "source_table": _text(index_row.get("source") or payload.get("source") or "intelligence_card", 160),
        "source_id": _text(payload.get("kol_pool_id") or section, 160),
        "source_url": "",
        "evidence_id": f"section:{section}",
        "title": _text(index_row.get("label") or section, 220),
        "confidence": _float(index_row.get("confidence")),
    }


def _evidence_refs(section: str, index_row: dict[str, Any], payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []

    if section == "competitors":
        refs.extend(_ref_from_mapping(section, "competitor_evidence", row) for row in _as_list(payload.get("evidence")))
    elif section == "brand_signal":
        refs.extend(_ref_from_mapping(section, "brand_signal", row) for row in _as_list(payload.get("signals")))
    elif section == "comment_intelligence":
        refs.extend(_ref_from_mapping(section, "comment_intelligence", row) for row in _as_list(payload.get("evidence")))
        refs.extend(_ref_from_mapping(section, "comment_sample", row) for row in _as_list(payload.get("samples")))
    elif section == "video_analysis":
        refs.extend(_ref_from_mapping(section, "video_analysis", row) for row in _as_list(payload.get("evidence")))
    elif section == "memory_card":
        for key in ("recent_cooperations", "recent_posts"):
            refs.extend(_ref_from_mapping(section, key, row, fallback_id=key) for row in _as_list(payload.get(key)))
        history = _as_dict(payload.get("history_match"))
        if history:
            refs.append(_ref_from_mapping(section, "history_match", history, fallback_id="history_match"))
    elif section == "product_fit":
        for key in ("official_catalog", "discovery", "rule_evidence"):
            refs.extend(_ref_from_mapping(section, key, row, fallback_id=key) for row in _as_list(payload.get(key)))
    elif section == "dimensions11":
        blocks = _as_dict(payload.get("blocks"))
        confidence = _as_dict(payload.get("confidence"))
        for key, block in blocks.items():
            refs.append(
                {
                    "section": section,
                    "source": "dimensions11_rule_blocks",
                    "source_table": "vkpi_kol_pool + cached_posts",
                    "source_id": _text(key, 120),
                    "source_url": "",
                    "evidence_id": f"dimensions11:{key}",
                    "title": _text(block or key, 220),
                    "confidence": _float(confidence.get(key)),
                }
            )
    elif section == "freshness":
        refs.append(_section_ref(section, index_row, payload))

    if not refs:
        refs.append(_section_ref(section, index_row, payload))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        key = _text(ref.get("evidence_id") or _hash_key(ref.get("section"), ref.get("source_table"), ref.get("source_id")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
        if len(deduped) >= max(1, min(25, int(limit or 8))):
            break
    return deduped


def _section_summary_text(section: str, payload: dict[str, Any], index_row: dict[str, Any]) -> str:
    status = _text(index_row.get("status") or payload.get("status") or "ready", 40)
    evidence_count = _int(index_row.get("evidence_count"))
    if section == "freshness":
        tier = _text(payload.get("tier") or "unknown", 40)
        reason = _text(payload.get("reason") or payload.get("tier_reason") or "freshness_state", 160)
        return f"Refresh tier is {tier}; reason={reason}; evidence_count={max(evidence_count, 1)}."
    if section == "dimensions11":
        score = _int(payload.get("overall_score"))
        ready_blocks = sum(1 for value in _as_dict(payload.get("confidence")).values() if _float(value) > 0)
        return f"11D profile is {status}; overall_score={score}; ready_blocks={ready_blocks}; evidence_count={evidence_count}."
    if section == "competitors":
        summary = _as_dict(payload.get("summary"))
        brand = _text(summary.get("competitor_brand") or "none", 80)
        risk = _text(summary.get("risk_tier") or "unknown", 40)
        return f"Competitor relation is {status}; top_brand={brand}; risk_tier={risk}; evidence_count={evidence_count}."
    if section == "brand_signal":
        return f"Brand signals are {status}; signal_count={_int(payload.get('signal_count'))}; cached_posts={_int(payload.get('cached_post_count'))}."
    if section == "comment_intelligence":
        contract = _as_dict(payload.get("contract"))
        return (
            f"Comment intelligence is {status}; cached_comments={_int(payload.get('cached_comment_count'))}; "
            f"declared={_int(contract.get('declared'))}; cap={_int(contract.get('cap'))}; evidence_count={evidence_count}."
        )
    if section == "video_analysis":
        fields = _as_dict(payload.get("field_counts"))
        field_names = ", ".join(sorted(fields.keys())[:5]) if fields else "none"
        return (
            f"Video analysis is {status}; analyzed_rows={_int(payload.get('analyzed_count'))}; "
            f"stored_rows={_int(payload.get('row_count'))}; fields={field_names}; evidence_count={evidence_count}."
        )
    if section == "memory_card":
        history = _as_dict(payload.get("history_match"))
        competitor = _as_dict(payload.get("competitor_memory"))
        return (
            f"Memory card is {status}; cooperation_count={_int(history.get('cooperation_count'))}; "
            f"competitor_risk={_text(competitor.get('risk_tier') or 'unknown', 40)}; evidence_count={evidence_count}."
        )
    if section == "product_fit":
        return (
            f"Product fit is {status}; candidates={_int(payload.get('count'))}; "
            f"official_catalog={_int(payload.get('official_catalog_count'))}; discovery={_int(payload.get('discovery_count'))}."
        )
    return f"{_text(index_row.get('label') or section, 80)} is {status}; evidence_count={evidence_count}."


def _summary_item(section: str, card: dict[str, Any], index_row: dict[str, Any], *, ref_limit: int) -> dict[str, Any]:
    payload = _as_dict(card.get(section))
    refs = _evidence_refs(section, index_row, payload, limit=ref_limit)
    summary_text = _section_summary_text(section, payload, index_row)
    return {
        "summary_uid": f"evsum_{section}_{_hash_key(card.get('kol_pool_id'), section, summary_text)}",
        "section": section,
        "label": _text(index_row.get("label") or section, 120),
        "status": _text(index_row.get("status") or payload.get("status") or ("ready" if section == "freshness" else "unknown"), 40),
        "summary_text": summary_text,
        "evidence_count": _int(index_row.get("evidence_count")),
        "confidence": _float(index_row.get("confidence")),
        "source": _text(index_row.get("source") or payload.get("source") or "intelligence_card", 160),
        "evidence_refs": refs,
        "traceable": bool(refs),
        "generation": {
            "method": "deterministic_extract_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "summarize_existing_evidence_only",
        },
    }


def build_kol_pool_evidence_summary(
    kol_pool_id: int,
    *,
    include_product_fit: bool = True,
    ref_limit: int = 8,
    include_llm_preflight: bool = True,
) -> dict[str, Any]:
    card = kol_intelligence_card.build_kol_pool_intelligence_card(
        int(kol_pool_id),
        include_product_fit=bool(include_product_fit),
    )
    index = _section_index(card)
    summaries = [
        _summary_item(section, card, index.get(section, {"section": section}), ref_limit=ref_limit)
        for section in SECTION_ORDER
        if section in card
    ]
    checks = {
        "all_summaries_traceable": bool(summaries) and all(bool(item.get("evidence_refs")) for item in summaries),
        "no_provider_calls": not bool(card.get("provider_calls")),
        "no_llm_calls": not bool(card.get("llm_calls")),
        "no_write_db": not bool(card.get("write_db")),
        "existing_evidence_only": True,
    }
    payload: dict[str, Any] = {
        "mode": "read_only_kol_evidence_summary_v0",
        "generated_at": _utcnow(),
        "kol_pool_id": int(kol_pool_id),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "policy": {
            "existing_evidence_only": True,
            "new_fact_generation": False,
            "summary_method": "deterministic_extract_v0",
            "llm_allowed": False,
        },
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "item": card.get("item") if isinstance(card.get("item"), dict) else {},
        "decision_support": card.get("decision_support") if isinstance(card.get("decision_support"), dict) else {},
        "summaries": summaries,
        "summary_count": len(summaries),
        "evidence_ref_count": sum(len(item.get("evidence_refs") or []) for item in summaries),
    }
    if include_llm_preflight:
        prompt = "\n".join(_text(item.get("summary_text"), 300) for item in summaries)
        payload["llm_budget_preflight"] = llm_gateway.budget_preflight(
            prompt,
            purpose="p4_evidence_summary",
            max_output_tokens=300,
            cost_tag="cron:p4_evidence_summary",
        )
    return payload
