"""P7.83 read-only Brief Agent v0.

This assembles an operator brief from Recommendation Agent candidates. It only
summarizes existing evidence-linked candidates and never writes notifications,
tasks, outreach, recommendation rows, sync runs, provider calls, or LLM output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.intelligence.brief_agent import (
    BRIEF_AGENT_VERSION,
    as_dict,
    build_brief_agent_report,
    parse_kol_pool_ids,
    recommendation_report_available,
)
from app.domains.intelligence import recommendation_use_case as recommendation_agent_v0


DEFAULT_OPS_DIR = "runtime/ops"
P7_82_PATTERN = "*p7-82-recommendation-agent-v0.json"
logger = get_logger(__name__)


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
        logger.debug("Failed to load brief agent artifact JSON from %s", path, exc_info=True)
        return {}


def _latest_recommendation_agent_report(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P7_82_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": as_dict(payload.get("summary")),
    }


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
    explicit_ids = parse_kol_pool_ids(kol_pool_ids)
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
        return {"source": "fresh_recommendation_agent_explicit_ids", "loaded": recommendation_report_available(report), "artifact": {}, "report": report}
    latest = _latest_recommendation_agent_report(ops_dir) if use_latest_recommendation_artifact else {"loaded": False}
    if latest.get("loaded"):
        report = latest.get("report") or {}
        return {"source": "latest_p7_82_recommendation_agent_artifact", "loaded": recommendation_report_available(report), "artifact": latest, "report": report}
    report = recommendation_agent_v0.build_recommendation_agent_v0(
        ops_dir=ops_dir,
        limit=limit,
        min_evidence_refs=min_evidence_refs,
        ref_limit=ref_limit,
        claim_limit=claim_limit,
        use_latest_evidence_artifact=True,
    )
    return {"source": "fresh_recommendation_agent", "loaded": recommendation_report_available(report), "artifact": {}, "report": report}


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
    return build_brief_agent_report(
        rec_source=rec_source,
        rec_report=as_dict(rec_source.get("report")),
        ops_dir=ops_dir,
        limit=safe_limit,
        min_evidence_refs=safe_min_refs,
        ref_limit=safe_ref_limit,
        claim_limit=safe_claim_limit,
        use_latest_recommendation_artifact=bool(use_latest_recommendation_artifact),
        explicit_kol_pool_ids=parse_kol_pool_ids(kol_pool_ids),
    )


__all__ = [
    "BRIEF_AGENT_VERSION",
    "DEFAULT_OPS_DIR",
    "P7_82_PATTERN",
    "build_brief_agent_v0",
    "recommendation_agent_v0",
]
