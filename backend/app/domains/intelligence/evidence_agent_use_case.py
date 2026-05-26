"""P7.81 read-only Evidence Agent v0.

This organizes existing KOL evidence into traceable chains. It does not create
new facts, call providers, call LLMs, write rows, enqueue tasks, or trigger sync.
Targets come from explicit KOL pool IDs or the latest P6.77 weekly action plan
artifact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domains.intelligence.evidence_agent import (
    EVIDENCE_AGENT_VERSION,
    as_dict,
    as_int,
    as_list,
    build_error_chain,
    build_evidence_agent_report,
    build_evidence_chain_from_summary,
    explicit_targets,
    parse_kol_pool_ids,
    weekly_targets,
)
import app.domains.evidence.summary as evidence_summary


DEFAULT_OPS_DIR = "runtime/ops"
P6_77_PATTERN = "*p6-77-weekly-action-plan-v0.json"
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
        logger.debug("Failed to load evidence agent artifact JSON from %s", path, exc_info=True)
        return {}


def _latest_weekly_action_plan(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P6_77_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": as_dict(payload.get("summary")),
        "actions": as_list(payload.get("actions")),
    }


def _build_chain(target: dict[str, Any], *, include_product_fit: bool, ref_limit: int, claim_limit: int) -> dict[str, Any]:
    kol_pool_id = as_int(target.get("kol_pool_id"))
    try:
        payload = evidence_summary.build_kol_pool_evidence_summary(
            kol_pool_id,
            include_product_fit=include_product_fit,
            ref_limit=max(1, min(25, int(ref_limit or 8))),
            include_llm_preflight=False,
        )
    except Exception as exc:
        return build_error_chain(target, exc)
    return build_evidence_chain_from_summary(
        target,
        payload,
        ref_limit=max(1, min(100, int(ref_limit or 24))),
        claim_limit=max(1, min(50, int(claim_limit or 12))),
    )


def build_evidence_agent_v0(
    *,
    kol_pool_ids: str | list[int] | tuple[int, ...] = "",
    ops_dir: str = DEFAULT_OPS_DIR,
    limit: int = 12,
    ref_limit: int = 24,
    claim_limit: int = 12,
    include_product_fit: bool = True,
) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(limit or 12)))
    safe_ref_limit = max(1, min(100, int(ref_limit or 24)))
    safe_claim_limit = max(1, min(50, int(claim_limit or 12)))
    explicit_ids = parse_kol_pool_ids(kol_pool_ids)
    weekly = _latest_weekly_action_plan(ops_dir) if not explicit_ids else {"loaded": False, "actions": [], "summary": {}}
    targets = explicit_targets(explicit_ids, safe_limit) if explicit_ids else weekly_targets(weekly, safe_limit)
    chains = [
        _build_chain(
            target,
            include_product_fit=bool(include_product_fit),
            ref_limit=safe_ref_limit,
            claim_limit=safe_claim_limit,
        )
        for target in targets
    ]
    return build_evidence_agent_report(
        chains=chains,
        targets=targets,
        weekly=weekly,
        explicit_ids=explicit_ids,
        ops_dir=ops_dir,
        limit=safe_limit,
        ref_limit=safe_ref_limit,
        claim_limit=safe_claim_limit,
        include_product_fit=bool(include_product_fit),
        p6_77_pattern=P6_77_PATTERN,
    )


__all__ = [
    "DEFAULT_OPS_DIR",
    "EVIDENCE_AGENT_VERSION",
    "P6_77_PATTERN",
    "build_evidence_agent_v0",
    "evidence_summary",
]
