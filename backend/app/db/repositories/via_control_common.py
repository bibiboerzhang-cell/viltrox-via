"""Shared row mapping helpers for Via control-loop ledgers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import is_postgres_runtime

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any, default: Any) -> str:
    data = default if value is None else value
    return json.dumps(data, ensure_ascii=False)

def _nullable_timestamp(value: Any) -> Any:
    text = str(value or "").strip()
    return text or None

def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default

def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        if is_postgres_runtime():
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                (str(table_name or "").strip(),),
            ).fetchall()
            return {str(row["column_name"] or "").strip() for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"] or "").strip() for row in rows}
    except Exception:
        return set()

def _decision_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "decision_id": row["decision_id"] or "",
        "session_key": row["session_key"] or "",
        "session_id": int(row["session_id"] or 0),
        "user_id": int(row["user_id"] or 0),
        "persona_id": int(row["persona_id"] or 0),
        "decision_type": row["decision_type"] or "",
        "trigger_type": row["trigger_type"] or "",
        "trigger_payload": _load_json(row["trigger_payload_json"], {}),
        "state_snapshot": _load_json(row["state_snapshot_json"], {}),
        "candidates": _load_json(row["candidates_json"], []),
        "chosen_action": _load_json(row["chosen_action_json"], {}),
        "policy_key": row["policy_key"] or "",
        "policy_version": row["policy_version"] or "",
        "context_refs": _load_json(row["context_refs_json"], []),
        "latency_ms": float(row["latency_ms"] or 0.0),
        "cost_estimate": float(row["cost_estimate"] or 0.0),
        "created_at": row["created_at"] or "",
    }

def _outcome_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "outcome_id": row["outcome_id"] or "",
        "decision_id": row["decision_id"] or "",
        "session_key": row["session_key"] or "",
        "accepted": bool(row["accepted"]),
        "followup_depth": int(row["followup_depth"] or 0),
        "rephrase_needed": bool(row["rephrase_needed"]),
        "clicked_product": bool(row["clicked_product"]),
        "added_to_cart": bool(row["added_to_cart"]),
        "purchased": bool(row["purchased"]),
        "thumb_feedback": int(row["thumb_feedback"] or 0),
        "abuse_flag": int(row["abuse_flag"] or 0),
        "reward_score": float(row["reward_score"] or 0.0),
        "outcome_payload": _load_json(row["outcome_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }

def _reward_trace_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "trace_id": row["trace_id"] or "",
        "session_key": row["session_key"] or "",
        "decision_id": row["decision_id"] or "",
        "user_id": int(row["user_id"] or 0),
        "event_type": row["event_type"] or "",
        "surface": row["surface"] or "",
        "source": row["source"] or "",
        "origin": row["origin"] or "",
        "product_key": row["product_key"] or "",
        "event_value": float(row["event_value"] or 0.0),
        "idempotency_key": row["idempotency_key"] or "",
        "event_payload": _load_json(row["event_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }

def _retrieval_evidence_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "evidence_id": row["evidence_id"] or "",
        "session_key": row["session_key"] or "",
        "decision_id": row["decision_id"] or "",
        "policy_key": row["policy_key"] or "",
        "policy_version": row["policy_version"] or "",
        "retrieval_mode": row["retrieval_mode"] or "",
        "candidate_sources": _load_json(row["candidate_sources_json"], []),
        "selected_sources": _load_json(row["selected_sources_json"], []),
        "vector_hit_count": int(row["vector_hit_count"] or 0),
        "bundle_hit_count": int(row["bundle_hit_count"] or 0),
        "seed_hit_count": int(row["seed_hit_count"] or 0),
        "vector_limit": int(row["vector_limit"] or 0),
        "top_score": float(row["top_score"] or 0.0),
        "avg_score": float(row["avg_score"] or 0.0),
        "score_spread": float(row["score_spread"] or 0.0),
        "rerank_applied": bool(row["rerank_applied"]),
        "rerank_summary": _load_json(row["rerank_summary_json"], {}),
        "evidence_payload": _load_json(row["evidence_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }

def _proposal_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "proposal_key": row["proposal_key"] or "",
        "proposal_type": row["proposal_type"] or "",
        "policy_key": row["policy_key"] or "",
        "status": row["status"] or "proposed",
        "confidence": float(row["confidence"] or 0.0),
        "impact_score": float(row["impact_score"] or 0.0),
        "evidence": _load_json(row["evidence_json"], {}),
        "proposal": _load_json(row["proposal_json"], {}),
        "window_days": int(row["window_days"] or 0),
        "evaluator_version": row["evaluator_version"] or "",
        "reviewed_by": row["reviewed_by"] or "",
        "review_note": row["review_note"] or "",
        "reviewed_at": row["reviewed_at"] or "",
        "applied_version_key": row["applied_version_key"] or "",
        "applied_by": row["applied_by"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "applied_at": row["applied_at"] or "",
    }

def _policy_version_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "version_key": row["version_key"] or "",
        "policy_key": row["policy_key"] or "",
        "version_label": row["version_label"] or "",
        "status": row["status"] or "",
        "source_proposal_key": row["source_proposal_key"] or "",
        "config": _load_json(row["config_json"], {}),
        "approved_by": row["approved_by"] or "",
        "approved_at": row["approved_at"] or "",
        "applied_by": row["applied_by"] or "",
        "applied_at": row["applied_at"] or "",
        "review_note": row["review_note"] or "",
        "created_at": row["created_at"] or "",
    }

def _rollout_alert_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "alert_key": row["alert_key"] or "",
        "policy_key": row["policy_key"] or "",
        "version_key": row["version_key"] or "",
        "version_label": row["version_label"] or "",
        "alert_type": row["alert_type"] or "",
        "severity": row["severity"] or "",
        "status": row["status"] or "",
        "recommendation": row["recommendation"] or "",
        "reason_text": row["reason_text"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "observed_at": row["observed_at"] or "",
        "created_at": row["created_at"] or "",
        "resolved_at": row["resolved_at"] or "",
    }

def _routing_provider_stat_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    exposure_count = int(row["exposure_count"] or 0)
    success_count = int(row["success_count"] or 0)
    reward_sum = float(row["reward_sum"] or 0.0)
    return {
        "id": int(row["id"]),
        "bucket_key": row["bucket_key"] or "",
        "target": row["target"] or "",
        "provider": row["provider"] or "",
        "exposure_count": exposure_count,
        "success_count": success_count,
        "reward_sum": reward_sum,
        "guard_fail_count": int(row["guard_fail_count"] or 0),
        "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
        "avg_cost_estimate": float(row["avg_cost_estimate"] or 0.0),
        "avg_reward": round(reward_sum / max(1, exposure_count), 4),
        "success_rate": round(success_count / max(1, exposure_count), 4),
        "last_outcome_at": row["last_outcome_at"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "updated_at": row["updated_at"] or "",
    }

def _memory_retention_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    retention_key = row["retention_key"] if "retention_key" in row_keys else row["memory_key"]
    user_id = int(row["user_id"] or 0) if "user_id" in row_keys else 0
    session_key = row["session_key"] if "session_key" in row_keys else ""
    fact_key = row["fact_key"] if "fact_key" in row_keys else ""
    source_ref = row["source_ref"] if "source_ref" in row_keys else ":".join(
        part for part in (row["target_type"] if "target_type" in row_keys else "", row["target_id"] if "target_id" in row_keys else "") if part
    )
    decay_state = row["decay_state"] if "decay_state" in row_keys else ""
    return {
        "id": int(row["id"]),
        "retention_key": retention_key or "",
        "memory_key": retention_key or "",
        "user_id": user_id,
        "session_key": session_key or "",
        "memory_tier": row["memory_tier"] or "",
        "memory_kind": row["memory_kind"] or "",
        "fact_key": fact_key or "",
        "source_ref": source_ref or "",
        "target_type": row["target_type"] if "target_type" in row_keys else "",
        "target_id": row["target_id"] if "target_id" in row_keys else "",
        "confirmed_hits": int(row["confirmed_hits"] or 0),
        "reinforcement_count": int(row["reinforcement_count"] or 0),
        "cumulative_reward": float(row["cumulative_reward"] or 0.0),
        "last_hit_at": row["last_hit_at"] or "",
        "last_promoted_at": row["last_promoted_at"] or "",
        "decay_state": decay_state or "",
        "status": row["status"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "updated_at": row["updated_at"] or "",
    }
