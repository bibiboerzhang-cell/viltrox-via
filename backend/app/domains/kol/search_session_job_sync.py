"""Canonical search-session job terminal reconciliation.

Both the web-side late-link replay and the Apify worker call this domain-owned
implementation.  Provider execution stays in workers; this module only reduces
already-persisted queue state into session items and summaries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.domains.kol.search_session_job_analysis import (
    rebuild_search_session_summary as _persist_search_session_summary,
    search_session_analysis_summary_from_ready_cache,
    search_session_item_counts as _summary_item_counts,
)
from app.domains.kol.search_session_job_lineage import (
    lineage_item_state,
    lineage_jobs_for_item,
)
from app.domains.kol.search_session_job_support import as_dict, int_or_none, json_dumps, loads
from app.domains.kol.search_session_job_sync_load import load_job_lineages, resolve_item_state
from app.domains.tasks.search_session_lineage import search_session_lineages


logger = get_logger(__name__)


def search_session_job_state(raw_status: str, reason: str = "") -> tuple[str, str]:
    status = str(raw_status or "").strip().lower()
    reason_text = str(reason or "").strip().lower()
    if status == "running":
        return "running", "analysis"
    if status == "queued":
        return "queued", "analysis"
    if status == "done":
        if "skipped_legacy_cache_unverified" in reason_text:
            return "partial", "summary"
        if "skipped_existing_analysis_cache" in reason_text:
            return "already_analyzed", "summary"
        return "ready", "summary"
    if status in {"failed", "blocked", "triage"}:
        return "failed", "analysis"
    return "unknown", "analysis"


def session_url_enrichment_error(payload: dict[str, Any]) -> str:
    """Return a compact error when account/video enrichment partially failed."""

    def flow_error(flow: dict[str, Any], label: str) -> str:
        status = str(flow.get("status") or "").strip()
        errors = int_or_none(flow.get("errors")) or 0
        if errors <= 0 and "error" not in status:
            return ""
        messages: list[str] = []
        for item in flow.get("items") or []:
            if not isinstance(item, dict):
                continue
            error = str(item.get("error") or "").strip()
            if error:
                metadata = (
                    item.get("metadata")
                    if isinstance(item.get("metadata"), dict)
                    else {}
                )
                title = str(
                    metadata.get("title")
                    or metadata.get("content_url")
                    or item.get("title")
                    or item.get("content_url")
                    or "video"
                ).strip()
                messages.append(f"{title}: {error}")
            if len(messages) >= 3:
                break
        detail = "; ".join(messages) if messages else status or "partial_failure"
        return f"{label}: {detail}"

    profile_flow = (
        payload.get("profile_flow")
        if isinstance(payload.get("profile_flow"), dict)
        else {}
    )
    video_flow = (
        payload.get("video_flow")
        if isinstance(payload.get("video_flow"), dict)
        else {}
    )
    representative = profile_flow.get("representative_video_analysis") or video_flow.get(
        "representative_video_analysis"
    )
    history = profile_flow.get("history_video_evidence") or video_flow.get(
        "history_video_evidence"
    )
    parts = []
    if isinstance(representative, dict):
        error = flow_error(representative, "代表视频分析")
        if error:
            parts.append(error)
    if isinstance(history, dict):
        error = flow_error(history, "历史视频物化")
        if error:
            parts.append(error)
    return " | ".join(parts)[:1000]


def search_session_status_from_items(items: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "").strip() for item in items}
    if statuses.intersection({"queued", "running", "already_queued"}):
        return "running"
    if statuses.intersection({"failed"}):
        return "partial"
    if statuses.intersection({"partial"}):
        return "partial"
    if statuses:
        return "ready"
    return "ready"


def search_session_item_counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    return _summary_item_counts(items)


def rebuild_search_session_summary(
    cur: Any,
    *,
    session_id: int,
    session_status: str,
) -> None:
    return _persist_search_session_summary(
        cur,
        session_id=session_id,
        session_status=session_status,
    )


def sync_search_session_job(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> bool:
    """Best-effort worker-compatible wrapper around terminal reconciliation."""

    try:
        synced_count = sync_search_session_job_impl(
            conn,
            job_id,
            raw_status=raw_status,
            reason=reason,
            analysis_summary=analysis_summary,
        )
        return int(synced_count or 0) > 0
    except Exception as exc:
        logger.warning(
            "search session job sync failed | job_id=%s status=%s error=%s",
            job_id,
            raw_status,
            exc,
        )
        return False


def sync_search_session_job_impl(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    reason: str = "",
    analysis_summary: dict[str, Any] | None = None,
) -> int:
    loaded = load_job_lineages(
        conn,
        job_id,
        row_factory=dict_row,
        loads=loads,
        search_session_lineages=search_session_lineages,
        int_or_none=int_or_none,
    )
    if loaded is None:
        return 0
    row, payload, unique_lineages = loaded

    synced_items: list[dict[str, Any]] = []
    current_analysis_summary = analysis_summary
    for (session_id, item_id), roles in unique_lineages.items():
        resolver_projection: dict[str, Any] = {}
        if "resolver" in roles:
            from app.domains.kol.video_url_resolver import (
                video_url_session_sync_projection,
            )

            resolver_projection = video_url_session_sync_projection(payload)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT payload_json
                FROM vkpi_kol_search_session_items
                WHERE id=%s
                  AND session_id=%s
                LIMIT 1
                """,
                (int(item_id), int(session_id)),
            )
            item_row = cur.fetchone() or {}
        if not item_row:
            continue
        existing_payload = (
            item_row.get("payload_json")
            if isinstance(item_row.get("payload_json"), dict)
            else loads(item_row.get("payload_json"), {})
        )
        if not isinstance(existing_payload, dict):
            existing_payload = {}
        enrichment_error = session_url_enrichment_error(existing_payload)
        item_status, stage, downstream, optional_gaps, required_tasks_complete = (
            resolve_item_state(
                conn,
                existing_payload=existing_payload,
                roles=roles,
                session_id=int(session_id),
                item_id=int(item_id),
                raw_status=raw_status,
                reason=reason,
                job_row=row,
                lineage_jobs_for_item=lineage_jobs_for_item,
                lineage_item_state=lineage_item_state,
                search_session_job_state=search_session_job_state,
            )
        )
        if item_status in {"ready", "already_analyzed"} and enrichment_error:
            item_status = "partial"
            stage = "summary"
            required_tasks_complete = False
        if current_analysis_summary is None and item_status in {
            "ready",
            "already_analyzed",
            "partial",
        }:
            current_analysis_summary = search_session_analysis_summary_from_ready_cache(
                conn,
                payload,
            )
        item_error = str(
            enrichment_error or reason or row.get("last_error") or ""
        )[:1000]
        item_patch: dict[str, Any] = {
            "job_status": raw_status,
            "job_last_error": item_error,
            "job_updated_at": datetime.now(timezone.utc).isoformat(),
            "required_tasks_complete": required_tasks_complete,
        }
        if downstream is not None:
            item_patch["downstream_jobs"] = downstream
        if optional_gaps is not None:
            item_patch["optional_gaps"] = optional_gaps
        if current_analysis_summary:
            item_patch["analysis"] = current_analysis_summary
        if resolver_projection:
            item_patch.update(resolver_projection.get("payload_patch") or {})
        if downstream is not None:
            from app.domains.kol.video_url_resolver import (
                reconcile_video_url_ai_progress,
            )

            progress_source = {
                **existing_payload,
                **(resolver_projection.get("payload_patch") or {}),
            }
            reconciled_progress = reconcile_video_url_ai_progress(
                progress_source,
                downstream,
            )
            if reconciled_progress:
                item_patch["video_url_resolution"] = reconciled_progress
                compact_video_flow = as_dict(progress_source.get("video_flow"))
                item_patch["video_flow"] = {
                    **compact_video_flow,
                    "resolution_progress": reconciled_progress,
                }
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE vkpi_kol_search_session_items
                    SET status=%s,
                        stage=%s,
                        kol_pool_id=COALESCE(%s, kol_pool_id),
                        evidence_id=COALESCE(%s, evidence_id),
                        payload_json = payload_json || %s::jsonb,
                        updated_at=NOW()
                    WHERE id=%s
                      AND session_id=%s
                    """,
                    (
                        item_status,
                        stage,
                        resolver_projection.get("kol_pool_id"),
                        resolver_projection.get("evidence_id"),
                        json_dumps(item_patch),
                        int(item_id),
                        int(session_id),
                    ),
                )
                cur.execute(
                    """
                    SELECT status, stage
                    FROM vkpi_kol_search_session_items
                    WHERE session_id=%s
                    """,
                    (int(session_id),),
                )
                session_status = search_session_status_from_items(
                    [dict(item) for item in (cur.fetchall() or [])]
                )
                rebuild_search_session_summary(
                    cur,
                    session_id=int(session_id),
                    session_status=session_status,
                )
        synced_items.append(
            {
                "search_session_id": int(session_id),
                "search_session_item_id": int(item_id),
                "status": item_status,
                "stage": stage,
            }
        )

    if not synced_items:
        return 0
    payload["search_session_item_statuses"] = synced_items
    payload["search_session_last_job_status"] = raw_status
    payload["search_session_last_error"] = str(
        reason or row.get("last_error") or ""
    )[:500]
    first = synced_items[0]
    payload["search_session_item_status"] = first["status"]
    payload["search_session_stage"] = first["stage"]
    if current_analysis_summary:
        payload["search_session_cache_id"] = current_analysis_summary.get("cache_id")
        payload["search_session_analysis_status"] = current_analysis_summary.get(
            "status"
        )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET payload=%s::jsonb WHERE id=%s",
                (json_dumps(payload), int(job_id)),
            )
    return len(synced_items)


# Historical symbols used by worker imports/tests.
_rebuild_search_session_summary = rebuild_search_session_summary
_search_session_item_counts = search_session_item_counts
_search_session_job_state = search_session_job_state
_search_session_status_from_items = search_session_status_from_items
_session_url_enrichment_error = session_url_enrichment_error
_sync_search_session_job_impl = sync_search_session_job_impl


__all__ = [
    "rebuild_search_session_summary",
    "search_session_item_counts",
    "search_session_job_state",
    "search_session_status_from_items",
    "session_url_enrichment_error",
    "sync_search_session_job",
    "sync_search_session_job_impl",
    "_rebuild_search_session_summary",
    "_search_session_item_counts",
    "_search_session_job_state",
    "_search_session_status_from_items",
    "_session_url_enrichment_error",
    "_sync_search_session_job_impl",
]
