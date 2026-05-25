"""Pure Evidence Agent report and evidence-chain builders."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


EVIDENCE_AGENT_VERSION = "evidence-agent-v0.1"
REQUIRED_SECTIONS = (
    "freshness",
    "dimensions11",
    "competitors",
    "brand_signal",
    "comment_intelligence",
    "video_analysis",
    "memory_card",
    "product_fit",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def as_float(value: Any) -> float:
    try:
        return float(str(value or "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_kol_pool_ids(value: Any) -> list[int]:
    if isinstance(value, str):
        raw_values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []
    ids: list[int] = []
    for raw in raw_values:
        kol_id = as_int(raw)
        if kol_id > 0 and kol_id not in ids:
            ids.append(kol_id)
    return ids


def weekly_targets(weekly: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(limit or 12)))
    targets: list[dict[str, Any]] = []
    seen: set[int] = set()
    for action in as_list(weekly.get("actions")):
        if not isinstance(action, dict):
            continue
        entity = as_dict(action.get("entity"))
        kol_pool_id = as_int(entity.get("kol_pool_id"))
        if kol_pool_id <= 0 or kol_pool_id in seen:
            continue
        seen.add(kol_pool_id)
        targets.append(
            {
                "kol_pool_id": kol_pool_id,
                "source": "p6_77_weekly_action_plan",
                "action_type": text(action.get("action_type"), 120),
                "priority": text(action.get("priority"), 40),
                "score": as_float(action.get("score")),
                "title": text(action.get("title"), 240),
                "reason": text(action.get("reason"), 300),
                "entity": entity,
            }
        )
        if len(targets) >= safe_limit:
            break
    return targets


def explicit_targets(kol_pool_ids: list[int], limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(limit or 12)))
    return [{"kol_pool_id": kol_id, "source": "explicit_kol_pool_ids"} for kol_id in kol_pool_ids[:safe_limit]]


def _ref_key(ref: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        text(ref.get("section"), 120),
        text(ref.get("evidence_id"), 180),
        text(ref.get("source_table"), 180),
        text(ref.get("source_id"), 180),
    )


def _normalize_ref(section: str, ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "section": section,
        "evidence_id": text(ref.get("evidence_id"), 180),
        "source": text(ref.get("source"), 160),
        "source_table": text(ref.get("source_table"), 160),
        "source_id": text(ref.get("source_id"), 180),
        "source_url": text(ref.get("source_url"), 500),
        "title": text(ref.get("title"), 240),
        "confidence": as_float(ref.get("confidence")),
    }


def _evidence_refs(summaries: list[Any], limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(100, int(limit or 24)))
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        section = text(summary.get("section"), 120)
        for ref in as_list(summary.get("evidence_refs")):
            if not isinstance(ref, dict):
                continue
            normalized = _normalize_ref(section, ref)
            key = _ref_key(normalized)
            if key in seen:
                continue
            seen.add(key)
            refs.append(normalized)
            if len(refs) >= safe_limit:
                return refs
    return refs


def _extractive_claims(summaries: list[Any], claim_limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(50, int(claim_limit or 12)))
    claims: list[dict[str, Any]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        refs = [ref for ref in as_list(summary.get("evidence_refs")) if isinstance(ref, dict)]
        if not refs:
            continue
        summary_text = text(summary.get("summary_text"), 360)
        if not summary_text:
            continue
        section = text(summary.get("section"), 120)
        claims.append(
            {
                "section": section,
                "claim_text": summary_text,
                "method": "extractive_from_existing_evidence_summary",
                "evidence_ref_count": len(refs),
                "evidence_refs": [_normalize_ref(section, ref) for ref in refs[:5]],
                "new_fact_generated": False,
            }
        )
        if len(claims) >= safe_limit:
            break
    return claims


def _section_rows(summaries: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        rows.append(
            {
                "section": text(summary.get("section"), 120),
                "label": text(summary.get("label"), 160),
                "status": text(summary.get("status"), 60),
                "evidence_count": as_int(summary.get("evidence_count")),
                "evidence_ref_count": len(as_list(summary.get("evidence_refs"))),
                "confidence": as_float(summary.get("confidence")),
                "traceable": bool(summary.get("traceable")),
                "source": text(summary.get("source"), 180),
            }
        )
    return rows


def _missing_sections(section_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_section = {row.get("section"): row for row in section_rows}
    missing: list[dict[str, Any]] = []
    for section in REQUIRED_SECTIONS:
        row = by_section.get(section)
        if not row:
            missing.append({"section": section, "reason": "section_missing"})
            continue
        status = text(row.get("status")).lower()
        if status in {"empty", "unavailable", "not_configured", "skipped", "unknown"} or as_int(row.get("evidence_ref_count")) <= 0:
            missing.append({"section": section, "reason": status or "no_traceable_refs"})
    return missing


def build_error_chain(target: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "kol_pool_id": as_int(target.get("kol_pool_id")),
        "target": target,
        "status": "error",
        "error": f"{type(exc).__name__}: {str(exc)[:260]}",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "evidence_ref_count": 0,
        "claims": [],
        "sections": [],
        "missing_sections": [{"section": "all", "reason": "evidence_summary_failed"}],
    }


def build_evidence_chain_from_summary(
    target: dict[str, Any],
    payload: dict[str, Any],
    *,
    ref_limit: int,
    claim_limit: int,
) -> dict[str, Any]:
    kol_pool_id = as_int(target.get("kol_pool_id"))
    summaries = as_list(payload.get("summaries"))
    sections = _section_rows(summaries)
    refs = _evidence_refs(summaries, ref_limit)
    claims = _extractive_claims(summaries, claim_limit)
    item = as_dict(payload.get("item"))
    return {
        "kol_pool_id": kol_pool_id,
        "target": target,
        "status": "ready" if payload.get("passed") else "partial",
        "item": {
            "id": item.get("id") or kol_pool_id,
            "platform": item.get("platform"),
            "handle": item.get("handle"),
            "display_name": item.get("display_name"),
            "profile_url": item.get("profile_url"),
        },
        "decision_support": as_dict(payload.get("decision_support")),
        "summary_count": as_int(payload.get("summary_count")),
        "evidence_ref_count": len(refs),
        "sections": sections,
        "missing_sections": _missing_sections(sections),
        "claims": claims,
        "evidence_refs": refs,
        "provider_calls": bool(payload.get("provider_calls")),
        "llm_calls": bool(payload.get("llm_calls")),
        "write_db": bool(payload.get("write_db")),
        "policy": as_dict(payload.get("policy")),
    }


def build_evidence_agent_report(
    *,
    chains: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    weekly: dict[str, Any],
    explicit_ids: list[int],
    ops_dir: str,
    limit: int,
    ref_limit: int,
    claim_limit: int,
    include_product_fit: bool,
    p6_77_pattern: str,
) -> dict[str, Any]:
    error_count = sum(1 for item in chains if item.get("status") == "error")
    side_effect_violations = [
        item.get("kol_pool_id")
        for item in chains
        if item.get("provider_calls") or item.get("llm_calls") or item.get("write_db")
    ]
    missing_counter: Counter[str] = Counter()
    for chain in chains:
        for item in as_list(chain.get("missing_sections")):
            if isinstance(item, dict):
                missing_counter[text(item.get("section"), 120)] += 1
    if explicit_ids:
        agent_status = "ready" if chains and not error_count else "partial" if chains else "no_targets"
        target_source_loaded = True
        target_source = "explicit_kol_pool_ids"
    else:
        target_source_loaded = bool(weekly.get("loaded"))
        target_source = "p6_77_weekly_action_plan"
        if not target_source_loaded:
            agent_status = "source_missing"
        elif not chains:
            agent_status = "no_targets"
        elif error_count:
            agent_status = "partial"
        else:
            agent_status = "ready"
    checks = {
        "agent_version_set": bool(EVIDENCE_AGENT_VERSION),
        "target_source_loaded": target_source_loaded,
        "honest_no_targets_handled": bool(chains) or agent_status in {"no_targets", "source_missing"},
        "chains_traceable_or_empty": all(item.get("status") == "error" or item.get("evidence_ref_count", 0) >= 0 for item in chains),
        "extractive_claims_only": all(
            not claim.get("new_fact_generated")
            for chain in chains
            for claim in as_list(chain.get("claims"))
            if isinstance(claim, dict)
        ),
        "side_effects_blocked": not side_effect_violations,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "tasks_blocked": True,
    }
    return {
        "mode": "p7_81_evidence_agent_v0",
        "generated_at": _now(),
        "evidence_agent_version": EVIDENCE_AGENT_VERSION,
        "agent_type": "read_only_evidence_organizer",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "ops_dir": ops_dir,
            "limit": limit,
            "ref_limit": ref_limit,
            "claim_limit": claim_limit,
            "include_product_fit": bool(include_product_fit),
            "target_source": target_source,
            "p6_77_pattern": p6_77_pattern,
            "explicit_kol_pool_ids": explicit_ids,
        },
        "summary": {
            "agent_status": agent_status,
            "target_source": target_source,
            "target_count": len(targets),
            "chain_count": len(chains),
            "error_count": error_count,
            "evidence_ref_count": sum(as_int(item.get("evidence_ref_count")) for item in chains),
            "claim_count": sum(len(as_list(item.get("claims"))) for item in chains),
            "missing_section_counts": dict(missing_counter),
            "side_effect_violations": side_effect_violations,
            "source_scope": "existing_intelligence_card_and_runtime_ops_only",
        },
        "target_source": {
            "weekly_action_plan": {
                "loaded": bool(weekly.get("loaded")),
                "artifact_path": weekly.get("artifact_path", ""),
                "artifact_name": weekly.get("artifact_name", ""),
                "summary": weekly.get("summary", {}),
            },
            "explicit_ids_used": bool(explicit_ids),
        },
        "chains": chains,
        "policy": {
            "read_only": True,
            "organize_existing_evidence_only": True,
            "new_fact_generation": False,
            "no_alert_write": True,
            "no_task_creation": True,
            "no_provider_calls": True,
            "no_llm_calls": True,
            "no_sync_or_refresh": True,
            "human_review_required": True,
        },
        "next_steps": [
            "Use chains to inspect evidence gaps before any Recommendation Agent consumes them.",
            "Do not treat extractive claims as new facts; every claim must keep evidence_refs.",
            "Fill missing sections through normal data-trust workflows, not by agent inference.",
        ],
    }
