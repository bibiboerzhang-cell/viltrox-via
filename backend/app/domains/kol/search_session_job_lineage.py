"""Progressive search-session lineage reduction shared by web and workers."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.domains.kol.search_session_job_support import (
    LINEAGE_STAGE_ROLES,
    as_dict,
    int_or_none,
    item_profile_state,
    json_dumps,
    loads,
)
from app.domains.tasks.search_session_lineage import search_session_lineages


LINEAGE_ACTIVE_JOB_STATUSES = frozenset(
    {"queued", "running", "retrying", "processing"}
)
LINEAGE_READY_JOB_STATUSES = frozenset({"done"})
LINEAGE_FAILED_JOB_STATUSES = frozenset(
    {"failed", "blocked", "triage", "cancelled"}
)
# Optional enrichment statuses never launder a real terminal job failure.  They
# only describe best-effort gaps once the required profile/result is available.
OPTIONAL_GAP_SIGNALS = ("audience", "contact")
OPTIONAL_COMPLETE_STATUSES = frozenset(
    {"ready", "ok", "done", "complete", "completed", "already_enriched", "enriched"}
)
OPTIONAL_EMPTY_STATUSES = frozenset(
    {"no_contacts", "no_audience", "not_found", "unavailable", "not_applicable", "skipped"}
)


def optional_gap_state(status: str, *, role_state: str) -> str:
    """Classify one optional sub-stage without overstating completion."""

    if role_state == "ready":
        return "complete"
    normalized = str(status or "").strip().lower()
    if not normalized:
        return "not_requested"
    if normalized in OPTIONAL_COMPLETE_STATUSES:
        return "complete"
    if normalized in OPTIONAL_EMPTY_STATUSES:
        return "empty"
    return "incomplete"


def optional_gaps(
    profile_execute: dict[str, Any],
    role_states: dict[str, str],
) -> dict[str, Any]:
    gaps: dict[str, Any] = {}
    incomplete: list[str] = []
    for signal in OPTIONAL_GAP_SIGNALS:
        enrichment = as_dict(profile_execute.get(f"{signal}_enrichment"))
        status = str(enrichment.get("status") or "").strip().lower()
        state = optional_gap_state(status, role_state=role_states.get(signal, ""))
        gaps[signal] = {"state": state, "status": status}
        if state == "incomplete":
            incomplete.append(signal)
    gaps["incomplete"] = incomplete
    return gaps


def item_video_resolution_state(
    item_payload: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    flow = as_dict(item_payload.get("video_flow"))
    resolution = as_dict(item_payload.get("video_url_resolution")) or as_dict(
        flow.get("resolution_progress")
    )
    resolver_jobs = [
        job for job in jobs if str(job.get("role") or "").lower() == "resolver"
    ]
    if resolver_jobs:
        latest = max(
            resolver_jobs,
            key=lambda job: (
                str(job.get("updated_at") or ""),
                int(job.get("id") or 0),
            ),
        )
        latest_progress = as_dict(
            as_dict(latest.get("payload")).get("video_url_resolution")
        )
        if latest_progress:
            resolution = latest_progress
    return resolution


def resolution_stage(resolution: dict[str, Any]) -> str:
    return {
        "resolve_video": "identified",
        "identify_creator": "profile",
        "cache_media": "evidence",
        "ai_analysis": "analysis",
    }.get(str(resolution.get("current_step") or "").strip().lower(), "identified")


def lineage_role_state(statuses: list[str]) -> str:
    normalized = {
        str(value or "").strip().lower()
        for value in statuses
        if str(value or "").strip()
    }
    if normalized & LINEAGE_ACTIVE_JOB_STATUSES:
        return "active"
    if normalized & LINEAGE_FAILED_JOB_STATUSES:
        return "failed"
    if normalized and normalized <= LINEAGE_READY_JOB_STATUSES:
        return "ready"
    return "failed" if normalized else "not_requested"


def lineage_jobs_for_item(
    conn: psycopg.Connection[Any],
    *,
    session_id: int,
    item_id: int,
) -> list[dict[str, Any]]:
    """Read every shared queue job serving one Smart Search item."""

    containment = json_dumps(
        [
            {
                "search_session_id": int(session_id),
                "search_session_item_id": int(item_id),
            }
        ]
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
        raw_payload = (
            row.get("payload")
            if isinstance(row.get("payload"), dict)
            else loads(row.get("payload"), {})
        )
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        matching = [
            entry
            for entry in search_session_lineages(payload)
            if int_or_none(entry.get("search_session_id")) == int(session_id)
            and int_or_none(entry.get("search_session_item_id")) == int(item_id)
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


def lineage_item_state(
    item_payload: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce profile plus downstream jobs into one honest item state."""

    profile_execute = item_profile_state(item_payload)
    profile_status = str(profile_execute.get("status") or "").strip().lower()
    resolution = item_video_resolution_state(item_payload, jobs)
    resolution_base = str(resolution.get("base_status") or "").strip().lower()
    if resolution and not profile_status:
        profile_status = resolution_base or str(
            resolution.get("status") or ""
        ).strip().lower()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        role = str(job.get("role") or "").strip().lower()
        if role:
            grouped.setdefault(role, []).append(job)
    latest_by_role = {
        role: max(
            role_jobs,
            key=lambda job: (
                str(job.get("updated_at") or ""),
                int(job.get("id") or 0),
            ),
        )
        for role, role_jobs in grouped.items()
    }
    role_states = {
        role: (
            "partial"
            if "skipped_legacy_cache_unverified"
            in str(job.get("last_error") or "").strip().lower()
            else lineage_role_state([str(job.get("status") or "")])
        )
        for role, job in latest_by_role.items()
    }
    active = any(state == "active" for state in role_states.values())
    failed = any(state in {"failed", "partial"} for state in role_states.values())
    gaps = optional_gaps(profile_execute, role_states)

    if "failed" in profile_status or profile_status in {
        "error",
        "crawl_failed",
        "unsupported",
    }:
        item_status, stage = "failed", "profile"
    elif active:
        item_status, stage = "running", (
            resolution_stage(resolution)
            if role_states.get("resolver") == "active" and resolution
            else "analysis"
        )
    elif failed:
        item_status, stage = "partial", "summary"
    elif profile_status in {"ready", "already_analyzed"} or resolution_base == "ready":
        item_status, stage = "ready", "summary"
    else:
        item_status, stage = "partial", "profile"

    downstream = {
        role: {
            "state": role_states.get(role, "not_requested"),
            "job_ids": [
                int(job["id"])
                for job in grouped.get(role, [])
                if job.get("id") is not None
            ],
            "last_error": next(
                (
                    str(job.get("last_error") or "")[:500]
                    for job in reversed(grouped.get(role, []))
                    if job.get("last_error")
                ),
                "",
            ),
        }
        for role in sorted(set(LINEAGE_STAGE_ROLES) | set(grouped))
    }
    return {
        "item_status": item_status,
        "stage": stage,
        "profile_status": profile_status,
        "downstream": downstream,
        "optional_gaps": gaps,
        "required_tasks_complete": item_status == "ready" and not active,
    }


# Compatibility aliases retain the historical worker-private symbol names.
_LINEAGE_STAGE_ROLES = LINEAGE_STAGE_ROLES
_item_profile_state = item_profile_state
_lineage_item_state = lineage_item_state
_lineage_jobs_for_item = lineage_jobs_for_item
_lineage_role_state = lineage_role_state
_optional_gap_state = optional_gap_state


__all__ = [
    "LINEAGE_STAGE_ROLES",
    "item_profile_state",
    "lineage_item_state",
    "lineage_jobs_for_item",
    "lineage_role_state",
    "optional_gap_state",
    "_LINEAGE_STAGE_ROLES",
    "_item_profile_state",
    "_lineage_item_state",
    "_lineage_jobs_for_item",
    "_lineage_role_state",
    "_optional_gap_state",
]
