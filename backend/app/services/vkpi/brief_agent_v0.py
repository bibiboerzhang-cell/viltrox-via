"""P7.83 read-only Brief Agent v0.

This assembles an operator brief from Recommendation Agent candidates. It only
summarizes existing evidence-linked candidates and never writes notifications,
tasks, outreach, recommendation rows, sync runs, provider calls, or LLM output.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.vkpi import recommendation_agent_v0


BRIEF_AGENT_VERSION = "brief-agent-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"
P7_82_PATTERN = "*p7-82-recommendation-agent-v0.json"
DECISION_LABELS = {
    "contact_candidate": "Contact review candidate",
    "watch_candidate": "Watch candidate",
    "caution_candidate": "Caution candidate",
    "avoid_candidate": "Avoid candidate",
    "review_candidate": "Review candidate",
    "needs_evidence_review": "Needs evidence review",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return int(default or 0)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(str(value if value is not None else default).replace(",", "").strip())
        return parsed if parsed == parsed else default
    except (TypeError, ValueError):
        return float(default or 0.0)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _hash_key(*parts: Any) -> str:
    joined = "|".join(_text(part, 180) for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _latest_artifact(ops_dir: str, pattern: str) -> Path | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_recommendation_agent_report(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P7_82_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": _as_dict(payload.get("summary")),
    }


def _recommendation_report_available(report: dict[str, Any]) -> bool:
    if not report:
        return False
    summary = _as_dict(report.get("summary"))
    checks = _as_dict(report.get("checks"))
    if summary.get("agent_status") == "source_missing":
        return False
    if checks.get("evidence_source_loaded") is False:
        return False
    return True


def _parse_kol_pool_ids(value: Any) -> list[int]:
    if isinstance(value, str):
        raw_values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = []
    ids: list[int] = []
    for raw in raw_values:
        kol_id = _int(raw)
        if kol_id > 0 and kol_id not in ids:
            ids.append(kol_id)
    return ids


def _clean_refs(refs: Any, *, limit: int) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _as_list(refs):
        if not isinstance(raw, dict):
            continue
        evidence_id = _text(raw.get("evidence_id") or _hash_key(raw.get("section"), raw.get("source_table"), raw.get("source_id")), 180)
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        cleaned.append(
            {
                "section": _text(raw.get("section"), 120),
                "evidence_id": evidence_id,
                "source": _text(raw.get("source"), 160),
                "source_table": _text(raw.get("source_table"), 160),
                "source_id": _text(raw.get("source_id"), 180),
                "source_url": _text(raw.get("source_url"), 500),
                "title": _text(raw.get("title"), 240),
                "confidence": _float(raw.get("confidence")),
            }
        )
        if len(cleaned) >= max(1, min(30, int(limit or 8))):
            break
    return cleaned


def _identity(candidate: dict[str, Any]) -> dict[str, Any]:
    item = _as_dict(candidate.get("item"))
    return {
        "kol_pool_id": _int(candidate.get("kol_pool_id") or item.get("id")),
        "platform": _text(item.get("platform"), 80),
        "handle": _text(item.get("handle"), 160),
        "display_name": _text(item.get("display_name"), 180),
        "profile_url": _text(item.get("profile_url"), 500),
    }


def _candidate_title(candidate: dict[str, Any]) -> str:
    ident = _identity(candidate)
    handle = ident.get("handle") or ident.get("display_name") or str(ident.get("kol_pool_id"))
    decision = _text(candidate.get("suggested_decision"), 80)
    label = DECISION_LABELS.get(decision, decision or "Candidate")
    score = _float(candidate.get("score"))
    return f"{label}: @{handle} score={score:.1f}"


def _candidate_body(candidate: dict[str, Any]) -> str:
    target = _as_dict(candidate.get("target"))
    quality = _as_dict(candidate.get("evidence_quality"))
    competitor = _as_dict(candidate.get("competitor_context"))
    return (
        f"{_text(target.get('reason'), 220) or _text(target.get('title'), 220)}; "
        f"confidence={_text(candidate.get('confidence'), 40)}; "
        f"refs={_int(candidate.get('evidence_ref_count'))}; "
        f"missing_sections={_int(quality.get('missing_count'))}; "
        f"competitor_risk={_text(competitor.get('risk_tier'), 40) or 'unknown'}."
    )


def _brief_item(candidate: dict[str, Any], *, max_refs: int) -> dict[str, Any] | None:
    refs = _clean_refs(candidate.get("evidence_refs"), limit=max_refs)
    if not refs:
        return None
    ident = _identity(candidate)
    decision = _text(candidate.get("suggested_decision"), 80)
    return {
        "brief_uid": f"p7-83:candidate:{ident['kol_pool_id']}:{_hash_key(decision, candidate.get('score'))}",
        "brief_type": "candidate_summary",
        "priority": "high" if decision == "contact_candidate" else "medium",
        "title": _candidate_title(candidate),
        "body": _candidate_body(candidate),
        "identity": ident,
        "suggested_decision": decision,
        "score": _float(candidate.get("score")),
        "confidence": _text(candidate.get("confidence"), 40),
        "evidence_refs": refs,
        "source_candidate_uid": _text(candidate.get("candidate_uid"), 180),
        "generation": {
            "method": "deterministic_brief_agent_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "existing_recommendation_candidate_evidence_only",
        },
    }


def _gap_item(candidate: dict[str, Any], *, max_refs: int) -> dict[str, Any] | None:
    quality = _as_dict(candidate.get("evidence_quality"))
    missing = [item for item in _as_list(quality.get("missing_sections")) if isinstance(item, dict)]
    if not missing:
        return None
    refs = _clean_refs(candidate.get("evidence_refs"), limit=max_refs)
    if not refs:
        return None
    ident = _identity(candidate)
    sections = [_text(item.get("section"), 80) for item in missing if _text(item.get("section"), 80)]
    return {
        "brief_uid": f"p7-83:gap:{ident['kol_pool_id']}:{_hash_key(sections)}",
        "brief_type": "evidence_gap",
        "priority": "medium",
        "title": f"Evidence gaps for @{ident.get('handle') or ident.get('kol_pool_id')}",
        "body": f"Missing or empty sections: {', '.join(sections[:6])}. Fill these through normal data-trust workflows before escalating automation.",
        "identity": ident,
        "missing_sections": missing,
        "evidence_refs": refs,
        "source_candidate_uid": _text(candidate.get("candidate_uid"), 180),
        "generation": {
            "method": "deterministic_brief_agent_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "coverage_gap_requires_existing_refs",
        },
    }


def _risk_item(candidate: dict[str, Any], *, max_refs: int) -> dict[str, Any] | None:
    competitor = _as_dict(candidate.get("competitor_context"))
    tier = _text(competitor.get("risk_tier"), 80)
    if tier not in {"avoid", "caution"}:
        return None
    refs = _clean_refs(candidate.get("evidence_refs"), limit=max_refs)
    if not refs:
        return None
    ident = _identity(candidate)
    brand = _text(competitor.get("brand"), 120) or "competitor"
    return {
        "brief_uid": f"p7-83:risk:{ident['kol_pool_id']}:{_hash_key(tier, brand)}",
        "brief_type": "risk_note",
        "priority": "high" if tier == "avoid" else "medium",
        "title": f"Competitor risk for @{ident.get('handle') or ident.get('kol_pool_id')}: {tier}",
        "body": f"Existing competitor relation marks {brand} as {tier}; human review should confirm before contact.",
        "identity": ident,
        "competitor_context": competitor,
        "evidence_refs": refs,
        "source_candidate_uid": _text(candidate.get("candidate_uid"), 180),
        "generation": {
            "method": "deterministic_brief_agent_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "risk_note_requires_existing_refs",
        },
    }


def _next_action(candidate: dict[str, Any], *, max_refs: int) -> dict[str, Any] | None:
    refs = _clean_refs(candidate.get("evidence_refs"), limit=max_refs)
    if not refs:
        return None
    ident = _identity(candidate)
    decision = _text(candidate.get("suggested_decision"), 80)
    if decision == "contact_candidate":
        text = "Human reviewer: verify Product Fit, competitor risk, and missing sections before choosing contact."
        priority = "high"
    elif decision == "watch_candidate":
        text = "Human reviewer: keep this KOL on watch until stronger evidence appears."
        priority = "medium"
    elif decision in {"caution_candidate", "avoid_candidate"}:
        text = "Human reviewer: inspect risk evidence before any outreach decision."
        priority = "high"
    else:
        text = "Human reviewer: inspect the evidence chain before assigning a decision."
        priority = "medium"
    return {
        "action_uid": f"p7-83:action:{ident['kol_pool_id']}:{_hash_key(decision, text)}",
        "action_type": "human_review",
        "priority": priority,
        "text": text,
        "identity": ident,
        "suggested_decision": decision,
        "evidence_refs": refs,
        "source_candidate_uid": _text(candidate.get("candidate_uid"), 180),
        "generation": {
            "method": "deterministic_brief_agent_v0",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": "human_action_requires_evidence_refs",
        },
    }


def _backlinks(items: list[dict[str, Any]], actions: list[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [*items, *actions]:
        for ref in _clean_refs(source.get("evidence_refs"), limit=10):
            key = _text(ref.get("evidence_id"), 180)
            if not key or key in seen:
                continue
            seen.add(key)
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _all_traceable(rows: list[dict[str, Any]]) -> bool:
    return all(bool(_clean_refs(row.get("evidence_refs"), limit=1)) for row in rows)


def _load_or_build_recommendation_report(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...],
    ops_dir: str,
    limit: int,
    min_evidence_refs: int,
    ref_limit: int,
    claim_limit: int,
    use_latest_recommendation_artifact: bool,
) -> dict[str, Any]:
    explicit_ids = _parse_kol_pool_ids(kol_pool_ids)
    if explicit_ids:
        report = recommendation_agent_v0.build_recommendation_agent_v0(
            kol_pool_ids=explicit_ids,
            ops_dir=ops_dir,
            limit=limit,
            min_evidence_refs=min_evidence_refs,
            ref_limit=ref_limit,
            claim_limit=claim_limit,
            use_latest_evidence_artifact=True,
        )
        return {"source": "fresh_recommendation_agent_explicit_ids", "loaded": _recommendation_report_available(report), "artifact": {}, "report": report}
    latest = _latest_recommendation_agent_report(ops_dir) if use_latest_recommendation_artifact else {"loaded": False}
    if latest.get("loaded"):
        report = latest.get("report") or {}
        return {"source": "latest_p7_82_recommendation_agent_artifact", "loaded": _recommendation_report_available(report), "artifact": latest, "report": report}
    report = recommendation_agent_v0.build_recommendation_agent_v0(
        ops_dir=ops_dir,
        limit=limit,
        min_evidence_refs=min_evidence_refs,
        ref_limit=ref_limit,
        claim_limit=claim_limit,
        use_latest_evidence_artifact=True,
    )
    return {"source": "fresh_recommendation_agent", "loaded": _recommendation_report_available(report), "artifact": {}, "report": report}


def build_brief_agent_v0(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...] = "",
    ops_dir: str = DEFAULT_OPS_DIR,
    limit: int = 8,
    min_evidence_refs: int = 3,
    ref_limit: int = 8,
    claim_limit: int = 12,
    use_latest_recommendation_artifact: bool = True,
) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(limit or 8)))
    safe_min_refs = max(1, min(20, int(min_evidence_refs or 3)))
    safe_ref_limit = max(1, min(50, int(ref_limit or 8)))
    safe_claim_limit = max(1, min(50, int(claim_limit or 12)))
    rec_source = _load_or_build_recommendation_report(
        kol_pool_ids=kol_pool_ids,
        ops_dir=ops_dir,
        limit=max(safe_limit, 12),
        min_evidence_refs=safe_min_refs,
        ref_limit=max(safe_ref_limit, safe_min_refs),
        claim_limit=safe_claim_limit,
        use_latest_recommendation_artifact=bool(use_latest_recommendation_artifact),
    )
    rec_report = _as_dict(rec_source.get("report"))
    candidates = [item for item in _as_list(rec_report.get("candidates")) if isinstance(item, dict)]
    blocked = [item for item in _as_list(rec_report.get("blocked_candidates")) if isinstance(item, dict)]
    side_effect_violations: list[str] = []
    if rec_report.get("provider_calls") or rec_report.get("llm_calls") or rec_report.get("write_db") or rec_report.get("sync_triggered") or rec_report.get("task_enqueued"):
        side_effect_violations.append("recommendation_agent_report")
    selected = candidates[:safe_limit]
    items: list[dict[str, Any]] = []
    dropped_untraceable = 0
    for candidate in selected:
        item = _brief_item(candidate, max_refs=safe_ref_limit)
        if item is None:
            dropped_untraceable += 1
        else:
            items.append(item)
        gap = _gap_item(candidate, max_refs=safe_ref_limit)
        if gap:
            items.append(gap)
        risk = _risk_item(candidate, max_refs=safe_ref_limit)
        if risk:
            items.append(risk)
    actions = [item for item in (_next_action(candidate, max_refs=safe_ref_limit) for candidate in selected) if isinstance(item, dict)]
    backlinks = _backlinks(items, actions)
    decision_counts: Counter[str] = Counter(_text(item.get("suggested_decision"), 80) for item in selected)
    item_type_counts: Counter[str] = Counter(_text(item.get("brief_type"), 80) for item in items)
    if not rec_source.get("loaded") or not rec_report:
        agent_status = "source_missing"
    elif not candidates and not blocked:
        agent_status = "no_targets"
    elif not candidates and blocked:
        agent_status = "blocked_no_candidates"
    elif candidates and not items and dropped_untraceable:
        agent_status = "blocked_no_traceable_brief"
    elif side_effect_violations:
        agent_status = "blocked_side_effect_violation"
    else:
        agent_status = "ready"
    checks = {
        "agent_version_set": bool(BRIEF_AGENT_VERSION),
        "recommendation_source_loaded": bool(rec_source.get("loaded") and rec_report),
        "recommendation_side_effects_blocked": not side_effect_violations,
        "brief_items_traceable_or_empty": _all_traceable(items) if items else agent_status in {"no_targets", "blocked_no_candidates", "blocked_no_traceable_brief"},
        "next_actions_traceable_or_empty": _all_traceable(actions) if actions else agent_status in {"no_targets", "blocked_no_candidates", "blocked_no_traceable_brief"},
        "generated_facts_blocked": all(_as_dict(item.get("generation")).get("method") == "deterministic_brief_agent_v0" for item in items + actions),
        "no_notifications_written": True,
        "no_tasks_created": True,
        "no_outreach_triggered": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    headline = (
        f"Brief Agent v0 prepared {len(selected)} candidate briefs from {len(candidates)} recommendation candidates."
        if candidates
        else "Brief Agent v0 found no recommendation candidates in the current source."
    )
    return {
        "mode": "p7_83_brief_agent_v0",
        "generated_at": _now(),
        "brief_agent_version": BRIEF_AGENT_VERSION,
        "agent_type": "read_only_operator_brief",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "notification_written": False,
        "outreach_triggered": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "ops_dir": ops_dir,
            "limit": safe_limit,
            "min_evidence_refs": safe_min_refs,
            "ref_limit": safe_ref_limit,
            "claim_limit": safe_claim_limit,
            "use_latest_recommendation_artifact": bool(use_latest_recommendation_artifact),
            "explicit_kol_pool_ids": _parse_kol_pool_ids(kol_pool_ids),
        },
        "summary": {
            "agent_status": agent_status,
            "headline": headline,
            "recommendation_source": rec_source.get("source"),
            "candidate_count": len(candidates),
            "selected_candidate_count": len(selected),
            "blocked_candidate_count": len(blocked),
            "brief_item_count": len(items),
            "next_action_count": len(actions),
            "evidence_backlink_count": len(backlinks),
            "dropped_untraceable_count": dropped_untraceable,
            "decision_counts": dict(decision_counts),
            "brief_item_type_counts": dict(item_type_counts),
            "side_effect_violations": side_effect_violations,
            "source_scope": "existing_recommendation_agent_candidates_only",
        },
        "recommendation_source": {
            "source": rec_source.get("source"),
            "loaded": bool(rec_source.get("loaded")),
            "artifact": {
                "artifact_path": _as_dict(rec_source.get("artifact")).get("artifact_path", ""),
                "artifact_name": _as_dict(rec_source.get("artifact")).get("artifact_name", ""),
                "summary": _as_dict(rec_source.get("artifact")).get("summary", {}),
            },
            "report_summary": _as_dict(rec_report.get("summary")),
        },
        "headline": headline,
        "brief_items": items,
        "next_actions": actions,
        "evidence_backlinks": backlinks,
        "blocked_candidates": blocked,
        "policy": {
            "read_only": True,
            "summarize_existing_candidates_only": True,
            "new_fact_generation": False,
            "no_notification_write": True,
            "no_task_creation": True,
            "no_outreach": True,
            "no_provider_calls": True,
            "no_llm_calls": True,
            "no_sync_or_refresh": True,
            "human_review_required": True,
            "every_brief_item_requires_evidence_refs": True,
            "every_next_action_requires_evidence_refs": True,
        },
        "next_steps": [
            "Use the brief as a read-only operator checklist.",
            "Open evidence refs before taking contact, watch, caution, or avoid decisions.",
            "Persist decisions only through existing human review surfaces.",
        ],
    }
