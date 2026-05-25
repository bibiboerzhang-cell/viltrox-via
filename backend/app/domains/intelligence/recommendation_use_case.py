"""P7.82 read-only Recommendation Agent v0.

This turns existing weekly actions and Evidence Agent chains into ranked
candidate suggestions for human review. It never writes recommendation rows,
creates tasks, triggers outreach, calls providers, calls LLMs, or starts sync.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains.intelligence.recommendation_agent import (
    COMPETITOR_SCORE,
    FEEDBACK_SCORE,
    RECOMMENDATION_AGENT_VERSION,
    as_dict,
    as_float,
    as_int,
    as_list,
    build_recommendation_agent_report,
    build_recommendation_candidate,
    evidence_report_available,
    parse_kol_pool_ids,
    rank_recommendation_candidates,
    text,
)
from app.domains.intelligence import evidence_agent_use_case as evidence_agent_v0


DEFAULT_OPS_DIR = "runtime/ops"
P7_81_PATTERN = "*p7-81-evidence-agent-v0.json"


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
        "summary": as_dict(payload.get("summary")),
    }


def _feedback_context(kol_pool_id: int, platform: str = "", handle: str = "") -> dict[str, Any]:
    if not kol_pool_id or not _table_exists("vkpi_recommendation_feedback") or not _table_exists("vkpi_kol_recommendations"):
        return {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "feedback_unavailable"}
    where = ["rec.kol_pool_id=?"]
    params: list[Any] = [int(kol_pool_id)]
    clean_platform = text(platform, 80).lower()
    clean_handle = text(handle, 160).lower()
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
        return {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "feedback_read_error", "error": text(exc, 200)}
    counts: dict[str, int] = {}
    latest: dict[str, Any] = {}
    for raw in rows:
        row = dict(raw)
        feedback_type = text(row.get("feedback_type"), 80).lower()
        if not feedback_type:
            continue
        counts[feedback_type] = counts.get(feedback_type, 0) + 1
        if not latest:
            latest = {
                "feedback_type": feedback_type,
                "note": text(row.get("note"), 240),
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
        return {"risk_tier": "unknown", "risk_score": 0.0, "brand": "", "source": "competitor_relation_read_error", "error": text(exc, 200)}
    if not row:
        return {"risk_tier": "opportunity", "risk_score": 0.0, "brand": "", "source": "no_persisted_relation"}
    item = dict(row)
    tier = text(item.get("risk_tier"), 80).lower() or "opportunity"
    if tier not in COMPETITOR_SCORE:
        tier = "unknown"
    return {
        "risk_tier": tier,
        "risk_score": as_float(item.get("risk_score")),
        "brand": text(item.get("competitor_brand"), 160).lower(),
        "relation": item,
        "source": "vkpi_competitor_relation",
    }


def _load_or_build_evidence_report(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...],
    ops_dir: str,
    limit: int,
    ref_limit: int,
    claim_limit: int,
    use_latest_evidence_artifact: bool,
) -> dict[str, Any]:
    explicit_ids = parse_kol_pool_ids(kol_pool_ids)
    if explicit_ids:
        report = evidence_agent_v0.build_evidence_agent_v0(
            kol_pool_ids=explicit_ids,
            ops_dir=ops_dir,
            limit=limit,
            ref_limit=ref_limit,
            claim_limit=claim_limit,
            include_product_fit=True,
        )
        return {"source": "fresh_evidence_agent_explicit_ids", "loaded": evidence_report_available(report), "artifact": {}, "report": report}
    latest = _latest_evidence_agent_report(ops_dir) if use_latest_evidence_artifact else {"loaded": False}
    if latest.get("loaded"):
        report = latest.get("report") or {}
        return {"source": "latest_p7_81_evidence_agent_artifact", "loaded": evidence_report_available(report), "artifact": latest, "report": report}
    report = evidence_agent_v0.build_evidence_agent_v0(
        ops_dir=ops_dir,
        limit=limit,
        ref_limit=ref_limit,
        claim_limit=claim_limit,
        include_product_fit=True,
    )
    return {"source": "fresh_evidence_agent_weekly_actions", "loaded": evidence_report_available(report), "artifact": {}, "report": report}


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
    evidence_report = as_dict(evidence_source.get("report"))
    chains = [chain for chain in as_list(evidence_report.get("chains")) if isinstance(chain, dict)]
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    side_effect_violations: list[str] = []
    for chain in chains:
        if chain.get("provider_calls") or chain.get("llm_calls") or chain.get("write_db"):
            side_effect_violations.append(str(chain.get("kol_pool_id") or "unknown"))
        item = as_dict(chain.get("item"))
        kol_pool_id = as_int(chain.get("kol_pool_id"))
        competitor = _competitor_context(kol_pool_id)
        feedback = _feedback_context(kol_pool_id, text(item.get("platform")), text(item.get("handle")))
        candidate, blocked_item = build_recommendation_candidate(
            chain,
            competitor=competitor,
            feedback=feedback,
            min_evidence_refs=safe_min_refs,
            ref_limit=safe_ref_limit,
        )
        if candidate:
            candidates.append(candidate)
        if blocked_item:
            blocked.append(blocked_item)
    ranked_candidates = rank_recommendation_candidates(candidates, safe_limit)
    return build_recommendation_agent_report(
        evidence_source=evidence_source,
        evidence_report=evidence_report,
        chains=chains,
        ranked_candidates=ranked_candidates,
        blocked=blocked,
        side_effect_violations=side_effect_violations,
        ops_dir=ops_dir,
        limit=safe_limit,
        min_evidence_refs=safe_min_refs,
        ref_limit=safe_ref_limit,
        claim_limit=safe_claim_limit,
        use_latest_evidence_artifact=bool(use_latest_evidence_artifact),
        explicit_kol_pool_ids=parse_kol_pool_ids(kol_pool_ids),
    )


__all__ = [
    "DEFAULT_OPS_DIR",
    "P7_81_PATTERN",
    "RECOMMENDATION_AGENT_VERSION",
    "_competitor_context",
    "_feedback_context",
    "build_recommendation_agent_v0",
    "evidence_agent_v0",
]
