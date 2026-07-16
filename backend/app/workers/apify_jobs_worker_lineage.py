"""Progressive search-session lineage reduction for the Apify worker.

This module is deliberately independent from ``apify_jobs_worker_session`` so
the session synchronizer can stay below the repository line-size gate without
introducing a circular import.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domains.tasks.search_session_lineage import search_session_lineages
from app.workers.apify_jobs_worker_helpers import _as_dict, _int_or_none, _json, _loads


_LINEAGE_ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "retrying", "processing"})
_LINEAGE_READY_JOB_STATUSES = frozenset({"done"})
_LINEAGE_FAILED_JOB_STATUSES = frozenset({"failed", "blocked", "triage", "cancelled"})
_LINEAGE_STAGE_ROLES = ("resolver", "video", "comments", "audience")


def _item_profile_state(item_payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize progressive profile state across discovery and URL items."""

    return {
        **_as_dict(item_payload.get("profile_flow")),
        **_as_dict(item_payload.get("profile_execute")),
    }


def _item_video_resolution_state(
    item_payload: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prefer the latest resolver job progress, with item history fallback."""

    flow = _as_dict(item_payload.get("video_flow"))
    resolution = _as_dict(item_payload.get("video_url_resolution")) or _as_dict(
        flow.get("resolution_progress")
    )
    resolver_jobs = [job for job in jobs if str(job.get("role") or "").lower() == "resolver"]
    if resolver_jobs:
        latest = max(
            resolver_jobs,
            key=lambda job: (str(job.get("updated_at") or ""), int(job.get("id") or 0)),
        )
        latest_progress = _as_dict(_as_dict(latest.get("payload")).get("video_url_resolution"))
        if latest_progress:
            resolution = latest_progress
    return resolution


def _resolution_stage(resolution: dict[str, Any]) -> str:
    return {
        "resolve_video": "identified",
        "identify_creator": "profile",
        "cache_media": "evidence",
        "ai_analysis": "analysis",
    }.get(str(resolution.get("current_step") or "").strip().lower(), "identified")


def _lineage_role_state(statuses: list[str]) -> str:
    normalized = {str(value or "").strip().lower() for value in statuses if str(value or "").strip()}
    if normalized & _LINEAGE_ACTIVE_JOB_STATUSES:
        return "active"
    if normalized & _LINEAGE_FAILED_JOB_STATUSES:
        return "failed"
    if normalized and normalized <= _LINEAGE_READY_JOB_STATUSES:
        return "ready"
    return "failed" if normalized else "not_requested"


def _lineage_jobs_for_item(
    conn: psycopg.Connection[Any],
    *,
    session_id: int,
    item_id: int,
) -> list[dict[str, Any]]:
    """Read every shared queue job serving one Smart Search item."""

    containment = _json(
        [{"search_session_id": int(session_id), "search_session_item_id": int(item_id)}]
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, job_type, status, payload, last_error, updated_at
            FROM apify_jobs
            WHERE (
                    payload->>'search_session_id'=%s
                AND payload->>'search_session_item_id'=%s
                  )
               OR COALESCE(payload->'search_session_lineage', '[]'::jsonb) @> %s::jsonb
            ORDER BY id
            """,
            (str(int(session_id)), str(int(item_id)), containment),
        )
        rows = cur.fetchall() or []

    jobs: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for row in rows:
        raw_payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload"), {})
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        matching = [
            entry
            for entry in search_session_lineages(payload)
            if _int_or_none(entry.get("search_session_id")) == int(session_id)
            and _int_or_none(entry.get("search_session_item_id")) == int(item_id)
        ]
        if not matching:
            role = str(payload.get("search_session_role") or "").strip().lower()
            matching = [{"role": role}] if role else []
        for entry in matching:
            role = str(entry.get("role") or "").strip().lower()
            if not role:
                continue
            key = (int(row.get("id") or 0), role)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(
                {
                    "id": row.get("id"),
                    "job_type": row.get("job_type"),
                    "status": str(row.get("status") or "").strip().lower(),
                    "role": role,
                    "payload": payload,
                    "last_error": row.get("last_error"),
                    "updated_at": row.get("updated_at"),
                }
            )
    return jobs


def _lineage_item_state(
    item_payload: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce profile + downstream jobs into one honest item state."""

    profile_execute = _item_profile_state(item_payload)
    profile_status = str(profile_execute.get("status") or "").strip().lower()
    resolution = _item_video_resolution_state(item_payload, jobs)
    resolution_base = str(resolution.get("base_status") or "").strip().lower()
    if resolution and not profile_status:
        profile_status = resolution_base or str(resolution.get("status") or "").strip().lower()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        role = str(job.get("role") or "").strip().lower()
        if role:
            grouped.setdefault(role, []).append(job)
    latest_by_role = {
        role: max(
            role_jobs,
            key=lambda job: (str(job.get("updated_at") or ""), int(job.get("id") or 0)),
        )
        for role, role_jobs in grouped.items()
    }
    role_states = {
        role: _lineage_role_state([str(job.get("status") or "")])
        for role, job in latest_by_role.items()
    }
    active = any(state == "active" for state in role_states.values())
    failed = any(state == "failed" for state in role_states.values())

    audience = _as_dict(profile_execute.get("audience_enrichment"))
    audience_status = str(audience.get("status") or "").strip().lower()
    audience_incomplete = audience_status in {
        "pending", "partial", "error", "failed", "waiting_for_evidence", "waiting_for_profile",
    } and role_states.get("audience") != "ready"
    contacts = _as_dict(profile_execute.get("contact_enrichment"))
    contact_status = str(contacts.get("status") or "").strip().lower()
    contact_incomplete = contact_status in {"pending", "partial", "error", "failed"}

    if "failed" in profile_status or profile_status in {"error", "crawl_failed", "unsupported"}:
        item_status, stage = "failed", "profile"
    elif active:
        item_status, stage = "running", (
            _resolution_stage(resolution)
            if role_states.get("resolver") == "active" and resolution
            else "analysis"
        )
    elif failed or audience_incomplete or contact_incomplete:
        item_status, stage = "partial", "summary"
    elif profile_status in {"ready", "already_analyzed"} or resolution_base == "ready":
        item_status, stage = "ready", "summary"
    else:
        item_status, stage = "partial", "profile"

    downstream = {
        role: {
            "state": role_states.get(role, "not_requested"),
            "job_ids": [int(job["id"]) for job in grouped.get(role, []) if job.get("id") is not None],
            "last_error": next(
                (str(job.get("last_error") or "")[:500] for job in reversed(grouped.get(role, [])) if job.get("last_error")),
                "",
            ),
        }
        for role in sorted(set(_LINEAGE_STAGE_ROLES) | set(grouped))
    }
    return {
        "item_status": item_status,
        "stage": stage,
        "profile_status": profile_status,
        "downstream": downstream,
        "required_tasks_complete": item_status == "ready" and not active,
    }
