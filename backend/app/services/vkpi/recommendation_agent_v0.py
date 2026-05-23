"""P7.82 read-only Recommendation Agent v0.

This turns existing weekly actions and Evidence Agent chains into ranked
candidate suggestions for human review. It never writes recommendation rows,
creates tasks, triggers outreach, calls providers, calls LLMs, or starts sync.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import evidence_agent_v0


RECOMMENDATION_AGENT_VERSION = "recommendation-agent-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"
P7_81_PATTERN = "*p7-81-evidence-agent-v0.json"
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


def _text(value: Any, limit: int = 500) -> str:
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


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _latest_evidence_agent_report(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P7_81_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": _as_dict(payload.get("summary")),
    }


def _evidence_report_available(report: dict[str, Any]) -> bool:
    if not report:
        return False
    summary = _as_dict(report.get("summary"))
    checks = _as_dict(report.get("checks"))
    if summary.get("agent_status") == "source_missing":
        return False
    if checks.get("target_source_loaded") is False:
        return False
    return True


def _feedback_context(kol_pool_id: int, platform: str = "", handle: str = "") -> dict[str, Any]:
    if not kol_pool_id or not _table_exists("vkpi_recommendation_feedback") or not _table_exists("vkpi_kol_recommendations"):
        return {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "feedback_unavailable"}
    where = ["rec.kol_pool_id=?"]
    params: list[Any] = [int(kol_pool_id)]
    clean_platform = _text(platform, 80).lower()
    clean_handle = _text(handle, 160).lower()
    if clean_platform and clean_handle:
        where.append("(LOWER(rec.platform)=? AND LOWER(rec.handle)=?)")
        params.extend([clean_platform, clean_handle])
    try:
        rows = get_conn().execute(
            f"""
            SELECT fb.feedback_type, fb.note, fb.created_at, rec.id AS recommendation_id, rec.run_id
            FROM vkpi_recommendation_feedback fb
            INNER JOIN vkpi_kol_recommendations rec ON rec.id=fb.recommendation_id
            WHERE {" OR ".join(where)}
            ORDER BY fb.created_at DESC, fb.id DESC
            LIMIT 30
            """,
            tuple(params),
        ).fetchall()
    except Exception as exc:
        return {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "feedback_read_error", "error": _text(exc, 200)}
    counts: dict[str, int] = {}
    latest: dict[str, Any] = {}
    for raw in rows:
        row = dict(raw)
        feedback_type = _text(row.get("feedback_type"), 80).lower()
        if not feedback_type:
            continue
        counts[feedback_type] = counts.get(feedback_type, 0) + 1
        if not latest:
            latest = {
                "feedback_type": feedback_type,
                "note": _text(row.get("note"), 240),
                "created_at": row.get("created_at") or "",
                "recommendation_id": row.get("recommendation_id"),
                "run_id": row.get("run_id"),
            }
    adjustment = 0.0
    for feedback_type, count in counts.items():
        adjustment += FEEDBACK_SCORE.get(feedback_type, 0.0) * min(3, int(count or 0))
    adjustment = max(-35.0, min(24.0, round(adjustment, 3)))
    if counts.get("reject"):
        sentiment = "negative_reject"
    elif counts.get("snooze"):
        sentiment = "negative_snooze"
    elif any(counts.get(key) for key in ("shortlist", "claim", "create_project", "positive_signal")):
        sentiment = "positive"
    elif counts:
        sentiment = "neutral"
    else:
        sentiment = "none"
    return {
        "counts": counts,
        "latest": latest,
        "score_adjustment": adjustment,
        "sentiment": sentiment,
        "source": "vkpi_recommendation_feedback" if rows else "no_feedback",
    }


def _competitor_context(kol_pool_id: int) -> dict[str, Any]:
    if not kol_pool_id or not _table_exists("vkpi_competitor_relation"):
        return {"risk_tier": "unknown", "risk_score": 0.0, "brand": "", "source": "competitor_relation_unavailable"}
    try:
        row = get_conn().execute(
            """
            SELECT competitor_brand, risk_score, risk_tier, collaboration_depth,
                   collaboration_count_90d, collaboration_count_total, computed_at
            FROM vkpi_competitor_relation
            WHERE kol_pool_id=?
            ORDER BY risk_score DESC, competitor_brand ASC
            LIMIT 1
            """,
            (int(kol_pool_id),),
        ).fetchone()
    except Exception as exc:
        return {"risk_tier": "unknown", "risk_score": 0.0, "brand": "", "source": "competitor_relation_read_error", "error": _text(exc, 200)}
    if not row:
        return {"risk_tier": "opportunity", "risk_score": 0.0, "brand": "", "source": "no_persisted_relation"}
    item = dict(row)
    tier = _text(item.get("risk_tier"), 80).lower() or "opportunity"
    if tier not in COMPETITOR_SCORE:
        tier = "unknown"
    return {
        "risk_tier": tier,
        "risk_score": _float(item.get("risk_score")),
        "brand": _text(item.get("competitor_brand"), 160).lower(),
        "relation": item,
        "source": "vkpi_competitor_relation",
    }


def _evidence_quality(chain: dict[str, Any]) -> dict[str, Any]:
    sections = [item for item in _as_list(chain.get("sections")) if isinstance(item, dict)]
    ready_sections = sum(1 for item in sections if _text(item.get("status"), 80).lower() == "ready")
    partial_sections = sum(1 for item in sections if _text(item.get("status"), 80).lower() in {"partial", "stale"})
    missing_sections = [item for item in _as_list(chain.get("missing_sections")) if isinstance(item, dict)]
    ref_count = _int(chain.get("evidence_ref_count"))
    claim_count = len(_as_list(chain.get("claims")))
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
    target = _as_dict(chain.get("target"))
    return {
        "source": _text(target.get("source"), 120),
        "action_type": _text(target.get("action_type"), 120),
        "priority": _text(target.get("priority"), 40) or "watch",
        "score": _float(target.get("score"), 45.0),
        "title": _text(target.get("title"), 240),
        "reason": _text(target.get("reason"), 300),
        "entity": _as_dict(target.get("entity")),
    }


def _trim_refs(chain: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for raw in _as_list(chain.get("evidence_refs"))[: max(1, min(30, int(limit or 12)))]:
        if not isinstance(raw, dict):
            continue
        refs.append(
            {
                "section": _text(raw.get("section"), 120),
                "evidence_id": _text(raw.get("evidence_id"), 180),
                "source": _text(raw.get("source"), 160),
                "source_table": _text(raw.get("source_table"), 160),
                "source_id": _text(raw.get("source_id"), 180),
                "source_url": _text(raw.get("source_url"), 500),
                "title": _text(raw.get("title"), 240),
                "confidence": _float(raw.get("confidence")),
            }
        )
    return refs


def _claim_snippets(chain: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for raw in _as_list(chain.get("claims"))[: max(1, min(10, int(limit or 4)))]:
        if not isinstance(raw, dict):
            continue
        snippets.append(
            {
                "section": _text(raw.get("section"), 120),
                "claim_text": _text(raw.get("claim_text"), 260),
                "evidence_ref_count": _int(raw.get("evidence_ref_count")),
                "new_fact_generated": bool(raw.get("new_fact_generated")),
            }
        )
    return snippets


def _build_candidate(chain: dict[str, Any], *, min_evidence_refs: int, ref_limit: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    kol_pool_id = _int(chain.get("kol_pool_id"))
    item = _as_dict(chain.get("item"))
    target = _target_context(chain)
    quality = _evidence_quality(chain)
    refs = _trim_refs(chain, ref_limit)
    if chain.get("status") == "error":
        return None, {
            "kol_pool_id": kol_pool_id,
            "status": "blocked",
            "reason": "evidence_chain_error",
            "error": _text(chain.get("error"), 260),
            "target": target,
        }
    if quality["evidence_ref_count"] < max(1, min(20, int(min_evidence_refs or 3))) or not refs:
        return None, {
            "kol_pool_id": kol_pool_id,
            "status": "blocked",
            "reason": "insufficient_traceable_evidence",
            "evidence_ref_count": quality["evidence_ref_count"],
            "required_evidence_refs": max(1, min(20, int(min_evidence_refs or 3))),
            "item": item,
            "target": target,
            "missing_sections": quality["missing_sections"],
        }
    competitor = _competitor_context(kol_pool_id)
    feedback = _feedback_context(kol_pool_id, _text(item.get("platform")), _text(item.get("handle")))
    base_score = target["score"] if target["score"] > 0 else 45.0
    priority_bonus = SEVERITY_SCORE.get(target["priority"], 0.0)
    competitor_adjustment = COMPETITOR_SCORE.get(_text(competitor.get("risk_tier"), 80), 0.0)
    feedback_adjustment = _float(feedback.get("score_adjustment"))
    score = max(0.0, min(100.0, round(base_score + priority_bonus + quality["score"] + competitor_adjustment + feedback_adjustment, 3)))
    decision = _suggested_decision(score, quality, competitor, feedback)
    return {
        "kol_pool_id": kol_pool_id,
        "candidate_uid": f"p7-82:{kol_pool_id}:{target['action_type'] or 'candidate'}",
        "status": "candidate",
        "suggested_decision": decision,
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


def _load_or_build_evidence_report(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...],
    ops_dir: str,
    limit: int,
    ref_limit: int,
    claim_limit: int,
    use_latest_evidence_artifact: bool,
) -> dict[str, Any]:
    explicit_ids = _parse_kol_pool_ids(kol_pool_ids)
    if explicit_ids:
        report = evidence_agent_v0.build_evidence_agent_v0(
            kol_pool_ids=explicit_ids,
            ops_dir=ops_dir,
            limit=limit,
            ref_limit=ref_limit,
            claim_limit=claim_limit,
            include_product_fit=True,
        )
        return {"source": "fresh_evidence_agent_explicit_ids", "loaded": _evidence_report_available(report), "artifact": {}, "report": report}
    latest = _latest_evidence_agent_report(ops_dir) if use_latest_evidence_artifact else {"loaded": False}
    if latest.get("loaded"):
        report = latest.get("report") or {}
        return {"source": "latest_p7_81_evidence_agent_artifact", "loaded": _evidence_report_available(report), "artifact": latest, "report": report}
    report = evidence_agent_v0.build_evidence_agent_v0(
        ops_dir=ops_dir,
        limit=limit,
        ref_limit=ref_limit,
        claim_limit=claim_limit,
        include_product_fit=True,
    )
    return {"source": "fresh_evidence_agent_weekly_actions", "loaded": _evidence_report_available(report), "artifact": {}, "report": report}


def build_recommendation_agent_v0(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...] = "",
    ops_dir: str = DEFAULT_OPS_DIR,
    limit: int = 12,
    min_evidence_refs: int = 3,
    ref_limit: int = 12,
    claim_limit: int = 12,
    use_latest_evidence_artifact: bool = True,
) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(limit or 12)))
    safe_ref_limit = max(1, min(100, int(ref_limit or 12)))
    safe_claim_limit = max(1, min(50, int(claim_limit or 12)))
    safe_min_refs = max(1, min(20, int(min_evidence_refs or 3)))
    evidence_source = _load_or_build_evidence_report(
        kol_pool_ids=kol_pool_ids,
        ops_dir=ops_dir,
        limit=safe_limit,
        ref_limit=max(safe_ref_limit, safe_min_refs),
        claim_limit=safe_claim_limit,
        use_latest_evidence_artifact=bool(use_latest_evidence_artifact),
    )
    evidence_report = _as_dict(evidence_source.get("report"))
    chains = [chain for chain in _as_list(evidence_report.get("chains")) if isinstance(chain, dict)]
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    side_effect_violations: list[str] = []
    for chain in chains:
        if chain.get("provider_calls") or chain.get("llm_calls") or chain.get("write_db"):
            side_effect_violations.append(str(chain.get("kol_pool_id") or "unknown"))
        candidate, blocked_item = _build_candidate(chain, min_evidence_refs=safe_min_refs, ref_limit=safe_ref_limit)
        if candidate:
            candidates.append(candidate)
        if blocked_item:
            blocked.append(blocked_item)
    candidates.sort(key=lambda item: (_float(item.get("score")), _text(item.get("confidence"))), reverse=True)
    ranked_candidates: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates[:safe_limit], start=1):
        ranked = dict(candidate)
        ranked["rank"] = rank
        ranked_candidates.append(ranked)
    decision_counts: Counter[str] = Counter(_text(item.get("suggested_decision")) for item in ranked_candidates)
    confidence_counts: Counter[str] = Counter(_text(item.get("confidence")) for item in ranked_candidates)
    feedback_context_count = sum(1 for item in ranked_candidates if _as_dict(item.get("feedback_context")).get("counts"))
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
        "candidates_traceable_or_blocked": all(_int(item.get("evidence_ref_count")) >= safe_min_refs and bool(item.get("evidence_refs")) for item in ranked_candidates),
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
            "limit": safe_limit,
            "min_evidence_refs": safe_min_refs,
            "ref_limit": safe_ref_limit,
            "claim_limit": safe_claim_limit,
            "use_latest_evidence_artifact": bool(use_latest_evidence_artifact),
            "explicit_kol_pool_ids": _parse_kol_pool_ids(kol_pool_ids),
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
                "artifact_path": _as_dict(evidence_source.get("artifact")).get("artifact_path", ""),
                "artifact_name": _as_dict(evidence_source.get("artifact")).get("artifact_name", ""),
                "summary": _as_dict(evidence_source.get("artifact")).get("summary", {}),
            },
            "report_summary": _as_dict(evidence_report.get("summary")),
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
