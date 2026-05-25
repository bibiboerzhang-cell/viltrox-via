"""Pure Recommendation Agent scoring and report builders."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


RECOMMENDATION_AGENT_VERSION = "recommendation-agent-v0.1"
SEVERITY_SCORE = {"critical": 16.0, "high": 10.0, "medium": 5.0, "watch": 0.0}
COMPETITOR_SCORE = {"avoid": -35.0, "caution": -12.0, "safe": 0.0, "opportunity": 3.0, "unknown": 0.0}
FEEDBACK_SCORE = {
    "shortlist": 8.0,
    "claim": 6.0,
    "create_project": 8.0,
    "positive_signal": 5.0,
    "feedback": 2.0,
    "reject": -20.0,
    "snooze": -8.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(str(value if value is not None else default).replace(",", "").strip())
        return parsed if parsed == parsed else default
    except (TypeError, ValueError):
        return float(default or 0.0)


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


def evidence_report_available(report: dict[str, Any]) -> bool:
    if not report:
        return False
    summary = as_dict(report.get("summary"))
    checks = as_dict(report.get("checks"))
    if summary.get("agent_status") == "source_missing":
        return False
    if checks.get("target_source_loaded") is False:
        return False
    return True


def _evidence_quality(chain: dict[str, Any]) -> dict[str, Any]:
    sections = [item for item in as_list(chain.get("sections")) if isinstance(item, dict)]
    ready_sections = sum(1 for item in sections if text(item.get("status"), 80).lower() == "ready")
    partial_sections = sum(1 for item in sections if text(item.get("status"), 80).lower() in {"partial", "stale"})
    missing_sections = [item for item in as_list(chain.get("missing_sections")) if isinstance(item, dict)]
    ref_count = as_int(chain.get("evidence_ref_count"))
    claim_count = len(as_list(chain.get("claims")))
    score = min(35.0, ref_count * 0.45 + claim_count * 1.5 + ready_sections * 2.5 + partial_sections) - min(25.0, len(missing_sections) * 2.5)
    return {
        "score": round(max(0.0, min(35.0, score)), 3),
        "ready_sections": ready_sections,
        "partial_sections": partial_sections,
        "missing_count": len(missing_sections),
        "missing_sections": missing_sections,
        "evidence_ref_count": ref_count,
        "claim_count": claim_count,
    }


def _suggested_decision(score: float, quality: dict[str, Any], competitor: dict[str, Any], feedback: dict[str, Any]) -> str:
    if competitor.get("risk_tier") == "avoid":
        return "avoid_candidate"
    if quality.get("evidence_ref_count", 0) <= 0:
        return "needs_evidence_review"
    if feedback.get("sentiment") in {"negative_reject", "negative_snooze"}:
        return "caution_candidate"
    if score >= 75:
        return "contact_candidate"
    if score >= 55:
        return "watch_candidate"
    return "review_candidate"


def _confidence(score: float, quality: dict[str, Any]) -> str:
    if score >= 75 and quality.get("evidence_ref_count", 0) >= 8 and quality.get("missing_count", 0) <= 4:
        return "high"
    if score >= 55 and quality.get("evidence_ref_count", 0) >= 3:
        return "medium"
    return "low"


def _target_context(chain: dict[str, Any]) -> dict[str, Any]:
    target = as_dict(chain.get("target"))
    return {
        "source": text(target.get("source"), 120),
        "action_type": text(target.get("action_type"), 120),
        "priority": text(target.get("priority"), 40) or "watch",
        "score": as_float(target.get("score"), 45.0),
        "title": text(target.get("title"), 240),
        "reason": text(target.get("reason"), 300),
        "entity": as_dict(target.get("entity")),
    }


def _trim_refs(chain: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw in as_list(chain.get("evidence_refs"))[: max(1, min(30, int(limit or 12)))]:
        if not isinstance(raw, dict):
            continue
        refs.append(
            {
                "section": text(raw.get("section"), 120),
                "evidence_id": text(raw.get("evidence_id"), 180),
                "source": text(raw.get("source"), 160),
                "source_table": text(raw.get("source_table"), 160),
                "source_id": text(raw.get("source_id"), 180),
                "source_url": text(raw.get("source_url"), 500),
                "title": text(raw.get("title"), 240),
                "confidence": as_float(raw.get("confidence")),
            }
        )
    return refs


def _claim_snippets(chain: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for raw in as_list(chain.get("claims"))[: max(1, min(10, int(limit or 4)))]:
        if not isinstance(raw, dict):
            continue
        snippets.append(
            {
                "section": text(raw.get("section"), 120),
                "claim_text": text(raw.get("claim_text"), 260),
                "evidence_ref_count": as_int(raw.get("evidence_ref_count")),
                "new_fact_generated": bool(raw.get("new_fact_generated")),
            }
        )
    return snippets


def build_recommendation_candidate(
    chain: dict[str, Any],
    *,
    competitor: dict[str, Any],
    feedback: dict[str, Any],
    min_evidence_refs: int,
    ref_limit: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    kol_pool_id = as_int(chain.get("kol_pool_id"))
    item = as_dict(chain.get("item"))
    target = _target_context(chain)
    quality = _evidence_quality(chain)
    refs = _trim_refs(chain, ref_limit)
    required_refs = max(1, min(20, int(min_evidence_refs or 3)))
    if chain.get("status") == "error":
        return None, {
            "kol_pool_id": kol_pool_id,
            "status": "blocked",
            "reason": "evidence_chain_error",
            "error": text(chain.get("error"), 260),
            "target": target,
        }
    if quality["evidence_ref_count"] < required_refs or not refs:
        return None, {
            "kol_pool_id": kol_pool_id,
            "status": "blocked",
            "reason": "insufficient_traceable_evidence",
            "evidence_ref_count": quality["evidence_ref_count"],
            "required_evidence_refs": required_refs,
            "item": item,
            "target": target,
            "missing_sections": quality["missing_sections"],
        }
    base_score = target["score"] if target["score"] > 0 else 45.0
    priority_bonus = SEVERITY_SCORE.get(target["priority"], 0.0)
    competitor_adjustment = COMPETITOR_SCORE.get(text(competitor.get("risk_tier"), 80), 0.0)
    feedback_adjustment = as_float(feedback.get("score_adjustment"))
    score = max(0.0, min(100.0, round(base_score + priority_bonus + quality["score"] + competitor_adjustment + feedback_adjustment, 3)))
    return {
        "kol_pool_id": kol_pool_id,
        "candidate_uid": f"p7-82:{kol_pool_id}:{target['action_type'] or 'candidate'}",
        "status": "candidate",
        "suggested_decision": _suggested_decision(score, quality, competitor, feedback),
        "recommendation_type": "human_review_candidate",
        "score": score,
        "confidence": _confidence(score, quality),
        "item": {
            "id": item.get("id") or kol_pool_id,
            "platform": item.get("platform"),
            "handle": item.get("handle"),
            "display_name": item.get("display_name"),
            "profile_url": item.get("profile_url"),
        },
        "target": target,
        "score_inputs": {
            "target_score": base_score,
            "priority_bonus": priority_bonus,
            "evidence_quality_score": quality["score"],
            "competitor_adjustment": competitor_adjustment,
            "feedback_adjustment": feedback_adjustment,
        },
        "evidence_quality": quality,
        "competitor_context": competitor,
        "feedback_context": feedback,
        "claims": _claim_snippets(chain),
        "evidence_refs": refs,
        "evidence_ref_count": quality["evidence_ref_count"],
        "generated_facts": False,
        "human_confirmation_required": True,
        "recommended_next_step": "Open the evidence chain, verify Product Fit and competitor risk, then choose contact/watch/caution/avoid manually.",
    }, None


def rank_recommendation_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    sorted_candidates = sorted(candidates, key=lambda item: (as_float(item.get("score")), text(item.get("confidence"))), reverse=True)
    ranked_candidates: list[dict[str, Any]] = []
    for rank, candidate in enumerate(sorted_candidates[: max(1, min(50, int(limit or 12)))], start=1):
        ranked = dict(candidate)
        ranked["rank"] = rank
        ranked_candidates.append(ranked)
    return ranked_candidates


def build_recommendation_agent_report(
    *,
    evidence_source: dict[str, Any],
    evidence_report: dict[str, Any],
    chains: list[dict[str, Any]],
    ranked_candidates: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    side_effect_violations: list[str],
    ops_dir: str,
    limit: int,
    min_evidence_refs: int,
    ref_limit: int,
    claim_limit: int,
    use_latest_evidence_artifact: bool,
    explicit_kol_pool_ids: list[int],
) -> dict[str, Any]:
    decision_counts: Counter[str] = Counter(text(item.get("suggested_decision")) for item in ranked_candidates)
    confidence_counts: Counter[str] = Counter(text(item.get("confidence")) for item in ranked_candidates)
    feedback_context_count = sum(1 for item in ranked_candidates if as_dict(item.get("feedback_context")).get("counts"))
    if not evidence_source.get("loaded") or not evidence_report:
        agent_status = "source_missing"
    elif not chains:
        agent_status = "no_targets"
    elif not ranked_candidates and blocked:
        agent_status = "blocked_no_traceable_candidates"
    elif side_effect_violations:
        agent_status = "blocked_side_effect_violation"
    else:
        agent_status = "ready"
    checks = {
        "agent_version_set": bool(RECOMMENDATION_AGENT_VERSION),
        "evidence_source_loaded": bool(evidence_source.get("loaded") and evidence_report),
        "evidence_agent_side_effects_blocked": not side_effect_violations,
        "candidates_traceable_or_blocked": all(
            as_int(item.get("evidence_ref_count")) >= min_evidence_refs and bool(item.get("evidence_refs"))
            for item in ranked_candidates
        ),
        "generated_facts_blocked": all(item.get("generated_facts") is False for item in ranked_candidates),
        "human_confirmation_required": all(item.get("human_confirmation_required") is True for item in ranked_candidates) and True,
        "no_recommendation_rows_written": True,
        "no_outreach_triggered": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "tasks_blocked": True,
    }
    return {
        "mode": "p7_82_recommendation_agent_v0",
        "generated_at": _now(),
        "recommendation_agent_version": RECOMMENDATION_AGENT_VERSION,
        "agent_type": "read_only_recommendation_candidate_planner",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "outreach_triggered": False,
        "recommendation_rows_written": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "ops_dir": ops_dir,
            "limit": limit,
            "min_evidence_refs": min_evidence_refs,
            "ref_limit": ref_limit,
            "claim_limit": claim_limit,
            "use_latest_evidence_artifact": bool(use_latest_evidence_artifact),
            "explicit_kol_pool_ids": explicit_kol_pool_ids,
        },
        "summary": {
            "agent_status": agent_status,
            "evidence_source": evidence_source.get("source"),
            "chain_count": len(chains),
            "candidate_count": len(ranked_candidates),
            "blocked_count": len(blocked),
            "decision_counts": dict(decision_counts),
            "confidence_counts": dict(confidence_counts),
            "feedback_context_count": feedback_context_count,
            "side_effect_violations": side_effect_violations,
            "source_scope": "existing_evidence_agent_chains_feedback_and_competitor_tables_only",
        },
        "evidence_source": {
            "source": evidence_source.get("source"),
            "loaded": bool(evidence_source.get("loaded")),
            "artifact": {
                "artifact_path": as_dict(evidence_source.get("artifact")).get("artifact_path", ""),
                "artifact_name": as_dict(evidence_source.get("artifact")).get("artifact_name", ""),
                "summary": as_dict(evidence_source.get("artifact")).get("summary", {}),
            },
            "report_summary": as_dict(evidence_report.get("summary")),
        },
        "candidates": ranked_candidates,
        "blocked_candidates": blocked,
        "policy": {
            "read_only": True,
            "propose_candidates_only": True,
            "no_recommendation_persistence": True,
            "no_recommendation_action": True,
            "no_project_created": True,
            "no_outreach_triggered": True,
            "no_task_creation": True,
            "no_provider_calls": True,
            "no_llm_calls": True,
            "no_sync_or_refresh": True,
            "human_confirmation_required": True,
        },
        "next_steps": [
            "Review candidates in a human workflow before any contact/watch/caution/avoid decision.",
            "Persist decisions only through the existing decision or recommendation review surfaces.",
            "If a candidate is blocked, fill evidence gaps through normal data-trust workflows.",
        ],
    }
