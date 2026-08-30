"""Final-v1 analysis projections used by search-session job reconciliation."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.kol.search_session_job_support import (
    LINEAGE_STAGE_ROLES,
    as_dict,
    compact_text,
    derive_method,
    final_v1_payload,
    int_or_none,
    item_profile_state,
    json_dumps,
    loads,
    score_confidence,
    score_value,
    target,
)


def score_entry(layer6: dict[str, Any], key: str) -> dict[str, Any] | None:
    scores = as_dict(layer6.get("scores"))
    raw = scores.get(key)
    if raw is None and key == "marketing_value_score":
        raw = layer6.get("marketing_value_score")
    value = score_value(raw)
    if value is None:
        return None
    entry: dict[str, Any] = {"score": value}
    confidence = score_confidence(raw)
    if confidence is not None:
        entry["confidence"] = confidence
    if isinstance(raw, dict):
        for meta_key in ("rationale", "reason", "evidence"):
            if raw.get(meta_key) is not None:
                entry[meta_key] = compact_text(raw.get(meta_key), 420)
    return entry


def search_session_analysis_summary_from_result(
    *,
    cache_id: int | None,
    derive_method: str,
    target_type: str,
    target_id: str,
    evidence: dict[str, Any] | None,
    result: dict[str, Any],
    cost: float | None = None,
) -> dict[str, Any] | None:
    if derive_method != "video_analysis_final_v1" or target_type != "video":
        return None
    payload = final_v1_payload(result)
    layer1 = as_dict(payload.get("layer1_visual_content"))
    layer5 = as_dict(payload.get("layer5_recommendations"))
    layer6 = as_dict(payload.get("layer6_flags_and_scores"))
    cost_info = as_dict(payload.get("cost"))
    marketing = score_entry(layer6, "marketing_value_score")
    if not marketing:
        return {
            "status": "ready",
            "derive_method": derive_method,
            "cache_id": cache_id,
            "source_evidence_id": int_or_none(target_id),
            "missing": "marketing_value_score",
        }
    score_keys = (
        "content_quality_score",
        "viewer_heart_score",
        "channel_value_score",
        "asset_reuse_score",
        "product_proof_score",
        "marketing_value_score",
    )
    scores = {
        key: entry
        for key in score_keys
        if (entry := score_entry(layer6, key))
    }
    evidence = evidence or {}
    return {
        "status": "ready",
        "derive_method": derive_method,
        "cache_id": cache_id,
        "source_evidence_id": int_or_none(target_id),
        "kol_pool_id": int_or_none(
            evidence.get("kol_pool_id")
            or as_dict(payload.get("source")).get("kol_pool_id")
        ),
        "source_url": evidence.get("content_url")
        or as_dict(payload.get("source")).get("url"),
        "title": compact_text(
            evidence.get("title")
            or evidence.get("video_title")
            or as_dict(payload.get("source")).get("title"),
            320,
        ),
        "llm_v6_fit": marketing.get("score"),
        "confidence": marketing.get("confidence"),
        "scores": scores,
        "summary": compact_text(
            layer1.get("content_summary")
            or layer6.get("key_hook")
            or layer6.get("final_verdict"),
            700,
        ),
        "recommendations": {
            "cooperation_recommendation": layer5.get("cooperation_recommendation"),
            "buyout_or_license_recommendation": layer5.get(
                "buyout_or_license_recommendation"
            ),
            "why": layer5.get("why"),
        },
        "risk": {
            "risk_flags": layer6.get("risk_flags"),
            "final_verdict": layer6.get("final_verdict"),
            "key_hook": layer6.get("key_hook"),
        },
        "cost": cost,
        "latency_ms": int_or_none(cost_info.get("latency_ms")),
    }


def search_session_analysis_summary_from_ready_cache(
    conn: psycopg.Connection[Any],
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    target_type, target_id = target(payload)
    method = derive_method(payload)
    if method != "video_analysis_final_v1" or target_type != "video" or not target_id:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_type, target_id, derive_method, model,
                   prompt_version, result, cost, status
            FROM vkpi_analysis_cache
            WHERE target_type=%s
              AND target_id=%s
              AND derive_method=%s
              AND status='ready'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (target_type, target_id, method),
        )
        cache = cur.fetchone()
        cur.execute(
            """
            SELECT id, kol_pool_id, content_url, title, video_title
            FROM vkpi_kol_video_evidence
            WHERE id=%s
            LIMIT 1
            """,
            (int_or_none(target_id),),
        )
        evidence = cur.fetchone() or {}
    if not cache:
        return None
    result = (
        cache.get("result")
        if isinstance(cache.get("result"), dict)
        else loads(cache.get("result"), {})
    )
    summary = search_session_analysis_summary_from_result(
        cache_id=int_or_none(cache.get("id")),
        derive_method=method,
        target_type=target_type,
        target_id=target_id,
        evidence=dict(evidence),
        result=result if isinstance(result, dict) else {},
        cost=float(cache.get("cost") or 0.0),
    )
    reuse = canonical_final_v1_cache_reuse(
        cache,
        target_type=target_type,
        target_id=target_id,
        derive_method=method,
    )
    if reuse.get("reusable") is not True:
        summary.update(
            {
                "status": "legacy_unverified",
                "cache_reuse_status": "legacy_unverified",
                "revalidation_required": True,
                "evaluation_only": False,
                "production_authorized": False,
                "claim_status": "descriptive_only",
                "model_readiness_status": "legacy_cache_unverified",
            }
        )
    return summary


_PROFILE_ACTIVE_STATES = frozenset(
    {
        "",
        "unknown",
        "planned",
        "pending",
        "queued",
        "running",
        "retrying",
        "processing",
    }
)
_PROFILE_FAILURE_STATES = frozenset(
    {"error", "crawl_failed", "unsupported"}
)
_ITEM_TERMINAL_STATES = frozenset(
    {
        "ready",
        "already_analyzed",
        "partial",
        "failed",
        "skipped",
        "blocked",
        "cancelled",
        "canceled",
    }
)
_PROFILE_SUCCESS_FALLBACK_STATES = frozenset(
    {"ready", "already_analyzed", "partial"}
)
_DOWNSTREAM_STATES = frozenset(
    {"ready", "active", "failed", "not_requested"}
)


def _summary_item(row: dict[str, Any]) -> dict[str, Any]:
    payload = (
        row.get("payload_json")
        if isinstance(row.get("payload_json"), dict)
        else loads(row.get("payload_json"), {})
    )
    updated_at = row.get("updated_at")
    return {
        "id": row.get("id"),
        "item_type": row.get("item_type"),
        "status": row.get("status"),
        "stage": row.get("stage"),
        "rank": row.get("rank"),
        "score": row.get("score"),
        "kol_pool_id": row.get("kol_pool_id"),
        "evidence_id": row.get("evidence_id"),
        "job_id": row.get("job_id"),
        "source_url": row.get("source_url"),
        "job_status": payload.get("job_status")
        if isinstance(payload, dict)
        else None,
        "job_last_error": payload.get("job_last_error")
        if isinstance(payload, dict)
        else None,
        "analysis": payload.get("analysis")
        if isinstance(payload, dict)
        else None,
        "profile_status": str(item_profile_state(payload).get("status") or "")
        .strip()
        .lower()
        if isinstance(payload, dict)
        else "",
        "downstream": payload.get("downstream_jobs")
        if isinstance(payload, dict)
        else None,
        "updated_at": updated_at.isoformat()
        if hasattr(updated_at, "isoformat")
        else updated_at,
    }


def _summary_items(item_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_summary_item(row) for row in item_rows]


def _profile_status(item: dict[str, Any]) -> str:
    return str(item.get("profile_status") or "").strip().lower()


def _profile_failed(item: dict[str, Any]) -> bool:
    status = _profile_status(item)
    return "failed" in status or status in _PROFILE_FAILURE_STATES


def _profile_terminal(item: dict[str, Any]) -> bool:
    status = _profile_status(item)
    return status not in _PROFILE_ACTIVE_STATES or (
        not status
        and str(item.get("status") or "").strip().lower()
        in _ITEM_TERMINAL_STATES
    )


def _profile_succeeded(item: dict[str, Any]) -> bool:
    status = _profile_status(item)
    return (
        status not in _PROFILE_ACTIVE_STATES
        and "failed" not in status
        and status not in _PROFILE_FAILURE_STATES
    ) or (
        not status
        and str(item.get("status") or "").strip().lower()
        in _PROFILE_SUCCESS_FALLBACK_STATES
    )


def _profile_counts(
    items: list[dict[str, Any]],
    counts: dict[str, Any],
    *,
    progressive: bool,
) -> tuple[int, int]:
    profile_ready = sum(
        1
        for item in items
        if item.get("profile_status") in {"ready", "already_analyzed"}
    )
    profile_failed = sum(1 for item in items if _profile_failed(item))
    if not progressive:
        profile_ready = int(counts.get("ready") or 0)
        profile_failed = int((counts.get("by_status") or {}).get("failed") or 0)
    return profile_ready, profile_failed


def _downstream_state(item: dict[str, Any], role: str) -> str:
    downstream = (
        item.get("downstream")
        if isinstance(item.get("downstream"), dict)
        else {}
    )
    state = str(as_dict(downstream.get(role)).get("state") or "not_requested")
    normalized = state.strip().lower()
    return normalized if normalized in _DOWNSTREAM_STATES else "failed"


def _role_progress(
    items: list[dict[str, Any]],
    *,
    role: str,
    total: int,
) -> dict[str, int]:
    states = [_downstream_state(item, role) for item in items]
    requested = sum(1 for state in states if state != "not_requested")
    return {
        "ready": sum(1 for state in states if state == "ready"),
        "active": sum(1 for state in states if state == "active"),
        "failed": sum(1 for state in states if state == "failed"),
        "not_requested": max(0, total - requested),
    }


def _stage_progress(
    items: list[dict[str, Any]],
    *,
    total: int,
) -> dict[str, dict[str, int]]:
    return {
        role: _role_progress(items, role=role, total=total)
        for role in LINEAGE_STAGE_ROLES
    }


def _terminal_item_count(counts: dict[str, Any]) -> int:
    by_status = counts.get("by_status") or {}
    return sum(
        int(by_status.get(status) or 0)
        for status in _ITEM_TERMINAL_STATES
    )


def _progress_state(
    current_summary: dict[str, Any],
    items: list[dict[str, Any]],
    counts: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, int]], int, int, int, bool]:
    progress = (
        current_summary.get("progress")
        if isinstance(current_summary.get("progress"), dict)
        else {}
    )
    progressive = any(
        item.get("profile_status") or item.get("downstream") for item in items
    )
    profile_ready, profile_failed = _profile_counts(
        items,
        counts,
        progressive=progressive,
    )
    total = int(progress.get("total") or len(items))
    stages = _stage_progress(items, total=total)
    terminal_count = _terminal_item_count(counts)
    profile_terminal_count = terminal_count
    profile_succeeded_count = profile_ready
    if progressive:
        profile_terminal_count = sum(1 for item in items if _profile_terminal(item))
        profile_succeeded_count = sum(
            1 for item in items if _profile_succeeded(item)
        )
    profile_terminal_count = min(
        total,
        max(profile_terminal_count, int(progress.get("profile_completed") or 0)),
    )
    profile_succeeded_count = max(
        profile_succeeded_count,
        min(
            profile_terminal_count,
            int(progress.get("profile_succeeded") or 0),
        ),
    )
    merged = {
        **progress,
        "base": max(int(progress.get("base") or 0), len(items)),
        "total": total,
        "profile_ready": profile_ready,
        "profile_failed": profile_failed,
        "profile_completed": profile_terminal_count,
        "profile_succeeded": profile_succeeded_count,
        "profile_remaining": max(0, total - profile_terminal_count),
        "complete_ready": int(counts.get("ready") or 0),
        "complete_partial": int((counts.get("by_status") or {}).get("partial") or 0),
    }
    if progressive:
        merged.update(
            {
                "video": stages["video"],
                "comments": stages["comments"],
                "audience": stages["audience"],
            }
        )
    return (
        merged,
        stages,
        terminal_count,
        profile_ready,
        profile_failed,
        progressive,
    )


def _primary_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            item
            for item in items
            if str(item.get("item_type") or "").startswith("url_")
        ),
        items[0] if items else {},
    )


def build_search_session_summary(
    current_summary: dict[str, Any],
    item_rows: list[dict[str, Any]],
    *,
    session_status: str,
) -> dict[str, Any]:
    items = _summary_items(item_rows)
    counts = search_session_item_counts(items)
    progress, stages, terminal_count, profile_ready, profile_failed, progressive = (
        _progress_state(current_summary, items, counts)
    )
    active_downstream = sum(stage["active"] for stage in stages.values())
    contract = completion_contract(
        base_count=int(progress.get("base") or 0),
        total=int(progress.get("total") or 0),
        terminal_count=terminal_count,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=active_downstream,
        stage_progress=stages if progressive else None,
    )
    progress.update(contract)
    primary = _primary_item(items)
    phase = (
        "profile"
        if session_status == "running"
        else ("complete" if session_status == "ready" else "partial")
    )
    summary = {
        **current_summary,
        "phase": phase,
        "progress": progress,
        "item_status": primary.get("status"),
        "job_status": primary.get("job_status"),
        "job_last_error": primary.get("job_last_error"),
        "analysis": primary.get("analysis"),
        "counts": counts,
        "items_written": len(items),
        **contract,
    }
    if session_status != "running":
        summary["terminal_synced_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def search_session_item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_stage: dict[str, int] = {}
    ready = errors = skipped = executed = 0
    for item in items:
        status = str(item.get("status") or "unknown").strip()
        stage = str(item.get("stage") or "identified").strip()
        by_status[status] = by_status.get(status, 0) + 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if status in {"ready", "already_analyzed"}:
            ready += 1
        if status in {"failed", "partial"}:
            errors += 1
        if status == "skipped":
            skipped += 1
        if status not in {
            "planned",
            "identified",
            "matched",
            "queued",
            "running",
            "unknown",
        }:
            executed += 1
    return {
        "by_status": by_status,
        "by_stage": by_stage,
        "ready": ready,
        "errors": errors,
        "skipped": skipped,
        "executed": executed,
    }


def rebuild_search_session_summary(
    cur: Any,
    *,
    session_id: int,
    session_status: str,
) -> None:
    cur.execute(
        """
        SELECT result_summary_json
        FROM vkpi_kol_search_sessions
        WHERE id=%s
        LIMIT 1
        """,
        (int(session_id),),
    )
    session_row = cur.fetchone() or {}
    current_summary = session_row.get("result_summary_json")
    current_summary = (
        current_summary
        if isinstance(current_summary, dict)
        else loads(current_summary, {})
    )
    if not isinstance(current_summary, dict):
        current_summary = {}
    cur.execute(
        """
        SELECT id, item_type, status, stage, rank, score, kol_pool_id, evidence_id, job_id, source_url, payload_json, updated_at
        FROM vkpi_kol_search_session_items
        WHERE session_id=%s
        ORDER BY rank NULLS LAST, id
        """,
        (int(session_id),),
    )
    item_rows = cur.fetchall() or []
    summary = build_search_session_summary(
        current_summary,
        item_rows,
        session_status=session_status,
    )
    cur.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET status=%s,
            result_summary_json=%s::jsonb,
            updated_at=NOW()
        WHERE id=%s
        """,
        (session_status, json_dumps(summary), int(session_id)),
    )


# Historical worker-private names remain importable through compatibility files.
_score_entry = score_entry
_search_session_analysis_summary_from_result = (
    search_session_analysis_summary_from_result
)
_search_session_analysis_summary_from_ready_cache = (
    search_session_analysis_summary_from_ready_cache
)


__all__ = [
    "score_entry",
    "build_search_session_summary",
    "rebuild_search_session_summary",
    "search_session_analysis_summary_from_ready_cache",
    "search_session_analysis_summary_from_result",
    "search_session_item_counts",
    "_score_entry",
    "_search_session_analysis_summary_from_ready_cache",
    "_search_session_analysis_summary_from_result",
]
